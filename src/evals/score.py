"""자동 채점과 실패 유형 (problem.md §9.3 · §10).

정답 충족 · 근거 적합은 사람(또는 LLM 판정)이 채점하므로 여기서 재지 않는다.
여기서는 기록만으로 알 수 있는 것: Hit@k, 상태별 개수, 지연 · 토큰, 실패 유형.
"""

from statistics import mean, median
from typing import Any

from shared.core.models import EvalItem, RunRecord
from shared.core.schemas import AnswerStatus


def failure_type(item: EvalItem, record: RunRecord, k: int = 5) -> str | None:
    """§10 실패 유형. 실패가 아니면 None.

    호출 오류(검색 / 생성 구분) → 근거 없음 문항 대응 → 검색 누락(후보에 정답 없음)
    → 순위 실패(후보엔 있지만 상위 k 밖) → 생성 오류(정답이 상위 k 에 있는데 근거 없음이라 답함)
    → 잘못된 인용(정답이 아닌 청크만 인용) 순으로 본다.
    """
    if record.status == AnswerStatus.RETRIEVAL_ERROR:
        return "검색 호출 오류"
    if record.status == AnswerStatus.GENERATION_ERROR:
        return "생성 호출 오류"
    if not item.answerable:
        return None if record.status == AnswerStatus.NO_EVIDENCE else "근거 없음 대응 실패"
    gold = set(item.gold_chunk_ids)
    if not gold & {c.chunk_id for c in record.candidates}:
        return "검색 누락"
    if not item.hit_at(record, k):
        return "순위 실패"
    if record.status == AnswerStatus.NO_EVIDENCE:
        return "생성 오류"
    if not gold & set(record.cited_chunk_ids):
        return "잘못된 인용"
    return None


def reciprocal_rank(item: EvalItem, record: RunRecord, k: int = 5) -> float:
    """상위 k 안 첫 허용 근거 청크의 1/순위. 없거나 검색 실패면 0 (MRR@k 의 한 항)."""
    gold = set(item.gold_chunk_ids)
    for rank, chunk_id in enumerate(record.top_chunk_ids(k), start=1):
        if chunk_id in gold:
            return 1.0 / rank
    return 0.0


def evidence_recall(
    citations: list[dict[str, Any]], record: RunRecord, k: int = 5
) -> dict[str, Any]:
    """원문 근거 회수 (Hit@k 와 별도 지표).

    인용마다 발췌 글자(공백 제외) 중 상위 k 청크가 덮는 비율을 잰다. 같은 근거를 통째로 담은
    청크가 여럿이면(overlap) 그중 하나만 와도 1.0, 근거가 여러 청크로 나뉘었으면 다 와야 1.0 이다.
    missing 인용(정답 청크 없음)은 0 으로 센다.
    """
    top = set(record.top_chunk_ids(k))
    per_citation = []
    for c in citations:
        excerpt: str = c.get("excerpt", "")
        need = {i for i, ch in enumerate(excerpt) if ch != " "}
        got = {
            i
            for p in c.get("pieces", [])
            if p["chunk_id"] in top
            for i in range(p["start"], p["end"])
        }
        ratio = len(need & got) / len(need) if need else 0.0
        per_citation.append(
            {"citation": c["citation"], "status": c["status"], "recall": round(ratio, 3)}
        )
    return {
        "all_recovered": bool(per_citation) and all(p["recall"] == 1.0 for p in per_citation),
        "mean_recall": round(mean(p["recall"] for p in per_citation), 3) if per_citation else 0.0,
        "citations": per_citation,
    }


def summarize(
    items: list[EvalItem],
    records: list[RunRecord],
    k: int = 5,
    citations: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, object]:
    """한 실행(한 구성)의 요약.

    Hit@k · MRR@k 분모는 답변 가능 문항 전체다 (problem.md §9.3 '분모 17', §10 '검색 실패는 0').
    호출 오류 문항도 분모에 남는다: 검색 호출 오류는 후보가 없어 0, 생성 호출 오류는 검색 결과로 잰다.
    citations(문항별 map_citations 결과)를 주면 근거 전체 회수도 따로 낸다.
    """
    by_id = {item.question_id: item for item in items}
    scored = [(by_id[r.question_id], r) for r in records]
    answerable = [(i, r) for i, r in scored if i.answerable]
    hits = [i.hit_at(r, k) for i, r in answerable]
    rrs = [reciprocal_rank(i, r, k) for i, r in answerable]
    evidence = {
        i.question_id: evidence_recall(citations.get(i.question_id, []), r, k)
        for i, r in answerable
        if citations is not None
    }
    totals = [r.latency_ms["total"] for r in records if "total" in r.latency_ms]
    statuses: dict[str, int] = {}
    failures: dict[str, int] = {}
    for item, record in scored:
        statuses[record.status] = statuses.get(record.status, 0) + 1
        kind = failure_type(item, record, k)
        if kind:
            failures[kind] = failures.get(kind, 0) + 1
    return {
        "n": len(records),
        f"hit@{k}": round(sum(hits) / len(hits), 3) if hits else None,
        f"hit@{k}_n": len(hits),
        f"mrr@{k}": round(sum(rrs) / len(rrs), 3) if rrs else None,
        "evidence_all_recovered": (
            sum(e["all_recovered"] for e in evidence.values()) if citations is not None else None
        ),
        "evidence_mean_recall": (
            round(mean(e["mean_recall"] for e in evidence.values()), 3) if evidence else None
        ),
        "evidence_by_question": evidence or None,
        "latency_mean_ms": round(mean(totals)) if totals else None,
        "latency_median_ms": round(median(totals)) if totals else None,
        "tokens_input_mean": _mean_of(records, "input"),
        "tokens_output_mean": _mean_of(records, "output"),
        "status": statuses,
        "failures": failures,
    }


def _mean_of(records: list[RunRecord], key: str) -> float | None:
    values = [r.tokens[key] for r in records if key in r.tokens]
    return round(mean(values), 1) if values else None
