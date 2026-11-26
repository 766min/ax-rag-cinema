"""POST /api/ask — 로어 챗봇. scripts/ask.py 와 같은 서비스 경로(answer_movie_question)를 그대로 쓴다.

질문 하나씩 처리한다 (이전 대화를 기억하지 않는다).
"""

import asyncio
import logging
import os
import re
import uuid
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from api.limits import RateLimit, client_ip
from api.works import title_of
from service.answer import ID_LIKE
from shared.core.exceptions import SharedException
from shared.core.schemas import AnswerStatus, APIResponse

router = APIRouter(tags=["ask"])

FAKE_ASK = os.getenv("FAKE_ASK", "").strip().lower() in {"true", "1", "yes", "on"}
# 팀 공식 구성 C+ (리라이트 + 하이브리드 + 재정렬). 재정렬 모델(rerank 그룹)이 없는 서버는 B+ 로
ASK_MODE = os.getenv("ASK_MODE", "C+")
# 각 검색기에서 가져오는 후보 수 (팀 평가값 40). 재정렬은 후보 수에 비례해 느려져서 CPU 서버는 줄여 쓴다
ASK_POOL = int(os.getenv("ASK_POOL", "40"))
ASK_WEB = os.getenv("ASK_WEB", "").strip().lower() in {"true", "1", "yes", "on"}
per_ip = RateLimit(
    int(os.getenv("ASK_PER_MINUTE", "6")), 60, "질문이 너무 잦아요. 1분 뒤에 다시 물어봐 주세요."
)
per_ip_day = RateLimit(
    int(os.getenv("ASK_PER_IP_DAY", "60")),
    86400,
    "오늘 물어볼 수 있는 만큼 다 물었어요. 내일 또 와 주세요.",
)
daily = RateLimit(
    int(os.getenv("ASK_PER_DAY", "500")), 86400, "오늘 질문 한도를 다 썼어요. 내일 다시 와 주세요."
)
# 동시에 도는 검색(BM25 · 재정렬) 수. CPU 를 나눠 쓰면 모두 느려지므로 나머지는 줄을 선다
_busy = asyncio.Semaphore(int(os.getenv("ASK_CONCURRENCY", "2")))
CACHE_MAX = 500  # 답 캐시에 두는 질문 수 (메모리 상한)
_lock = asyncio.Lock()
_state: dict[str, Any] = {}


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    work_id: str | None = Field(
        None, description="영화 화면에서 물으면 그 작품 (제목을 질문에 붙인다)"
    )


class Source(BaseModel):
    n: int = Field(description="답 안의 근거 번호 (1부터)")
    chunk_id: str
    kind: str = Field(description="document (문서) · web_snippet (웹 · 미검증)")
    title: str | None
    title_ko: str | None
    section_path: str | None = Field(description="문서 안 위치 (예: Plot)")
    excerpt: str = Field(description="근거 글 앞부분")
    source_url: str
    license: str | None


class AskData(BaseModel):
    request_id: str
    question: str = Field(description="실제로 처리한 질문 (작품 제목이 붙었을 수 있음)")
    answer: str
    status: AnswerStatus
    search_query: str = Field(description="검색에 쓴 말 (화면의 '이렇게 찾았어요')")
    partial: bool = Field(description="질문의 일부만 자료로 확인됨")
    sources: list[Source]


BRACKETS = re.compile(r"\[([^\[\]]+)\]")


def number_citations(answer: str, chunk_ids: list[str]) -> str:
    """답 속 [청크 ID] 를 출처 번호 [1] 로 바꾼다. 출처 목록에 없는 ID 는 지운다."""
    number = {cid: str(n) for n, cid in enumerate(chunk_ids, start=1)}

    def swap(m: re.Match[str]) -> str:
        parts = [p.strip() for p in re.split(r"[,;]", m.group(1))]  # 모델이 ; 로 나누기도 한다
        if not all(p in number or p.isdigit() or ID_LIKE.match(p) for p in parts):
            return m.group(0)  # 청크 ID 가 아닌 보통 괄호 글은 그대로
        kept = [number.get(p, p) for p in parts if p in number or p.isdigit()]
        return f"[{', '.join(dict.fromkeys(kept))}]" if kept else ""

    return BRACKETS.sub(swap, answer)


async def _runtime() -> dict[str, Any]:
    """처음 질문 때 한 번만 DB 색인 · 별칭 · 생성기를 연다 (수 초 걸림)."""
    async with _lock:
        if not _state:
            from retrieve.pipeline import MODES, SearchConfig
            from service.answer import make_retrieve
            from service.answer_cache import AnswerCache
            from service.runtime import open_runtime, run_meta_values

            if ASK_MODE not in MODES:
                raise RuntimeError(f"ASK_MODE={ASK_MODE} 는 없는 구성입니다 ({list(MODES)})")
            rt = await asyncio.to_thread(open_runtime, [ASK_MODE], context="parent")
            config = SearchConfig(pool=ASK_POOL)
            base = make_retrieve(rt.retriever, MODES[ASK_MODE], config)

            async def retrieve(query: str) -> Any:
                async def run() -> Any:
                    return await base(query)

                # 검색(BM25 · 벡터 · 재정렬)은 동기 계산이라 다른 요청이 멈추지 않게 별도 스레드에서
                async with _busy:
                    return await asyncio.to_thread(asyncio.run, run())

            _state.update(
                rt=rt,
                mode=MODES[ASK_MODE],
                retrieve=retrieve,
                top_k=config.generate_k,
                cache=_bounded(
                    AnswerCache(
                        {"index": rt.index_version, **run_meta_values()},
                        {**config.params(), **rt.info},
                    )
                ),
            )
    return _state


def _bounded(cache: Any) -> Any:
    """팀 AnswerCache 는 끝없이 쌓이므로 CACHE_MAX 를 넘으면 가장 오래된 답부터 버린다."""
    put = cache.put

    def bounded_put(key: str, value: dict[str, Any]) -> None:
        while len(cache._store) >= CACHE_MAX:
            cache._store.pop(next(iter(cache._store)))
        put(key, value)

    cache.put = bounded_put
    return cache


async def warm_up() -> None:
    """서버 시작 때 미리 준비. 실패해도 서버는 뜨고, 첫 질문 때 다시 시도한다 (그때 503 으로 알린다)."""
    try:
        await _runtime()
    except Exception:  # 원인(색인 없음 · 재정렬 모델 없음 등)을 서버 로그에 남긴다
        logging.getLogger(__name__).exception("챗봇 준비 실패 (ASK_MODE=%s)", ASK_MODE)


async def _web(question: str) -> Any:
    from websearch import _store_from_settings, search_web_for_question

    return await search_web_for_question(
        question, aliases=_state["rt"].aliases, store=_store_from_settings()
    )


def _fake(question: str) -> dict[str, Any]:
    return {
        "status": AnswerStatus.ANSWERED,
        "answer_ko": "타임 스톤은 시간을 다루는 인피니티 스톤이에요. [1]",
        "search_query": "What is the Time Stone?",
        "partial": False,
        "citations": [
            {
                "chunk_id": "fake-1",
                "kind": "document",
                "title": "Features of the Marvel Cinematic Universe",
                "title_ko": None,
                "section_path": "Infinity Stones > Time Stone",
                "excerpt": "The Time Stone is an Infinity Stone that can manipulate time…",
                "source_url": "https://en.wikipedia.org/wiki/Features_of_the_Marvel_Cinematic_Universe",
                "license": "CC BY-SA 4.0",
            }
        ],
    }


async def answer(question: str) -> dict[str, Any]:
    if FAKE_ASK:
        return _fake(question)
    from service.movie_qa import answer_movie_question

    if not _state and _lock.locked():  # 서버가 막 켜져 색인 · 모델을 읽는 중 (수 분 걸릴 수 있음)
        raise SharedException(
            "챗봇을 준비하고 있어요. 1~2분 뒤에 다시 물어봐 주세요.",
            status_code=503,
            error_code="RUNTIME_WARMING_UP",
        )
    try:
        s = await _runtime()
    except Exception as exc:  # DB · 데이터셋 · 재정렬 모델이 준비되지 않음
        logging.getLogger(__name__).exception("챗봇 준비 실패 (ASK_MODE=%s)", ASK_MODE)
        raise SharedException(
            "챗봇을 준비하지 못했어요. 잠시 후 다시 시도해 주세요.",
            status_code=503,
            error_code="RUNTIME_UNAVAILABLE",
        ) from exc
    return await answer_movie_question(
        question,
        retrieve=s["retrieve"],
        generate_en=s["rt"].generate_en,
        aliases=s["rt"].aliases,
        mode=s["mode"],
        top_k=s["top_k"],
        web=_web if ASK_WEB else None,
        cache=s["cache"],
    )


@router.post("/ask")
async def ask(req: AskRequest, request: Request) -> APIResponse[AskData]:
    ip = client_ip(request)
    per_ip.check(ip)
    per_ip_day.check(ip)
    daily.check("all")
    question = req.question.strip()
    if req.work_id:  # 첫 호출 때 데이터셋을 읽으므로 서버가 멈추지 않게 스레드에서
        question = f"({await asyncio.to_thread(title_of, req.work_id)}) {question}"
    request_id = f"req_{uuid.uuid4().hex[:12]}"
    result = await answer(question)
    status = AnswerStatus(result["status"])
    # 검색 · 생성 장애는 503 (정상적인 '근거 없음'과 구분, 팀 ServiceUnavailableException 규칙)
    if status in (AnswerStatus.RETRIEVAL_ERROR, AnswerStatus.GENERATION_ERROR):
        raise SharedException(
            "답변을 만드는 중 오류가 났어요. 잠시 후 다시 시도해 주세요.",
            status_code=503,
            error_code=status.upper(),
            errors={"request_id": request_id},
        )
    citations = result.get("citations") or []
    return APIResponse.ok(
        AskData(
            request_id=request_id,
            question=question,
            answer=number_citations(result["answer_ko"], [c["chunk_id"] for c in citations]),
            status=status,
            search_query=result.get("search_query") or question,
            partial=bool(result.get("partial")),
            sources=[
                Source(n=n, **{k: c.get(k) for k in Source.model_fields if k != "n"})
                for n, c in enumerate(citations, start=1)
            ],
        )
    )
