import argparse
import asyncio
import json
from datetime import date
from pathlib import Path

from evals.items import check_ready, excerpts_by_question, load_queries, read_rows, to_items
from evals.recorder import Recorder
from evals.run import RunMeta, input_text, run_questions
from evals.score import summarize
from retrieve.pipeline import MODES, SearchConfig, effective_mode
from service.runtime import alias_version, open_runtime, run_meta_values
from shared.core.logger import setup_logging


def run_id(results: Path, name: str, mode: str, trial: bool) -> str:
    base = f"{date.today().isoformat()}-{name}-{mode.replace('+', 'plus')}"
    if not trial:
        return base
    n = 1
    while (results / f"{base}-try{n}.jsonl").exists():
        n += 1
    return f"{base}-try{n}"


def main() -> None:
    parser = argparse.ArgumentParser(description="구성별 일괄 평가")
    parser.add_argument(
        "--questions", required=True, help="평가셋 파일 (question_set.jsonl · eval/*.json)"
    )
    parser.add_argument("--modes", default="A,B,C,C+")
    parser.add_argument("--name", default="gather", help="run_id 가운데 이름")
    parser.add_argument(
        "--try", dest="trial", action="store_true", help="시험 실행 (run_id 끝 -try{n}, git 제외)"
    )
    parser.add_argument("--ids", default="", help="이 문항만 (쉼표, 스모크용)")
    parser.add_argument("--queries", default=None, help="저장한 영문 검색어 파일")
    parser.add_argument("--context", default="chunk", choices=("chunk", "parent"))
    parser.add_argument("--parent-chars", type=int, default=3000)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--pool", type=int, default=20)
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument("--results", default="results")
    parser.add_argument(
        "--no-rewrite", action="store_true", help="영문 리라이트 끄기 (리라이트 효과 비교용)"
    )
    args = parser.parse_args()  # fmt: skip
    setup_logging()

    # 1) 구성 목록 확인
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    unknown = [m for m in modes if m not in MODES]
    if unknown:
        raise SystemExit(f"모르는 구성: {unknown}")
    # 리라이트가 켜져 있으면(기본) A · B · C 를 A+ · B+ · C+ 로 돌린다. 겹치는 구성은 한 번만
    rewrite = not args.no_rewrite
    asked = modes
    modes = list(dict.fromkeys(effective_mode(m, rewrite) for m in modes))
    if modes != asked:
        print(f"영문 리라이트 켜짐: {','.join(asked)} → {','.join(modes)} (끄려면 --no-rewrite)")
    # 2) 준비
    rt = open_runtime(modes, context=args.context, parent_chars=args.parent_chars)
    rows = read_rows(args.questions)
    if args.ids:
        wanted = set(args.ids.split(","))
        rows = [r for r in rows if (r.get("query_id") or r.get("question_id")) in wanted]
    # 3) 정답 키를 지금 색인의 청크에 잇고 → 버전이 맞는지 확인
    items, _ = to_items(rows, list(rt.chunks.values()))
    check_ready(items, rt.index_version)
    by_id = {i.question_id: i for i in items}
    excerpts = excerpts_by_question(rows)
    queries = load_queries(args.queries) if args.queries else None
    # question_set.jsonl 로 돈 결과는 '개발 · 탐색 실행' 으로 표시 (최종셋 실측과 구분)
    run_kind = (
        "dev-exploration" if Path(args.questions).name == "question_set.jsonl" else "official"
    )
    config = SearchConfig(top_k=args.top_k, pool=args.pool, rrf_k=args.rrf_k)
    meta = RunMeta(
        index_version=rt.index_version,
        alias_version=alias_version(rt.aliases),
        params={**rt.info, "run_kind": run_kind, "questions_file": Path(args.questions).name, "rewrite_default": rewrite,
                "queries_file": Path(args.queries).name if args.queries else None},
        **run_meta_values(),
    )  # fmt: skip
    results = Path(args.results)
    # 4)·5) 구성마다 실행 → 요약
    for mode in modes:
        recorder = Recorder(
            run_id(results, args.name, mode, args.trial), results_dir=results.resolve()
        )
        records = asyncio.run(
            run_questions(items, mode_name=mode, retriever=rt.retriever, generate_en=rt.generate_en,
                          aliases=rt.aliases, config=config, recorder=recorder, meta=meta, queries=queries,
                          rewrite=rewrite)
        )  # fmt: skip
        inputs = {
            r.question_id: input_text(r.params["read"], rt.chunks, rt.sources) for r in records
        }
        summary = summarize(records, by_id, k=args.top_k, inputs=inputs, excerpts=excerpts)
        print(
            json.dumps(
                {"run_id": recorder.run_id, "run_kind": run_kind, **summary},
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
