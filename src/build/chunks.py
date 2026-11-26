"""공통 데이터셋(cineverse-dataset-schema)의 절 → 팀 Chunk. 원문 글자 위치를 함께 남긴다.

청크의 text 는 항상 부모 원문(절 본문 또는 에피소드 요약)의 `[char_start:char_end]` 그대로다.
위치만 계산하다 마지막에 한 번 잘라, 청크가 원문과 어긋나지 않게 한다.
"""

import hashlib
import json
import os
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shared.core.models import Chunk, Document, Work
from shared.core.schemas import Section

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def dataset_dir(path: str | Path | None = None) -> Path:
    """인자 > CINEVERSE_DATASET_DIR > 저장소 루트 data/cineverse-dataset-schema"""
    configured = path or os.getenv("CINEVERSE_DATASET_DIR")
    return Path(configured) if configured else PROJECT_ROOT / "data" / "cineverse-dataset-schema"


# ---------- 레시피 ----------


@dataclass(frozen=True)
class Recipe:
    """청킹 설정. 이름이 색인(컬렉션 · DB)을 가른다"""

    name: str
    max_chars: int = 800  # 겹친 문장은 빼고 센다
    overlap: dict[str, int] = field(default_factory=dict)  # 절 종류별 겹침 문장 수
    default_overlap: int = 0
    header: bool = True

    def overlap_for(self, section: Section) -> int:
        return self.overlap.get(str(section), self.default_overlap)

    def params(self) -> dict[str, Any]:
        return {
            "recipe": self.name,
            "max_chars": self.max_chars,
            "overlap": dict(self.overlap),
            "default_overlap": self.default_overlap,
            "header": self.header,
        }


_CK09 = {"synopsis": 0, "intro": 0, "plot": 2, "biography": 1, "lore": 1}
RECIPES: dict[str, Recipe] = {
    "s800": Recipe("s800"),  # 팀 합의 전 임시 기준선
    "s800-nohdr": Recipe("s800-nohdr", header=False),
    "s800-o1": Recipe("s800-o1", default_overlap=1),
    "ck05": Recipe("ck05", max_chars=1000, default_overlap=1),
    "ck09": Recipe("ck09", max_chars=1000, overlap=_CK09),
}


# ---------- 문장 · 문단 위치 ----------

# 마침표가 문장 끝이 아닌 약어
_ABBREVIATIONS = frozenset(
    {
        "mr", "mrs", "ms", "dr", "st", "jr", "sr", "vs", "etc", "no", "mt", "lt", "gen", "col",
        "sgt", "capt", "prof", "inc", "co", "ltd", "rev", "fr", "sen", "rep", "gov", "pres",
        "vol", "pt", "ep", "ch", "fig", "approx", "dept", "est", "bros", "corp",
        "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    }
)  # fmt: skip
# 문장 끝 후보: . ! ? (+ 닫는 따옴표 · 괄호) + 공백, 다음이 대문자 · 숫자 · 여는 따옴표
_BOUNDARY = re.compile(r"([.!?])([\"”’')\]]*)(\s+)(?=[\"“‘(\[]?[A-Z0-9])")
_INITIALS = re.compile(r"(?:[A-Za-z]\.)+")  # S.H.I.E.L.D. · U.S.
_OPENERS = "\"'(“‘["
FLAT_TABLE = re.compile(r"^\+ ")  # 평탄화된 표 줄
TINY_CHARS = 30


@dataclass(frozen=True)
class Unit:
    """원문 안의 구간 [start, end). whole=False 는 긴 문장을 글자 수로 강제로 자른 조각"""

    start: int
    end: int
    whole: bool = True


def sentence_spans(text: str, start: int = 0, end: int | None = None) -> list[Unit]:
    """text[start:end] 를 영어 문장 구간으로. 약어(Dr.) · 이니셜(S.H.I.E.L.D.) 뒤에서는 자르지 않는다"""
    end = len(text) if end is None else end
    out: list[Unit] = []
    cur = start
    for m in _BOUNDARY.finditer(text, start, end):
        if m.group(1) == ".":
            before = text[cur : m.start(1) + 1].split()
            token = before[-1].lstrip(_OPENERS) if before else ""
            if token[:-1].lower() in _ABBREVIATIONS or _INITIALS.fullmatch(token):
                continue
        out.append(Unit(cur, m.end(2)))
        cur = m.end()
    if cur < end and text[cur:end].strip():
        out.append(Unit(cur, end))
    return [_trim(text, u) for u in out]


def _trim(text: str, unit: Unit) -> Unit:
    s, e = unit.start, unit.end
    while s < e and text[s].isspace():
        s += 1
    while e > s and text[e - 1].isspace():
        e -= 1
    return Unit(s, e, unit.whole)


def _hard_split(text: str, unit: Unit, max_chars: int) -> list[Unit]:
    """max_chars 보다 긴 문장을 '; ' · ', ' · 공백에서 나눈다. 잘린 조각은 겹침 재료로 쓰지 않는다"""
    out: list[Unit] = []
    s = unit.start
    while unit.end - s > max_chars:
        piece = text[s : s + max_chars]
        cut = -1
        for sep in ("; ", ", ", " "):
            i = piece.rfind(sep)
            if i > max_chars // 3:  # 너무 앞에서 자르면 조각이 지나치게 짧아진다
                cut = i + len(sep)
                break
        cut = cut if cut > 0 else max_chars
        out.append(_trim(text, Unit(s, s + cut, whole=False)))
        s += cut
    out.append(_trim(text, Unit(s, unit.end, whole=False)))
    return [u for u in out if u.end > u.start]


def paragraph_runs(text: str) -> list[list[Unit]]:
    """줄 = 문단. 표 줄은 빼고, 표 줄이 끼면 그 자리에서 묶음을 끊는다 (청크가 표를 건너 이어지지 않게)"""
    runs: list[list[Unit]] = [[]]
    pos = 0
    for line in text.split("\n"):
        start, pos = pos, pos + len(line) + 1
        if not line.strip():
            continue
        if FLAT_TABLE.match(line.strip()):
            runs.append([])
            continue
        runs[-1].append(_trim(text, Unit(start, start + len(line))))
    return [run for run in runs if run]


def chunk_spans(text: str, max_chars: int, overlap: int = 0) -> list[tuple[Unit, int]]:
    """원문을 (구간, 겹친 글자 수) 목록으로. 문단을 모으고, 긴 문단만 문장 경계에서 나눈다.

    구간의 길이(end - start)는 원문의 구분자를 포함해 max_chars 이하다 (한 문장이 넘으면 강제로 자른다).
    겹침은 앞 조각의 마지막 문장 n개를 앞에 붙인다. 앞 조각이 강제로 잘렸거나 겹칠 글이
    max_chars 의 절반을 넘으면 붙이지 않는다.
    """
    out: list[tuple[Unit, int]] = []
    for run in paragraph_runs(text):
        units: list[Unit] = []
        for para in run:
            if para.end - para.start <= max_chars:
                units.append(para)
                continue
            for sent in sentence_spans(text, para.start, para.end):
                if sent.end - sent.start <= max_chars:
                    units.append(sent)
                else:
                    units.extend(_hard_split(text, sent, max_chars))
        packed: list[list[Unit]] = []
        for unit in units:
            if packed and unit.end - packed[-1][0].start <= max_chars:
                packed[-1].append(unit)
            else:
                packed.append([unit])
        for i, group in enumerate(packed):
            start, end = group[0].start, group[-1].end
            added = 0
            if overlap and i > 0 and packed[i - 1][-1].whole and group[0].whole:
                prev = packed[i - 1]
                tail = sentence_spans(text, prev[0].start, prev[-1].end)[-overlap:]
                if tail and start - tail[0].start <= max_chars // 2:
                    added = start - tail[0].start
                    start = tail[0].start
            out.append((Unit(start, end), added))
    return out


# ---------- 데이터셋 ----------


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


@dataclass
class Dataset:
    works: list[Work]
    documents: dict[str, Document]
    works_meta: dict[str, dict[str, Any]]  # extra/works_meta.jsonl
    docs_meta: dict[str, dict[str, Any]]  # extra/documents_meta.jsonl
    sections: dict[str, list[dict[str, Any]]]  # doc_id → 절 목록

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Dataset":
        root = dataset_dir(path)
        if not (root / "works.jsonl").exists():
            raise FileNotFoundError(
                f"데이터셋이 없습니다: {root}. CINEVERSE_DATASET_DIR 또는 data/cineverse-dataset-schema 를 확인하세요"
            )
        sections: dict[str, list[dict[str, Any]]] = {}
        for row in load_jsonl(root / "sections.jsonl"):
            sections.setdefault(row["doc_id"], []).append(row)
        return cls(
            works=[Work.model_validate(r) for r in load_jsonl(root / "works.jsonl")],
            documents={
                r["doc_id"]: Document.model_validate(r)
                for r in load_jsonl(root / "documents.jsonl")
            },
            works_meta={r["work_id"]: r for r in load_jsonl(root / "extra" / "works_meta.jsonl")},
            docs_meta={r["doc_id"]: r for r in load_jsonl(root / "extra" / "documents_meta.jsonl")},
            sections=sections,
        )


def dataset_fingerprint(path: str | Path | None = None) -> str:
    """SHA256SUMS.txt 의 해시 앞 12자. 데이터 판을 실행 기록에 남긴다"""
    sums = dataset_dir(path) / "SHA256SUMS.txt"
    return hashlib.sha256(sums.read_bytes()).hexdigest()[:12] if sums.exists() else "unknown"


# ---------- 색인 범위 ----------

BIOGRAPHY_HEAD = re.compile(r"biography", re.IGNORECASE)
LORE_TITLES = frozenset(
    {
        "Features of the Marvel Cinematic Universe",
        "Marvel Cinematic Universe timeline",
        "Multiverse (Marvel Cinematic Universe)",
    }
)
LORE_SKIP_KINDS = frozenset({"creation", "reception", "release", "meta", "see_also"})
SCOPES = ("works", "works+characters+lore")
START_DATE = re.compile(r"\{\{\s*Start date\s*\|\s*(\d{4})", re.IGNORECASE)
POST_CREDIT = re.compile(r"\b(mid|post)-credits? scene", re.IGNORECASE)
MCU_SUFFIX = " (Marvel Cinematic Universe)"


def is_body_work(work: Work, meta: dict[str, Any]) -> bool:
    """architecture.md §3 색인 규칙: 정식 · 개봉 · 1차 수집"""
    return bool(work.is_canon and meta["status"] == "released" and meta["collect_wave"] == "1")


def select_works(ds: Dataset) -> dict[str, Work]:
    """{문서 ID: 작품} — 색인 규칙을 지키고 Wikipedia 문서가 있는 작품"""
    out: dict[str, Work] = {}
    for work in ds.works:
        meta = ds.works_meta[work.work_id]
        if is_body_work(work, meta) and meta["doc_id"]:
            out[meta["doc_id"]] = work
    return out


def work_header(
    title: str, year: int | None, kind: str, phase: str | None, order: int | None
) -> str:
    """검색용 머리말. 예: [Avengers: Endgame · 2019 film · Phase 3 · timeline #44]"""
    parts = [title, f"{year} {kind}" if year else kind]
    if phase:
        parts.append(f"Phase {phase}")
    if order:
        parts.append(f"timeline #{order}")
    return f"[{' · '.join(parts)}]"


def shown_year(work: Work, infobox: dict[str, Any] | None) -> int | None:
    """시즌 행의 year 는 시리즈 시작 연도라, 정보 상자의 방영 시작일을 먼저 본다"""
    if work.season is None and work.year is not None:
        return work.year
    params = (infobox or {}).get("params") or {}
    for key in ("first_aired", "released"):
        match = START_DATE.search((params.get(key) or {}).get("raw") or "")
        if match:
            return int(match.group(1))
    return work.year if work.season in (None, 1) else None


def display_name(title: str) -> str:
    return title.removesuffix(MCU_SUFFIX)


# ---------- 청크 만들기 ----------


@dataclass
class Block:
    """청크의 재료 하나. source 는 부모 원문(절 본문 또는 에피소드 요약)이다"""

    section_row: dict[str, Any]
    header: str
    source: str
    parent: str  # s{절} 또는 s{절}e{에피소드}
    flags: list[str] = field(default_factory=list)


def _section_blocks(row: dict[str, Any], header: str) -> list[Block]:
    return [Block(row, header, row["text"], f"s{row['section_index']}")] if row.get("text") else []


def _episode_blocks(row: dict[str, Any], header: str) -> list[Block]:
    """에피소드 요약마다 따로. 에피소드가 경계라 서로 합치지 않는다"""
    blocks: list[Block] = []
    for i, ep in enumerate(row.get("episode_entries") or [], start=1):
        summary = ep.get("summary") or ""
        if not summary.strip():
            continue
        number = ep.get("episode_number") or i
        name = f"Episode {number}: {ep['title']}" if ep.get("title") else f"Episode {number}"
        blocks.append(
            Block(
                row,
                f"{header[:-1]} · {name}]",
                summary,
                f"s{row['section_index']}e{i}",
                ["episode_summary"],
            )
        )
    return blocks


def content_hash(chunk_id: str, header: str, text: str) -> str:
    """architecture.md §4.2"""
    return hashlib.sha256("\n".join((chunk_id, header, text)).encode("utf-8")).hexdigest()


def version_of(pairs: Iterable[tuple[str, str]]) -> str:
    """(chunk_id, content_hash) 를 정렬해 다시 해시한 앞 16자. 순서와 상관없이 같은 값이 나온다"""
    lines = sorted(f"{cid}\t{h}" for cid, h in pairs)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:16]


def index_version(chunks: Iterable[Chunk]) -> str:
    return version_of((c.chunk_id, c.content_hash) for c in chunks)


def _entities(text: str, links: list[dict[str, Any]]) -> list[str]:
    found: list[str] = []
    for link in links:
        anchor, target = link.get("anchor_text") or "", link.get("target_title") or ""
        if anchor and target and anchor in text and target not in found:
            found.append(target)
    return found


def make_chunks(
    document: Document,
    section: Section,
    blocks: list[Block],
    recipe: Recipe,
    *,
    title_ko: str | None,
    title_original: str,
) -> list[Chunk]:
    """같은 종류의 재료를 청크로. 번호는 문서 안에서 그 종류끼리 1부터 센다"""
    overlap = recipe.overlap_for(section)
    pieces: list[tuple[Block, Unit, int]] = []
    for block in blocks:
        for unit, added in chunk_spans(block.source, recipe.max_chars, overlap):
            # 겹침을 뺀 길이로 걸러야 겹침 비교에서 청크 경계가 같다
            if unit.end - unit.start - added >= TINY_CHARS:
                pieces.append((block, unit, added))
    chunks: list[Chunk] = []
    for position, (block, unit, added) in enumerate(pieces, start=1):
        text = block.source[unit.start : unit.end]
        header = block.header if recipe.header else ""
        chunk_id = f"{document.doc_id}-{section}-p{position:02d}"
        row = block.section_row
        chunks.append(
            Chunk(
                chunk_id=chunk_id,
                doc_id=document.doc_id,
                work_id=document.work_id,
                source_id=document.source_id,
                section=section,
                position=position,
                total=len(pieces),
                text=text,
                header=header,
                title_ko=title_ko,
                title_original=title_original,
                source_url=document.source_url,
                revid=document.revid,
                entities=_entities(text, row.get("links") or []),
                flags=block.flags + (["post_credit"] if POST_CREDIT.search(text) else []),
                content_hash=content_hash(chunk_id, header, text),
                source_ids={
                    "team_document_id": document.source_ids.get("team_document_id"),
                    "team_section_index": str(row["section_index"]),
                    "team_section_path": " > ".join(row["section_path"]),
                    "parent_id": f"{document.doc_id}#{block.parent}",
                    "char_start": str(unit.start),
                    "char_end": str(unit.end),
                    "overlap_chars": str(added),
                    "fetched_at": document.fetched_at.isoformat() if document.fetched_at else None,
                    "license": document.license,
                },
            )
        )
    return chunks


def _work_chunks(ds: Dataset, document: Document, work: Work, recipe: Recipe) -> list[Chunk]:
    meta = ds.docs_meta[document.doc_id]
    header = work_header(
        work.title_original,
        shown_year(work, meta.get("infobox")),
        document.doc_type,
        work.phase,
        work.timeline_order,
    )
    rows = ds.sections.get(document.doc_id, [])
    synopsis = [
        b for r in rows if r["team_section"] == "synopsis" for b in _section_blocks(r, header)
    ]
    plot: list[Block] = []
    for row in rows:
        if row["team_section"] == "plot":
            plot += _section_blocks(row, header)
        plot += _episode_blocks(row, header)  # 시즌 문서의 에피소드 요약도 plot 으로
    names = {"title_ko": work.title_ko, "title_original": work.title_original}
    return make_chunks(document, Section.SYNOPSIS, synopsis, recipe, **names) + make_chunks(
        document, Section.PLOT, plot, recipe, **names
    )


def _character_chunks(ds: Dataset, document: Document, recipe: Recipe) -> list[Chunk]:
    title = ds.docs_meta[document.doc_id]["title"]
    base = f"[{display_name(title)} · MCU character"
    rows = ds.sections.get(document.doc_id, [])
    intro = [
        b for r in rows if r["team_section"] == "intro" for b in _section_blocks(r, f"{base}]")
    ]
    bio = [
        b
        for r in rows
        if BIOGRAPHY_HEAD.search(r["section_path"][0])
        for b in _section_blocks(r, f"{base} · {' > '.join(r['section_path'])}]")
    ]
    names = {"title_ko": None, "title_original": title}
    return make_chunks(document, Section.INTRO, intro, recipe, **names) + make_chunks(
        document, Section.BIOGRAPHY, bio, recipe, **names
    )


def _lore_chunks(ds: Dataset, document: Document, recipe: Recipe) -> list[Chunk]:
    title = ds.docs_meta[document.doc_id]["title"]
    name = display_name(title)
    blocks: list[Block] = []
    for row in ds.sections.get(document.doc_id, []):
        if row["section_kind"] in LORE_SKIP_KINDS:
            continue
        path = " > ".join(row["section_path"])
        blocks += _section_blocks(row, f"[{name}]" if path == "(lead)" else f"[{name} · {path}]")
    return make_chunks(document, Section.LORE, blocks, recipe, title_ko=None, title_original=title)


def build_chunks(ds: Dataset, recipe: Recipe, scope: str = "works+characters+lore") -> list[Chunk]:
    """본문 청크 전부. scope='works' 는 작품 문서만 (architecture.md §3 의 작품 규칙)"""
    if scope not in SCOPES:
        raise ValueError(f"scope 는 {SCOPES} 중 하나입니다: {scope}")
    by_doc = select_works(ds)
    chunks: list[Chunk] = []
    for doc_id, document in ds.documents.items():
        meta = ds.docs_meta[doc_id]
        if doc_id in by_doc:
            chunks += _work_chunks(ds, document, by_doc[doc_id], recipe)
        elif scope == "works":
            continue
        elif document.doc_type == "character" and meta["scope_status"] == "mcu":
            chunks += _character_chunks(ds, document, recipe)
        elif meta["title"] in LORE_TITLES:
            chunks += _lore_chunks(ds, document, recipe)
    ids = [c.chunk_id for c in chunks]
    if len(set(ids)) != len(ids):
        raise ValueError("chunk_id 가 겹칩니다")
    return chunks


def parent_sources(ds: Dataset) -> dict[str, str]:
    """parent_id → 부모 원문. 청크의 char_start · char_end 가 이 글자를 가리킨다"""
    out: dict[str, str] = {}
    for doc_id, rows in ds.sections.items():
        for row in rows:
            if row.get("text"):
                out[f"{doc_id}#s{row['section_index']}"] = row["text"]
            for i, ep in enumerate(row.get("episode_entries") or [], start=1):
                if (ep.get("summary") or "").strip():
                    out[f"{doc_id}#s{row['section_index']}e{i}"] = ep["summary"]
    return out


def write_jsonl(path: Path, chunks: Iterable[Chunk]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(chunk.model_dump_json() + "\n")


def read_jsonl(path: Path) -> Iterator[Chunk]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield Chunk.model_validate_json(line)
