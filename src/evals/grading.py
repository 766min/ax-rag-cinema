"""수동 채점용 CSV 행 만들기. 점수 칸은 비워 둔다 (자동 채점하지 않음)."""

import csv
from pathlib import Path
from typing import Any

from shared.core.models import Candidate, EvalItem, RunRecord

COLUMNS = [
    "recipe_id",
    "run_id",
    "question_id",
    "answerable",
    "question",
    "expected_answer",
    "key_facts",
    "gold_evidence",
    "gold_chunk_ids",
    "candidates",
    "status",
    "answer",
    "cited_chunks",
    "score_correct",
    "score_grounded",
    "score_no_evidence",
    "grading_note",
]


def grading_rows(
    recipe_id: str,
    items: list[EvalItem],
    records: list[RunRecord],
    raw_by_id: dict[str, dict[str, Any]],
    texts: dict[str, str],
    k: int = 5,
) -> list[dict[str, str]]:
    """문항마다 한 줄. texts: chunk_id → 색인 글 (인용 청크 내용을 보여 주려고)."""
    by_id = {item.question_id: item for item in items}
    rows = []
    for record in records:
        item = by_id[record.question_id]
        raw = raw_by_id.get(record.question_id, {})
        top = sorted(record.candidates, key=lambda c: c.rank)[:k]
        rows.append(
            {
                "recipe_id": recipe_id,
                "run_id": record.run_id,
                "question_id": record.question_id,
                "answerable": str(item.answerable),
                "question": item.question,
                "expected_answer": item.expected_answer or "",
                "key_facts": " / ".join(item.key_facts),
                "gold_evidence": raw.get("원문 근거 발췌", ""),
                "gold_chunk_ids": " ".join(item.gold_chunk_ids),
                "candidates": "\n".join(_candidate_line(c) for c in top),
                "status": str(record.status),
                "answer": record.answer or "",
                "cited_chunks": "\n\n".join(
                    f"[{cid}]\n{texts.get(cid, '')}" for cid in record.cited_chunk_ids
                ),
                "score_correct": "",
                "score_grounded": "",
                "score_no_evidence": "",
                "grading_note": "",
            }
        )
    return rows


def _candidate_line(c: Candidate) -> str:
    """순위 · 청크 · 단계별 점수 (없는 점수는 빼고)."""
    scores = {
        "vec": c.vector_score,
        "bm25": c.bm25_score,
        "rrf": c.rrf_score,
        "rerank": c.rerank_score,
        "before": c.rank_before_rerank,
    }
    shown = ", ".join(f"{k}={v:.4g}" for k, v in scores.items() if v is not None)
    return f"{c.rank}. {c.chunk_id} ({shown})"


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    """새 파일로만 쓴다 (덮어쓰기 금지). 엑셀에서 한글이 깨지지 않게 utf-8-sig."""
    with path.open("x", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
