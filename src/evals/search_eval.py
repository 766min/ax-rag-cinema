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
        endpoint = call["url"].rstrip("/").rsplit("/", 1)[-1]
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
        "outcome": outcome,
    }


def aggregate(rows: list[dict[str, Any]], k: int = 5) -> dict[str, Any]:
    """문항별 결과 → 한 조건의 요약. Hit · MRR 분모는 답변 가능 문항 전체 (V1 과 같음)."""
    answerable = [r for r in rows if r["answerable"]]
    unanswerable = [r for r in rows if not r["answerable"]]

    def avg(values: list[float]) -> float | None:
        return round(mean(values), 3) if values else None

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
        "best_gold_similarity_mean": avg(
            [r["best_gold_similarity"] for r in answerable if r["best_gold_similarity"] is not None]
        ),
        "outcomes": outcomes,
        "unanswerable_top1_similarity": [r["top1_similarity"] for r in unanswerable],
    }
