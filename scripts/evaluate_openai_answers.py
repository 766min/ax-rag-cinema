"""Evaluate grounded answers and context-order variants for OpenAI retrieval."""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]


def parse_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.S)
    return json.loads(cleaned)


def order_evidence(evidence: list[dict], mode: str) -> list[dict]:
    if mode == "before":
        return evidence
    if mode == "after":
        return list(reversed(evidence))
    middle = list(evidence)
    middle.sort(key=lambda item: (item["chunk_id"].split("-p")[-1], item["chunk_id"]))
    return middle


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=ROOT / "results" / "openai-full-experiment.json")
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "openai-answer-context-order.json")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-6-luna"))
    parser.add_argument("--orders", default="before,middle,after")
    args = parser.parse_args()
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY가 없습니다.")
    client = OpenAI(timeout=45)
    experiment = json.loads(args.input.read_text(encoding="utf-8"))
    orders = [item.strip() for item in args.orders.split(",") if item.strip()]
    records: list[dict] = []
    total = len(experiment["records"]) * len(orders)
    for row in experiment["records"]:
        for order in orders:
            evidence = order_evidence(row["evidence"], order)
            context = "\n\n".join(f"[{item['chunk_id']}] {item['text']}" for item in evidence)
            prompt = f"""Answer the Korean question using only the evidence. Cite every factual claim with [chunk_id]. If evidence is insufficient, say so.
Question: {row['question']}
Reference answer: {row['expected_answer']}
Key facts to check: {row['key_facts']}
Evidence:
{context}

Return JSON only:
{{"answer_ko":"...", "facts":[{{"fact":"...", "status":"met|partial|missing", "reason":"..."}}], "grounded":true}}"""
            started = time.perf_counter()
            try:
                response = client.responses.create(model=args.model, input=prompt)
                usage = getattr(response, "usage", None)
                parsed = parse_json(response.output_text)
                records.append({
                    "question_id": row["question_id"],
                    "question": row["question"],
                    "context_order": order,
                    "status": "answered",
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                    "tokens": {
                        "input": int(getattr(usage, "input_tokens", 0) or 0),
                        "output": int(getattr(usage, "output_tokens", 0) or 0),
                        "total": int(getattr(usage, "total_tokens", 0) or 0),
                    },
                    "evidence_ids": [item["chunk_id"] for item in evidence],
                    **parsed,
                })
            except Exception as exc:
                records.append({
                    "question_id": row["question_id"],
                    "question": row["question"],
                    "context_order": order,
                    "status": "generation_error",
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                    "tokens": {},
                    "error": str(exc),
                })
            print(f"answer {len(records)}/{total}", flush=True)
    summary: dict[str, dict] = {}
    for order in orders:
        subset = [row for row in records if row["context_order"] == order]
        counts = Counter(fact.get("status", "missing") for row in subset for fact in row.get("facts", []))
        summary[order] = {
            "answered": sum(row["status"] == "answered" for row in subset),
            "met": counts["met"],
            "partial": counts["partial"],
            "missing": counts["missing"],
            "avg_latency_ms": sum(row.get("latency_ms", 0) for row in subset) / max(1, len(subset)),
            "total_tokens": sum((row.get("tokens") or {}).get("total", 0) for row in subset),
        }
    output = {"model": args.model, "input": str(args.input), "orders": orders, "summary": summary, "records": records}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "summary": summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
