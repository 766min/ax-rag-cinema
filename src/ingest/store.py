"""Persist adapted handoff metadata and vector chunks."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from langchain_core.documents import Document as LangChainDocument
from psycopg.types.json import Jsonb

from ingest.handoff import ImportBundle
from shared.core.models import Chunk

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS works (
    work_id TEXT PRIMARY KEY,
    title_ko TEXT NOT NULL,
    title_original TEXT NOT NULL,
    format TEXT NOT NULL,
    year INTEGER,
    season INTEGER,
    parent_work_id TEXT,
    phase TEXT,
    timeline_order INTEGER,
    is_canon BOOLEAN,
    source_ids JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY,
    work_id TEXT REFERENCES works(work_id),
    source_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    license TEXT,
    revid BIGINT,
    fetched_at TIMESTAMPTZ,
    doc_type TEXT NOT NULL,
    source_ids JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE IF NOT EXISTS aliases (
    alias_ko TEXT NOT NULL,
    canonical_en TEXT NOT NULL,
    kind TEXT NOT NULL,
    target_id TEXT,
    PRIMARY KEY (alias_ko, canonical_en, kind)
);
CREATE TABLE IF NOT EXISTS characters (
    char_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    document_id TEXT,
    source_url TEXT,
    payload JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS ingest_runs (
    id BIGSERIAL PRIMARY KEY,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_path TEXT NOT NULL,
    base_index_version TEXT NOT NULL,
    enriched_index_version TEXT NOT NULL,
    report JSONB NOT NULL
);
"""


def _batches(values: list[Any], size: int) -> Iterable[list[Any]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def upsert_metadata(bundle: ImportBundle, source_path: str) -> None:
    from shared.core.vector_store import get_raw_db_connection

    connection = get_raw_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(SCHEMA_SQL)
            cursor.executemany(
                """
                INSERT INTO works (
                    work_id, title_ko, title_original, format, year, season, parent_work_id,
                    phase, timeline_order, is_canon, source_ids
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (work_id) DO UPDATE SET
                    title_ko=EXCLUDED.title_ko, title_original=EXCLUDED.title_original,
                    format=EXCLUDED.format, year=EXCLUDED.year, season=EXCLUDED.season,
                    parent_work_id=EXCLUDED.parent_work_id, phase=EXCLUDED.phase,
                    timeline_order=EXCLUDED.timeline_order, is_canon=EXCLUDED.is_canon,
                    source_ids=EXCLUDED.source_ids
                """,
                [
                    (
                        item.work_id,
                        item.title_ko,
                        item.title_original,
                        item.format,
                        item.year,
                        item.season,
                        item.parent_work_id,
                        item.phase,
                        item.timeline_order,
                        item.is_canon,
                        Jsonb(item.source_ids),
                    )
                    for item in bundle.works
                ],
            )
            cursor.executemany(
                """
                INSERT INTO documents (
                    doc_id, work_id, source_id, source_url, license, revid, fetched_at,
                    doc_type, source_ids
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (doc_id) DO UPDATE SET
                    work_id=EXCLUDED.work_id, source_id=EXCLUDED.source_id,
                    source_url=EXCLUDED.source_url, license=EXCLUDED.license,
                    revid=EXCLUDED.revid, fetched_at=EXCLUDED.fetched_at,
                    doc_type=EXCLUDED.doc_type, source_ids=EXCLUDED.source_ids
                """,
                [
                    (
                        item.doc_id,
                        item.work_id,
                        str(item.source_id),
                        item.source_url,
                        item.license,
                        item.revid,
                        item.fetched_at,
                        item.doc_type,
                        Jsonb(item.source_ids),
                    )
                    for item in bundle.documents
                ],
            )
            cursor.executemany(
                """
                INSERT INTO aliases (alias_ko, canonical_en, kind, target_id)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (alias_ko, canonical_en, kind) DO UPDATE
                SET target_id=EXCLUDED.target_id
                """,
                [
                    (item.alias_ko, item.canonical_en, str(item.kind), item.target_id)
                    for item in bundle.aliases
                ],
            )
            cursor.executemany(
                """
                INSERT INTO characters (char_id, name, document_id, source_url, payload)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (char_id) DO UPDATE SET
                    name=EXCLUDED.name, document_id=EXCLUDED.document_id,
                    source_url=EXCLUDED.source_url, payload=EXCLUDED.payload
                """,
                [
                    (
                        item.get("char_id") or item["character_id"],
                        item["name"],
                        item.get("document_id"),
                        item.get("source_url"),
                        Jsonb(item),
                    )
                    for item in bundle.characters
                ],
            )
            cursor.execute(
                """
                INSERT INTO ingest_runs (
                    source_path, base_index_version, enriched_index_version, report
                ) VALUES (%s, %s, %s, %s)
                """,
                (
                    source_path,
                    bundle.report.base_index_version,
                    bundle.report.enriched_index_version,
                    Jsonb(bundle.report.as_dict()),
                ),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _existing_hashes(collection_name: str) -> dict[str, str]:
    from shared.core.vector_store import get_raw_db_connection

    connection = get_raw_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT embedding.id, embedding.cmetadata->>'content_hash'
                FROM langchain_pg_embedding AS embedding
                JOIN langchain_pg_collection AS collection
                  ON collection.uuid = embedding.collection_id
                WHERE collection.name = %s
                """,
                (collection_name,),
            )
            return {str(row[0]): str(row[1]) for row in cursor.fetchall() if row[1]}
    finally:
        connection.close()


def _vector_document(chunk: Chunk, *, enriched: bool) -> LangChainDocument:
    metadata = chunk.model_dump(mode="json")
    metadata["enriched"] = enriched
    return LangChainDocument(
        id=chunk.chunk_id,
        page_content=f"{chunk.header}\n{chunk.text}".strip(),
        metadata=metadata,
    )


def upsert_vectors(
    chunks: list[Chunk], collection_name: str, *, enriched: bool, batch_size: int = 64
) -> dict[str, int]:
    from shared.core.vector_store import get_vector_store

    store = get_vector_store(collection_name)
    existing = _existing_hashes(collection_name)
    changed = [chunk for chunk in chunks if existing.get(chunk.chunk_id) != chunk.content_hash]
    for batch in _batches(changed, batch_size):
        store.add_documents(
            [_vector_document(chunk, enriched=enriched) for chunk in batch],
            ids=[chunk.chunk_id for chunk in batch],
        )
    return {
        "input": len(chunks),
        "upserted": len(changed),
        "unchanged_skipped": len(chunks) - len(changed),
    }


def import_bundle(bundle: ImportBundle, source_path: str, batch_size: int = 64) -> dict[str, Any]:
    from shared.core.config import settings

    upsert_metadata(bundle, source_path)
    base = upsert_vectors(
        bundle.chunks, settings.MOVIE_COLLECTION, enriched=False, batch_size=batch_size
    )
    enriched = upsert_vectors(
        bundle.enriched_chunks,
        settings.ENRICHED_COLLECTION,
        enriched=True,
        batch_size=batch_size,
    )
    return {
        "metadata": bundle.report.adapted_counts,
        "base_vectors": base,
        "enriched_vectors": enriched,
    }
