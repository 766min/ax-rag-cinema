"""검색만 하는 진단 실험 (V2 EXP-02 · EXP-03). 답변을 만들지 않으므로 RunRecord 대신 검색 기록을 남긴다.

- 질의는 한 번만 임베딩하고, 같은 벡터로 컬렉션을 검색한다 (레시피끼리 같은 질의 벡터를 쓰게)
- 점수는 코사인 유사도 = 1 - 코사인 거리 (PGVector `<=>`). V1 의 relevance score 와 같은 값이다
- 정답 청크는 상위 20개 밖이어도 DB 에서 직접 유사도와 컬렉션 전체 순위를 잰다
"""

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from statistics import mean
from typing import Any
from urllib.parse import urlsplit

from etl.load import DBConnection
from evals.score import evidence_recall_ids
from shared.core.models import EvalItem

_HTTP = re.compile(r'HTTP Request: (\w+) (\S+) "HTTP/[\d.]+ (\d{3})')

# 정답 청크의 유사도와 순위 (컬렉션 전체에서 그보다 가까운 청크 수 + 1)
_GOLD_SQL = """
WITH col AS (SELECT uuid FROM langchain_pg_collection WHERE name = %(collection)s),
scored AS (
    SELECT e.cmetadata->>'chunk_id' AS chunk_id, e.embedding <=> %(vec)s::vector AS distance
    FROM langchain_pg_embedding e WHERE e.collection_id = (SELECT uuid FROM col)
)
SELECT s.chunk_id, 1 - s.distance,
       (SELECT count(*) FROM scored o WHERE o.distance < s.distance) + 1
FROM scored s WHERE s.chunk_id = ANY(%(ids)s)
"""


class _HttpLog(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.calls: list[dict[str, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        match = _HTTP.search(record.getMessage())
        if match:
            method, url, status = match.groups()
            self.calls.append({"method": method, "url": url, "status": status})


@contextmanager
def http_calls() -> Iterator[list[dict[str, str]]]:
    """블록 안의 HTTP 요청(httpx INFO 로그)을 모은다. 재시도도 요청 한 줄로 남는다.

    shortcut: 응답 없이 끊긴 요청(타임아웃)은 로그가 없어 세지 못한다. 필요하면 openai 로거를 INFO 로.
    """
    handler = _HttpLog()
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield handler.calls
    finally:
        root.removeHandler(handler)


def summarize_calls(calls: list[dict[str, str]]) -> dict[str, Any]:
    by_endpoint: dict[str, int] = {}
    for call in calls:
        # 질의 문자열(?q=…)은 빼고 경로 끝만 센다 (Brave 요청 주소에 질문이 들어 있어서)
        endpoint = urlsplit(call["url"]).path.rstrip("/").rsplit("/", 1)[-1]
        by_endpoint[endpoint] = by_endpoint.get(endpoint, 0) + 1
    return {
        "requests": len(calls),
        "by_endpoint": by_endpoint,
        "non_2xx": [c for c in calls if not c["status"].startswith("2")],
    }


def search_vector(store: Any, vector: list[float], k: int) -> list[tuple[str, float]]:
    """상위 k개 (chunk_id, 코사인 유사도). PGVector 가 돌려주는 값은 거리라 1 - 거리로 바꾼다.

    store: PGVector (similarity_search_with_score_by_vector 는 VectorStore 공통 인터페이스에 없다).
    """
    found = store.similarity_search_with_score_by_vector(vector, k=k)
    return [(doc.metadata["chunk_id"], 1.0 - float(distance)) for doc, distance in found]


def gold_scores(
    conn: DBConnection, collection: str, vector: list[float], chunk_ids: list[str]
) -> dict[str, dict[str, float | int]]:
    if not chunk_ids:
        return {}
    cur = conn.cursor()
    cur.execute(
        _GOLD_SQL,
        {"collection": collection, "vec": str(vector), "ids": chunk_ids},
    )
    return {row[0]: {"similarity": float(row[1]), "rank": int(row[2])} for row in cur.fetchall()}


def score_question(
    item: EvalItem,
    hits: list[tuple[str, float]],
    gold: dict[str, dict[str, float | int]],
    citations: list[dict[str, Any]],
    k: int = 5,
) -> dict[str, Any]:
    """문항 하나의 검색 지표. 근거 없음 문항은 정답이 없으므로 1위 점수만 남긴다."""
    ids = [chunk_id for chunk_id, _ in hits]
    top1 = hits[0][1] if hits else None
    if not item.answerable:
        return {"answerable": False, "top1_similarity": top1}
    gold_ids = set(item.gold_chunk_ids)
    first = min((info["rank"] for info in gold.values()), default=None)
    in_pool = next((i for i, cid in enumerate(ids, start=1) if cid in gold_ids), None)
    evidence = evidence_recall_ids(citations, ids[:k])
    if in_pool is not None and in_pool <= k:
        outcome = "hit"
    elif in_pool is not None:
        outcome = "순위 실패"  # 후보 20개 안에는 있지만 상위 k 밖
    else:
        outcome = "검색 누락"
    return {
        "answerable": True,
        f"hit@{k}": in_pool is not None and in_pool <= k,
        "hit@pool": in_pool is not None,
        f"rr@{k}": 1.0 / in_pool if in_pool is not None and in_pool <= k else 0.0,
        "first_gold_rank_in_pool": in_pool,
        "first_gold_rank_in_collection": first,
        "gold": gold,
        "top1_similarity": top1,
        "best_gold_similarity": max((g["similarity"] for g in gold.values()), default=None),
        "evidence": evidence,
        "evidence_by_section": evidence_by_section(citations, evidence),
        "outcome": outcome,
    }


def aggregate(rows: list[dict[str, Any]], k: int = 5) -> dict[str, Any]:
    """문항별 결과 → 한 조건의 요약. Hit · MRR 분모는 답변 가능 문항 전체 (V1 과 같음)."""
    answerable = [r for r in rows if r["answerable"]]
    unanswerable = [r for r in rows if not r["answerable"]]

    def avg(values: list[float | None]) -> float | None:
        # BM25 단독은 겹치는 단어가 없으면 후보가 0개라 1위 점수가 없다 (None 은 빼고 평균)
        present = [v for v in values if v is not None]
        return round(mean(present), 3) if present else None

    outcomes: dict[str, int] = {}
    for r in answerable:
        outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
    return {
        "n": len(rows),
        "answerable_n": len(answerable),
        f"hit@{k}": avg([float(r[f"hit@{k}"]) for r in answerable]),
        f"mrr@{k}": avg([r[f"rr@{k}"] for r in answerable]),
        "hit@20": avg([float(r["hit@pool"]) for r in answerable]),
        "evidence_mean_recall": avg([r["evidence"]["mean_recall"] for r in answerable]),
        "evidence_all_recovered": sum(r["evidence"]["all_recovered"] for r in answerable),
        "top1_similarity_mean": avg([r["top1_similarity"] for r in answerable]),
        "empty_results": sum(r.get("top1_similarity") is None for r in rows),
        "best_gold_similarity_mean": avg(
            [r["best_gold_similarity"] for r in answerable if r["best_gold_similarity"] is not None]
        ),
        "outcomes": outcomes,
        "unanswerable_top1_similarity": [r["top1_similarity"] for r in unanswerable],
    }


# --- V3: A · B · C 단계별 기록 ---------------------------------------------------------

STAGE_MODES = ("vector", "bm25", "hybrid", "hybrid_rerank")  # bm25 단독은 진단용 (공식 구성 아님)


def run_stages(
    query: str,
    vector_hits: list[tuple[str, float]],
    bm25: Any,
    texts: dict[str, str],
    pool: int,
    reranker: Any | None = None,
) -> dict[str, Any]:
    """한 질의의 단계별 후보 (점수 · 순위 · 시간). bm25: retrieval.search.BM25Index.

    hybrid = RRF(벡터 상위 pool, BM25 상위 pool) 상위 pool 개. hybrid_rerank = 그 pool 개를 재정렬.
    """
    import time

    from retrieval.refine import rerank
    from retrieval.search import rrf_merge, vector_candidates

    timings: dict[str, float] = {}
    started = time.perf_counter()
    bm25_hits = bm25.search(query, pool)
    timings["bm25_ms"] = round((time.perf_counter() - started) * 1000, 2)
    started = time.perf_counter()
    hybrid = rrf_merge(vector_hits, bm25_hits)[:pool]
    timings["rrf_ms"] = round((time.perf_counter() - started) * 1000, 2)

    stages: dict[str, Any] = {
        "vector": [c.model_dump(mode="json") for c in vector_candidates(vector_hits)],
        "bm25": [
            {"chunk_id": cid, "rank": i, "bm25_score": round(score, 4)}
            for i, (cid, score) in enumerate(bm25_hits, start=1)
        ],
        "hybrid": [c.model_dump(mode="json") for c in hybrid],
    }
    if reranker is not None:
        started = time.perf_counter()
        reranked = rerank(query, hybrid, texts, reranker)
        timings["rerank_ms"] = round((time.perf_counter() - started) * 1000, 2)
        stages["hybrid_rerank"] = [c.model_dump(mode="json") for c in reranked]
        stages["rerank_truncated_pairs"] = reranker.truncated(
            query, [texts[c.chunk_id] for c in hybrid]
        )
    vector_ids = {cid for cid, _ in vector_hits}
    bm25_ids = {cid for cid, _ in bm25_hits}
    stages["overlap_vector_bm25"] = len(vector_ids & bm25_ids)
    stages["timings"] = timings
    return stages


def gold_movement(stages: dict[str, Any], gold_ids: list[str]) -> dict[str, Any]:
    """정답 청크마다 단계별 순위 (없으면 None) · 어느 검색기에서만 찾았는지."""
    gold = set(gold_ids)

    def ranks(name: str) -> dict[str, int]:
        return {c["chunk_id"]: c["rank"] for c in stages.get(name, []) if c["chunk_id"] in gold}

    by_stage = {name: ranks(name) for name in STAGE_MODES if name in stages}
    in_vector, in_bm25 = set(by_stage.get("vector", {})), set(by_stage.get("bm25", {}))
    return {
        "ranks": {cid: {name: by_stage[name].get(cid) for name in by_stage} for cid in gold_ids},
        "only_vector": sorted(in_vector - in_bm25),
        "only_bm25": sorted(in_bm25 - in_vector),
        "both": sorted(in_vector & in_bm25),
    }


# --- V6: 상위 k 의 원문 중복 · 절 종류별 근거 회수 ---------------------------------------

_SECTION_IN_ID = re.compile(r"-(synopsis|plot|intro|biography|lore)-p\d+$")


def chunk_positions(
    chunks: list[Any], sections: dict[tuple[str, str], str]
) -> dict[str, tuple[str, str, int, int]]:
    """chunk_id → (원래 doc_…, 절 번호, 시작, 끝). 위치는 공백 정규화한 절 원문 기준 (chunk_spans)."""
    from etl.chunking import chunk_spans

    groups: dict[tuple[str, str], list[Any]] = {}
    for c in chunks:
        key = (c.source_ids.get("team_document_id") or "", c.source_ids.get("section_index") or "")
        groups.setdefault(key, []).append(c)
    positions: dict[str, tuple[str, str, int, int]] = {}
    for key, members in groups.items():
        members.sort(key=lambda c: c.position)
        for c, span in zip(
            members, chunk_spans(sections[key], [m.text for m in members]), strict=True
        ):
            if span:
                positions[c.chunk_id] = (key[0], key[1], span[0], span[1])
    return positions


def context_redundancy(
    top_ids: list[str], positions: dict[str, tuple[str, str, int, int]]
) -> dict[str, Any]:
    """상위 청크들의 원문 중복 (PLAN 정의).

    중복률 = 1 - |같은 절 구간들의 합집합| / Σ 구간 길이. 고유 범위 = 합집합 글자 수.
    고유 문서 수 = 서로 다른 원래 문서 수. 위치를 모르는 청크는 빼고 센다 (unlocated).
    """
    by_section: dict[tuple[str, str], list[tuple[int, int]]] = {}
    docs: set[str] = set()
    unlocated = 0
    for cid in top_ids:
        pos = positions.get(cid)
        if pos is None:
            unlocated += 1
            continue
        docs.add(pos[0])
        by_section.setdefault((pos[0], pos[1]), []).append((pos[2], pos[3]))
    total = sum(e - s for spans in by_section.values() for s, e in spans)
    union = 0
    for spans in by_section.values():
        end = -1
        for s, e in sorted(spans):
            if s >= end:
                union += e - s
            elif e > end:
                union += e - end
            end = max(end, e)
    return {
        "span_chars": total,
        "unique_chars": union,
        "duplication_rate": round(1 - union / total, 4) if total else None,
        "unique_docs": len(docs),
        "unlocated": unlocated,
    }


def evidence_by_section(
    citations: list[dict[str, Any]], evidence: dict[str, Any]
) -> list[dict[str, Any]]:
    """인용마다 (정답 청크의 절 종류, 근거 회수율). missing 인용은 절 종류를 'missing' 으로."""
    rows = []
    for cite, ev in zip(citations, evidence["citations"], strict=True):
        ids = cite.get("chunk_ids") or []
        match = _SECTION_IN_ID.search(ids[0]) if ids else None
        rows.append({"section": match.group(1) if match else "missing", "recall": ev["recall"]})
    return rows


def aggregate_v6(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """aggregate() 에 더하는 V6 지표 (답변 가능 문항 기준, 절 종류별은 인용 단위)."""
    answerable = [r for r in rows if r["answerable"]]

    def avg(values: list[float]) -> float | None:
        present = [v for v in values if v is not None]
        return round(mean(present), 4) if present else None

    sections: dict[str, list[float]] = {}
    for r in answerable:
        for item in r.get("evidence_by_section", []):
            sections.setdefault(item["section"], []).append(item["recall"])
    red = [r["top_redundancy"] for r in answerable if "top_redundancy" in r]
    return {
        "top5_duplication_rate_mean": avg([x["duplication_rate"] for x in red]),
        "top5_unique_chars_mean": avg([x["unique_chars"] for x in red]),
        "top5_unique_docs_mean": avg([x["unique_docs"] for x in red]),
        "evidence_recall_by_section": {
            k: {"citations": len(v), "mean_recall": round(mean(v), 3)}
            for k, v in sorted(sections.items())
        },
    }
