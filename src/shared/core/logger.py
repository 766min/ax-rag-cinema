import logging
import os

from dotenv import load_dotenv

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d) - %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 우리 코드를 DEBUG로 켜도 시끄럽지 않게 WARNING으로 고정할 외부 라이브러리
# (httpx는 API 호출 횟수를 INFO로 보여 줘서 학습용으로 남겨 둔다)
_NOISY_LOGGERS = ("httpcore", "urllib3", "openai", "langfuse")

# 우리가 붙인 핸들러에 다는 표시 (중복 등록 방지용)
_HANDLER_MARK = "_cineverse_handler"


def _resolve_level(level: str | int | None) -> int:
    """인자 > LOGGING_MODE 환경변수 > INFO 순서로 로그 레벨을 정한다."""
    if isinstance(level, int):
        return level

    name = (level or os.getenv("LOGGING_MODE") or "INFO").strip().upper()
    mapping = logging.getLevelNamesMapping()
    if name not in mapping:
        logging.getLogger(__name__).warning("알 수 없는 로그 레벨 %r, INFO로 설정합니다.", name)
        return logging.INFO
    return mapping[name]


def setup_logging(level: str | int | None = None) -> None:
    """로깅을 설정한다. 여러 번 불러도 핸들러는 하나만 붙고 레벨만 갱신된다."""
    load_dotenv()  # 이미 있는 환경변수는 덮어쓰지 않는다

    root = logging.getLogger()
    already = any(getattr(h, _HANDLER_MARK, False) for h in root.handlers)
    if not already:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        setattr(handler, _HANDLER_MARK, True)
        root.addHandler(handler)

    root.setLevel(_resolve_level(level))

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> logging.Logger:
    logger_name = name or os.getenv("LOGGER_NAME", "rag-api")
    return logging.getLogger(logger_name)


# 학습 중에는 import만 해도 INFO가 보이도록 기본 설정을 한 번 해 둔다.
# 다시 불러도 핸들러는 늘지 않고, 시작점에서 setup_logging("debug")처럼 레벨만 바꿀 수 있다.
setup_logging()

logger = get_logger()  # 기존 코드 호환용