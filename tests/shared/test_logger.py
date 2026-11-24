import logging
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from shared.core import logger as log_mod


def _is_ours(handler: logging.Handler) -> bool:
    return bool(
        getattr(handler, log_mod._HANDLER_MARK, False)
        or getattr(handler, log_mod._FILE_HANDLER_MARK, False)
    )


@pytest.fixture
def clean_root(monkeypatch: pytest.MonkeyPatch) -> Iterator[logging.Logger]:
    """우리 핸들러를 떼어 낸 깨끗한 루트 로거를 주고, 테스트가 끝나면 원래대로 돌린다."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    for handler in saved_handlers:
        if _is_ours(handler):
            root.removeHandler(handler)

    # 실제 .env 와 셸 환경변수가 테스트 결과를 바꾸지 않게 막는다
    monkeypatch.setattr(log_mod, "load_dotenv", lambda: None)
    for key in ("LOGGING_MODE", "LOG_FILE"):
        monkeypatch.delenv(key, raising=False)

    yield root

    for handler in root.handlers[:]:
        if _is_ours(handler):
            root.removeHandler(handler)
            handler.close()
    for handler in saved_handlers:
        if handler not in root.handlers:
            root.addHandler(handler)
    root.setLevel(saved_level)


def _our_handlers(root: logging.Logger) -> list[logging.Handler]:
    return [h for h in root.handlers if _is_ours(h)]


# --- 핸들러 중복 방지 ---------------------------------------------------------


def test_여러번_호출해도_로그는_한번만(
    clean_root: logging.Logger, capsys: pytest.CaptureFixture[str]
) -> None:
    log_mod.setup_logging()
    log_mod.setup_logging()
    log_mod.get_logger("t").info("한번")

    assert len(_our_handlers(clean_root)) == 1
    assert capsys.readouterr().err.count("한번") == 1


def test_다른_핸들러는_지우지_않는다(clean_root: logging.Logger) -> None:
    foreign = logging.NullHandler()
    clean_root.addHandler(foreign)
    try:
        log_mod.setup_logging()
        assert foreign in clean_root.handlers
    finally:
        clean_root.removeHandler(foreign)


# --- 로그 레벨 ---------------------------------------------------------------


def test_기본_레벨은_INFO(clean_root: logging.Logger) -> None:
    log_mod.setup_logging()
    assert clean_root.level == logging.INFO


def test_환경변수로_레벨_지정(clean_root: logging.Logger, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOGGING_MODE", "warning")
    log_mod.setup_logging()
    assert clean_root.level == logging.WARNING


def test_인자가_환경변수보다_우선(
    clean_root: logging.Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOGGING_MODE", "warning")
    log_mod.setup_logging("debug")
    assert clean_root.level == logging.DEBUG


def test_모르는_레벨은_경고_후_INFO(
    clean_root: logging.Logger, caplog: pytest.LogCaptureFixture
) -> None:
    log_mod.setup_logging("verbose")
    assert clean_root.level == logging.INFO
    assert "알 수 없는 로그 레벨" in caplog.text


# --- 파일 로그 ---------------------------------------------------------------


def test_파일_로그는_지정할_때만(clean_root: logging.Logger) -> None:
    log_mod.setup_logging()
    assert not any(getattr(h, log_mod._FILE_HANDLER_MARK, False) for h in clean_root.handlers)


def test_파일에_한번만_기록(clean_root: logging.Logger, tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "app.log"  # logs/ 폴더는 아직 없음
    log_mod.setup_logging(log_file=str(log_file))
    log_mod.setup_logging(log_file=str(log_file))
    log_mod.get_logger("t").info("파일에도")

    assert log_file.read_text(encoding="utf-8").count("파일에도") == 1


def test_상대경로는_프로젝트_루트_기준(
    clean_root: logging.Logger, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(log_mod, "_PROJECT_ROOT", tmp_path)  # 실제 프로젝트에 파일을 만들지 않는다
    log_mod.setup_logging(log_file="logs/app.log")
    assert (tmp_path / "logs" / "app.log").exists()


# --- 민감 정보 마스킹 ---------------------------------------------------------


def _format(message: str, secrets: list[str] | None = None) -> str:
    formatter = log_mod._MaskingFormatter("%(message)s", log_mod.DATE_FORMAT, secrets or [])
    return formatter.format(logging.makeLogRecord({"msg": message}))


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("key=sk-proj-abcDEF123456789", "key=sk-***"),
        ("Authorization: Bearer eyJabc.def", "Authorization: Bearer ***"),
        ("postgresql+psycopg://rag:p@ss@localhost/db", "postgresql+psycopg://rag:***@localhost/db"),
        ("task-manager http://localhost:8000/ask", "task-manager http://localhost:8000/ask"),
    ],
)
def test_패턴_마스킹(message: str, expected: str) -> None:
    assert _format(message) == expected


def test_환경변수_비밀값_마스킹() -> None:
    assert _format("brave=BSAxyz9876543210", ["BSAxyz9876543210"]) == "brave=***"


def test_traceback_안도_마스킹() -> None:
    formatter = log_mod._MaskingFormatter("%(message)s", log_mod.DATE_FORMAT, [])
    try:
        raise ValueError("bad sk-proj-abcDEF123456789")
    except ValueError:
        record = logging.makeLogRecord({"msg": "실패", "exc_info": sys.exc_info()})
    text = formatter.format(record)

    assert "sk-proj-abcDEF123456789" not in text
    assert "ValueError: bad sk-***" in text


def test_비밀값_환경변수_수집(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_API_KEY", "abcdefgh1234")
    monkeypatch.setenv("TEST_SHORT_KEY", "abc")  # 8자 미만은 제외
    monkeypatch.setenv("TEST_MODEL", "gpt-something-long")  # 이름이 규칙에 안 맞으면 제외

    secrets = log_mod._collect_env_secrets()
    assert "abcdefgh1234" in secrets
    assert "abc" not in secrets
    assert "gpt-something-long" not in secrets