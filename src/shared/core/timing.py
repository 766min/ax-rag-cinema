import asyncio
import functools
import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar, cast

from shared.core.logger import get_logger

P = ParamSpec("P")
R = TypeVar("R")


def _status(error: BaseException | None) -> str:
    """실행 결과를 success / cancelled / error 중 하나로 나눈다."""
    if error is None:
        return "success"
    if isinstance(error, asyncio.CancelledError):
        return "cancelled"
    return "error"


def _report(log: logging.Logger, label: str, start: float, error: BaseException | None) -> None:
    """걸린 시간을 로그로 남긴다. 여기서 난 오류는 원래 함수의 결과·예외를 덮지 않게 삼킨다."""
    try:
        duration_ms = (time.perf_counter() - start) * 1000
        status = _status(error)
        if error is None:
            log.info("%s %s %.1fms", label, status, duration_ms)
        else:
            log.warning("%s %s %.1fms (%s)", label, status, duration_ms, type(error).__name__)
    except Exception:
        pass


def measure_time(
    label: str | None = None, logger: logging.Logger | None = None
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """함수 실행 시간을 duration_ms 로 재서 로그에 남긴다. 동기·비동기 함수 모두 지원한다.

    shortcut: generator 는 만들어지는 순간만 재므로 실제 반복 시간은 측정되지 않는다.
    스트리밍 측정이 필요해지면 별도 처리를 추가한다.
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        name = label or func.__qualname__
        log = logger or get_logger(func.__module__)

        if inspect.iscoroutinefunction(func):
            async_func = cast(Callable[P, Awaitable[Any]], func)

            @functools.wraps(func)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
                start = time.perf_counter()
                error: BaseException | None = None
                try:
                    return await async_func(*args, **kwargs)
                except BaseException as exc:  # CancelledError 도 잡아서 기록한 뒤 다시 던진다
                    error = exc
                    raise
                finally:
                    _report(log, name, start, error)

            return cast(Callable[P, R], async_wrapper)

        @functools.wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            start = time.perf_counter()
            error: BaseException | None = None
            try:
                return func(*args, **kwargs)
            except BaseException as exc:
                error = exc
                raise
            finally:
                _report(log, name, start, error)

        return sync_wrapper

    return decorator
