"""DB 연결 팩토리가 연결 대기 상한(DB_CONNECT_TIMEOUT)을 넘기는지"""

from typing import Any

from shared.core import vector_store
from shared.core.config import settings


def test_raw_connection_passes_connect_timeout(monkeypatch: Any) -> None:
    seen: dict[str, Any] = {}

    def fake_connect(dsn: str, **kwargs: Any) -> str:
        seen["dsn"], seen["kwargs"] = dsn, kwargs
        return "conn"

    monkeypatch.setattr(vector_store.psycopg, "connect", fake_connect)
    assert vector_store.get_raw_db_connection() == "conn"
    assert seen["kwargs"] == {"connect_timeout": settings.DB_CONNECT_TIMEOUT}
    assert not seen["dsn"].startswith("postgresql+psycopg://")


def test_vector_store_passes_connect_timeout_to_engine(monkeypatch: Any) -> None:
    seen: dict[str, Any] = {}

    def fake_pgvector(**kwargs: Any) -> str:
        seen.update(kwargs)
        return "store"

    monkeypatch.setattr(vector_store, "PGVector", fake_pgvector)
    monkeypatch.setattr(vector_store, "get_embeddings", lambda: "emb")
    assert vector_store.get_vector_store("c") == "store"
    assert seen["engine_args"] == {"connect_args": {"connect_timeout": settings.DB_CONNECT_TIMEOUT}}
