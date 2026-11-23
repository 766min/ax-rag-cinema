import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    # LLM 모델 설정
    API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    LLM_PROVIDE: str = os.getenv("LLM_PROVIDER", "openai")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-5-mini")
    REASONING_EFFORT: str = os.getenv("REASONING_EFFORT", "low")

    # PostgreSQL (PGVector) 접속 정보
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://postgres:postgre@localhost:5432/rag_db",
    )

    # 도메인별 컬렉션 분리
    CHATBOT_COLLECTION: str = os.getenv("CHATBOT_COLLECTION", "mcu_movies")

settings = Settings()