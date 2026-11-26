"""질문 준비(service/query_prep.py): 저장한 영문 검색어 · LLM 리라이트 · 리라이트 끔 세 갈래."""

import asyncio

from service.query_prep import prepare_query
from shared.core.models import Alias

ALIASES = [Alias(alias_ko="아이언맨", canonical_en="Iron Man", kind="work", target_id="tt0371746")]
Q = "아이언맨은 어떻게 탈출해?"


def prep(**kw: object) -> tuple[dict, list[str]]:
    calls: list[str] = []

    async def translator(_system: str, text: str) -> str:
        calls.append(text)
        return "How does [[TERM_0]] escape?"

    out = asyncio.run(prepare_query(Q, ALIASES, translator=translator, **kw))  # type: ignore[arg-type]
    return out, calls


def test_rewrites_by_default() -> None:
    out, calls = prep()

    assert len(calls) == 1
    assert out["query_en"] == "How does Iron Man escape?"
    assert out["query_source"] == "llm" and out["rewrite_failed"] is False
    assert out["query_normalized"].endswith("Iron Man")


def test_saved_query_skips_the_llm() -> None:
    out, calls = prep(query_en="How did Tony Stark escape the cave?")

    assert calls == []
    assert out["query_en"] == "How did Tony Stark escape the cave?"
    assert out["query_source"] == "file"


def test_rewrite_off_only_normalizes() -> None:
    out, calls = prep(rewrite=False, query_en="ignored")

    assert calls == []
    assert out["query_en"] == "" and out["query_source"] == "none"
    assert out["query_normalized"] == "아이언맨은 어떻게 탈출해? Iron Man"
