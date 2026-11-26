import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from eval_fakes import CHUNKS, NO_ROW, ROW, record

from build.chunks import index_version
from evals.items import check_ready, load_queries, probes, squash, to_items
from evals.judge import Judge, calibrate, judge_records
from evals.score import excerpt_in, failure_stage, summarize
from shared.core.schemas import AnswerStatus


def test_gold_excerpt_maps_to_chunk_and_index_version() -> None:
    items, report = to_items([ROW, NO_ROW], CHUNKS)
    assert items[0].gold_chunk_ids == [CHUNKS[1].chunk_id]
    assert items[0].gold_index_version == index_version(CHUNKS)
    assert items[0].key_facts == ["갑옷 제작", "동굴 탈출"]
    assert items[1].gold_chunk_ids == [] and not items[1].answerable
    assert report[0]["evidence"][0]["whole_excerpt_in_one_chunk"] is True


def test_check_ready_refuses_other_index_version() -> None:
    items, _ = to_items([ROW], CHUNKS)
    check_ready(items, index_version(CHUNKS))
    with pytest.raises(ValueError, match="색인"):
        check_ready(items, "other")


def test_load_queries_reads_minji_format(tmp_path: Path) -> None:
    path = tmp_path / "q.json"
    path.write_text(json.dumps({"version": "t1", "queries": {"Q001": {"query_en": "What is X?"}}}))
    assert load_queries(path) == {"Q001": "What is X?"}
    jl = tmp_path / "q.jsonl"
    jl.write_text('{"question_id": "Q2", "query_en": "Y?"}\n')
    assert load_queries(jl) == {"Q2": "Y?"}


def test_squash_and_probes_ignore_restored_punctuation() -> None:
    assert squash("Peotor's suit, ") == "peotorssuit"
    assert excerpt_in("x PEOTORS SUIT y", ["Peotor's suit"])
    assert len(probes("a" * 100)) == 3


def test_failure_stages_in_order() -> None:
    [item], _ = to_items([ROW], CHUNKS)
    gold, other = CHUNKS[1].chunk_id, CHUNKS[0].chunk_id
    filler = [f"tt0371746-wikipedia-plot-p{n:02d}" for n in range(10, 15)]
    excerpt = ["He builds a suit of armor to escape the cave."]
    assert (
        failure_stage(record(AnswerStatus.RETRIEVAL_ERROR, [], [], "timeout"), item) == "call_error"
    )
    assert failure_stage(record(AnswerStatus.NO_EVIDENCE, [other], []), item) == "retrieval_miss"
    assert failure_stage(record(AnswerStatus.NO_EVIDENCE, [*filler, gold], []), item) == "rank_drop"
    r = record(AnswerStatus.ANSWERED, [gold], [gold])
    assert failure_stage(r, item, "unrelated text", excerpt) == "evidence_sentence_missing"
    assert (
        failure_stage(record(AnswerStatus.NO_EVIDENCE, [gold], []), item, CHUNKS[1].text, excerpt)
        == "generation_error"
    )
    assert failure_stage(r, item, CHUNKS[1].text, excerpt) == "ok"


def test_summary_keeps_denominators_apart() -> None:
    items, _ = to_items([ROW, NO_ROW], CHUNKS)
    by_id = {i.question_id: i for i in items}
    ok = record(AnswerStatus.ANSWERED, [CHUNKS[1].chunk_id], [CHUNKS[1].chunk_id])
    none = record(AnswerStatus.NO_EVIDENCE, [], []).model_copy(update={"question_id": "Q901"})
    s = summarize([ok, none], by_id)
    assert s["hit_at_5"] == "1/1" and s["unanswerable_no_evidence"] == "1/1"
    assert s["answerable_n"] == 1 and s["unanswerable_n"] == 1 and s["calls_succeeded"] == "2/2"
    assert s["ragas_id_precision_mean"] == 1.0 and s["ragas_id_recall_mean"] == 1.0
    assert s["bm25_zero"] == "2/2"  # 후보에 BM25 점수가 하나도 없다


class ScriptedLLM:
    """기준 답안이면 met, 아니면 missing 을 돌려주는 가짜 채점 모델"""

    def invoke(self, messages: list[Any]) -> Any:
        text = messages[1].content
        reference = text.split("Reference answer: ")[1].split("\n\n")[0]
        answer = text.split("Answer to grade:\n")[1]
        n = text.count("\n", text.index("Key facts:"), text.index("Answer to grade")) - 2
        verdict = "met" if answer.strip() == reference.strip() else "missing"
        return SimpleNamespace(content=json.dumps({"facts": [{"n": i, "verdict": verdict} for i in range(1, n + 1)], "contradiction": False}))  # fmt: skip


def test_calibrate_and_judge_records_with_fake_model() -> None:
    other = {**ROW, "query_id": "Q902", "expected_answer": "다른 답."}
    items, _ = to_items([ROW, other, NO_ROW], CHUNKS)
    judge = Judge(ScriptedLLM())
    cal = calibrate(items, judge)
    assert cal["reference_passed"] == "2/2" and cal["mismatched_passed"] == "0/2"
    rec = record(AnswerStatus.ANSWERED, [CHUNKS[1].chunk_id], [CHUNKS[1].chunk_id])
    rec = rec.model_copy(update={"params": {"answer_ko": "갑옷을 만들어 탈출한다."}})
    rows = judge_records([rec], {i.question_id: i for i in items}, judge)
    assert rows[0]["passed"] is True and rows[0]["by"] == "llm:j2"
