"""Read the team handoff ZIP and adapt it to the repository's canonical models."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shared.core.models import Alias, Chunk, Document, SourceId, Work

ZIP_ROOT = "handoff/"


def content_hash(chunk_id: str, header: str, text: str) -> str:
    """Return the stable hash defined by architecture.md §4.2."""
    return hashlib.sha256(f"{chunk_id}\n{header}\n{text}".encode()).hexdigest()


def index_version(chunks: list[Chunk]) -> str:
    """Hash the sorted canonical chunk IDs and hashes."""
    value = "\n".join(
        f"{c.chunk_id}:{c.content_hash}" for c in sorted(chunks, key=lambda c: c.chunk_id)
    )
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass
class ImportReport:
    source_counts: dict[str, int] = field(default_factory=dict)
    adapted_counts: dict[str, int] = field(default_factory=dict)
    skipped: Counter[str] = field(default_factory=Counter)
    base_index_version: str = ""
    enriched_index_version: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_counts": self.source_counts,
            "adapted_counts": self.adapted_counts,
            "skipped": dict(sorted(self.skipped.items())),
            "base_index_version": self.base_index_version,
            "enriched_index_version": self.enriched_index_version,
        }


@dataclass
class ImportBundle:
    works: list[Work]
    documents: list[Document]
    aliases: list[Alias]
    chunks: list[Chunk]
    enriched_chunks: list[Chunk]
    characters: list[dict[str, Any]]
    report: ImportReport


class HandoffReader:
    def __init__(self, path: Path):
        self.path = path

    def records(self, name: str) -> Iterator[dict[str, Any]]:
        with zipfile.ZipFile(self.path) as archive, archive.open(f"{ZIP_ROOT}{name}") as binary:
            for line in io.TextIOWrapper(binary, encoding="utf-8-sig"):
                if line.strip():
                    yield json.loads(line)

    def json(self, name: str) -> dict[str, Any]:
        with zipfile.ZipFile(self.path) as archive:
            return json.loads(archive.read(f"{ZIP_ROOT}{name}").decode("utf-8-sig"))


def _source_ids(row: dict[str, Any], **extra: Any) -> dict[str, str | None]:
    result = {
        str(key): None if value is None else str(value)
        for key, value in (row.get("source_ids") or {}).items()
    }
    for key, value in extra.items():
        result[key] = (
            None
            if value is None
            else (
                json.dumps(value, ensure_ascii=False)
                if isinstance(value, (list, dict))
                else str(value)
            )
        )
    return result


def _media_type(value: Any) -> str:
    mapping = {
        "tv_series": "tvSeries",
        "tv_episode": "tvEpisode",
        "tv_season": "tvSeason",
    }
    return mapping.get(str(value), str(value or "unknown"))


def _year(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _require_unique(values: list[str], label: str) -> None:
    duplicates = [value for value, count in Counter(values).items() if count > 1]
    if duplicates:
        preview = ", ".join(duplicates[:5])
        raise ValueError(f"중복 {label}가 있습니다 ({len(duplicates)}개): {preview}")


def adapt_handoff(path: Path) -> ImportBundle:
    reader = HandoffReader(path)
    manifest = reader.json("manifest.json")
    if manifest.get("schema_version") != "team-v1":
        raise ValueError(f"지원하지 않는 handoff schema_version: {manifest.get('schema_version')}")

    raw_works = list(reader.records("works.jsonl"))
    raw_documents = list(reader.records("documents.jsonl"))
    raw_chunks = list(reader.records("chunks.jsonl"))
    raw_enriched = list(reader.records("enriched/enriched_chunks.jsonl"))
    raw_aliases = list(reader.records("aliases.jsonl"))
    raw_characters = list(reader.records("characters.jsonl"))
    inventory = {row["tconst"]: row for row in reader.records("imdb_inventory.jsonl")}

    report = ImportReport(
        source_counts={
            "works": len(raw_works),
            "documents": len(raw_documents),
            "chunks": len(raw_chunks),
            "enriched_chunks": len(raw_enriched),
            "aliases": len(raw_aliases),
            "characters": len(raw_characters),
        }
    )
    for name, actual in report.source_counts.items():
        expected = (manifest.get("counts") or {}).get(name)
        if expected is not None and expected != actual:
            raise ValueError(f"manifest count 불일치: {name} expected={expected}, actual={actual}")

    work_map: dict[str, str] = {}
    works: list[Work] = []
    work_rows: dict[str, dict[str, Any]] = {}
    for row in raw_works:
        imdb_id = (row.get("source_ids") or {}).get("imdb_tconst")
        if not imdb_id:
            report.skipped["work_without_imdb_id"] += 1
            continue
        if not row.get("title_ko"):
            report.skipped["work_without_korean_title"] += 1
            continue
        work_map[row["work_id"]] = imdb_id
        work_rows[imdb_id] = row
        imdb = inventory.get(imdb_id, {})
        works.append(
            Work(
                work_id=imdb_id,
                title_ko=row["title_ko"],
                title_original=row["title_original"],
                format=_media_type(row.get("content_type")),
                year=_year(imdb.get("year") or imdb.get("startYear")),
                season=row.get("season"),
                parent_work_id=None,
                phase=None if row.get("phase") is None else str(row["phase"]),
                timeline_order=row.get("timeline_order"),
                is_canon=row.get("is_canon") if isinstance(row.get("is_canon"), bool) else None,
                source_ids=_source_ids(
                    row,
                    handoff_work_id=row.get("work_id"),
                    source_names=row.get("source_names"),
                ),
            )
        )

    document_rows = {row["doc_id"]: row for row in raw_documents}
    document_to_work: dict[str, str] = {}
    for canonical_id, row in work_rows.items():
        original_doc = (row.get("source_ids") or {}).get("wikipedia_document_id")
        if original_doc:
            document_to_work[original_doc] = canonical_id
    for row in raw_chunks:
        raw_work_id = row.get("work_id")
        mapped_work_id = work_map.get(str(raw_work_id)) if raw_work_id else None
        if mapped_work_id:
            document_to_work[row["doc_id"]] = mapped_work_id

    doc_id_map: dict[str, str] = {}
    documents: list[Document] = []
    for row in raw_documents:
        canonical_work_id = document_to_work.get(row["doc_id"])
        if canonical_work_id:
            canonical_doc_id = f"{canonical_work_id}-wikipedia"
        elif row.get("doc_kind") in {"character", "universe", "character_list"} and row.get(
            "pageid"
        ):
            canonical_doc_id = f"wp-{row['pageid']}"
        else:
            report.skipped["document_without_canonical_work"] += 1
            continue
        doc_id_map[row["doc_id"]] = canonical_doc_id
        documents.append(
            Document(
                doc_id=canonical_doc_id,
                work_id=canonical_work_id,
                source_id=SourceId.WIKIPEDIA_EN,
                source_url=row.get("source_url") or row.get("permalink") or "",
                license=row.get("license") or row.get("license_id"),
                revid=row.get("revid") or row.get("revision_id"),
                fetched_at=row.get("fetched_at") or row.get("revision_timestamp"),
                doc_type=row.get("doc_kind") or row.get("doc_type") or "content",
                source_ids=_source_ids(
                    row,
                    handoff_doc_id=row.get("doc_id"),
                    team_document_id=row.get("document_id"),
                    wikidata_qid=row.get("wikidata_qid"),
                    enwiki_pageid=row.get("pageid"),
                ),
            )
        )

    chunks: list[Chunk] = []
    source_chunk_to_canonical: dict[str, Chunk] = {}
    for row in raw_chunks:
        raw_work_id = row.get("work_id")
        raw_doc_id = row.get("doc_id")
        canonical_work_id = work_map.get(str(raw_work_id)) if raw_work_id else None
        mapped_doc_id = doc_id_map.get(str(raw_doc_id)) if raw_doc_id else None
        if not canonical_work_id or not mapped_doc_id:
            report.skipped["chunk_without_canonical_work"] += 1
            continue
        doc = document_rows[row["doc_id"]]
        work = work_rows[canonical_work_id]
        chunk_id = f"{mapped_doc_id}-{row['section']}-p{int(row['position']):02d}"
        chunk = Chunk(
            chunk_id=chunk_id,
            doc_id=mapped_doc_id,
            work_id=canonical_work_id,
            source_id=SourceId.WIKIPEDIA_EN,
            section=row["section"],
            position=row["position"],
            total=row["total"],
            text=row["text"],
            header=row.get("header") or "",
            title_ko=work.get("title_ko"),
            title_original=work.get("title_original"),
            source_url=doc.get("source_url") or doc.get("permalink") or "",
            revid=doc.get("revid") or doc.get("revision_id"),
            entities=row.get("entities") or [],
            flags=row.get("flags") or [],
            content_hash=content_hash(chunk_id, row.get("header") or "", row["text"]),
            source_ids=_source_ids(
                row,
                handoff_chunk_id=row.get("chunk_id"),
                handoff_doc_id=row.get("doc_id"),
                handoff_work_id=row.get("work_id"),
                source_plot_ids=row.get("source_plot_ids"),
            ),
        )
        chunks.append(chunk)
        source_chunk_to_canonical[row["chunk_id"]] = chunk

    enriched_chunks: list[Chunk] = []
    for row in raw_enriched:
        source_ids = row.get("source_chunk_ids") or [row.get("chunk_id")]
        base = next((source_chunk_to_canonical.get(value) for value in source_ids if value), None)
        if base is None:
            report.skipped["enriched_chunk_without_canonical_base"] += 1
            continue
        enriched_text = row.get("cleaned_text") or row.get("text") or ""
        character_names = [
            item.get("name_en")
            for item in (row.get("character_mentions") or [])
            if isinstance(item, dict) and item.get("name_en")
        ]
        enriched_chunks.append(
            Chunk.model_validate(
                {
                    **base.model_dump(),
                    "text": enriched_text,
                    "entities": list(
                        dict.fromkeys([*(row.get("entities") or []), *character_names])
                    ),
                    "content_hash": content_hash(base.chunk_id, base.header, enriched_text),
                    "source_ids": _source_ids(
                        row,
                        handoff_chunk_id=row.get("chunk_id"),
                        source_chunk_ids=source_ids,
                        source_document_ids=row.get("source_document_ids"),
                        source_revisions=row.get("source_revisions"),
                        processing=row.get("processing"),
                        summary_original=row.get("summary_original"),
                    ),
                }
            )
        )

    aliases = [
        Alias.model_validate(row)
        for row in raw_aliases
        if row.get("target_id") in {w.work_id for w in works}
    ]
    _require_unique([item.work_id for item in works], "work_id")
    _require_unique([item.doc_id for item in documents], "doc_id")
    _require_unique([item.chunk_id for item in chunks], "base chunk_id")
    _require_unique([item.chunk_id for item in enriched_chunks], "enriched chunk_id")
    _require_unique(
        [str(item.get("char_id") or item.get("character_id")) for item in raw_characters],
        "char_id",
    )
    report.skipped["alias_without_canonical_work"] += len(raw_aliases) - len(aliases)
    report.adapted_counts = {
        "works": len(works),
        "documents": len(documents),
        "chunks": len(chunks),
        "enriched_chunks": len(enriched_chunks),
        "aliases": len(aliases),
        "characters": len(raw_characters),
    }
    report.base_index_version = index_version(chunks)
    report.enriched_index_version = index_version(enriched_chunks)
    return ImportBundle(works, documents, aliases, chunks, enriched_chunks, raw_characters, report)
