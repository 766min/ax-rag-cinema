"""웹 보강분 적재 · 재조회 (architecture.md §6 의 2 · 6단계, 튜터 실습 확인 시나리오 ③ · ④)"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from langchain_core.documents import Document as LangChainDocument

from shared.core.models import Alias
from websearch import lookup_key, search_web_for_question, to_team_format, to_vector_document
from webstore import SearchRecord, WebStore, from_vector_document

NOW = datetime(2026, 11, 25, 12, tzinfo=UTC)

PAYLOAD = {
    "web": {
        "results": [
            {
                "title": "Thunderbolts* box office",
                "url": "https://example.com/a",
                "description": "Grossed $382 million.",
            },
            {
                "title": "Thunderbolts* review",
                "url": "https://example.com/b",
                "description": "Critics praised it.",
            },
        ]
    }
}

ALIASES = [
    Alias(alias_ko="썬더볼츠", canonical_en="Thunderbolts*", kind="work", target_id="tt20969586"),
]


class MemoryCollection:
    """PGVector 대신. add_documents 는 같은 id 면 덮어쓴다 (PGVector 의 ON CONFLICT 와 같음)"""

    def __init__(self) -> None:
        self.rows: dict[str, LangChainDocument] = {}
        self.add_calls: list[list[str]] = []

    def add_documents(self, documents: list[LangChainDocument], **kwargs: Any) -> list[str]:
        ids = list(kwargs["ids"])
        self.add_calls.append(ids)
        for i, doc in zip(ids, documents, strict=True):
            self.rows[i] = doc
        return ids

    def get_by_ids(self, ids: Any, /) -> list[LangChainDocument]:
        return [self.rows[i] for i in ids if i in self.rows]


class MemoryLog:
    def __init__(self) -> None:
        self.rows: dict[str, SearchRecord] = {}

    def get(self, key: str) -> SearchRecord | None:
        return self.rows.get(key)

    def put(self, record: SearchRecord) -> None:
        self.rows[record.lookup_key] = record


def _store(now: datetime = NOW) -> WebStore:
    return WebStore(MemoryCollection(), MemoryLog(), ttl_days=30, now=lambda: now)


async def _translator(_system: str, text: str) -> str:
    return text.replace("흥행 수익은", "box office gross")


def _ask(store: WebStore, question: str = "썬더볼츠 흥행 수익은?", payload: Any = PAYLOAD) -> Any:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["q"])
        if isinstance(payload, int):
            return httpx.Response(payload)
        return httpx.Response(200, json=payload)

    async def run() -> Any:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await search_web_for_question(
                question,
                aliases=ALIASES,
                translator=_translator,
                api_key="test-key",
                client=client,
                store=store,
            )

    _rewrite, outcome = asyncio.run(run())
    return outcome, calls


def test_first_question_calls_brave_and_stores_chunks() -> None:
    store = _store()
    outcome, calls = _ask(store)
    assert len(calls) == 1
    assert outcome.web_called is True and outcome.web_cache_hit is False
    collection = store.collection
    assert isinstance(collection, MemoryCollection)
    assert set(collection.rows) == {r.chunk.chunk_id for r in outcome.results}
    record = store.log.get(outcome.lookup_key)
    assert record is not None
    assert record.chunk_ids == [r.chunk.chunk_id for r in outcome.results]
    assert record.work_id == "tt20969586"


def test_same_question_again_reuses_store_without_calling_brave() -> None:
    store = _store()
    first, _ = _ask(store)
    # 띄어쓰기 · 끝 문장부호가 달라도 같은 lookup_key
    second, calls = _ask(store, "썬더볼츠   흥행 수익은")
    assert calls == []
    assert second.web_called is False and second.web_cache_hit is True
    assert [r.chunk.chunk_id for r in second.results] == [r.chunk.chunk_id for r in first.results]
    assert second.results[0].chunk.flags == ["unverified"]
    assert second.results[0].document.source_url == "https://example.com/a"


def test_zero_result_search_is_remembered() -> None:
    store = _store()
    _ask(store, payload={"web": {"results": []}})
    again, calls = _ask(store, payload={"web": {"results": []}})
    assert calls == []
    assert again.web_cache_hit is True and again.results == []


def test_failed_brave_call_is_not_recorded_so_next_question_retries() -> None:
    store = _store()
    failed, _ = _ask(store, payload=500)
    assert failed.web_failed is True
    assert store.log.get(failed.lookup_key) is None
    _, calls = _ask(store)
    assert len(calls) == 1


def test_expired_record_searches_again() -> None:
    old = _store(NOW)
    _ask(old)
    later = WebStore(old.collection, old.log, ttl_days=30, now=lambda: NOW + timedelta(days=31))
    outcome, calls = _ask(later)
    assert len(calls) == 1 and outcome.web_cache_hit is False


def test_unchanged_chunk_is_not_embedded_again() -> None:
    store = _store()
    _ask(store)
    collection = store.collection
    assert isinstance(collection, MemoryCollection)
    assert len(collection.add_calls) == 1
    # 다른 질문이 같은 URL 을 다시 받아 옴 → 내용이 같으면 다시 임베딩하지 않는다
    outcome, calls = _ask(store, "썬더볼츠 평가는?")
    assert len(calls) == 1
    assert len(collection.add_calls) == 1
    report = store.save(outcome, query_ko="썬더볼츠 평가는?", work_id=None)
    assert report.inserted == 0 and report.unchanged_skipped == len(outcome.results)


def test_earlier_question_still_finds_shared_url_after_later_question() -> None:
    """같은 URL 이 두 질문에 나오면 청크 메타데이터의 lookup_key 는 덮인다. 기록 표로 찾는다"""
    store = _store()
    first, _ = _ask(store)
    _ask(store, "썬더볼츠 평가는?")
    again, calls = _ask(store)
    assert calls == []
    assert [r.chunk.chunk_id for r in again.results] == [r.chunk.chunk_id for r in first.results]


def test_store_failure_does_not_block_the_answer() -> None:
    class BrokenLog(MemoryLog):
        def get(self, key: str) -> SearchRecord | None:
            raise ConnectionError("db down")

    store = WebStore(MemoryCollection(), BrokenLog(), now=lambda: NOW)
    outcome, calls = _ask(store)
    assert len(calls) == 1
    assert outcome.results and outcome.web_store_failed is True


def test_vector_row_round_trips_to_team_format() -> None:
    [result] = to_team_format(
        PAYLOAD["web"]["results"][:1],
        query_ko="q",
        query_normalized="q",
        query_en="q",
        work_id="tt20969586",
        fetched_at=NOW,
    )
    back = from_vector_document(to_vector_document(result))
    assert back.chunk == result.chunk
    assert back.document == result.document
    assert back.metadata["lookup_key"] == lookup_key("q")


@pytest.mark.parametrize("ttl", [0])
def test_zero_ttl_always_searches(ttl: int) -> None:
    store = WebStore(MemoryCollection(), MemoryLog(), ttl_days=ttl, now=lambda: NOW)
    _ask(store)
    later = WebStore(store.collection, store.log, ttl_days=ttl, now=lambda: NOW + timedelta(1))
    _, calls = _ask(later)
    assert len(calls) == 1


def test_record_whose_chunks_were_all_deleted_searches_again() -> None:
    store = _store()
    _ask(store)
    collection = store.collection
    assert isinstance(collection, MemoryCollection)
    collection.rows.clear()  # 웹 컬렉션을 비움
    outcome, calls = _ask(store)
    assert len(calls) == 1 and outcome.web_cache_hit is False
    assert len(collection.rows) == len(outcome.results)


def test_reuse_skips_llm_rewrite_and_returns_saved_query() -> None:
    """저장분이 있으면 LLM 영어 변환을 부르지 않는다 (키는 사전 치환만으로 만든다)"""
    store = _store()
    first, _ = _ask(store)
    calls: list[str] = []

    async def counting_translator(_system: str, text: str) -> str:
        calls.append(text)
        return "should not be used"

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Brave 를 부르면 안 된다")

    async def run() -> Any:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await search_web_for_question(
                "썬더볼츠 흥행 수익은",
                aliases=ALIASES,
                translator=counting_translator,
                api_key="test-key",
                client=client,
                store=store,
            )

    rewrite, outcome = asyncio.run(run())
    assert calls == []
    assert rewrite["rewrite_skipped"] is True
    assert rewrite["query_en"] == first.query_en == outcome.query_en
    assert outcome.web_cache_hit is True


def test_miss_still_runs_llm_rewrite() -> None:
    store = _store()
    calls: list[str] = []

    async def counting_translator(_system: str, text: str) -> str:
        calls.append(text)
        return text.replace("흥행 수익은?", "box office gross")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=PAYLOAD)

    async def run() -> Any:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await search_web_for_question(
                "썬더볼츠 흥행 수익은?",
                aliases=ALIASES,
                translator=counting_translator,
                api_key="test-key",
                client=client,
                store=store,
            )

    rewrite, outcome = asyncio.run(run())
    assert len(calls) == 1
    assert rewrite["rewrite_skipped"] is False
    assert outcome.web_called is True
