import asyncio
import logging
import re
import time

import pytest

from shared.core.timing import measure_time

# 로그 한 줄 예: "retrieval success 21.3ms" / "boom error 0.1ms (ValueError)"
_LINE = re.compile(r"(?P<label>\S+) (?P<status>success|error|cancelled) (?P<ms>[\d.]+)ms")


def _records(caplog: pytest.LogCaptureFixture) -> list[re.Match[str]]:
    return [m for r in caplog.records if (m := _LINE.search(r.getMessage()))]


@pytest.fixture(autouse=True)
def _info_level(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)


# --- 동기 함수 ---------------------------------------------------------------


def test_동기_반환값과_인자를_그대로_전달(caplog: pytest.LogCaptureFixture) -> None:
    @measure_time(label="add")
    def add(a: int, b: int = 0) -> int:
        """더한다."""
        return a + b

    assert add(1, b=2) == 3
    assert add.__name__ == "add"  # functools.wraps 로 메타데이터 보존
    assert add.__doc__ == "더한다."
    [line] = _records(caplog)
    assert (line["label"], line["status"]) == ("add", "success")


def test_동기_실행시간을_ms로_기록(caplog: pytest.LogCaptureFixture) -> None:
    @measure_time(label="slow")
    def slow() -> None:
        time.sleep(0.02)

    slow()
    [line] = _records(caplog)
    assert float(line["ms"]) >= 15


def test_동기_예외는_기록하고_다시_던진다(caplog: pytest.LogCaptureFixture) -> None:
    @measure_time(label="boom")
    def boom() -> None:
        raise ValueError("bad")

    with pytest.raises(ValueError, match="bad"):
        boom()
    [line] = _records(caplog)
    assert line["status"] == "error"
    assert "(ValueError)" in caplog.text


def test_label이_없으면_함수이름(caplog: pytest.LogCaptureFixture) -> None:
    @measure_time()
    def chunking() -> None:
        pass

    chunking()
    [line] = _records(caplog)
    assert line["label"].endswith("chunking")


# --- 비동기 함수 -------------------------------------------------------------


def test_비동기_반환값_유지(caplog: pytest.LogCaptureFixture) -> None:
    @measure_time(label="retrieval")
    async def retrieve(query: str) -> list[str]:
        await asyncio.sleep(0.01)
        return [query]

    assert asyncio.run(retrieve("q")) == ["q"]
    [line] = _records(caplog)
    assert (line["label"], line["status"]) == ("retrieval", "success")
    assert float(line["ms"]) >= 5


def test_비동기_예외는_기록하고_다시_던진다(caplog: pytest.LogCaptureFixture) -> None:
    @measure_time(label="generation")
    async def generate() -> None:
        raise TimeoutError

    with pytest.raises(TimeoutError):
        asyncio.run(generate())
    [line] = _records(caplog)
    assert line["status"] == "error"


def test_비동기_취소도_기록(caplog: pytest.LogCaptureFixture) -> None:
    @measure_time(label="reranking")
    async def rerank() -> None:
        await asyncio.sleep(10)

    async def main() -> None:
        task = asyncio.create_task(rerank())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    [line] = _records(caplog)
    assert line["status"] == "cancelled"


# --- 측정 실패가 원래 결과를 덮지 않음 -------------------------------------------


class _BrokenLogger(logging.Logger):
    def info(self, *args: object, **kwargs: object) -> None:  # type: ignore[override]
        raise RuntimeError("로그 실패")

    def warning(self, *args: object, **kwargs: object) -> None:  # type: ignore[override]
        raise RuntimeError("로그 실패")


def test_로그가_실패해도_반환값은_그대로() -> None:
    @measure_time(label="x", logger=_BrokenLogger("broken"))
    def ok() -> int:
        return 42

    assert ok() == 42


def test_로그가_실패해도_원래_예외가_나온다() -> None:
    @measure_time(label="x", logger=_BrokenLogger("broken"))
    def boom() -> None:
        raise ValueError("원래 예외")

    with pytest.raises(ValueError, match="원래 예외"):
        boom()
