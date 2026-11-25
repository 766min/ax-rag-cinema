"""웹 검색 도구를 한국어 질문 하나로 직접 돌려 본다 (Brave 실제 호출).

    uv run python scripts/try_websearch.py "썬더볼츠 흥행 수익은 얼마야?"
    uv run python scripts/try_websearch.py "썬더볼츠 흥행 수익은 얼마야?" --count 3 --no-rewrite

필요한 것: .env 의 BRAVE_API_KEY (+ 영어 변환을 쓰면 OPENAI_API_KEY · LLM_* 설정),
data/cineverse-dataset-schema/aliases.jsonl.
.env 의 WEB_STORE=true 면 결과를 웹 컬렉션 · web_search_log 에 넣고, 같은 질문을 다시 하면
Brave 없이 넣어 둔 결과를 쓴다 (web_cache_hit). false 면 화면에만 찍고 저장하지 않는다.
--no-rewrite 는 저장소를 거치지 않는다.
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tools.translation import load_aliases, normalize_query  # noqa: E402
from websearch import search_web_for_question, single_work_id, web_search  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser(description="Brave 웹 검색 도구 시험 실행")
    parser.add_argument("question", help="한국어 영화 질문")
    parser.add_argument("--count", type=int, default=5, help="받을 결과 수 (1~20)")
    parser.add_argument(
        "--no-rewrite",
        action="store_true",
        help="LLM 영어 변환 없이 사전 치환한 질문으로만 검색 (빠르고 OpenAI 호출 없음)",
    )
    args = parser.parse_args()

    started = time.perf_counter()
    if args.no_rewrite:
        aliases = load_aliases()
        normalized, matched = normalize_query(args.question, aliases)
        outcome = await web_search(
            query_ko=args.question,
            query_normalized=normalized,
            query_en="",
            work_id=single_work_id(matched, aliases),
            entities=[item["canonical_en"] for item in matched],
            count=args.count,
        )
        rewrite_failed = None
    else:
        rewrite, outcome = await search_web_for_question(args.question, count=args.count)
        normalized, rewrite_failed = rewrite["query_normalized"], rewrite["rewrite_failed"]
    elapsed = time.perf_counter() - started

    print(f"질문          : {args.question}")
    print(f"사전 치환     : {normalized}")
    print(f"검색어        : {outcome.query_en}")
    print(f"영어 변환 실패: {rewrite_failed}")
    print(f"lookup_key    : {outcome.lookup_key}")
    print(f"걸린 시간     : {elapsed:.1f}초")
    print(f"web_called    : {outcome.web_called}")
    print(f"web_cache_hit : {outcome.web_cache_hit}")
    print(f"web_store_failed: {outcome.web_store_failed}")
    if outcome.web_failed:
        print(f"\n웹 검색 실패: {outcome.error}")
        return
    print(f"\n결과 {len(outcome.results)}건 (모두 미검증)\n")
    for number, item in enumerate(outcome.results, 1):
        chunk = item.chunk
        print(f"[{number}] {chunk.title_original}")
        print(f"    {chunk.source_url}")
        print(f"    {chunk.text}")
        print(f"    chunk_id={chunk.chunk_id} work_id={chunk.work_id} flags={chunk.flags}\n")


if __name__ == "__main__":
    asyncio.run(main())
