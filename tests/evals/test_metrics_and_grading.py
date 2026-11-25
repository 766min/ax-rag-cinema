from pathlib import Path

from evals.grading import COLUMNS, grading_rows, write_csv
from evals.score import evidence_recall, reciprocal_rank, summarize
from shared.core.models import Candidate, EvalItem, EvidenceSource, RunRecord, Trace
from shared.core.schemas import AnswerStatus, RetrievalMode


def _record(
    qid: str,
    ranked: list[str],
    status: AnswerStatus = AnswerStatus.NO_EVIDENCE,
    cited: list[str] | None = None,
) -> RunRecord:
    return RunRecord.model_validate(
        {
            "run_id": "r",
            "question_id": qid,
            "status": status,
            "error": "boom" if status == AnswerStatus.RETRIEVAL_ERROR else None,
            "cited_chunk_ids": cited or [],
            "answer": "답 [1]" if cited else None,
            "candidates": [Candidate(chunk_id=c, rank=i) for i, c in enumerate(ranked, start=1)],
            "embedding_model": "e",
            "llm_model": "l",
            "prompt_version": "p1",
            "trace": Trace(
                query_ko="q",
                query_normalized="q",
                retrieval_mode=RetrievalMode.VECTOR,
                evidence_source=EvidenceSource.NONE,
                index_version="v",
            ),
        }
    )


def _item(qid: str, gold: list[str], answerable: bool = True) -> EvalItem:
    return EvalItem(
        question_id=qid,
        question="질문",
        answerable=answerable,
        gold_chunk_ids=gold if answerable else [],
        gold_index_version="v" if answerable else None,
    )


# --- MRR@5 ---------------------------------------------------------------------


def test_MRR은_첫_정답_청크_순위의_역수() -> None:
    item = _item("Q1", ["g"])
    assert reciprocal_rank(item, _record("Q1", ["x", "g", "y"])) == 0.5
    assert reciprocal_rank(item, _record("Q1", ["x", "y", "z", "w", "v", "g"])) == 0.0  # 6위
    assert reciprocal_rank(item, _record("Q1", [], AnswerStatus.RETRIEVAL_ERROR)) == 0.0


def test_MRR_분모는_답변_가능_문항_전체() -> None:
    items = [_item("Q1", ["g"]), _item("Q2", ["h"]), _item("Q9", [], answerable=False)]
    records = [
        _record("Q1", ["g"]),
        _record("Q2", [], AnswerStatus.RETRIEVAL_ERROR),
        _record("Q9", []),
    ]
    assert summarize(items, records)["mrr@5"] == 0.5  # (1 + 0) / 2, 근거 없음 문항 제외


# --- 근거 전체 회수 -----------------------------------------------------------------

EXCERPT = "Thanos wants all six stones"  # 공백 제외 23자


def _citation(pieces: list[tuple[str, int, int]], status: str = "split") -> dict:
    return {
        "citation": 1,
        "status": status,
        "excerpt": EXCERPT,
        "pieces": [{"chunk_id": c, "start": s, "end": e} for c, s, e in pieces],
    }


def test_같은_근거를_통째로_담은_청크가_여럿이면_하나만_와도_회수() -> None:
    cite = _citation([("a", 0, len(EXCERPT)), ("b", 0, len(EXCERPT))], status="contained")
    result = evidence_recall([cite], _record("Q1", ["b"]))
    assert result["all_recovered"] is True
    assert result["citations"][0]["recall"] == 1.0


def test_나뉜_근거는_조각이_모두_와야_회수() -> None:
    cite = _citation([("a", 0, 12), ("b", 12, len(EXCERPT))])
    half = evidence_recall([cite], _record("Q1", ["a"]))
    full = evidence_recall([cite], _record("Q1", ["b", "a"]))

    assert half["all_recovered"] is False
    assert 0 < half["citations"][0]["recall"] < 1
    assert full["all_recovered"] is True


def test_정답_청크가_없는_인용은_0() -> None:
    missing = {"citation": 2, "status": "missing", "chunk_ids": []}
    result = evidence_recall([_citation([("a", 0, len(EXCERPT))]), missing], _record("Q1", ["a"]))
    assert result["all_recovered"] is False
    assert [c["recall"] for c in result["citations"]] == [1.0, 0.0]


def test_요약에_근거_회수를_Hit과_따로_남긴다() -> None:
    items = [_item("Q1", ["a", "b"])]
    records = [_record("Q1", ["a"])]
    summary = summarize(
        items, records, citations={"Q1": [_citation([("a", 0, 12), ("b", 12, 27)])]}
    )
    assert summary["hit@5"] == 1.0  # 정답 청크 하나는 왔으므로 Hit@5 는 1
    assert summary["evidence_all_recovered"] == 0  # 하지만 근거 전체는 못 가져옴


# --- 수동 채점 CSV ------------------------------------------------------------------


def test_채점_시트는_점수칸을_비워_두고_새_파일로만_쓴다(tmp_path: Path) -> None:
    items = [_item("Q1", ["a"])]
    record = _record("Q1", ["a", "x"], AnswerStatus.ANSWERED, cited=["a"])
    rows = grading_rows(
        "CK03", items, [record], {"Q1": {"원문 근거 발췌": "[1] 원문"}}, {"a": "본문 A"}
    )

    assert list(rows[0]) == COLUMNS
    row = rows[0]
    assert (row["recipe_id"], row["question_id"], row["gold_evidence"]) == (
        "CK03",
        "Q1",
        "[1] 원문",
    )
    assert row["candidates"].startswith("1. a")
    assert "본문 A" in row["cited_chunks"]
    assert all(
        row[c] == ""
        for c in ("score_correct", "score_grounded", "score_no_evidence", "grading_note")
    )

    path = tmp_path / "grading.csv"
    write_csv(path, rows)
    try:
        write_csv(path, rows)
        raise AssertionError("덮어쓰면 안 된다")
    except FileExistsError:
        pass
