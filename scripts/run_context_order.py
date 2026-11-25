"""V7 / EXP-10: 같은 상위 5 청크로 문맥 배치만 바꿔 답변을 생성한다 (검색은 다시 하지 않는다).

uv run python scripts/run_context_order.py --results-version v7 --exp exp10a --reps 3
uv run python scripts/run_context_order.py --results-version v7 --exp exp10b --reps 3

- 문맥: V3 의 CK05 + C+Rewrite 검색 결과(재정렬 상위 5)를 그대로 쓴다 (--source). 처음 실행 때
  results/{버전}/contexts.jsonl 로 한 번 고정하고 이후에는 그 파일만 읽는다
- exp10a: 배치 CR01~CR04 × 문항 × 반복. exp10b: 상위 5 에 정답 청크가 있는 문항에서 그 청크만 처음 · 가운데 · 끝
- 실행 순서는 고정 seed 로 섞는다. 생성기 · 프롬프트(p1) · 모델은 공식 평가와 같다
- 결과: {exp}/{run_id}/trials.jsonl (원본) · summary.json · grading.csv (익명 ID) · mapping.csv (익명 ID → 조건)
"""

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from etl.chunking import RECIPES  # noqa: E402
from etl.dataset import (  # noqa: E402
    PROJECT_ROOT,
    build_chunks,
    collection_for,
    dataset_fingerprint,
    index_version,
    load_dataset,
)
from etl.load import load_corpus  # noqa: E402
from evals.context_order import plan_trials  # noqa: E402
from evals.items import load_eval_items, read_question_set  # noqa: E402
from evals.provenance import results_dir, run_info  # noqa: E402
from evals.recorder import measure  # noqa: E402
from evals.search_eval import http_calls, summarize_calls  # noqa: E402
from generation.answer import PROMPT_VERSION, generate_answer  # noqa: E402
from generation.reorder import STRATEGIES, arrange, token_layout  # noqa: E402
from shared.core.config import settings  # noqa: E402
from shared.core.vector_store import get_llm, get_raw_db_connection  # noqa: E402

DEFAULT_SOURCE = PROJECT_ROOT / "results" / "v3" / "exp04_06" / "exp04-06-ck05-r2" / "search.jsonl"
SEPARATOR = "\n\n---\n\n"  # generation.answer.format_context 와 같다
SCORE_COLUMNS = [
    "answer_correctness",
    "faithfulness",
    "citation_accuracy",
    "completeness",
    "evidence_utilization",
    "grading_note",
]


def fix_contexts(path: Path, source: Path) -> list[dict[str, Any]]:
    """질문별 상위 5 를 한 번만 정해 파일로 둔다. 이미 있으면 그 파일을 읽는다 (덮어쓰지 않음)."""
    if not path.exists():
        rows = [json.loads(line) for line in source.open(encoding="utf-8")]
        fixed = []
        for r in rows:
            if r["variant"] != "rewrite" or r["mode"] != "hybrid_rerank":
                continue
            top = r["stages"]["hybrid_rerank"][:5]
            fixed.append(
                {
                    "question_id": r["question_id"],
                    "query_used": r["query"]["query_used"],
                    "chunk_ids": [c["chunk_id"] for c in top],
                    "rerank_scores": [c["rerank_score"] for c in top],
                    "gold_chunk_ids": r["gold_chunk_ids"],
                    "index_version": r["index_version"],
                    "source": str(source.relative_to(PROJECT_ROOT)),
                    "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                }
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as f:
            for row in fixed:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return [json.loads(line) for line in path.open(encoding="utf-8")]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-version", required=True)
    parser.add_argument("--exp", choices=("exp10a", "exp10b"), required=True)
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20261125)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--run-id")
    parser.add_argument("--limit", type=int, help="앞에서부터 N 시도만 (시험용)")
    args = parser.parse_args()

    root = results_dir(args.results_version)
    run_id = args.run_id or f"{args.exp}-{datetime.now():%Y%m%d-%H%M%S}"
    out = root / args.exp / run_id
    out.mkdir(parents=True, exist_ok=False)
    info = run_info(args.results_version)
    contexts = fix_contexts(root / "contexts.jsonl", args.source)

    recipe = RECIPES["CK05"]
    fingerprint = dataset_fingerprint()
    version = index_version(recipe, fingerprint)
    if any(c["index_version"] != version for c in contexts):
        raise SystemExit("고정한 문맥의 색인 버전이 지금 CK05 와 다릅니다")
    conn = get_raw_db_connection()
    try:
        corpus = load_corpus(conn, collection_for(settings.MOVIE_COLLECTION, recipe, fingerprint))
    finally:
        conn.close()
    texts = {cid: text for cid, (text, _) in corpus.items()}
    data = load_dataset()
    items = {
        i.question_id: i
        for i in load_eval_items(build_chunks(data, recipe), data.sections, version)
    }
    raw_by_id = {raw["query_id"]: raw for raw in read_question_set()}
    by_q = {c["question_id"]: c for c in contexts}
    trials, excluded = plan_trials(
        args.exp, contexts, {q: items[q].answerable for q in by_q}, args.reps, args.seed
    )
    trials = trials[: args.limit] if args.limit else trials
    llm = get_llm()

    records: list[dict[str, Any]] = []
    with (out / "trials.jsonl").open("x", encoding="utf-8") as f:
        for trial in trials:
            ctx = by_q[trial["question_id"]]
            item = items[trial["question_id"]]
            ordered_ids = arrange(ctx["chunk_ids"], trial["ranks"])
            blocks = [f"[{i}] {texts[cid]}" for i, cid in enumerate(ordered_ids, start=1)]
            layout = token_layout(blocks, SEPARATOR)
            error = None
            with http_calls() as calls, measure() as m:
                try:
                    answer = generate_answer(
                        item.question, [(cid, texts[cid]) for cid in ordered_ids], llm
                    )
                except Exception as exc:  # noqa: BLE001 — 실패도 한 줄로 남긴다
                    answer, error = None, f"{type(exc).__name__}: {exc}"
            gold = set(item.gold_chunk_ids)
            cited = answer.cited_chunk_ids if answer else []
            record = {
                "run_id": run_id,
                **{k: trial[k] for k in trial if k != "ranks"},
                "retrieval_ranks_in_prompt_order": trial["ranks"],
                "chunks": [
                    {
                        "context_position": pos,
                        "retrieval_rank": rank,
                        "chunk_id": cid,
                        "rerank_score": ctx["rerank_scores"][rank - 1],
                        "is_gold": cid in gold,
                        "token_span": layout["spans"][pos - 1],
                    }
                    for pos, (rank, cid) in enumerate(
                        zip(trial["ranks"], ordered_ids, strict=True), start=1
                    )
                ],
                "context_tokens": layout["total"],
                "token_encoding": layout["encoding"],
                "status": str(answer.status) if answer else "generation_error",
                "answer": answer.answer if answer else None,
                "cited_chunk_ids": cited,
                "cited_positions": [ordered_ids.index(c) + 1 for c in cited],
                "cited_retrieval_ranks": [ctx["chunk_ids"].index(c) + 1 for c in cited],
                "citations_valid": all(c in ordered_ids for c in cited),
                "cited_gold": bool(gold & set(cited)),
                "gold_in_context": bool(gold & set(ordered_ids)),
                "error": error,
                "latency_ms": m.latency_ms,
                "tokens": m.tokens,
                "api_calls": summarize_calls(calls),
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            records.append(record)
            print(
                f"{trial['order']:>3} {trial['question_id']} {trial['condition']} r{trial['rep']}: "
                f"{record['status']} cited_pos={record['cited_positions']}"
            )

    _write_grading(out, records, items, raw_by_id, texts, by_q)
    with (out / "summary.json").open("x", encoding="utf-8") as f:
        json.dump(
            {
                "exp": args.exp,
                "run_id": run_id,
                "seed": args.seed,
                "reps": args.reps,
                "trials": len(records),
                "excluded_questions": excluded,
                "strategies": {k: list(v) for k, v in STRATEGIES.items()},
                "contexts_file": str((root / "contexts.jsonl").relative_to(PROJECT_ROOT)),
                "prompt_version": PROMPT_VERSION,
                "llm_model": settings.LLM_MODEL,
                "index_version": version,
                "dataset": fingerprint,
                "run_info": info,
                "by_condition": _by_condition(records),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"→ {out.relative_to(PROJECT_ROOT)}")


def _by_condition(records: list[dict[str, Any]]) -> dict[str, Any]:
    """조건별 자동 집계 (채점 아님): 상태 · 정답 청크 인용 · 인용 위치 · 반복 간 답 변화 · 토큰 · 시간."""
    from statistics import mean

    out: dict[str, Any] = {}
    for cond in sorted({r["condition"] for r in records}):
        rows = [r for r in records if r["condition"] == cond]
        answerable = [r for r in rows if r["gold_in_context"]]
        per_q: dict[str, set[str]] = {}
        for r in rows:
            per_q.setdefault(r["question_id"], set()).add(r["answer"] or "")
        cited_pos = [p for r in rows for p in r["cited_positions"]]
        out[cond] = {
            "trials": len(rows),
            "status": {s: sum(r["status"] == s for r in rows) for s in {r["status"] for r in rows}},
            "cited_gold_when_gold_in_context": f"{sum(r['cited_gold'] for r in answerable)}/{len(answerable)}",
            "cited_positions_hist": {p: cited_pos.count(p) for p in sorted(set(cited_pos))},
            "distinct_answers_per_question": {q: len(a) for q, a in sorted(per_q.items())},
            "errors": sum(r["error"] is not None for r in rows),
            "invalid_citations": sum(not r["citations_valid"] for r in rows),
            "input_tokens_mean": round(mean(r["tokens"].get("input", 0) for r in rows), 1),
            "output_tokens_mean": round(mean(r["tokens"].get("output", 0) for r in rows), 1),
            "context_tokens_mean": round(mean(r["context_tokens"] for r in rows), 1),
            "latency_total_ms_mean": round(mean(r["latency_ms"].get("total", 0) for r in rows)),
        }
    return out


def _write_grading(
    out: Path,
    records: list[dict[str, Any]],
    items: dict[str, Any],
    raw_by_id: dict[str, Any],
    texts: dict[str, str],
    by_q: dict[str, Any],
) -> None:
    """채점 시트는 익명 ID 로만 (조건은 mapping.csv 에). 문맥은 프롬프트에 들어간 순서대로 보여 준다."""
    ordered = sorted(records, key=lambda r: (r["question_id"], r["anon_id"]))
    with (out / "grading.csv").open("x", encoding="utf-8-sig", newline="") as f:
        columns = [
            "anon_id",
            "question_id",
            "question",
            "expected_answer",
            "key_facts",
            "gold_evidence",
            "context_as_shown",
            "status",
            "answer",
            "cited_numbers",
            *SCORE_COLUMNS,
        ]
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for r in ordered:
            item, raw = items[r["question_id"]], raw_by_id[r["question_id"]]
            writer.writerow(
                {
                    "anon_id": r["anon_id"],
                    "question_id": r["question_id"],
                    "question": item.question,
                    "expected_answer": item.expected_answer or "",
                    "key_facts": " / ".join(item.key_facts),
                    "gold_evidence": raw.get("원문 근거 발췌", ""),
                    "context_as_shown": "\n\n".join(
                        f"[{c['context_position']}] {texts[c['chunk_id']]}" for c in r["chunks"]
                    ),
                    "status": r["status"],
                    "answer": r["answer"] or "",
                    "cited_numbers": " ".join(str(p) for p in r["cited_positions"]),
                    **{k: "" for k in SCORE_COLUMNS},
                }
            )
    with (out / "mapping.csv").open("x", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["anon_id", "question_id", "condition", "rep", "order"]
        )
        writer.writeheader()
        for r in ordered:
            writer.writerow(
                {k: r[k] for k in ("anon_id", "question_id", "condition", "rep", "order")}
            )


if __name__ == "__main__":
    main()
