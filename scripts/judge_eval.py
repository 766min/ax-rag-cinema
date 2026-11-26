import argparse
import json
from pathlib import Path

from evals.items import read_rows, to_items
from evals.judge import Judge, calibrate, judge_records
from shared.core.models import RunRecord


# [역할] 명령의 본체
def main() -> None:
    parser = argparse.ArgumentParser(description="재채점")
    parser.add_argument("--questions", required=True)
    parser.add_argument("--runs", default="")
    parser.add_argument("--calibrate", action="store_true")
    parser.add_argument("--judge-model", default=None)
    parser.add_argument("--results", default="results")
    args = parser.parse_args()

    # 채점 모델 만들기 (LangChain 공통 팩토리 — 모델 이름만 바꿔 끼울 수 있다)
    from langchain.chat_models import init_chat_model

    from shared.core.config import settings

    llm = init_chat_model(
        model=args.judge_model or settings.LLM_MODEL,
        model_provider=settings.LLM_PROVIDER,
        api_key=settings.API_KEY,
        timeout=settings.LLM_TIMEOUT,
    )
    # 채점기 · 평가 문항 준비
    judge = Judge(llm)
    items, _ = to_items(read_rows(args.questions), [])
    by_id = {i.question_id: i for i in items}
    if args.calibrate:
        result = calibrate(items, judge)
        print(
            json.dumps(
                {k: v for k, v in result.items() if k != "rows"}, ensure_ascii=False, indent=2
            )
        )
    # 실행마다: 읽기 → 채점 → 문항별 근거 파일 → 요약
    for run_id in filter(None, args.runs.split(",")):
        path = Path(args.results) / f"{run_id}.jsonl"
        out = Path(args.results) / f"{run_id}-judge.jsonl"
        if out.exists():
            raise SystemExit(f"이미 채점한 실행입니다 (덮어쓰지 않는다): {out}")
        records = [
            RunRecord.model_validate_json(x)
            for x in path.read_text("utf-8").splitlines()
            if x.strip()
        ]
        rows = judge_records(records, by_id, judge)
        out.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
        )
        graded = [r for r in rows if r["fact_rate"] is not None]
        print(json.dumps({
            "run_id": run_id, "judge_model": args.judge_model or settings.LLM_MODEL, "label": "자동 판정",
            "answer_correct": f"{sum(r['passed'] for r in graded)}/{len(graded)}",
            "fact_rate_mean": round(sum(r["fact_rate"] for r in graded) / len(graded), 3) if graded else None,
            "no_evidence_handled": f"{sum(r['passed'] for r in rows if r['fact_rate'] is None)}/{len(rows) - len(graded)}",
        }, ensure_ascii=False, indent=2))  # fmt: skip


if __name__ == "__main__":
    main()
