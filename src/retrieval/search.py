"""검색: A 벡터 검색, B BM25 + RRF 결합 (architecture §5).

DB·임베딩을 부르는 함수는 검색 대상(store)을 인자로 받는다. 테스트에서 가짜 store 를 넣기 위해서다.
"""

import re
from collections.abc import Iterable

from langchain_core.vectorstores import VectorStore
from rank_bm25 import BM25Okapi

from shared.core.models import Candidate

Hit = tuple[str, float]  # (chunk_id, 점수)

RRF_K = 60
_TOKEN = re.compile(r"[a-z0-9]+|[가-힣]+")


def tokenize(text: str) -> list[str]:
    """영어 소문자 · 숫자 · 한글 단어. 문서는 영어이고, 질문에는 이름 사전이 영어 이름을 덧붙인다."""
    return _TOKEN.findall(text.lower())


class BM25Index:
    """청크 전체로 한 번만 만드는 BM25 색인. 질문마다 다시 만들지 않는다."""

    def __init__(self, docs: Iterable[tuple[str, str]]) -> None:
        pairs = list(docs)
        self._ids = [chunk_id for chunk_id, _ in pairs]
        self._bm25 = BM25Okapi([tokenize(text) for _, text in pairs])

    def search(self, query: str, k: int) -> list[Hit]:
        scores = self._bm25.get_scores(tokenize(query))
        ranked = sorted(zip(self._ids, scores, strict=True), key=lambda x: x[1], reverse=True)
        return [(chunk_id, float(score)) for chunk_id, score in ranked[:k] if score > 0]


def vector_search(store: VectorStore, query: str, k: int) -> list[Hit]:
    """코사인 유사도 상위 k개 (점수가 클수록 가깝다)."""
    found = store.similarity_search_with_relevance_scores(query, k=k)
    return [(doc.metadata["chunk_id"], float(score)) for doc, score in found]


def vector_candidates(hits: list[Hit]) -> list[Candidate]:
    """A 구성: 벡터 순위 그대로 후보를 만든다."""
    return [
        Candidate(
            chunk_id=chunk_id,
            rank=rank,
            rank_before_rerank=None,
            vector_score=score,
            bm25_score=None,
            rrf_score=None,
            rerank_score=None,
        )
        for rank, (chunk_id, score) in enumerate(hits, start=1)
    ]


def rrf_merge(
    vector_hits: list[Hit],
    bm25_hits: list[Hit],
    k: int = RRF_K,
    vector_weight: float = 1.0,
    bm25_weight: float = 1.0,
) -> list[Candidate]:
    """B 구성: 두 순위를 Reciprocal Rank Fusion 으로 합친다. 같은 청크는 하나로 합쳐진다.

    점수 = Σ weight / (k + 순위). 원래 점수는 척도가 달라 더하지 않고 기록만 한다.
    """
    rrf: dict[str, float] = {}
    vector_score: dict[str, float] = {}
    bm25_score: dict[str, float] = {}
    for rank, (chunk_id, score) in enumerate(vector_hits, start=1):
        rrf[chunk_id] = rrf.get(chunk_id, 0.0) + vector_weight / (k + rank)
        vector_score[chunk_id] = score
    for rank, (chunk_id, score) in enumerate(bm25_hits, start=1):
        rrf[chunk_id] = rrf.get(chunk_id, 0.0) + bm25_weight / (k + rank)
        bm25_score[chunk_id] = score

    ordered = sorted(rrf.items(), key=lambda x: x[1], reverse=True)
    return [
        Candidate(
            chunk_id=chunk_id,
            rank=rank,
            vector_score=vector_score.get(chunk_id),
            bm25_score=bm25_score.get(chunk_id),
            rrf_score=round(score, 6),
            rank_before_rerank=None,
            rerank_score=None,
        )
        for rank, (chunk_id, score) in enumerate(ordered, start=1)
    ]
