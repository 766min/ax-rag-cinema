from datetime import datetime, timezone
from typing import TypeVar, Any, Generic
from enum import StrEnum
from pydantic import BaseModel, Field

T = TypeVar("T")


class AnswerStatus(StrEnum):
    """답변 처리 상태"""
    ANSWERED = "answered" # 문서 근거를 찾아 답변함
    NO_EVIDENCE = "no_evidence" # 정상적인 근거 없음(문서에 없음)
    RETRIEVAL_ERROR = "retrieval_error" # 검색(DB·임베딩) 호출 실패
    GENERATION_ERROR = "generation_error" # 답변 모델 호출 실패
    WEB_UNVERIFIED = "web_unverified" # 웹 검색 결과로 답함(미검증, 평가에는 나오지 않음)


class Section(StrEnum):
    """청크 종류 (architecture.md §3)"""
    SYNOPSIS = "synopsis" # 스포일러 없는 짧은 소개
    PLOT = "plot" # 줄거리 문단
    INTRO = "intro" # 인물 소개 문단 (두 편 이상 나온 인물)
    CONNECTION = "connection" # 연결 카드 (앞뒤 작품, 선행작 후보)
    CARD = "card" # 작품 카드 한 줄 (모든 작품)
    WEB = "web" # 웹 검색 결과 스니펫 (웹 컬렉션 전용)


class RetrievalMode(StrEnum):
    """검색 구성 (problem.md §9.2)"""
    VECTOR = "vector" # A: Vector만
    HYBRID = "hybrid" # B: BM25 + Vector (RRF 결합)
    HYBRID_RERANK = "hybrid_rerank" # C: Hybrid + Re-ranking
    HYBRID_RERANK_REWRITE = "hybrid_rerank_rewrite" # C+리라이트: C + 쿼리 리라이트
    HYBRID_RERANK_ENRICHED = "hybrid_rerank_enriched" # D: C + LLM 가공 색인


class APIResponse(BaseModel, Generic[T]):
    success: bool = Field(description="처리 성공/실패 여부")
    code: str | None = Field(None, description="비즈니스 식별 코드 (성공 시 OK, 실패 시 에러 코드)")
    message: str | None = Field(None, description="성공 또는 실패 시 설명")
    errors: dict[str, list[str]] | list[str] | Any | None = Field(None, description="실패 시 세부 오류 내용")
    data: T | None = Field(None, description="성공 시 응답 데이터")
    responded_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="응답 날짜시간(UTC)"
    )

    @classmethod
    def ok(
        cls,
        data: T | None = None,
        message: str = "요청이 성공적으로 처리되었습니다.",
        code: str = "OK",
    ) -> "APIResponse[T]":
        return cls(success=True, code=code, message=message, errors=None, data=data)

    @classmethod
    def fail(
        cls,
        message: str,
        code: str = "BAD_REQUEST",
        errors: dict[str, list[str]] | list[str] | Any | None = None,
    ) -> "APIResponse[None]":
        return cls(success=False, code=code, message=message, errors=errors, data=None)