# .env를 읽어서 settings 객체로 제공하는 역할
import os

from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    """반드시 있어야 하는 값을 읽는다. 없으면 앱을 시작하지 않는다."""
    value = os.getenv(key)
    if not value:
        raise RuntimeError(f".env에 {key} 값이 없습니다. .env 양식을 확인하세요.")
    return value


def _optional(key: str, default: str) -> str:
    """없어도 되는 값을 읽는다. 없으면 기본값을 쓴다."""
    value = os.getenv(key)
    return value if value else default


def _flag(key: str, default: bool = False) -> bool:
    """켜고 끄는 값(true/false)을 읽는다."""
    value = os.getenv(key)
    if not value:
        return default
    return value.strip().lower() in {"true", "1", "yes", "on"}


class Settings:
    # 필수: 없으면 앱이 뜨지 않는다
    API_KEY: str = _require("OPENAI_API_KEY")
    EMBEDDING_MODEL: str = _require("EMBEDDING_MODEL")
    LLM_PROVIDER: str = _require("LLM_PROVIDER")
    LLM_MODEL: str = _require("LLM_MODEL")
    REASONING_EFFORT: str = _require("REASONING_EFFORT")
    LLM_TIMEOUT: int = int(_require("LLM_TIMEOUT"))
    DATABASE_URL: str = _require("DATABASE_URL")
    MOVIE_COLLECTION: str = _require("MOVIE_COLLECTION")  # 고정 문서 컬렉션 (A·B·C 평가용)

    # 선택: D 구성 (LLM 가공 색인, 원문 컬렉션과 분리)
    ENRICHED_COLLECTION: str = _optional("ENRICHED_COLLECTION", "mcu_movies_enriched")

    # 선택: 웹 검색 (서비스 경로 전용, 평가에서는 끔)
    BRAVE_API_KEY: str = _optional("BRAVE_API_KEY", "")
    WEB_COLLECTION: str = _optional("WEB_COLLECTION", "mcu_web")
    WEB_STORE: bool = _flag("WEB_STORE", default=False)  # 저장 권한 있는 Brave 요금제에서만 true
    WEB_STORE_TTL_DAYS: int = int(_optional("WEB_STORE_TTL_DAYS", "30"))  # 지나면 다시 검색


settings = Settings()
