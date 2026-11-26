"""서비스 경로(movie_qa)의 구성별 분기 · 인용 출처 · 근거 없음과 장애 구분 · 웹 보강 · 답변 캐시.

검색 · 생성 · 번역 · 웹 검색은 가짜 함수로 바꿔 끼워 실제 API 없이 돌린다.
"""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from service_fakes import POOL, Calls, Q, ask, chunk, ports, web_outcome

from retrieve.pipeline import MODES
from service.answer_cache import AnswerCache
from service.movie_qa import EnglishDraft, answer_movie_question
from shared.core.models import SourceId
from shared.core.schemas import AnswerStatus, Section

NORMALIZED = "아이언맨은 누구야? Iron Man"  # 별칭 치환한 원질문


@pytest.mark.parametrize("mode", ["A", "B", "C"])
def test_official_modes_without_rewrite_search_the_normalized_question(mode: str) -> None:
    """리라이트 없는 구성은 리라이트를 부르지 않고 별칭 치환한 질문으로 검색한다."""
    calls = Calls()

    out = ask(calls, mode)

    assert calls.rewrite == 0
    assert calls.search == [NORMALIZED]
    assert out["query_en"] == ""
    assert out["search_query"] == NORMALIZED
    assert out["status"] == AnswerStatus.ANSWERED


@pytest.mark.parametrize("mode", ["C+", "A+", "B+"])
def test_rewrite_modes_call_rewrite_once(mode: str) -> None:
    """리라이트 구성은 리라이트를 정확히 한 번 부르고 영어 검색어로 검색한다."""
    calls = Calls()

    out = ask(calls, mode)

    assert calls.rewrite == 1
    assert calls.search == ["Who is Iron Man?"]
    assert out["query_ko"] == Q
    assert out["query_normalized"] == NORMALIZED


def test_rewrite_failure_falls_back_and_is_recorded() -> None:
    calls = Calls()

    out = ask(calls, "C+", fail="rewrite")

    assert out["rewrite_failed"] is True
    assert calls.search == [NORMALIZED]


def test_saved_english_query_skips_llm_rewrite() -> None:
    """저장한 영문 검색어는 리라이트 구성에서만 쓰고, 쓰면 LLM 리라이트를 부르지 않는다."""
    calls = Calls()

    out = ask(calls, "A+", query_en="Who is Iron Man in the MCU?")

    assert calls.rewrite == 0
    assert calls.search == ["Who is Iron Man in the MCU?"]
    assert out["query_source"] == "file"
    assert out["rewrite_failed"] is False

    plain = ask(Calls(), "A", query_en="ignored")
    assert plain["search_query"] == NORMALIZED
    assert plain["query_source"] == "none"


def test_generation_gets_original_question_and_only_top_k() -> None:
    """생성에는 원 한국어 질문과 상위 5개만 가고, 기록에는 후보 전부가 남는다."""
    calls = Calls()

    out = ask(calls, "C+")

    question, ids = calls.generated_with[0]
    assert question == Q
    assert ids == [e.chunk_id for e in POOL[:5]]
    assert len(out["candidates"]) == 7


def test_citations_come_from_real_metadata() -> None:
    """인용 출처의 제목 · 판 · 라이선스 · 위치 · 수집 시점 · 발췌가 청크 메타데이터 값과 같다."""
    out = ask(Calls(), "A")

    [cit] = out["citations"]
    assert cit["chunk_id"] == POOL[1].chunk_id
    assert cit["rank"] == 2
    assert cit["title"] == "Iron Man"
    assert cit["revid"] == 7
    assert cit["license"] == "CC BY-SA 4.0"
    assert cit["char_start"] == "60"
    assert cit["fetched_at"].startswith("2026-11-23")
    assert cit["excerpt"] == POOL[1].text
    assert cit["kind"] == "document"
    assert out["sources"] == [{"chunk_id": POOL[1].chunk_id, "source_url": POOL[1].source_url}]


def test_empty_candidates_is_no_evidence_not_an_error() -> None:
    calls = Calls()

    out = ask(calls, "B", pool=[])

    assert out["status"] == AnswerStatus.NO_EVIDENCE
    assert out["no_evidence_reason"] == "empty_candidates"
    assert calls.generated_with == []


def test_model_no_evidence_is_separate_from_empty_candidates() -> None:
    draft = EnglishDraft(answer_en="NO_EVIDENCE", no_evidence=True)

    out = ask(Calls(), "C", draft=draft)

    assert out["status"] == AnswerStatus.NO_EVIDENCE
    assert out["no_evidence_reason"] == "model_no_evidence"
    assert out["cited_chunk_ids"] == []


@pytest.mark.parametrize(
    ("fail", "status", "stage"),
    [
        ("retrieve", AnswerStatus.RETRIEVAL_ERROR, "retrieve"),
        ("generate", AnswerStatus.GENERATION_ERROR, "generate"),
        ("translate", AnswerStatus.GENERATION_ERROR, "translate"),
    ],
)
def test_call_errors_are_never_no_evidence(fail: str, status: AnswerStatus, stage: str) -> None:
    """검색 · 생성 · 번역 장애는 근거 없음으로 세지 않고 실패 단계를 남긴다."""
    out = ask(Calls(), "C", fail=fail)

    assert out["status"] == status
    assert out["error_stage"] == stage
    assert out["error"]


def test_partial_answer_keeps_answered_status_with_a_flag() -> None:
    draft = EnglishDraft(answer_en=f"x [{POOL[0].chunk_id}]", cited_chunk_ids=[POOL[0].chunk_id],
                         partial=True)  # fmt: skip

    out = ask(Calls(), "A", draft=draft)

    assert out["status"] == AnswerStatus.ANSWERED
    assert out["partial"] is True


def test_web_fallback_runs_only_on_no_evidence_and_labels_snippets() -> None:
    """웹 보강은 근거 없음일 때만 부르고 답은 web_unverified + '검색 결과 발췌' 표시. 장애일 때는 부르지 않는다."""
    web_chunk = chunk(1).model_copy(
        update={"chunk_id": "web-0123456789ab-web-p01", "doc_id": "web-0123456789ab",
                "section": Section.WEB, "source_id": SourceId.BRAVE, "work_id": None}
    )  # fmt: skip
    draft_web = EnglishDraft(answer_en="w [web-0123456789ab-web-p01]",
                             cited_chunk_ids=["web-0123456789ab-web-p01"])  # fmt: skip
    asked: list[str] = []

    async def web(question: str) -> tuple[dict, Any]:
        asked.append(question)
        return {}, web_outcome([SimpleNamespace(chunk=web_chunk)])

    p = ports(Calls(), pool=[])
    p["generate_en"] = lambda q, e: asyncio.sleep(0, result=draft_web)

    out = asyncio.run(answer_movie_question(Q, mode=MODES["A"], top_k=5, web=web, **p))

    assert asked == [Q]
    assert out["status"] == AnswerStatus.WEB_UNVERIFIED
    assert out["evidence_source"] == "web"
    assert out["citations"][0]["label"] == "검색 결과 발췌 · 미검증"
    assert out["web_called"] is True

    errored = ask(Calls(), "C", fail="retrieve", web=web)
    assert errored["status"] == AnswerStatus.RETRIEVAL_ERROR
    assert asked == [Q]  # 장애에서는 웹을 부르지 않는다


def test_web_failure_keeps_no_evidence() -> None:
    async def web(question: str) -> tuple[dict, Any]:
        return {}, web_outcome([], failed=True)

    out = ask(Calls(), "A", pool=[], web=web)

    assert out["status"] == AnswerStatus.NO_EVIDENCE
    assert out["web_failed"] is True


def test_answer_cache_hit_skips_search_and_generation() -> None:
    """미적중 → 적중(검색 · 생성 없음) → 색인 버전이 바뀌면 다시 미적중. 구성이 다르면 키도 다르다."""
    versions = {"index_version": "v1", "llm_model": "m", "prompt_version": "p3-gather"}
    cache = AnswerCache(versions, {"top_k": 5})
    calls = Calls()

    first = ask(calls, "C", cache=cache)
    second = ask(calls, "C", cache=cache)

    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert len(calls.search) == 1
    assert len(calls.generated_with) == 1

    changed = AnswerCache({**versions, "index_version": "v2"}, {"top_k": 5})
    third = ask(calls, "C", cache=changed)

    assert third["cache_hit"] is False
    assert len(calls.generated_with) == 2
    assert cache.key("q", "hybrid") != cache.key("q", "vector")
