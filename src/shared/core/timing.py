import asyncio
import functools
import inspect
import logging
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, ParamSpec, TypeVar, cast

from shared.core.logger import get_logger

P = ParamSpec("P")
R = TypeVar("R")

# 지금 실행 중인 measure_time 단계 이름 (중첩되면 가장 안쪽)
_current_label: ContextVar[str | None] = ContextVar("timing_current_label", default=None)
# collect_timings() 블록 안에서만 측정 이벤트를 모으는 리스트
_sink: ContextVar[list[dict[str, Any]] | None] = ContextVar("timing_sink", default=None)


def current_label() -> str | None:
    """지금 실행 중인 measure_time 단계 이름. 단계 밖이면 None."""
    return _current_label.get()


@contextmanager
def collect_timings() -> Iterator[list[dict[str, Any]]]:
    """블록 안에서 끝난 measure_time 측정을 이벤트 dict 리스트로 모은다.

    비동기 작업마다 따로 모이고(contextvars), 블록이 끝나면 이전 상태로 돌아간다.
    shortcut: ThreadPoolExecutor.submit 처럼 컨텍스트를 복사하지 않는 스레드 안의 측정은 빠진다.
    asyncio.to_thread 는 복사하므로 괜찮다.
    """
    events: list[dict[str, Any]] = []
    token = _sink.set(events)
    try:
        yield events
    finally:
        _sink.reset(token)


def run_status(error: BaseException | None) -> str:
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
        status = run_status(error)
        events = _sink.get()
        if events is not None:
            events.append(
                {
                    "label": label,
                    "duration_ms": round(duration_ms, 1),
                    "status": status,
                    "error_type": type(error).__name__ if error else None,
                }
            )
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
                token = _current_label.set(name)
                start = time.perf_counter()
                error: BaseException | None = None
                try:
                    return await async_func(*args, **kwargs)
                except BaseException as exc:  # CancelledError 도 잡아서 기록한 뒤 다시 던진다
                    error = exc
                    raise
                finally:
                    _report(log, name, start, error)
                    _current_label.reset(token)

            return cast(Callable[P, R], async_wrapper)

        @functools.wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            token = _current_label.set(name)
            start = time.perf_counter()
            error: BaseException | None = None
            try:
                return func(*args, **kwargs)
            except BaseException as exc:
                error = exc
                raise
            finally:
                _report(log, name, start, error)
                _current_label.reset(token)

        return sync_wrapper

    return decorator
