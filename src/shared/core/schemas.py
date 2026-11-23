from datetime import datetime, timezone
from typing import TypeVar, Any, Generic
from enum import Enum
from pydantic import BaseModel, Field

T = TypeVar("T")


class AnswerStatus(str, Enum):
    """답변 처리 상태"""
    ANSWERED = "answered" # 근거를 찾아 답변함
    NO_EVIDENCE = "no_evidence" # 정상적인 근거 없음(문서에 없음)
    ERROR = "error" # 호출 실패(근거 없음과 구분)


class Section(str, Enum):
    """Wikipedia 영화 문서의 절 구분"""
    PLOT = "plot" # 줄거리
    CAST = "cast" # 출연진
    PRODUCTION = "production" # 제작
    RELEASE = "release" # 개봉, 흥행
    RECEPTION = "reception" # 평가
    INFOBOX = "infobox" # 정보상자(감독, 개봉일 등)
    OTHER = "other" # 그 외


class RetrievalModel(str, Enum):
    """검색 구성 (평가 A/B/C와 같은 뜻)"""
    VECTOR = "vector" # A: Vector만
    HYBRID = "hybrid" # B : BM25 + Vector (RRF 결합)
    HYBRID_RERANK = "hybrid_rerank" # C: hybrid + Re-ranking


class APIResponse(BaseModel, Generic[T]):
    success: bool = Field(description="처리 성공/실패 여부")
    code: str | None = Field(None, description="비즈니스 식별 코드 (성공 시 ok, 실패 시 에러 코드)")
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