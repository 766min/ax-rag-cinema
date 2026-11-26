"""질문 준비: 별칭 사전 치환 + 영문 리라이트. 검색 방식(벡터 · 하이브리드 · 재정렬)과 따로 정한다.

문서가 영어라 한국어 질문 그대로는 정답이 후보에도 잘 오르지 않는다. 그래서 모든 구성에서 기본으로 리라이트한다
(2026-11-26 dev 11문항 실측: 리라이트 없음 Hit@5 1~2/10 → 있음 5~6/10).
rewrite=False 는 리라이트 효과를 재는 비교 실험용이다.
"""

from collections.abc import Iterable
from typing import Any

from shared.core.models import Alias
from shared.core.timing import measure_time
from tools.translation import Rewrite, normalize_query, rewrite_movie_query


async def prepare_query(
    query_ko: str,
    aliases: Iterable[Alias],
    *,
    translator: Rewrite,
    rewrite: bool = True,
    query_en: str | None = None,
) -> dict[str, Any]:
    """검색에 쓸 질문을 만든다. query_source 에 영어 검색어의 출처(file · llm · none)를 남긴다.

    - query_en(저장해 둔 영문 검색어)이 있으면 LLM 을 부르지 않고 그것을 쓴다
    - 없으면 rewrite_movie_query 로 한 번 리라이트한다. 실패하면 query_en 이 비고 원질문으로 검색한다
    - rewrite=False 면 별칭 치환만 한다 (query_en 은 무시)
    """
    alias_list = list(aliases)
    if rewrite and query_en:
        normalized, matched = normalize_query(query_ko, alias_list)
        return {
            "query_ko": query_ko,
            "query_normalized": normalized,
            "query_en": query_en,
            "rewrite_failed": False,
            "matched_aliases": matched,
            "query_source": "file",
        }
    if rewrite:

        @measure_time(label="rewrite")
        async def _rewrite() -> dict:
            return await rewrite_movie_query(query_ko, alias_list, translator)

        return {**await _rewrite(), "query_source": "llm"}
    normalized, matched = normalize_query(query_ko, alias_list)
    return {
        "query_ko": query_ko,
        "query_normalized": normalized,
        "query_en": "",
        "rewrite_failed": False,
        "matched_aliases": matched,
        "query_source": "none",
    }
