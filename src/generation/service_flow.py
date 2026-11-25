"""V5 서비스 경로 확인 (architecture §5 · §6). 공식 평가(A · B · C · C+리라이트 · D)와 따로 쓴다.

한국어 질문 → 이름 사전 → 영어 리라이트 → 내부 검색(하이브리드 + 재정렬) → 근거 충분성 →
(부족하면) Brave → 미검증 표시 → 한국어 답.

- 재사용: tools.translation.rewrite_movie_query · translate_answer_to_korean (명석님),
  service.movie_qa.answer_movie_question (명석님, 번역 경로), websearch.web_search (명석님)
- 근거 충분성은 유사도 임계값이 아니라 생성 결과로 판단한다: 내부 근거로 만든 답이 no_evidence
  (인용할 근거 없음)일 때만 웹을 부른다 (V2 Q019: 근거 없는 질문도 1위 유사도가 0.63)
- 웹 결과는 저장하지 않는다 (WEB_STORE=false, Brave 약관). 기록에는 건수 · 상태만 남기고 웹 근거로 만든
  답 글은 지운다 (redact)
"""

import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel

from generation.answer import (
    NO_EVIDENCE_MESSAGE,
    AnswerResult,
    generate_answer,
    generate_answer_en,
)
from retrieval.pipeline import Retriever, SearchConfig
from retrieval.refine import Reranker, rerank
from shared.core.models import Alias
from shared.core.schemas import AnswerStatus, RetrievalMode

WEB_NOTICE = "[미검증 웹 출처 기반 답변] "
Translator = Callable[[str, str], Awaitable[str]]


class InternalSearch:
    """내부 문서 검색: 하이브리드(벡터 + BM25 + RRF) 상위 pool 개 → 재정렬 → 상위 top_k."""

    def __init__(
        self,
        retriever: Retriever,
        reranker: Reranker,
        config: SearchConfig,
        urls: dict[str, str],
    ) -> None:
        self.retriever, self.reranker, self.config, self.urls = retriever, reranker, config, urls

    def top(self, query: str) -> list[tuple[str, str, float | None]]:
        """(chunk_id, 답변 문맥 글, 재정렬 점수)."""
        candidates = self.retriever.search(query, RetrievalMode.HYBRID, self.config)
        ranked = rerank(query, candidates, self.retriever.texts, self.reranker)
        return [
            (c.chunk_id, self.retriever.context[c.chunk_id], c.rerank_score)
            for c in ranked[: self.config.top_k]
        ]


@dataclass
class WebOptions:
    enabled: bool = True
    api_key: str | None = None
    client: Any = None  # httpx.AsyncClient (테스트 · 오류 시나리오에서 가짜 응답)
    count: int = 5


@dataclass
class ServiceRecord:
    """한 질문의 서비스 경로 기록. 웹 결과 원문 · 스니펫은 담지 않는다."""

    question: str
    path: str  # korean_direct | en_translate
    query_normalized: str = ""
    query_en: str = ""
    rewrite_failed: bool = False
    search_query: str = ""
    internal_top: list[dict[str, Any]] = field(default_factory=list)
    internal_status: str = ""
    web_called: bool = False
    web_failed: bool = False
    web_error: str | None = None
    web_results: int = 0
    status: str = ""
    evidence_source: str = "none"  # documents | web | none
    answer_ko: str | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    latency_ms: dict[str, int] = field(default_factory=dict)

    def redacted(self) -> dict[str, Any]:
        """저장용: 웹 근거로 만든 답 글은 지운다 (웹 결과 원문을 옮겨 적었을 수 있어서)."""
        data = dict(self.__dict__)
        if self.evidence_source == "web":
            data["answer_ko"] = None
            data["answer_redacted"] = True
        return data


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


async def _web_answer(
    record: ServiceRecord,
    question: str,
    rewrite: dict[str, Any],
    web: WebOptions,
    answer_from: Callable[[list[tuple[str, str]]], Awaitable[AnswerResult]],
) -> None:
    """내부 근거가 부족할 때: Brave → 팀 양식 → 같은 생성기로 답 → 미검증 표시."""
    from websearch import web_search

    started = time.perf_counter()
    outcome = await web_search(
        query_ko=question,
        query_normalized=rewrite["query_normalized"],
        query_en=rewrite["query_en"],
        count=web.count,
        api_key=web.api_key,
        client=web.client,
    )
    record.latency_ms["web"] = _ms(started)
    record.web_called, record.web_failed = outcome.web_called, outcome.web_failed
    record.web_error, record.web_results = outcome.error, len(outcome.results)
    if outcome.web_failed or not outcome.results:
        return
    contexts = [(r.chunk.chunk_id, f"{r.chunk.header}\n{r.chunk.text}") for r in outcome.results]
    started = time.perf_counter()
    answer = await answer_from(contexts)
    record.latency_ms["web_generate"] = _ms(started)
    if answer.status != AnswerStatus.ANSWERED:
        return
    record.status, record.evidence_source = str(AnswerStatus.ANSWERED), "web"
    record.answer_ko = WEB_NOTICE + answer.answer
    record.sources = [
        {"chunk_id": cid, "origin": "web", "unverified": True} for cid in answer.cited_chunk_ids
    ]


async def run_korean_path(
    question: str,
    *,
    aliases: list[Alias],
    search: InternalSearch,
    llm: BaseChatModel,
    web: WebOptions,
    translator: Translator | None = None,
) -> ServiceRecord:
    """한국어 직접 생성 경로 (generation.answer.generate_answer, 프롬프트 p1)."""
    from tools.translation import rewrite_movie_query

    record = ServiceRecord(question=question, path="korean_direct")
    total = time.perf_counter()
    started = time.perf_counter()
    rewrite = await (
        rewrite_movie_query(question, aliases, translator)
        if translator
        else rewrite_movie_query(question, aliases)
    )
    record.latency_ms["rewrite"] = _ms(started)
    record.query_normalized, record.query_en = rewrite["query_normalized"], rewrite["query_en"]
    record.rewrite_failed = rewrite["rewrite_failed"]
    record.search_query = rewrite["query_en"] or rewrite["query_normalized"]

    started = time.perf_counter()
    top = search.top(record.search_query)
    record.latency_ms["search"] = _ms(started)
    record.internal_top = [{"chunk_id": c, "rerank_score": s} for c, _, s in top]
    started = time.perf_counter()
    answer = generate_answer(question, [(c, t) for c, t, _ in top], llm)
    record.latency_ms["generate"] = _ms(started)
    record.internal_status = str(answer.status)
    record.status = str(answer.status)
    if answer.status == AnswerStatus.ANSWERED:
        record.evidence_source, record.answer_ko = "documents", answer.answer
        record.sources = [
            {"chunk_id": c, "origin": "documents", "source_url": search.urls.get(c)}
            for c in answer.cited_chunk_ids
        ]
    elif web.enabled:

        async def korean(contexts: list[tuple[str, str]]) -> AnswerResult:
            return generate_answer(question, contexts, llm)

        await _web_answer(record, question, rewrite, web, korean)
    if record.status == str(AnswerStatus.NO_EVIDENCE):
        record.answer_ko = NO_EVIDENCE_MESSAGE
    record.latency_ms["total"] = _ms(total)
    return record


class _NoEvidence(Exception):
    """영어 생성기가 근거 없음이라고 답했다 (movie_qa 는 이 상태를 따로 받지 않아 예외로 알린다)."""


async def run_translate_path(
    question: str,
    *,
    aliases: list[Alias],
    search: InternalSearch,
    llm: BaseChatModel,
    web: WebOptions,
    translator: Translator | None = None,
) -> ServiceRecord:
    """명석님 경로: service.movie_qa.answer_movie_question (영어 초안 → 한국어 번역)."""
    from service.movie_qa import EnglishDraft, Evidence, answer_movie_question
    from tools.translation import model_translate, translate_answer_to_korean

    translate = translator or model_translate
    record = ServiceRecord(question=question, path="en_translate")
    total = time.perf_counter()

    async def retrieve(query: str) -> Sequence[Evidence]:
        started = time.perf_counter()
        top = search.top(query)
        record.latency_ms["search"] = _ms(started)
        record.internal_top = [{"chunk_id": c, "rerank_score": s} for c, _, s in top]
        return [
            Evidence(chunk_id=c, text=t, source_url=search.urls.get(c) or "unknown")
            for c, t, _ in top
        ]

    async def generate_en(query_en: str, evidence: Sequence[Evidence]) -> EnglishDraft:
        started = time.perf_counter()
        draft = generate_answer_en(query_en, [(e.chunk_id, e.text) for e in evidence], llm)
        record.latency_ms["generate"] = _ms(started)
        if draft.status != AnswerStatus.ANSWERED:
            raise _NoEvidence("no evidence")
        return EnglishDraft(answer_en=draft.answer, cited_chunk_ids=draft.cited_chunk_ids)

    result = await answer_movie_question(
        question,
        retrieve=retrieve,
        generate_en=generate_en,
        aliases=aliases,
        query_translator=translate,
        answer_translator=translate,
    )
    record.query_normalized, record.query_en = result["query_normalized"], result["query_en"]
    record.rewrite_failed, record.search_query = result["rewrite_failed"], result["search_query"]
    no_evidence = result["status"] == AnswerStatus.NO_EVIDENCE or (
        result["status"] == AnswerStatus.GENERATION_ERROR and result.get("error") == "no evidence"
    )
    record.internal_status = str(AnswerStatus.NO_EVIDENCE) if no_evidence else str(result["status"])
    record.status = record.internal_status
    if result["status"] == AnswerStatus.ANSWERED:
        record.evidence_source, record.answer_ko = "documents", result["answer_ko"]
        record.sources = [{**s, "origin": "documents"} for s in result["sources"]]
    elif result["status"] == AnswerStatus.GENERATION_ERROR and not no_evidence:
        record.error = result.get("error")
    elif web.enabled:

        async def english_then_korean(contexts: list[tuple[str, str]]) -> AnswerResult:
            draft = generate_answer_en(record.search_query, contexts, llm)
            if draft.status != AnswerStatus.ANSWERED:
                return draft
            translated = await translate_answer_to_korean(draft.answer, aliases, translate)
            if translated["translation_failed"]:
                return AnswerResult(AnswerStatus.GENERATION_ERROR, "")
            return AnswerResult(
                AnswerStatus.ANSWERED, translated["answer_ko"], draft.cited_chunk_ids
            )

        rewrite = {"query_normalized": record.query_normalized, "query_en": record.query_en}
        await _web_answer(record, question, rewrite, web, english_then_korean)
    if record.status == str(AnswerStatus.NO_EVIDENCE):
        record.answer_ko = NO_EVIDENCE_MESSAGE
    record.latency_ms["total"] = _ms(total)
    return record
