import os
from dotenv import load_dotenv

load_dotenv()

def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise RuntimeError(f".env에 {key} 값이 없습니다. .env 양식을 확인하세요.")
    return value

class Settings:
    API_KEY: str = _require("OPENAI_API_KEY")
    EMBEDDING_MODEL: str = _require("EMBEDDING_MODEL")
    LLM_PROVIDER: str = _require("LLM_PROVIDER")
    LLM_MODEL: str = _require("LLM_MODEL")
    REASONING_EFFORT: str = _require("REASONING_EFFORT")
    LLM_TIMEOUT: int = int(_require("LLM_TIMEOUT"))
    DATABASE_URL: str = _require("DATABASE_URL")
    MOVIE_COLLECTION: str = _require("MOVIE_COLLECTION")

settings = Settings()