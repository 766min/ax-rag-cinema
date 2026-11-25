import sys
from types import SimpleNamespace
from typing import Any

import pytest

import ingest.store as store_module
from ingest.handoff import ImportBundle, ImportReport
from ingest.store import storage_id, upsert_vectors
from shared.core.models import Chunk, SourceId


class EmbeddingDown(Exception):
    pass


class FakeStore:
    """Mimics PGVector: one id space shared by every collection."""

    def __init__(self, rows: dict[str, Any], collection: str, fail: bool = False) -> None:
        self.rows = rows
        self.collection = collection
        self.fail = fail

    def add_documents(self, documents: list[Any], ids: list[str]) -> None:
        if self.fail:
            raise RuntimeError("embedding API down")
        for row_id, document in zip(ids, documents, strict=True):
            self.rows[row_id] = (self.collection, document)


def _chunk(text: str) -> Chunk:
    return Chunk(
        chunk_id="tt0371746-wikipedia-plot-p01",
        doc_id="tt0371746-wikipedia",
        work_id="tt0371746",
        source_id=SourceId.WIKIPEDIA_EN,
        section="plot",
        position=1,
        total=1,
        text=text,
        source_url="https://example.org",
        content_hash=f"hash-{text}",
    )


def test_base_and_enriched_rows_do_not_overwrite_each_other() -> None:
    rows: dict[str, Any] = {}
    base, enriched = _chunk("original"), _chunk("cleaned")
    upsert_vectors(
        [base], "mcu_movies", enriched=False, store=FakeStore(rows, "mcu_movies"), existing={}
    )
    upsert_vectors(
        [enriched],
        "mcu_movies_enriched",
        enriched=True,
        store=FakeStore(rows, "mcu_movies_enriched"),
        existing={},
    )

    assert len(rows) == 2
    base_row = rows[storage_id("mcu_movies", base.chunk_id)]
    enriched_row = rows[storage_id("mcu_movies_enriched", base.chunk_id)]
    assert base_row[0] == "mcu_movies" and "original" in base_row[1].page_content
    assert enriched_row[0] == "mcu_movies_enriched" and "cleaned" in enriched_row[1].page_content
    # Citations still use the logical chunk_id.
    assert enriched_row[1].metadata["chunk_id"] == base.chunk_id


def test_unchanged_chunks_are_skipped_and_stale_rows_reported() -> None:
    chunk = _chunk("original")
    existing = {
        storage_id("mcu_movies", chunk.chunk_id): chunk.content_hash,
        chunk.chunk_id: "old-unprefixed-row",
    }
    rows: dict[str, Any] = {}
    result = upsert_vectors(
        [chunk],
        "mcu_movies",
        enriched=False,
        store=FakeStore(rows, "mcu_movies"),
        existing=existing,
    )
    assert result == {"input": 1, "upserted": 0, "unchanged_skipped": 1, "stale_remaining": 1}
    assert rows == {}


def test_failed_vector_load_marks_run_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, str]] = []
    fake_config = SimpleNamespace(
        settings=SimpleNamespace(MOVIE_COLLECTION="mcu_movies", ENRICHED_COLLECTION="mcu_e")
    )
    monkeypatch.setitem(sys.modules, "shared.core.config", fake_config)
    monkeypatch.setattr(store_module, "upsert_metadata", lambda bundle, path: None)
    monkeypatch.setattr(store_module, "_start_run", lambda bundle, path: 7)
    monkeypatch.setattr(
        store_module,
        "_finish_run",
        lambda run_id, status, vectors=None: calls.append((run_id, status)),
    )

    def broken(*args: Any, **kwargs: Any) -> dict[str, int]:
        raise EmbeddingDown

    monkeypatch.setattr(store_module, "upsert_vectors", broken)
    bundle = ImportBundle([], [], [], [_chunk("x")], [], [], ImportReport())
    with pytest.raises(EmbeddingDown):
        store_module.import_bundle(bundle, "handoff.zip")
    assert calls == [(7, "failed")]
