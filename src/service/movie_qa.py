from collections.abc import Awaitable, Callable, Iterable, Sequence
from typing import Any, Protocol

from pydantic import BaseModel, Field

from retrieve.pipeline import REWRITE_MODES, with_rewrite
from service.query_prep import prepare_query
from shared.core.models import Alias, Candidate, Chunk, EvidenceSource
from shared.core.schemas import AnswerStatus, RetrievalMode
from shared.core.timing import measure_time
from tools.translation import (
    Rewrite,
    load_aliases,
    model_translate,
    translate_answer_to_korean,
)

NO_EVIDENCE_KO = "확인 가능한 근거를 찾지 못했습니다."
WEB_LABEL = "검색 결과 발췌 · 미검증"
EXCERPT_CHARS = 300  # 출처 목록에 보여 줄 발췌 길이


class Evidence(BaseModel):
    """생성기에 넘기는 근거 한 개 = 청크 글 + 출처 URL."""

    chunk_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    header: str = ""
    chunk: Chunk | None = None  # 출처 표시용 메타데이터
    candidate: Candidate | None = None  # 검색 순위 · 점수


class EnglishDraft(BaseModel):
    """생성 모델의 영어 초안과 그 해석 결과."""

    answer_en: str = Field(min_length=1)
    # 비어 있으면 근거 없음으로 본다 (architecture.md §7)
    cited_chunk_ids: list[str] = Field(default_factory=list)
    no_evidence: bool = False  # 모델이 '근거 없음' 이라고 답했다
    # 일부만 근거로 확인했다고 모델이 표시했다 (상태는 바꾸지 않는다, 팀 미확정)
    partial: bool = False
    dropped_citations: list[str] = Field(default_factory=list)  # 근거 밖이라 버린 인용
    read: list[dict[str, Any]] = Field(
        default_factory=list
    )  # 모델이 읽은 범위 (청크 ID · 원문 위치)


# 바꿔 끼우는 함수의 모양
Retrieve = Callable[[str], Awaitable[Sequence[Evidence]]]  # 검색어 → 후보 (최종 순위순)
GenerateEnglish = Callable[
    [str, Sequence[Evidence]], Awaitable[EnglishDraft]
]  # (원 질문, 근거) → 초안
# websearch.search_web_for_question 과 같은 모양: (리라이트 정보, WebSearchOutcome)
WebFallback = Callable[[str], Awaitable[tuple[dict[str, Any], Any]]]


class AnswerCacheLike(Protocol):
    """service.answer_cache.AnswerCache 의 모양."""

    def key(self, query_normalized: str, mode: str) -> str: ...

    def get(self, key: str) -> dict[str, Any] | None: ...

    def put(self, key: str, value: dict[str, Any]) -> None: ...


def citation(evidence: Evidence, *, web: bool = False) -> dict[str, Any]:
    """출처 목록 한 줄. 값은 모두 실제 청크 메타데이터에서 가져온다 (모델이 지어낸 값이 아님)."""
    chunk = evidence.chunk
    ids = chunk.source_ids if chunk else {}
    return {
        "chunk_id": evidence.chunk_id,
        "kind": "web_snippet" if web else "document",
        "label": WEB_LABEL if web else None,
        "title": (chunk.title_original or chunk.title_ko) if chunk else None,
        "title_ko": chunk.title_ko if chunk else None,
        "source_url": evidence.source_url,
        "doc_id": chunk.doc_id if chunk else None,
        "section": str(chunk.section) if chunk else None,
        "section_path": ids.get("team_section_path"),
        "char_start": ids.get("char_start"),
        "char_end": ids.get("char_end"),
        "revid": chunk.revid if chunk else None,
        "fetched_at": ids.get("fetched_at"),
        "license": ids.get("license"),
        "excerpt": evidence.text[:EXCERPT_CHARS],
        "rank": evidence.candidate.rank if evidence.candidate else None,
    }


async def answer_movie_question(
    query_ko: str,
    *,
    retrieve: Retrieve,
    generate_en: GenerateEnglish,
    aliases: Iterable[Alias] | None = None,
    query_translator: Rewrite = model_translate,
    answer_translator: Rewrite = model_translate,
    mode: RetrievalMode = RetrievalMode.HYBRID_RERANK_REWRITE,
    top_k: int | None = None,
    web: WebFallback | None = None,
    cache: AnswerCacheLike | None = None,
    query_en: str | None = None,
    rewrite: bool = True,
) -> dict:
    """질문 하나를 서비스 경로(모듈 설명의 흐름 1~6)로 처리한다.

    retrieve 는 후보 전부를 최종 순위대로 돌려준다. 생성에는 상위 top_k 개만 간다 (None 이면 전부).
    rewrite: 영문 리라이트. 기본으로 모든 구성에서 켠다 (A · B · C 도 A+ · B+ · C+ 로 돈다).
    False 는 리라이트 효과를 재는 비교 실험용이고, 이름에 + 가 붙은 구성은 False 여도 리라이트한다.
    query_en: 저장해 둔 영문 검색어. 주면 LLM 리라이트를 부르지 않는다.
    청킹 · 부모 문맥 비교에서 실행마다 검색어가 달라지지 않게 하려는 것이다 (프롬프트 [6]).
    """
    # 1) 질문 준비. 별칭 사전을 주지 않으면 데이터셋 파일에서 읽는다
    alias_list = list(aliases) if aliases is not None else load_aliases()
    use_rewrite = rewrite or mode in REWRITE_MODES
    if use_rewrite:
        # 기록 · 캐시 키에 실제로 돈 구성(A → A+ …)이 남도록 구성 값도 바꾼다
        mode = with_rewrite(mode)
    translation = await prepare_query(
        query_ko, alias_list, translator=query_translator, rewrite=use_rewrite, query_en=query_en
    )
    # 리라이트를 안 쓰거나 실패해 query_en 이 비면 원질문(별칭 치환)으로 검색한다
    search_query = translation["query_en"] or translation["query_normalized"]
    # 실행 기록과 화면이 같은 칸을 쓰므로 모양을 먼저 정해 두고 단계마다 채운다
    result: dict[str, Any] = {
        **translation,
        "mode": str(mode),
        "search_query": search_query,
        "answer_en": "",
        "answer_ko": "",
        "cited_chunk_ids": [],
        "sources": [],
        "citations": [],
        "candidates": [],
        "read": [],
        "partial": False,
        "dropped_citations": [],
        "translation_failed": False,
        "no_evidence_reason": None,
        "error_stage": None,
        "evidence_source": str(EvidenceSource.NONE),
        "web_called": False,
        "web_cache_hit": False,
        "web_failed": False,
        "cache_hit": False,
    }
    # 2) 답변 캐시 (기본 꺼짐)
    cache_key = cache.key(translation["query_normalized"], str(mode)) if cache else None
    if cache and cache_key:
        hit = cache.get(cache_key)
        if hit is not None:
            return {**hit, "cache_hit": True}

    # 3) 검색
    @measure_time(label="retrieve")
    async def _retrieve() -> list[Evidence]:
        return list(await retrieve(search_query))

    try:
        evidence = await _retrieve()
    except Exception as exc:
        return {
            **result,
            "status": AnswerStatus.RETRIEVAL_ERROR,
            "error": f"{type(exc).__name__}: {exc}",
            "error_stage": "retrieve",
        }
    result["candidates"] = [e.candidate for e in evidence if e.candidate is not None]
    top = evidence[:top_k] if top_k is not None else evidence

    if not top:
        result.update(no_evidence_reason="empty_candidates")
        return await _no_evidence(result, query_ko, alias_list, generate_en, answer_translator, web)

    # 4) · 5) 생성 → 인용 검증 → 번역
    drafted = await _draft(query_ko, top, generate_en, answer_translator, alias_list)
    result.update(drafted)
    # 6) 근거 없음
    if result.get("status") == AnswerStatus.NO_EVIDENCE:
        return await _no_evidence(result, query_ko, alias_list, generate_en, answer_translator, web)
    if result["status"] == AnswerStatus.ANSWERED:
        result["evidence_source"] = str(EvidenceSource.DOCUMENTS)
        if cache and cache_key:
            cache.put(cache_key, result)
    return result


async def _draft(
    question: str,
    evidence: list[Evidence],
    generate_en: GenerateEnglish,
    answer_translator: Rewrite,
    aliases: list[Alias],
    *,
    web: bool = False,
) -> dict[str, Any]:
    """근거로 영어 초안 → 인용 검증 → 한국어 번역. 상태와 채울 칸을 돌려준다.

    web=True 는 웹 스니펫을 근거로 쓴 경우라 상태가 web_unverified(미검증)가 된다.
    """

    @measure_time(label="generate")
    async def _generate() -> EnglishDraft:
        return await generate_en(question, evidence)

    try:
        draft = await _generate()
        evidence_by_id = {item.chunk_id: item for item in evidence}
        if any(cid not in evidence_by_id for cid in draft.cited_chunk_ids):
            raise ValueError("검색 근거에 없는 청크가 답변 인용에 포함되었습니다.")
    except Exception as exc:
        return {
            "status": AnswerStatus.GENERATION_ERROR,
            "error": f"{type(exc).__name__}: {exc}",
            "error_stage": "generate",
        }
    out: dict[str, Any] = {
        "answer_en": draft.answer_en,
        "read": draft.read,
        "partial": draft.partial,
        "dropped_citations": draft.dropped_citations,
    }
    cited = list(dict.fromkeys(draft.cited_chunk_ids))  # 중복 제거, 처음 나온 순서 유지
    if draft.no_evidence or not cited:
        reason = "model_no_evidence" if draft.no_evidence else "no_valid_citations"
        return {**out, "status": AnswerStatus.NO_EVIDENCE, "no_evidence_reason": reason}

    @measure_time(label="translate")
    async def _translate() -> dict:
        return await translate_answer_to_korean(draft.answer_en, aliases, answer_translator)

    translated = await _translate()
    # 번역에 실패하면 영어 초안을 그대로 내보내지 않고 오류로 끝낸다
    if translated["translation_failed"]:
        return {
            **out,
            "status": AnswerStatus.GENERATION_ERROR,
            "translation_failed": True,
            "error": translated["error"],
            "error_stage": "translate",
        }
    return {
        **out,
        "status": AnswerStatus.WEB_UNVERIFIED if web else AnswerStatus.ANSWERED,
        "answer_ko": translated["answer_ko"],
        "cited_chunk_ids": cited,
        "sources": [
            {"chunk_id": cid, "source_url": evidence_by_id[cid].source_url} for cid in cited
        ],
        "citations": [citation(evidence_by_id[cid], web=web) for cid in cited],
    }


async def _no_evidence(
    result: dict[str, Any],
    question: str,
    aliases: list[Alias],
    generate_en: GenerateEnglish,
    answer_translator: Rewrite,
    web: WebFallback | None,
) -> dict[str, Any]:
    """정상적인 근거 없음. 웹 보강이 켜져 있을 때만 웹 검색을 탄다.

    검색 · 생성 장애는 이 함수로 오지 않는다 (장애를 웹 검색으로 덮지 않기 위해).
    """
    base = {
        **result,
        "status": AnswerStatus.NO_EVIDENCE,
        "answer_ko": NO_EVIDENCE_KO,
        "cited_chunk_ids": [],
        "sources": [],
        "citations": [],
    }
    if web is None:
        return base
    # 실패해도 예외 대신 web_failed 로 돌아온다
    _, outcome = await web(question)
    base.update(
        web_called=bool(outcome.web_called),
        web_cache_hit=bool(outcome.web_cache_hit),
        web_failed=bool(outcome.web_failed),
    )
    if outcome.web_failed or not outcome.results:
        return base
    # 웹 결과도 팀 Chunk 양식이라 같은 Evidence 로 바꿔 같은 생성기에 넘긴다
    web_evidence = [
        Evidence(
            chunk_id=item.chunk.chunk_id,
            text=item.chunk.text,
            source_url=item.chunk.source_url,
            header=item.chunk.header,
            chunk=item.chunk,
        )
        for item in outcome.results
    ]
    drafted = await _draft(
        question, web_evidence, generate_en, answer_translator, aliases, web=True
    )
    if drafted.get("status") != AnswerStatus.WEB_UNVERIFIED:
        return {**base, "web_error": drafted.get("error") or drafted.get("no_evidence_reason")}
    return {**base, **drafted, "evidence_source": str(EvidenceSource.WEB)}
