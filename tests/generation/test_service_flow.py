import asyncio
from typing import Any

import httpx
from fakes import FakeStore
from langchain_core.runnables import RunnableLambda

from generation.answer import NO_EVIDENCE_MESSAGE, CitedAnswer
from generation.service_flow import (
    WEB_NOTICE,
    InternalSearch,
    WebOptions,
    run_korean_path,
    run_translate_path,
)
from retrieval.pipeline import Retriever, SearchConfig
from shared.core.models import Alias, AliasKind

ALIASES = [Alias(alias_ko="엔드게임", canonical_en="Avengers: Endgame", kind=AliasKind.WORK)]
CORPUS = {"a": "Thanos snaps. [LLM-derived] x", "b": "Iron Man dies."}
CONTEXT = {"a": "Thanos snaps.", "b": "Iron Man dies."}


class SequenceLLM:
    """with_structured_output 이 부를 때마다 정해 둔 답을 차례로 돌려준다."""

    def __init__(self, answers: list[CitedAnswer]) -> None:
        self.answers = list(answers)
        self.prompts: list[Any] = []

    def with_structured_output(self, schema: Any) -> RunnableLambda:
        def run(prompt: Any) -> CitedAnswer:
            self.prompts.append(prompt)
            return self.answers.pop(0)

        return RunnableLambda(run)


class FirstWins:
    name = "first"

    def score(self, query: str, texts: list[str]) -> list[float]:
        return [float(-i) for i in range(len(texts))]


async def _translate_ok(system: str, text: str) -> str:
    return (
        "What happens in Avengers: Endgame?"
        if "[[TERM_0]]" not in text
        else text.replace("[[TERM_0]]의", "in [[TERM_0]]")
    )


async def _translate_fail(system: str, text: str) -> str:
    raise TimeoutError("LLM timeout")


def _search() -> InternalSearch:
    retriever = Retriever(FakeStore([("a", 0.9), ("b", 0.8)]), CORPUS, context=CONTEXT)  # type: ignore[arg-type]
    return InternalSearch(retriever, FirstWins(), SearchConfig(top_k=2, pool=2), {"a": "u-a"})


def _brave(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _brave_ok(request: httpx.Request) -> httpx.Response:
    assert request.headers["X-Subscription-Token"] == "test-key"
    return httpx.Response(
        200,
        json={
            "web": {
                "results": [
                    {"title": "T", "url": "https://ex.com/a", "description": "Box office: $1"}
                ]
            }
        },
    )


ANSWER = CitedAnswer(answer="답 [1]", cited_numbers=[1])
NONE = CitedAnswer(answer="없음", cited_numbers=[])


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_내부_근거가_충분하면_웹을_부르지_않고_원문만_문맥에_넣는다() -> None:
    llm = SequenceLLM([ANSWER])
    rec = _run(
        run_korean_path(
            "엔드게임 결말은?",
            aliases=ALIASES,
            search=_search(),
            llm=llm,  # type: ignore[arg-type]
            web=WebOptions(api_key="test-key", client=_brave(_brave_ok)),
            translator=_translate_ok,
        )
    )
    assert (rec.status, rec.evidence_source, rec.web_called) == ("answered", "documents", False)
    assert rec.sources == [{"chunk_id": "a", "origin": "documents", "source_url": "u-a"}]
    assert "LLM-derived" not in str(llm.prompts[0])


def test_내부_근거가_없으면_Brave를_부르고_미검증으로_표시하고_저장본에서_답을_지운다() -> None:
    llm = SequenceLLM([NONE, ANSWER])
    rec = _run(
        run_korean_path(
            "썬더볼츠 흥행 수익은?",
            aliases=ALIASES,
            search=_search(),
            llm=llm,  # type: ignore[arg-type]
            web=WebOptions(api_key="test-key", client=_brave(_brave_ok)),
            translator=_translate_ok,
        )
    )
    assert (rec.internal_status, rec.status, rec.evidence_source) == (
        "no_evidence",
        "answered",
        "web",
    )
    assert rec.web_called and not rec.web_failed and rec.web_results == 1
    assert rec.answer_ko.startswith(WEB_NOTICE)
    assert rec.sources[0]["origin"] == "web" and rec.sources[0]["unverified"] is True
    saved = rec.redacted()
    assert saved["answer_ko"] is None and saved["answer_redacted"] is True
    assert "Box office" not in str(saved)


def test_Brave_오류_타임아웃_관련없는_결과는_근거_없음으로_끝난다() -> None:
    def error(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    for handler, failed, error_text in (
        (error, True, "Brave Search HTTP 500"),
        (timeout, True, "ReadTimeout"),
        (_brave_ok, False, None),  # 결과는 왔지만 생성기가 근거 없음 → 정보 부족
    ):
        rec = _run(
            run_korean_path(
                "질문?",
                aliases=ALIASES,
                search=_search(),
                llm=SequenceLLM([NONE, NONE]),  # type: ignore[arg-type]
                web=WebOptions(api_key="test-key", client=_brave(handler)),
                translator=_translate_ok,
            )
        )
        assert (rec.status, rec.web_failed, rec.web_error) == ("no_evidence", failed, error_text)
        assert rec.answer_ko == NO_EVIDENCE_MESSAGE


def test_리라이트가_실패하면_사전_치환_질의로_검색한다() -> None:
    rec = _run(
        run_korean_path(
            "엔드게임 결말은?",
            aliases=ALIASES,
            search=_search(),
            llm=SequenceLLM([ANSWER]),  # type: ignore[arg-type]
            web=WebOptions(enabled=False),
            translator=_translate_fail,
        )
    )
    assert rec.rewrite_failed and rec.query_en == ""
    assert rec.search_query == rec.query_normalized == "엔드게임 결말은? Avengers: Endgame"


def test_번역_경로는_영어로_답하고_한국어로_옮기며_근거없음이면_웹으로_간다() -> None:
    ok = _run(
        run_translate_path(
            "엔드게임 결말은?",
            aliases=ALIASES,
            search=_search(),
            llm=SequenceLLM([ANSWER]),  # type: ignore[arg-type]
            web=WebOptions(enabled=False),
            translator=_translate_ok,
        )
    )
    assert (ok.path, ok.status, ok.evidence_source) == ("en_translate", "answered", "documents")
    assert ok.sources[0]["origin"] == "documents"

    web = _run(
        run_translate_path(
            "썬더볼츠 흥행 수익은?",
            aliases=ALIASES,
            search=_search(),
            llm=SequenceLLM([NONE, ANSWER]),  # type: ignore[arg-type]
            web=WebOptions(api_key="test-key", client=_brave(_brave_ok)),
            translator=_translate_ok,
        )
    )
    assert (web.internal_status, web.evidence_source, web.web_results) == ("no_evidence", "web", 1)
    assert web.answer_ko.startswith(WEB_NOTICE)
