"""팀 공통 데이터셋(data/cineverse-dataset-schema, 2026-11-25) → 색인 범위의 Document · Chunk.

works · documents 는 팀 모델(Work · Document)로 그대로 읽는다. 본문은 sections.jsonl 에 있다.

색인 범위 (V1 고정, 문서 156개):
- 작품 문서 64개 — architecture.md §3 본문 색인 규칙 (is_canon · status=released · collect_wave="1",
  시리즈 전체 행 제외 71편 중 문서가 있는 것). 절은 데이터셋의 team_section(synopsis · plot) 그대로
- MCU 인물 문서 63개 (scope_status=mcu) — lead → intro, biography → biography
- 세계관 문서 29개 — lead · other · biography → lore
인물 · 세계관 범위는 팀에서 아직 정하지 않아 V1 에서 임시로 고정한 값이다 (PR #16 은 Section 값만 더했다).
작품 카드 · 연결 카드는 만드는 코드가 없어 넣지 않는다.

평가셋 근거가 원래 문서 ID(doc_…)로 적혀 있어서, 청크의 source_ids 에 team_document_id 와 원래
절 번호를 남긴다 (evals.items 가 이 값으로 정답 청크를 찾는다).
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from etl.chunking import ChunkRecipe, split_text
from shared.core.models import Chunk, Document, Work
from shared.core.schemas import Section

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = PROJECT_ROOT / "data" / "cineverse-dataset-schema"
SCOPE_VERSION = "scope-v1-156docs"  # 색인 범위 규칙을 바꾸면 올린다 (결과 파일에 남김)

# 인물 · 세계관 문서: 원래 절 종류(section_kind) → 청크 종류
_CHARACTER = {"lead": Section.INTRO, "biography": Section.BIOGRAPHY}
_UNIVERSE = {"lead": Section.LORE, "other": Section.LORE, "biography": Section.LORE}
_WORK_SECTIONS = {Section.SYNOPSIS, Section.PLOT}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def content_hash(chunk_id: str, header: str, text: str) -> str:
    """chunk_id · 머리말 · 본문으로 만든 SHA-256 (§4.2 증분 적재 기준)."""
    return hashlib.sha256(f"{chunk_id}\n{header}\n{text}".encode()).hexdigest()


def dataset_fingerprint(dataset_dir: Path = DATASET_DIR) -> dict[str, str]:
    """SHA256SUMS.txt 의 파일을 모두 검사하고, 그 목록 파일의 SHA-256 을 데이터셋 체크섬으로 쓴다.

    한 파일이라도 다르거나 없으면 멈춘다 (바뀐 데이터로 실험하지 않게).
    """
    sums = dataset_dir / "SHA256SUMS.txt"
    lines = [
        line.split(maxsplit=1) for line in sums.read_text(encoding="utf-8").splitlines() if line
    ]
    bad = [
        name
        for expected, name in lines
        if not (dataset_dir / name).is_file()
        or hashlib.sha256((dataset_dir / name).read_bytes()).hexdigest() != expected
    ]
    if bad:
        raise ValueError(f"데이터셋 파일이 SHA256SUMS.txt 와 다릅니다: {bad}")
    return {
        "dataset": dataset_dir.name,
        "sha256": hashlib.sha256(sums.read_bytes()).hexdigest(),
        "sha256_of": "SHA256SUMS.txt",
        "files": str(len(lines)),
    }


def index_version(recipe: ChunkRecipe, fingerprint: dict[str, str]) -> str:
    """색인 버전 = 청킹 레시피 + 데이터셋 체크섬 앞 8자. 둘 중 하나만 바뀌어도 정답 키를 다시 본다."""
    return f"{recipe.version}@{fingerprint['sha256'][:8]}"


def collection_for(base: str, recipe: ChunkRecipe, fingerprint: dict[str, str]) -> str:
    """레시피 · 데이터셋마다 따로 쓰는 컬렉션 (예: mcu_movies_ck03_24ee6397).

    데이터셋을 이름에 넣어, 다른 데이터셋으로 다시 적재할 때 기존 컬렉션의 행을 지우지 않게 한다.
    """
    return f"{base}_{recipe.recipe_id.lower()}_{fingerprint['sha256'][:8]}"


@dataclass
class Dataset:
    works: dict[str, Work]
    documents: dict[str, Document]
    titles: dict[str, str]  # doc_id → 문서 제목 (documents_meta)
    scope: list[tuple[Document, dict[str, Any], Section]]  # 색인할 (문서, 절, 청크 종류), 파일 순서
    # (원래 doc_…, 절 번호) → 절 원문. 범위 밖 절도 넣는다 (근거가 범위 밖이면 missing 으로 보이게)
    sections: dict[tuple[str, str], str]


def _chunk_section(
    doc: Document, row: dict[str, Any], indexed_works: set[str], meta: dict[str, Any]
) -> Section | None:
    if doc.work_id is not None:
        if doc.work_id not in indexed_works:
            return None
        team = row["team_section"]
        return Section(team) if team in _WORK_SECTIONS else None
    if doc.doc_type == "character":
        return _CHARACTER.get(row["section_kind"]) if meta["scope_status"] == "mcu" else None
    if doc.doc_type == "universe":
        return _UNIVERSE.get(row["section_kind"])
    return None


def indexed_work_ids(works: dict[str, Work], works_meta: dict[str, dict[str, Any]]) -> set[str]:
    """architecture.md §3 본문 색인 규칙의 작품 (시리즈 전체 행 제외, 71편)."""
    return {
        work_id
        for work_id, work in works.items()
        if work.is_canon
        and works_meta[work_id]["status"] == "released"
        and works_meta[work_id]["collect_wave"] == "1"
        and works_meta[work_id]["row_kind"] != "series_parent"
    }


def load_dataset(dataset_dir: Path = DATASET_DIR) -> Dataset:
    works = {w["work_id"]: Work.model_validate(w) for w in read_jsonl(dataset_dir / "works.jsonl")}
    documents = {
        d["doc_id"]: Document.model_validate(d) for d in read_jsonl(dataset_dir / "documents.jsonl")
    }
    works_meta = {w["work_id"]: w for w in read_jsonl(dataset_dir / "extra" / "works_meta.jsonl")}
    docs_meta = {d["doc_id"]: d for d in read_jsonl(dataset_dir / "extra" / "documents_meta.jsonl")}
    indexed = indexed_work_ids(works, works_meta)

    scope: list[tuple[Document, dict[str, Any], Section]] = []
    all_sections: dict[tuple[str, str], str] = {}
    for row in read_jsonl(dataset_dir / "sections.jsonl"):
        doc = documents[row["doc_id"]]
        team_id = doc.source_ids.get("team_document_id") or ""
        all_sections[(team_id, str(row["section_index"]))] = row["text"]
        section = _chunk_section(doc, row, indexed, docs_meta[doc.doc_id])
        if section is not None:
            scope.append((doc, row, section))

    return Dataset(
        works=works,
        documents=documents,
        titles={doc_id: m["title"] for doc_id, m in docs_meta.items()},
        scope=scope,
        sections=all_sections,
    )


def scope_documents(data: Dataset) -> list[Document]:
    seen: dict[str, Document] = {}
    for doc, _, _ in data.scope:
        seen.setdefault(doc.doc_id, doc)
    return list(seen.values())


def build_chunks(data: Dataset, recipe: ChunkRecipe) -> list[Chunk]:
    """범위의 절을 레시피대로 잘라 팀 Chunk 로. position 은 문서 안 순번 (models.Chunk 설명)."""
    by_doc: dict[str, list[tuple[dict[str, Any], Section, str]]] = {}
    for doc, row, section in data.scope:
        for text in split_text(row["text"], recipe, str(section)):
            by_doc.setdefault(doc.doc_id, []).append((row, section, text))

    chunks: list[Chunk] = []
    for doc_id, pieces in by_doc.items():
        doc = data.documents[doc_id]
        work = data.works.get(doc.work_id) if doc.work_id else None
        title = data.titles[doc_id]
        for position, (row, section, text) in enumerate(pieces, start=1):
            chunk_id = f"{doc_id}-{section}-p{position:02d}"
            header = f"{title} > {' > '.join(row['section_path'])}"
            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    doc_id=doc_id,
                    work_id=doc.work_id,
                    source_id=doc.source_id,
                    section=section,
                    position=position,
                    total=len(pieces),
                    text=text,
                    header=header,
                    title_ko=work.title_ko if work else None,
                    title_original=work.title_original if work else title,
                    source_url=doc.source_url,
                    revid=doc.revid,
                    content_hash=content_hash(chunk_id, header, text),
                    source_ids={
                        "team_document_id": doc.source_ids.get("team_document_id"),
                        "section_index": str(row["section_index"]),
                    },
                )
            )
    return chunks
