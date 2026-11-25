"""DB 연결 팩토리가 연결 대기 상한(DB_CONNECT_TIMEOUT)을 넘기는지.

설정 모듈은 필수 값(.env)이 없으면 불러올 때 멈춘다. CI 에는 .env 가 없으므로
가짜 값을 먼저 넣고(이미 있는 값은 그대로 둔다) 모듈을 불러온다. 실제 키 · DB 는 쓰지 않는다.
"""

import importlib
from types import ModuleType
from typing import Any

import pytest

DUMMY_ENV = {
    "OPENAI_API_KEY": "test-dummy-key",
    "EMBEDDING_MODEL": "text-embedding-3-small",
    "LLM_PROVIDER": "openai",
    "LLM_MODEL": "test-model",
    "REASONING_EFFORT": "low",
    "LLM_TIMEOUT": "30",
    "DATABASE_URL": "postgresql+psycopg://user:pass@127.0.0.1:5432/test_db",
    "MOVIE_COLLECTION": "test_movies",
}


@pytest.fixture
def vector_store(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    import os

    for key, value in DUMMY_ENV.items():
        if not os.getenv(key):
            monkeypatch.setenv(key, value)
    return importlib.import_module("shared.core.vector_store")


def test_raw_connection_passes_connect_timeout(
    vector_store: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def fake_connect(dsn: str, **kwargs: Any) -> str:
        seen["dsn"], seen["kwargs"] = dsn, kwargs
        return "conn"

    monkeypatch.setattr(vector_store.psycopg, "connect", fake_connect)
    assert vector_store.get_raw_db_connection() == "conn"
    assert seen["kwargs"] == {"connect_timeout": vector_store.settings.DB_CONNECT_TIMEOUT}
    assert not seen["dsn"].startswith("postgresql+psycopg://")


def test_vector_store_passes_connect_timeout_to_engine(
    vector_store: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def fake_pgvector(**kwargs: Any) -> str:
        seen.update(kwargs)
        return "store"

    monkeypatch.setattr(vector_store, "PGVector", fake_pgvector)
    monkeypatch.setattr(vector_store, "get_embeddings", lambda: "emb")
    assert vector_store.get_vector_store("c") == "store"
    assert seen["engine_args"] == {
        "connect_args": {"connect_timeout": vector_store.settings.DB_CONNECT_TIMEOUT}
    }
