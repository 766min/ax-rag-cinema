"""질문 한 건을 실제 서비스 경로로 돌린다 (일괄 평가와 같은 movie_qa 경로).

    PYTHONPATH=src uv run --locked python scripts/ask.py "아이언맨은 어떻게 탈출해?" --mode A
    HF_HUB_OFFLINE=1 PYTHONPATH=src uv run --locked --with sentence-transformers==5.2.0 \\
        python scripts/ask.py "타임 스톤이 뭐야?" --mode C+ --context parent

화면에 원 질문 · 치환 · 검색어 · 상태 · 답변과 '후보 → 모델이 읽은 범위 → 인용' · 출처를 찍는다.

영문 리라이트는 모든 구성에서 기본으로 켠다 (--mode C 는 C+ 로 돈다). --no-rewrite 는 리라이트 효과 비교용.

OpenAI 호출: 리라이트 1(--query-en · --no-rewrite 면 0) + 질문 임베딩 1 + 생성 1 + (답했으면) 번역 1.
--web 은 근거 없음일 때만 Brave 를 부른다 (BRAVE_API_KEY · WEB_STORE 설정을 따른다. 평가에서는 쓰지 않는다).
"""

import argparse
import asyncio
import json
from typing import Any

from retrieve.pipeline import MODES, SearchConfig, effective_mode
from service.answer import make_retrieve
from service.movie_qa import answer_movie_question
from service.runtime import open_runtime
from shared.core.logger import setup_logging


async def main() -> None:
    parser = argparse.ArgumentParser(description="질문 한 건")
    parser.add_argument("question")
    parser.add_argument("--mode", default="C", choices=list(MODES))
    parser.add_argument("--context", default="chunk", choices=("chunk", "parent"))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--pool", type=int, default=20, help="결합 후보 깊이 (임시값 20)")
    parser.add_argument("--rrf-k", type=int, default=60, help="RRF 상수 (임시값 60)")
    parser.add_argument("--query-en", default=None, help="저장한 영문 검색어 (LLM 리라이트 대신)")
    parser.add_argument(
        "--no-rewrite", action="store_true", help="영문 리라이트 끄기 (리라이트 효과 비교용)"
    )
    parser.add_argument("--web", action="store_true", help="근거 없음일 때 웹 보강 (서비스 옵션)")
    parser.add_argument("--json", action="store_true", help="결과 전체를 JSON 으로")
    args = parser.parse_args()  # fmt: skip
    setup_logging()  # 단계별 시간이 로그에 찍힌다
    # 리라이트가 켜져 있으면(기본) A · B · C 를 A+ · B+ · C+ 로 돌린다
    name = effective_mode(args.mode, rewrite=not args.no_rewrite)

    rt = open_runtime([name], context=args.context)  # C 계열이면 재정렬 모델도 여기서 로드
    config = SearchConfig(top_k=args.top_k, pool=args.pool, rrf_k=args.rrf_k)
    web: Any = None
    if args.web:
        # dev websearch 의 '질문 → 저장분 확인 → Brave → 적재' 경로를 그대로 쓴다
        from websearch import _store_from_settings, search_web_for_question

        store = _store_from_settings()

        async def web(question: str) -> Any:
            return await search_web_for_question(question, aliases=rt.aliases, store=store)

    result = await answer_movie_question(
        args.question,
        retrieve=make_retrieve(rt.retriever, MODES[name], config),
        generate_en=rt.generate_en,
        aliases=rt.aliases,
        mode=MODES[name],
        top_k=args.top_k,
        web=web,
        query_en=args.query_en,
        rewrite=not args.no_rewrite,
    )

    if args.json:
        result["candidates"] = [c.model_dump() for c in result["candidates"]]
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return
    print(
        f"구성: {name} · 문맥: {args.context} · 색인 {rt.index_version} ({rt.info['database']}/{rt.info['collection']})"
    )
    print(
        f"질문: {result['query_ko']}\n사전 치환: {result['query_normalized']}\n검색어: {result['search_query']}"
    )
    print(
        f"리라이트 실패: {result['rewrite_failed']} · 상태: {result['status']} · 일부만 확인: {result['partial']}"
    )
    if result.get("error"):
        print(f"오류({result['error_stage']}): {result['error']}")
    print(f"\n{result['answer_ko']}\n")
    print("검색 후보 → 모델이 읽은 범위 → 인용")
    print(
        "  후보:",
        [c.chunk_id for c in result["candidates"][: args.top_k]],
        f"(전체 {len(result['candidates'])})",
    )
    print("  읽음:", [(b["ids"], b["span"], b["chars"]) for b in result["read"]])
    for c in result["citations"]:
        print(f"  [{c['chunk_id']}] {c['label'] or ''} {c['title']} · {c['section_path']} · "
              f"원문 {c['char_start']}–{c['char_end']} · 판 {c['revid']} · 수집 {c['fetched_at']}\n"
              f"      {c['source_url']}\n      \"{c['excerpt'][:160]}…\"")  # fmt: skip


if __name__ == "__main__":
    asyncio.run(main())
