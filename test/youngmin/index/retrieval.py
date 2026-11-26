"""검색 부품 — 벡터 · BM25 · RRF 결합 · 재정렬 (architecture.md §5).

구성마다 칸을 하나씩 더한다.
    A  벡터
    B  + BM25 + RRF
    C  + 재정렬 (RRF 후보 → 다국어 Cross-Encoder → 상위 5)
    C+ + 질문 전체를 영어 검색어로 (팀 번역 도구 rewrite_movie_query)
벡터와 BM25 는 같은 글자("머리말 + 본문")를 본다 (§3). 재정렬도 같은 글자를 질문과 한 쌍으로 읽는다.
"""

import re
from contextlib import closing
from dataclasses import dataclass
from typing import Any

from rank_bm25 import BM25Okapi

from shared.core.timing import measure_time

RRF_K = 60  # RRF 의 순위 완화 상수 (흔히 쓰는 값)
RERANKER = (
    "Dongjin-kr/ko-reranker"  # 한국어 · 영어 쌍으로 학습한 다국어 Cross-Encoder (XLM-RoBERTa)
)
TOKEN = re.compile(r"[0-9a-z가-힣]+")

ROWS = """
SELECT e.id, e.document, e.cmetadata
FROM langchain_pg_embedding e
JOIN langchain_pg_collection c ON e.collection_id = c.uuid
WHERE c.name = %s
"""


@dataclass
class Hit:
    chunk_id: str
    text: str
    header: str
    vector_score: float | None = None
    bm25_score: float | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None
    rank_before_rerank: int | None = None

    @property
    def search_text(self) -> str:
        return f"{self.header}\n{self.text}" if self.header else self.text


def tokenize(text: str) -> list[str]:
    """BM25 용 낱말. 영어는 소문자로, 한국어는 붙은 덩어리 그대로 (문서가 영어라 한국어는 거의 걸리지 않는다)"""
    return TOKEN.findall(text.lower())


def rrf(rankings: list[list[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """여러 순위표를 1/(k + 순위) 의 합으로 합친다. 점수가 같으면 먼저 나온 순위표의 순서를 따른다"""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    order = {cid: i for i, cid in enumerate(dict.fromkeys(c for r in rankings for c in r))}
    return sorted(scores.items(), key=lambda item: (-item[1], order[item[0]]))


class Corpus:
    """고정 문서 컬렉션의 행 전부 — BM25 색인과 청크 조회. DB 에서 한 번 읽는다"""

    def __init__(self, rows: list[tuple[str, str, dict[str, Any]]]) -> None:
        self.hits = {
            cid: Hit(cid, text or "", (meta or {}).get("header", "")) for cid, text, meta in rows
        }
        self.ids = list(self.hits)
        self.bm25 = BM25Okapi([tokenize(self.hits[cid].search_text) for cid in self.ids])
        # 원칙 5 — 청크가 속한 절(또는 에피소드). 부모 안의 청크는 문서 안 순서(position)대로
        self.parent_of: dict[str, str] = {}
        members: dict[str, list[tuple[int, str]]] = {}
        for cid, _, meta in rows:
            parent = ((meta or {}).get("source_ids") or {}).get("parent_id")
            if parent:  # 작품 카드 · 연결 카드는 부모가 없다
                self.parent_of[cid] = parent
                members.setdefault(parent, []).append((int(meta.get("position", 0)), cid))
        self.parents = {pid: [cid for _, cid in sorted(found)] for pid, found in members.items()}

    @classmethod
    def from_db(cls, collection: str) -> "Corpus":
        from shared.core.vector_store import get_raw_db_connection

        with closing(get_raw_db_connection()) as conn, conn.cursor() as cur:
            cur.execute(ROWS, (collection,))
            return cls(cur.fetchall())

    def bm25_search(self, query: str, k: int) -> list[tuple[str, float]]:
        scores = self.bm25.get_scores(tokenize(query))
        best = sorted(range(len(self.ids)), key=lambda i: -scores[i])[:k]
        return [(self.ids[i], float(scores[i])) for i in best if scores[i] > 0]


@dataclass
class Block:
    """답변 모델에 넘기는 근거 한 덩어리. ids 는 이 덩어리 안의 검색된 청크 (인용은 이 ID 로)"""

    ids: list[str]
    header: str
    text: str


def window(members: list[str], texts: dict[str, str], hits: list[str], max_chars: int) -> str:
    """부모 안에서 검색된 청크를 가운데 두고 앞뒤 문단을 번갈아 붙인다. max_chars 를 넘기 전까지.

    부모 전체가 max_chars 안이면 부모 전체다. 잘린 쪽에는 "…" 를 둔다.
    검색된 청크끼리 멀리 떨어져 그것만으로 넘치면 검색된 청크만 "…" 로 잇는다.
    """
    at = sorted(members.index(cid) for cid in hits)
    start, end = at[0], at[-1]

    def size(a: int, b: int) -> int:
        return sum(len(texts[c]) for c in members[a : b + 1]) + (b - a)

    if size(start, end) > max_chars:
        return "\n…\n".join(texts[members[i]] for i in at)
    turn = 0
    while start > 0 or end < len(members) - 1:
        left = start > 0 and (turn % 2 == 0 or end == len(members) - 1)
        a, b = (start - 1, end) if left else (start, end + 1)
        if size(a, b) > max_chars:
            break
        start, end, turn = a, b, turn + 1
    body = "\n".join(texts[c] for c in members[start : end + 1])
    return ("…\n" if start > 0 else "") + body + ("\n…" if end < len(members) - 1 else "")


def parent_blocks(top: list[Hit], corpus: "Corpus", max_chars: int) -> list[Block]:
    """원칙 5 — 작게 찾고 크게 넘긴다. 상위 청크를 부모 단위로 묶어 순위 순서대로 돌려준다"""
    grouped: dict[str, list[Hit]] = {}
    for hit in top:
        grouped.setdefault(corpus.parent_of.get(hit.chunk_id, hit.chunk_id), []).append(hit)
    texts = {cid: corpus.hits[cid].text for cid in corpus.hits}
    blocks = []
    for key, hits in grouped.items():
        ids = [h.chunk_id for h in hits]
        if key in corpus.parents:
            text = window(corpus.parents[key], texts, ids, max_chars)
        else:
            text = hits[0].text
        blocks.append(Block(ids, hits[0].header, text))
    return blocks


class Reranker:
    """다국어 Cross-Encoder. 질문과 청크를 한 쌍으로 읽어 점수를 낸다 (sentence-transformers 가 필요하다)"""

    def __init__(self, model_name: str = RERANKER, max_length: int = 512) -> None:
        try:
            import torch
            from sentence_transformers import CrossEncoder
        except ImportError as exc:  # 팀 의존성에 없어 실행할 때만 붙인다
            raise RuntimeError(
                "재정렬에는 sentence-transformers 가 필요합니다: "
                "uv run --with sentence-transformers==5.2.0 ..."
            ) from exc
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.name = model_name
        self.model = CrossEncoder(model_name, max_length=max_length, device=device)

    def scores(self, query: str, texts: list[str]) -> list[float]:
        return [float(s) for s in self.model.predict([(query, t) for t in texts])]


class Searcher:
    """구성 하나의 검색. retrieve() 는 후보 전부를 최종 순위대로 돌려준다 (상위 5개가 생성 모델로 간다)"""

    def __init__(
        self,
        mode: str,
        store: Any,
        corpus: Corpus | None = None,
        reranker: Reranker | None = None,
        candidates: int = 20,
    ) -> None:
        if mode not in ("A", "B", "C", "C+"):
            raise ValueError(f"구성은 A · B · C · C+ 중 하나입니다: {mode}")
        if mode != "A" and corpus is None:
            raise ValueError("B 부터는 BM25 코퍼스가 필요합니다")
        if mode in ("C", "C+") and reranker is None:
            raise ValueError("C 부터는 재정렬 모델이 필요합니다")
        self.mode, self.store, self.corpus, self.reranker = mode, store, corpus, reranker
        self.candidates = candidates

    @measure_time(label="retrieve")
    def first_stage(self, query: str) -> list[Hit]:
        """벡터(A), 또는 벡터 + BM25 를 RRF 로 합친 후보 (B 부터)"""
        found = self.store.similarity_search_with_score(query, k=self.candidates)
        vector = {
            doc.metadata["chunk_id"]: Hit(
                doc.metadata["chunk_id"],
                doc.page_content,
                doc.metadata.get("header", ""),
                vector_score=round(1 - distance, 4),
            )
            for doc, distance in found
        }
        if self.mode == "A":
            return list(vector.values())
        assert self.corpus is not None
        bm25 = dict(self.corpus.bm25_search(query, self.candidates))
        fused = rrf([list(vector), list(bm25)])[: self.candidates]
        hits = []
        for chunk_id, score in fused:
            hit = vector.get(chunk_id) or Hit(**vars(self.corpus.hits[chunk_id]))
            hit.bm25_score = round(bm25[chunk_id], 4) if chunk_id in bm25 else None
            hit.rrf_score = round(score, 6)
            hits.append(hit)
        return hits

    @measure_time(label="rerank")
    def rerank(self, query: str, hits: list[Hit]) -> list[Hit]:
        assert self.reranker is not None
        for rank, (hit, score) in enumerate(
            zip(hits, self.reranker.scores(query, [h.search_text for h in hits]), strict=True), 1
        ):
            hit.rank_before_rerank, hit.rerank_score = rank, round(score, 4)
        return sorted(hits, key=lambda h: -(h.rerank_score or 0.0))

    def retrieve(self, query: str) -> list[Hit]:
        hits = self.first_stage(query)
        return self.rerank(query, hits) if self.mode in ("C", "C+") and hits else hits
