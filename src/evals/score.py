import asyncio
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from evals.items import probes, squash
from shared.core.models import EvalItem, RunRecord
from shared.core.schemas import AnswerStatus

# '호출 장애' 로 보는 상태 — 근거 없음(no_evidence)과 절대 섞지 않는다
ERRORS = (AnswerStatus.RETRIEVAL_ERROR, AnswerStatus.GENERATION_ERROR)


def excerpt_in(text: str, excerpts: Sequence[str]) -> bool:
    """정답 발췌의 토막(처음 · 가운데 · 끝 중 하나)이 글 안에 있는가"""
    body = squash(text)
    return any(p in body for e in excerpts for p in probes(e))


def failure_stage(
    record: RunRecord, item: EvalItem, input_text: str | None = None,
    excerpts: Sequence[str] = (), k: int = 5,
) -> str:  # fmt: skip
    # ① 호출 오류 → ② 후보 밖 → ③ 상위 k 밖 → ④ 입력에 정답 문장 없음 → ⑤ 답 · 인용 실패 → ok
    if record.status in ERRORS:
        return "call_error"
    gold = set(item.gold_chunk_ids)
    ranked = [c.chunk_id for c in sorted(record.candidates, key=lambda c: c.rank)]
    if not gold & set(ranked):
        return "retrieval_miss"
    if not gold & set(ranked[:k]):
        return "rank_drop"
    if input_text is not None and excerpts and not excerpt_in(input_text, excerpts):
        return "evidence_sentence_missing"
    if record.status != AnswerStatus.ANSWERED or not gold & set(record.cited_chunk_ids):
        return "generation_error"
    return "ok"


def ragas_id_scores(record: RunRecord, item: EvalItem, k: int = 5) -> dict[str, float]:
    """RAGAS IDBasedContextPrecision / Recall — 상위 k 청크 ID 와 정답 청크 ID 대조 (LLM 없음)"""
    from ragas.dataset_schema import SingleTurnSample
    from ragas.metrics import IDBasedContextPrecision, IDBasedContextRecall

    retrieved: list[str | int] = list(record.top_chunk_ids(k))
    reference: list[str | int] = list(item.gold_chunk_ids)
    sample = SingleTurnSample(retrieved_context_ids=retrieved, reference_context_ids=reference)

    async def both() -> tuple[float, float]:
        p = await IDBasedContextPrecision().single_turn_ascore(sample)
        r = await IDBasedContextRecall().single_turn_ascore(sample)
        return float(p), float(r)

    precision, recall = asyncio.run(both())
    return {"precision": round(precision, 4), "recall": round(recall, 4)}


def summarize(
    records: Sequence[RunRecord],
    items: Mapping[str, EvalItem],
    *,
    k: int = 5,
    inputs: Mapping[str, str] | None = None,
    excerpts: Mapping[str, Sequence[str]] | None = None,
    with_ragas: bool = True,
) -> dict[str, Any]:
    """한 실행의 요약. 답변 가능 · 근거 없음 문항의 분모를 따로 둔다. Hit@k 는 팀 EvalItem.hit_at 으로 잰다"""
    answerable = [
        r
        for r in records
        if items[r.question_id].answerable and items[r.question_id].gold_chunk_ids
    ]
    # 분모를 나눈다: 정답 키가 있는 답변 가능 문항 / 근거 없음 문항
    unanswerable = [r for r in records if not items[r.question_id].answerable]
    pool = max((len(r.candidates) for r in records), default=0)
    hit_k = {r.question_id: items[r.question_id].hit_at(r, k) for r in answerable}
    hit_pool = {r.question_id: items[r.question_id].hit_at(r, max(pool, k)) for r in answerable}
    # 실패 단계별 문항 수 (inputs · excerpts 가 있어야 '근거 문장 누락' 을 판정할 수 있다)
    stages = Counter(
        failure_stage(r, items[r.question_id], (inputs or {}).get(r.question_id),
                      (excerpts or {}).get(r.question_id, ()), k)
        for r in answerable
    )  # fmt: skip
    in_input = [
        r.question_id for r in answerable
        if inputs and excerpts and excerpt_in(inputs.get(r.question_id, ""), excerpts.get(r.question_id, ()))
    ]  # fmt: skip
    ok = [r for r in records if r.status not in ERRORS]
    totals = [r.latency_ms["total"] for r in ok if "total" in r.latency_ms]
    ragas = [ragas_id_scores(r, items[r.question_id], k) for r in answerable] if with_ragas else []
    # B 계열(BM25 를 쓰는 구성) 중 오류가 아닌 기록. 후보에 BM25 점수가 하나도 없으면 BM25 0건이다
    hybrid = [
        r
        for r in records
        if r.params.get("mode") in ("B", "C", "C+", "B+") and r.status not in ERRORS
    ]
    bm25_zero = sum(all(c.bm25_score is None for c in r.candidates) for r in hybrid)
    return {
        "records": len(records),
        "calls_succeeded": f"{len(ok)}/{len(records)}",
        "status": dict(Counter(str(r.status) for r in records)),
        "errors": sum(r.status in ERRORS for r in records),
        "timeouts": sum("timeout" in (r.error or "").lower() for r in records),
        "answerable_n": len(answerable),
        f"hit_at_{k}": f"{sum(hit_k.values())}/{len(hit_k)}",
        "hit_at_pool": f"{sum(hit_pool.values())}/{len(hit_pool)}",
        "gold_excerpt_in_model_input": f"{len(in_input)}/{len(answerable)}" if inputs else None,
        "cited_gold": f"{sum(bool(set(r.cited_chunk_ids) & set(items[r.question_id].gold_chunk_ids)) for r in answerable)}/{len(answerable)}",
        "failure_stages": dict(stages),
        "unanswerable_n": len(unanswerable),
        "unanswerable_no_evidence": f"{sum(r.status == AnswerStatus.NO_EVIDENCE for r in unanswerable)}/{len(unanswerable)}",
        "partial_answers": sum(bool(r.params.get("partial")) for r in records),
        "rewrite_failed": sum(r.trace.rewrite_failed for r in records),
        "bm25_zero": f"{bm25_zero}/{len(hybrid)}" if hybrid else None,
        "latency_total_ms_median": int(statistics.median(totals)) if totals else None,
        "latency_n": len(totals),
        "tokens": dict(sum((Counter(r.tokens) for r in records), Counter())),
        "ragas_id_precision_mean": round(statistics.mean(x["precision"] for x in ragas), 4) if ragas else None,
        "ragas_id_recall_mean": round(statistics.mean(x["recall"] for x in ragas), 4) if ragas else None,
        "hits": sorted(q for q, h in hit_k.items() if h),
    }  # fmt: skip
