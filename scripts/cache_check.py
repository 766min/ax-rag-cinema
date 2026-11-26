import argparse
import asyncio
import json

from evals.items import read_rows
from retrieve.pipeline import MODES, SearchConfig
from service.answer import PROMPT_VERSION, make_retrieve
from service.answer_cache import AnswerCache
from service.movie_qa import answer_movie_question
from service.runtime import open_runtime, run_meta_values


# [역할] 명령의 본체: 같은 조건의 캐시와 프롬프트 판만 바꾼 캐시를 만들고 문항마다 1)~3)
async def main() -> None:
    parser = argparse.ArgumentParser(description="답변 캐시 확인")
    parser.add_argument("--questions", required=True)
    parser.add_argument("--ids", required=True)
    parser.add_argument("--mode", default="B", choices=list(MODES))
    args = parser.parse_args()
    rt = open_runtime([args.mode])
    config = SearchConfig()
    # 캐시 키에 들어갈 버전들 (모델 · 프롬프트 · 색인 · 데이터 판)
    versions = {
        **run_meta_values(),
        "index_version": rt.index_version,
        "dataset": rt.info["dataset"],
    }
    cache = AnswerCache(versions, config.params())
    changed = AnswerCache(
        {**versions, "prompt_version": PROMPT_VERSION + "-changed"}, config.params()
    )
    wanted = set(args.ids.split(","))
    rows = [
        r
        for r in read_rows(args.questions)
        if (r.get("query_id") or r.get("question_id")) in wanted
    ]
    # 문항마다 miss → hit → version_changed
    for row in rows:
        for label, use in (("miss", cache), ("hit", cache), ("version_changed", changed)):
            result = await answer_movie_question(
                row["question"], retrieve=make_retrieve(rt.retriever, MODES[args.mode], config),
                generate_en=rt.generate_en, aliases=rt.aliases, mode=MODES[args.mode],
                top_k=config.top_k, cache=use,
            )  # fmt: skip
            print(json.dumps({"id": row.get("query_id"), "step": label, "cache_hit": result["cache_hit"],
                              "status": str(result["status"])}, ensure_ascii=False))  # fmt: skip
    print(json.dumps({"cache": cache.stats, "changed": changed.stats}))


if __name__ == "__main__":
    asyncio.run(main())
