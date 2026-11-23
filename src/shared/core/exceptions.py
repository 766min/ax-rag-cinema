from typing import Generic, TypeVar
from fastapi import status

T = TypeVar("T")

class SharedException(Exception, Generic[T]):
    """모든 애플리케이션 예외의 공통 부모 클래스"""
    def __init__(
        self,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        error_code: str = "BAD_REQUEST",
        errors: T | None = None,
    ):
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.errors: T | None = errors
        super().__init__(message)


class BadRequestException(SharedException[T]):
    def __init__(self, message: str, error_code: str = "BAD_REQUEST", errors: T | None = None):
        super().__init__(message=message, status_code=status.HTTP_400_BAD_REQUEST, error_code=error_code, errors=errors)


class NotFoundException(SharedException[None]):
    def __init__(self, message: str = "리소스를 찾을 수 없습니다.", error_code: str = "NOT_FOUND"):
        super().__init__(message=message, status_code=status.HTTP_404_NOT_FOUND, error_code=error_code, errors=None)


class ServiceUnavailableException(SharedException[None]):
    """LLM·임베딩·DB 등 외부 서비스 호출 실패 (정상적인 근거 없음과 구분)"""
    def __init__(self, message: str = "외부 서비스 호출에 실패했습니다.", error_code: str = "SERVICE_UNAVAILABLE"):
        super().__init__(message=message, status_code=status.HTTP_503_SERVICE_UNAVAILABLE, error_code=error_code, errors=None)