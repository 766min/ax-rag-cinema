"""handoff 데이터(data/local/cineverse_handoff) → 팀 스키마(Work · Document · Chunk) 변환과 청킹.

ID 규칙은 models.py(check_doc_id)를 따른다.
- 작품 문서: {work_id}-wikipedia, 작품이 없는 인물 · 세계관 문서: wp-{pageid}
- 원본 ID(cnt_… · doc_…)와 원본 절 번호는 source_ids 에 남긴다 (골든셋 근거가 원본 ID 로 적혀 있음)
"""

import hashlib
import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Literal

from langchain_text_splitters import RecursiveCharacterTextSplitter

from shared.core.models import Chunk, Document, SourceId, Work
from shared.core.schemas import Section

PROJECT_ROOT = Path(__file__).resolve().parents[2]
HANDOFF_DIR = PROJECT_ROOT / "data" / "local" / "cineverse_handoff"


@dataclass(frozen=True)
class ChunkRecipe:
    """청킹 방식 하나. 바꾸면 청크 번호가 밀리므로 version 도 달라진다 (정답 키 gold_index_version 과 맞춤).

    어떤 방식이든 절(section) 하나씩 따로 자르므로 절 · 문서(작품) 경계를 넘지 않는다.
    """

    recipe_id: str
    method: Literal["paragraph", "rcts"]
    size: int = 0  # rcts: chunk_size (글자 수, length_function=len)
    overlap: int = 0  # rcts: chunk_overlap
    merge_min: int = 150  # paragraph: 이보다 짧은 문단은 다음 문단과 합친다
    min_chars: int = 10  # 공통: 이보다 짧은 최종 조각은 버린다 (표 잔해)
    separators: tuple[str, ...] = ("\n", ". ", " ", "")  # rcts: 문단 → 문장 → 단어 → 글자
    keep_separator: Literal["start", "end"] = "end"

    @property
    def version(self) -> str:
        if self.method == "paragraph":
            return f"handoff-{self.recipe_id}-para-m{self.merge_min}-min{self.min_chars}"
        return f"handoff-{self.recipe_id}-rcts{self.size}-o{self.overlap}-min{self.min_chars}"

    def split(self, text: str) -> tuple[list[str], list[str]]:
        """(남긴 조각, min_chars 미만이라 버린 조각)."""
        if self.method == "paragraph":
            raw = _paragraphs(text, self.merge_min)
        else:
            raw = [piece.strip() for piece in _rcts(self).split_text(text)]
        kept = [p for p in raw if len(p) >= self.min_chars]
        dropped = [p for p in raw if p and len(p) < self.min_chars]
        return kept, dropped


RECIPES: dict[str, ChunkRecipe] = {
    r.recipe_id: r
    for r in (
        ChunkRecipe("CK01", "paragraph"),
        ChunkRecipe("CK02", "rcts", size=500, overlap=100),
        ChunkRecipe("CK03", "rcts", size=1000, overlap=100),
        ChunkRecipe("CK04", "rcts", size=1500, overlap=100),
    )
}
DEFAULT_RECIPE = RECIPES["CK03"]


@cache
def _rcts(recipe: ChunkRecipe) -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=recipe.size,
        chunk_overlap=recipe.overlap,
        separators=list(recipe.separators),
        keep_separator=recipe.keep_separator,
    )


def _paragraphs(text: str, merge_min: int) -> list[str]:
    """줄 단위 문단으로 나누고, merge_min 보다 짧은 문단은 다음 문단과 합친다 (길이 상한 없음).

    마지막 문단이 짧으면 앞 문단에 붙인다. 빈 줄은 버린다.
    """
    pieces: list[str] = []
    buffer = ""
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        buffer = f"{buffer}\n{line}" if buffer else line
        if len(buffer) >= merge_min:
            pieces.append(buffer)
            buffer = ""
    if buffer:
        if pieces:
            pieces[-1] = f"{pieces[-1]}\n{buffer}"
        else:
            pieces.append(buffer)
    return pieces


def split_text(text: str, recipe: ChunkRecipe = DEFAULT_RECIPE) -> list[str]:
    """절 본문을 레시피대로 나눈다 (짧은 조각은 버린 뒤)."""
    return recipe.split(text)[0]


def dataset_fingerprint(handoff_dir: Path = HANDOFF_DIR) -> dict[str, str]:
    """통합 데이터셋 식별 정보: manifest 의 이름 · 스키마 · 생성 시각 + 파일 전체의 SHA-256.

    원본에 버전명이 없어서 임의 버전명을 붙이지 않고 이 값으로 식별한다. 숨김 파일(.DS_Store 등)과
    ZIP 원본은 계산에서 뺀다 (같은 데이터인데 값이 달라지지 않게).
    """
    manifest = json.loads((handoff_dir / "manifest.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256()
    files = sorted(
        p
        for p in handoff_dir.rglob("*")
        if p.is_file() and not p.name.startswith(".") and p.suffix != ".zip"
    )
    for path in files:
        digest.update(path.relative_to(handoff_dir).as_posix().encode())
        digest.update(path.read_bytes())
    return {
        "dataset": str(manifest.get("dataset")),
        "schema_version": str(manifest.get("schema_version")),
        "generated_at": str(manifest.get("generated_at")),
        "sha256": digest.hexdigest(),
        "files": str(len(files)),
    }


def index_version(recipe: ChunkRecipe, fingerprint: dict[str, str]) -> str:
    """색인 버전 = 청킹 레시피 + 데이터셋 체크섬 앞 8자. 둘 중 하나만 바뀌어도 정답 키를 다시 본다."""
    return f"{recipe.version}@{fingerprint['sha256'][:8]}"


def collection_for(base: str, recipe: ChunkRecipe) -> str:
    """레시피마다 따로 쓰는 PGVector 컬렉션 이름 (예: mcu_movies_ck03). 기본 컬렉션은 건드리지 않는다."""
    return f"{base}_{recipe.recipe_id.lower()}"


# 문서 종류별로 자를 절(section_kind) → 청크 종류
SECTION_RULES: dict[str, dict[str, Section]] = {
    "film": {"lead": Section.SYNOPSIS, "plot": Section.PLOT},
    "character": {"lead": Section.INTRO, "biography": Section.INTRO},
    # shortcut: 세계관 문서에 맞는 Section 값이 없어 intro 로 둔다. 팀에서 값을 정하면 바꾼다
    "universe": {"lead": Section.INTRO, "other": Section.INTRO, "biography": Section.INTRO},
}

# 한 문서를 여러 tt 작품이 공유할 때 문서를 붙일 대표 작품의 우선순위 (낮을수록 먼저)
_OWNER_PRIORITY = {"movie": 0, "tv_series": 1, "short": 2, "special": 3}
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def content_hash(chunk_id: str, header: str, text: str) -> str:
    """chunk_id · 머리말 · 본문으로 만든 SHA-256 (§4.2 증분 적재 기준)."""
    return hashlib.sha256(f"{chunk_id}\n{header}\n{text}".encode()).hexdigest()


def _is_tt(work_id: str) -> bool:
    return work_id.startswith("tt")


def to_work(raw: dict[str, Any]) -> Work:
    """handoff 작품 한 줄 → Work. 값 모양을 팀 모델에 맞춘다."""
    year = _YEAR.search(str(raw.get("release_date") or ""))
    season = raw.get("season")
    phase = raw.get("phase")
    return Work(
        work_id=raw["work_id"],
        title_ko=raw["title_ko"],
        title_original=raw["title_original"],
        format=raw.get("format") or raw.get("content_type") or "unknown",
        year=int(year.group()) if year else None,
        season=int(season) if season else None,
        parent_work_id=None,  # shortcut: 시즌·에피소드는 1차 범위 밖이라 비운다
        phase=str(phase) if phase is not None else None,
        timeline_order=raw.get("timeline_order"),
        is_canon=None,  # 원본이 모두 "unknown"
        source_ids={k: (str(v) if v is not None else None) for k, v in raw["source_ids"].items()},
    )


def owner_works(works_raw: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """원본 문서 ID(doc_…) → 그 문서를 대표하는 tt 작품.

    shortcut: 한 문서를 여러 작품이 공유하면(시리즈와 에피소드 등) 우선순위가 가장 높은 작품 하나에만
    붙인다. 에피소드별 문서가 필요해지면 작품마다 문서를 나누도록 바꾼다.
    """
    best: dict[str, dict[str, Any]] = {}
    for w in works_raw:
        if not _is_tt(w["work_id"]):
            continue
        doc = w["source_ids"].get("wikipedia_document_id")
        if not doc:
            continue
        rank = _OWNER_PRIORITY.get(w.get("content_type") or "", 9)
        current = best.get(doc)
        if current is None or rank < _OWNER_PRIORITY.get(current.get("content_type") or "", 9):
            best[doc] = w
    return best


def _doc_kind(raw_doc: dict[str, Any], owners: dict[str, dict[str, Any]]) -> str | None:
    """이 문서를 어떤 규칙으로 자를지. 1차 범위 밖이면 None."""
    if raw_doc["doc_id"] in owners:
        return None if raw_doc["title"].startswith("List of") else "film"
    if raw_doc["doc_type"] in ("character", "universe"):
        return raw_doc["doc_type"]
    return None


def new_doc_id(raw_doc: dict[str, Any], owner: dict[str, Any] | None) -> str:
    return f"{owner['work_id']}-wikipedia" if owner else f"wp-{raw_doc['pageid']}"


def to_document(raw_doc: dict[str, Any], owner: dict[str, Any] | None) -> Document:
    return Document(
        doc_id=new_doc_id(raw_doc, owner),
        work_id=owner["work_id"] if owner else None,
        source_id=SourceId.WIKIPEDIA_EN,
        source_url=raw_doc["source_url"],
        license=raw_doc.get("license"),
        revid=raw_doc.get("revid"),
        fetched_at=raw_doc.get("fetched_at"),
        doc_type="film" if owner else raw_doc["doc_type"],
        source_ids={
            "team_document_id": raw_doc["doc_id"],
            "pageid": str(raw_doc["pageid"]),
            "wikidata_qid": raw_doc.get("wikidata_qid"),
        },
    )


def _doc_chunks(
    raw_doc: dict[str, Any], kind: str, owner: dict[str, Any] | None, recipe: ChunkRecipe
) -> Iterator[Chunk]:
    rules = SECTION_RULES[kind]
    doc_id = new_doc_id(raw_doc, owner)
    pieces: list[tuple[Section, dict[str, Any], str]] = []
    for section in raw_doc["sections"]:
        chunk_section = rules.get(section["section_kind"])
        if chunk_section is None:
            continue
        for text in split_text(section["text"], recipe):
            pieces.append((chunk_section, section, text))

    for position, (chunk_section, section, text) in enumerate(pieces, start=1):
        chunk_id = f"{doc_id}-{chunk_section}-p{position:02d}"
        path = " > ".join(section["section_path"])
        header = f"{raw_doc['title']} > {path}"
        yield Chunk(
            chunk_id=chunk_id,
            doc_id=doc_id,
            work_id=owner["work_id"] if owner else None,
            source_id=SourceId.WIKIPEDIA_EN,
            section=chunk_section,
            position=position,
            total=len(pieces),
            text=text,
            header=header,
            title_ko=owner.get("title_ko") if owner else None,
            title_original=owner.get("title_original") if owner else raw_doc["title"],
            source_url=raw_doc["source_url"],
            revid=raw_doc.get("revid"),
            content_hash=content_hash(chunk_id, header, text),
            source_ids={
                "team_document_id": raw_doc["doc_id"],
                "section_index": str(section["section_index"]),
            },
        )


def build(
    works_raw: list[dict[str, Any]],
    docs_raw: list[dict[str, Any]],
    recipe: ChunkRecipe = DEFAULT_RECIPE,
) -> tuple[list[Work], list[Document], list[Chunk]]:
    """원본 작품 · 문서 → 1차 범위의 Work · Document · Chunk."""
    owners = owner_works(works_raw)
    works = [to_work(w) for w in works_raw if _is_tt(w["work_id"])]
    documents: list[Document] = []
    chunks: list[Chunk] = []
    for raw_doc in docs_raw:
        kind = _doc_kind(raw_doc, owners)
        if kind is None:
            continue
        owner = owners.get(raw_doc["doc_id"])
        doc_chunks = list(_doc_chunks(raw_doc, kind, owner, recipe))
        if not doc_chunks:
            continue
        documents.append(to_document(raw_doc, owner))
        chunks.extend(doc_chunks)
    return works, documents, chunks


def load_handoff(
    handoff_dir: Path = HANDOFF_DIR, recipe: ChunkRecipe = DEFAULT_RECIPE
) -> tuple[list[Work], list[Document], list[Chunk]]:
    """handoff 폴더를 읽어 변환한다. 원본 파일은 고치지 않는다."""
    return build(
        read_jsonl(handoff_dir / "works.jsonl"),
        read_jsonl(handoff_dir / "documents.jsonl"),
        recipe,
    )


def find_chunk_ids(chunks: Iterable[Chunk], team_document_id: str, section_index: int) -> list[str]:
    """원본 문서 ID(doc_…)와 원본 절 번호로 청크 ID 를 찾는다 (골든셋 근거 → gold_chunk_ids)."""
    return [
        c.chunk_id
        for c in chunks
        if c.source_ids.get("team_document_id") == team_document_id
        and c.source_ids.get("section_index") == str(section_index)
    ]
