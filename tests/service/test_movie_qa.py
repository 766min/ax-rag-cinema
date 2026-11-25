import asyncio

from service.movie_qa import EnglishDraft, Evidence, answer_movie_question
from shared.core.models import Alias
from shared.core.schemas import AnswerStatus

ALIASES = [Alias(alias_ko="아이언맨", canonical_en="Iron Man", kind="work", target_id="tt0371746")]
EVIDENCE = Evidence(
    chunk_id="tt0371746-wikipedia-plot-p01",
    text="Tony Stark builds an armored suit.",
    source_url="https://example.org/iron-man",
)


def test_service_orders_english_search_draft_and_korean_translation() -> None:
    calls: list[str] = []

    async def query_translator(_system: str, text: str) -> str:
        calls.append("rewrite")
        assert text == "[[TERM_0]]은 누구야?"
        return "Who is [[TERM_0]]?"

    async def retrieve(query: str) -> list[Evidence]:
        calls.append("retrieve")
        assert query == "Who is Iron Man?"
        return [EVIDENCE]

    async def generate_en(query: str, evidence: list[Evidence]) -> EnglishDraft:
        calls.append("generate_en")
        assert query == "Who is Iron Man?"
        assert evidence == [EVIDENCE]
        return EnglishDraft(
            answer_en="Iron Man is Tony Stark. [tt0371746-wikipedia-plot-p01]",
            cited_chunk_ids=[EVIDENCE.chunk_id],
        )

    async def answer_translator(_system: str, text: str) -> str:
        calls.append("translate_answer")
        # References are protected before names, so the chunk ID takes TERM_0.
        assert text == "[[TERM_1]] is [[TERM_2]]. [[[TERM_0]]]"
        return "[[TERM_1]]은 [[TERM_2]]입니다. [[[TERM_0]]]"

    result = asyncio.run(
        answer_movie_question(
            "아이언맨은 누구야?",
            retrieve=retrieve,
            generate_en=generate_en,
            aliases=ALIASES,
            query_translator=query_translator,
            answer_translator=answer_translator,
        )
    )
    assert calls == ["rewrite", "retrieve", "generate_en", "translate_answer"]
    assert result["status"] == AnswerStatus.ANSWERED
    assert result["answer_ko"] == "아이언맨은 토니 스타크입니다. [tt0371746-wikipedia-plot-p01]"
    assert result["sources"] == [{"chunk_id": EVIDENCE.chunk_id, "source_url": EVIDENCE.source_url}]


def test_service_uses_normalized_query_when_rewrite_fails() -> None:
    async def query_translator(_system: str, _text: str) -> str:
        raise RuntimeError("model unavailable")

    async def retrieve(query: str) -> list[Evidence]:
        assert query == "아이언맨은 누구야? Iron Man"
        return []

    async def generate_en(_query: str, _evidence: list[Evidence]) -> EnglishDraft:
        raise AssertionError("no evidence must not invoke generation")

    result = asyncio.run(
        answer_movie_question(
            "아이언맨은 누구야?",
            retrieve=retrieve,
            generate_en=generate_en,
            aliases=ALIASES,
            query_translator=query_translator,
        )
    )
    assert result["status"] == AnswerStatus.NO_EVIDENCE
    assert result["rewrite_failed"] is True


def test_service_rejects_citation_outside_retrieved_evidence() -> None:
    async def query_translator(_system: str, text: str) -> str:
        return text.replace("[[TERM_0]]", "Iron Man")

    async def retrieve(_query: str) -> list[Evidence]:
        return [EVIDENCE]

    async def generate_en(_query: str, _evidence: list[Evidence]) -> EnglishDraft:
        return EnglishDraft(answer_en="A claim. [unknown-p01]", cited_chunk_ids=["unknown-p01"])

    async def answer_translator(_system: str, _text: str) -> str:
        raise AssertionError("invalid citations must not reach translation")

    result = asyncio.run(
        answer_movie_question(
            "아이언맨은 누구야?",
            retrieve=retrieve,
            generate_en=generate_en,
            aliases=ALIASES,
            query_translator=query_translator,
            answer_translator=answer_translator,
        )
    )
    assert result["status"] == AnswerStatus.GENERATION_ERROR
