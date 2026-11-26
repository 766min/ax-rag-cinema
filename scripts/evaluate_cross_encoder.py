"""Evaluate a real Cross-Encoder reranker on saved Hybrid candidates.

This is an experiment-only script. It reads the saved retrieval candidates and
does not modify the team's database or source code. The default model is an
English MS MARCO model; this is paired with the English Query Rewrite output.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sentence_transformers import CrossEncoder

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="cross-encoder/ms-marco-MiniLM-L-6-v2")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    model = CrossEncoder(args.model)
    rows: list[dict] = []
    ranks: list[int] = []
    for item in payload["records"]:
        mode = item["queries"]["rewrite"]
        candidate_ids = mode.get("hybrid_candidates", mode.get("candidates", []))
        candidate_hits = mode.get("candidate_hits", mode.get("hits", {}))
        pairs = [
            (mode["query"], candidate_hits[chunk_id]["text"])
            for chunk_id in candidate_ids
            if chunk_id in candidate_hits
        ]
        scores = model.predict(pairs, show_progress_bar=False) if pairs else []
        ranked = [
            chunk_id
            for _, chunk_id in sorted(zip(scores, candidate_ids), reverse=True)
            if chunk_id in candidate_hits
        ]
        gold = set(item.get("gold_ids", []))
        rank = next((index for index, chunk_id in enumerate(ranked, 1) if chunk_id in gold), None)
        if rank is not None:
            ranks.append(rank)
        rows.append(
            {
                "question_id": item["question_id"],
                "question": item["question"],
                "rewrite_query": mode["query"],
                "gold_count": item.get("gold_count", 0),
                "rank": rank,
                "hit_at_5": rank is not None and rank <= args.top_k,
                "top_hits": ranked[: args.top_k],
            }
        )
    scored = [row for row in rows if row["gold_count"]]
    output = {
        "input": str(args.input),
        "cross_encoder_model": args.model,
        "candidate_source": "rewrite_hybrid_candidates",
        "rows": len(rows),
        "scored_rows": len(scored),
        "summary": {
            "hit_at_5": sum(row["hit_at_5"] for row in scored),
            "total": len(scored),
            "mrr": sum(1 / rank for rank in ranks) / len(scored) if scored else 0,
        },
        "records": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "summary": output["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
