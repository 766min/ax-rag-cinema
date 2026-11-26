"""Index the 38 canonical MCU film documents with an OpenAI embedding model.

This is a personal-branch experiment utility. It creates a separate PGVector
collection and never touches the team's configured collection unless the user
explicitly passes that collection name.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_postgres import PGVector
from langchain_postgres.vectorstores import DistanceStrategy

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "data" / "cineverse-dataset-schema"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def slug_words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9]+", text.lower())


def content_hash(chunk_id: str, header: str, text: str) -> str:
    raw = f"{chunk_id}\n{header}\n{text}".encode()
    return hashlib.sha256(raw).hexdigest()


def normalized_section_name(section: dict) -> str:
    """Map source section labels to the team's stable retrieval labels."""
    raw = " ".join(
        str(section.get(key) or "")
        for key in ("team_section", "section_kind", "section_path")
    ).lower()
    if "synopsis" in raw:
        return "synopsis"
    if "plot" in raw:
        return "plot"
    if "lead" in raw or "intro" in raw:
        return "intro"
    if "biograph" in raw or "early life" in raw or "fictional character" in raw:
        return "biography"
    if "feature" in raw or "object" in raw or "lore" in raw:
        return "lore"
    return section.get("team_section") or section.get("section_kind") or "other"


def split_text(text: str, max_chars: int = 900, overlap_chars: int = 0) -> list[str]:
    """Split paragraphs without losing sentence order."""
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", text) if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) <= max_chars and len(current) + len(paragraph) + 1 <= max_chars:
            current = f"{current}\n{paragraph}".strip()
            continue
        if current:
            chunks.append(current)
        if len(paragraph) <= max_chars:
            current = paragraph
            continue
        sentences = re.split(r"(?<=[.!?])\s+", paragraph)
        current = ""
        for sentence in sentences:
            if not sentence:
                continue
            if current and len(current) + len(sentence) + 1 > max_chars:
                chunks.append(current)
                current = sentence
            else:
                current = f"{current} {sentence}".strip()
        if current:
            chunks.append(current)
            current = ""
    if current:
        chunks.append(current)
    if overlap_chars <= 0:
        return chunks
    overlapped: list[str] = []
    for index, chunk in enumerate(chunks):
        if index == 0:
            overlapped.append(chunk)
            continue
        prefix = chunks[index - 1][-overlap_chars:]
        overlapped.append(f"{prefix}\n{chunk}".strip())
    return overlapped


def build_documents(max_chars: int, overlap_chars: int) -> list[Document]:
    works = {row["work_id"]: row for row in read_jsonl(DATASET / "works.jsonl")}
    documents = {
        row["doc_id"]: row
        for row in read_jsonl(DATASET / "documents.jsonl")
        if row.get("doc_type") == "film" and row.get("work_id") in works and works[row["work_id"]].get("is_canon") is True
    }
    return build_documents_from_scope(documents, max_chars, overlap_chars, include_all_sections=False)


def build_documents_from_scope(
    documents: dict[str, dict], max_chars: int, overlap_chars: int, include_all_sections: bool
) -> list[Document]:
    works = {row["work_id"]: row for row in read_jsonl(DATASET / "works.jsonl")}
    sections = read_jsonl(DATASET / "sections.jsonl")
    grouped: dict[str, list[dict]] = {}
    for section in sections:
        # The team movie index evaluates synopsis/plot only. Character,
        # reception, cast and other article sections belong to separate
        # content scopes and would make this embedding comparison unfair.
        if section["doc_id"] in documents and section.get("text", "").strip() and (
            include_all_sections or section.get("team_section") in {"synopsis", "plot"}
        ):
            grouped.setdefault(section["doc_id"], []).append(section)

    output: list[Document] = []
    for doc_id, source in documents.items():
        work = works.get(source.get("work_id"), {})
        title = work.get("title_original") or doc_id
        sections_by_name: dict[str, list[dict]] = {}
        for section in sorted(grouped.get(doc_id, []), key=lambda item: item["section_index"]):
            section_name = normalized_section_name(section)
            sections_by_name.setdefault(section_name, []).append(section)
        for section_name, section_rows in sections_by_name.items():
            combined_text = "\n\n".join(row["text"] for row in section_rows)
            parts = split_text(combined_text, max_chars=max_chars, overlap_chars=overlap_chars)
            for position, text in enumerate(parts, 1):
                chunk_id = f"{doc_id}-{section_name}-p{position:02d}"
                header = f"[{title} · {work.get('year') or ''} · {section_name}]"
                metadata = {
                    "chunk_id": chunk_id,
                    "doc_id": doc_id,
                    "work_id": source.get("work_id"),
                    "title_original": title,
                    "title_ko": work.get("title_ko"),
                    "section": section_name,
                    "position": position,
                    "total": len(parts),
                    "source_url": source.get("source_url"),
                    "revid": source.get("revid"),
                    "content_hash": content_hash(chunk_id, header, text),
                    "embedding_model": os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
                }
                output.append(Document(page_content=f"{header}\n{text}", metadata=metadata))
    return output


def build_gold_documents(max_chars: int, overlap_chars: int) -> list[Document]:
    documents = {
        row["doc_id"]: row
        for row in read_jsonl(DATASET / "documents.jsonl")
        if (row.get("source_ids") or {}).get("team_document_id")
    }
    references = "\n".join(row.get("근거 문서·절·리비전", "") for row in read_jsonl(ROOT / "question_set.jsonl"))
    referenced_ids = set(re.findall(r"doc_[0-9a-f]+", references))
    selected = {
        key: value
        for key, value in documents.items()
        if (value.get("source_ids") or {}).get("team_document_id") in referenced_ids
    }
    return build_documents_from_scope(selected, max_chars, overlap_chars, include_all_sections=True)


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--collection", default="mcu_movies_openai_small_v1")
    parser.add_argument("--model", default=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"))
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/rag_db"))
    parser.add_argument("--max-chars", type=int, default=900)
    parser.add_argument("--overlap-chars", type=int, default=0)
    parser.add_argument("--scope", choices=("movies", "gold"), default="movies")
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "openai-team-movie-chunks.jsonl")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    documents = (
        build_documents(args.max_chars, args.overlap_chars)
        if args.scope == "movies"
        else build_gold_documents(args.max_chars, args.overlap_chars)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "\n".join(json.dumps({"text": doc.page_content, **doc.metadata}, ensure_ascii=False) for doc in documents) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"chunks": len(documents), "collection": args.collection, "model": args.model, "output": str(args.output)}, ensure_ascii=False))
    if args.dry_run:
        return
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY가 없습니다. .env에 넣고 다시 실행하세요.")

    embeddings = OpenAIEmbeddings(model=args.model, api_key=os.environ["OPENAI_API_KEY"])
    store = PGVector(
        embeddings=embeddings,
        collection_name=args.collection,
        connection=args.database_url,
        distance_strategy=DistanceStrategy.COSINE,
        use_jsonb=True,
    )
    # langchain-postgres stores custom IDs in a table with a global uniqueness
    # constraint. Prefix storage IDs per experiment; preserve the team chunk_id
    # in metadata for gold matching and citations.
    storage_ids = [f"{args.collection}::{doc.metadata['chunk_id']}" for doc in documents]
    store.add_documents(documents, ids=storage_ids)
    print(json.dumps({"indexed": len(documents), "collection": args.collection, "model": args.model}, ensure_ascii=False))


if __name__ == "__main__":
    main()
