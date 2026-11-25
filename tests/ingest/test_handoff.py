import json
import zipfile
from pathlib import Path

from ingest.handoff import adapt_handoff, content_hash, index_version


def _jsonl(*rows: dict) -> str:
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def _fixture_zip(tmp_path: Path) -> Path:
    path = tmp_path / "handoff.zip"
    mapped_work = {
        "work_id": "cnt_mapped",
        "title_ko": "아이언맨",
        "title_original": "Iron Man",
        "content_type": "movie",
        "season": None,
        "phase": 1,
        "timeline_order": 1,
        "is_canon": "unknown",
        "source_ids": {
            "imdb_tconst": "tt0371746",
            "team_content_id": "cnt_mapped",
            "wikipedia_document_id": "doc_mapped",
        },
        "source_names": ["team_dataset", "wikipedia", "imdb"],
    }
    unmapped_work = {
        **mapped_work,
        "work_id": "cnt_unmapped",
        "title_ko": None,
        "title_original": "Unknown",
        "source_ids": {
            "imdb_tconst": None,
            "team_content_id": "cnt_unmapped",
            "wikipedia_document_id": "doc_unmapped",
        },
    }
    document = {
        "doc_id": "doc_mapped",
        "document_id": "doc_mapped",
        "doc_kind": "content",
        "doc_type": "content",
        "source_id": "wikipedia_en",
        "source_url": "https://example.org/iron-man",
        "permalink": "https://example.org/iron-man?oldid=1",
        "license": "CC BY-SA 4.0",
        "revid": 1,
        "revision_id": 1,
        "fetched_at": None,
        "revision_timestamp": None,
        "pageid": 123,
        "wikidata_qid": "Q1",
    }
    unmapped_document = {**document, "doc_id": "doc_unmapped", "document_id": "doc_unmapped"}
    character_document = {
        **document,
        "doc_id": "doc_character",
        "document_id": "doc_character",
        "doc_kind": "character",
        "title": "Tony Stark",
        "pageid": 456,
    }
    empty_universe_document = {
        **document,
        "doc_id": "doc_universe",
        "document_id": "doc_universe",
        "doc_kind": "universe",
        "pageid": 789,
    }
    chunk = {
        "chunk_id": "doc_mapped-plot-p01",
        "doc_id": "doc_mapped",
        "work_id": "cnt_mapped",
        "section": "plot",
        "position": 1,
        "total": 1,
        "text": "Tony Stark builds a suit.",
        "header": "[Iron Man · plot]",
        "entities": ["Tony Stark"],
        "flags": [],
        "source_plot_ids": ["plot-1"],
    }
    unmapped_chunk = {
        **chunk,
        "chunk_id": "doc_unmapped-plot-p01",
        "doc_id": "doc_unmapped",
        "work_id": "cnt_unmapped",
    }
    character_chunk = {
        **chunk,
        "chunk_id": "doc_character-intro-p01",
        "doc_id": "doc_character",
        "work_id": None,
        "section": "intro",
        "text": "Tony Stark is a genius inventor.",
        "header": "[Tony Stark · intro]",
    }
    enriched = {
        **chunk,
        "cleaned_text": "Tony Stark creates an armored suit.",
        "source_chunk_ids": [chunk["chunk_id"]],
        "source_document_ids": [document["doc_id"]],
        "source_revisions": [1],
        "character_mentions": [{"name_en": "Tony Stark"}],
        "processing": {"model": "test-model"},
        "summary_original": "Tony creates a suit.",
    }
    alias = {
        "alias_ko": "아이언맨",
        "canonical_en": "Iron Man",
        "kind": "work",
        "target_id": "tt0371746",
    }
    character = {
        "char_id": "chr_1",
        "character_id": "chr_1",
        "name": "Tony Stark",
        "document_id": "doc_character",
        "source_url": "https://example.org/tony",
    }
    inventory = {"tconst": "tt0371746", "year": "2008"}
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "handoff/manifest.json",
            json.dumps({"schema_version": "team-v1"}),
        )
        archive.writestr("handoff/works.jsonl", _jsonl(mapped_work, unmapped_work))
        archive.writestr(
            "handoff/documents.jsonl",
            _jsonl(document, unmapped_document, character_document, empty_universe_document),
        )
        archive.writestr("handoff/chunks.jsonl", _jsonl(chunk, unmapped_chunk, character_chunk))
        archive.writestr("handoff/enriched/enriched_chunks.jsonl", _jsonl(enriched))
        archive.writestr("handoff/aliases.jsonl", _jsonl(alias))
        archive.writestr("handoff/characters.jsonl", _jsonl(character))
        archive.writestr("handoff/imdb_inventory.jsonl", _jsonl(inventory))
    return path


def test_adapter_converts_ids_and_preserves_source_ids(tmp_path: Path) -> None:
    bundle = adapt_handoff(_fixture_zip(tmp_path))
    assert bundle.report.adapted_counts == {
        "works": 1,
        "documents": 3,
        "chunks": 2,
        "enriched_chunks": 1,
        "aliases": 1,
        "characters": 1,
    }
    assert bundle.report.skipped["work_without_imdb_id"] == 1
    assert bundle.report.skipped["chunk_work_without_imdb_id"] == 1
    assert bundle.report.skipped["wp_document_without_chunks"] == 1

    work = bundle.works[0]
    assert work.work_id == "tt0371746"
    assert work.source_ids["handoff_work_id"] == "cnt_mapped"
    assert work.year == 2008

    document = bundle.documents[0]
    assert document.doc_id == "tt0371746-wikipedia"
    assert document.source_ids["handoff_doc_id"] == "doc_mapped"

    chunk = bundle.chunks[0]
    assert chunk.chunk_id == "tt0371746-wikipedia-plot-p01"
    assert chunk.content_hash == content_hash(chunk.chunk_id, chunk.header, chunk.text)
    assert bundle.enriched_chunks[0].chunk_id == chunk.chunk_id
    assert bundle.enriched_chunks[0].text == "Tony Stark creates an armored suit."
    assert bundle.report.base_index_version == index_version(bundle.chunks)

    # Character pages have no work, but their chunks must still be indexed (#14).
    character = next(item for item in bundle.chunks if item.doc_id.startswith("wp-"))
    assert character.chunk_id == "wp-456-intro-p01"
    assert character.work_id is None
    assert character.title_original == "Tony Stark"


def test_adapter_rejects_unknown_schema(tmp_path: Path) -> None:
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("handoff/manifest.json", json.dumps({"schema_version": "future"}))
    try:
        adapt_handoff(path)
    except ValueError as exc:
        assert "schema_version" in str(exc)
    else:
        raise AssertionError("unknown schema must be rejected")
