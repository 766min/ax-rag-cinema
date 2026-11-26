"""공통 데이터셋(cineverse-dataset-schema)의 절 → 팀 양식 Chunk.

실행 (저장소 루트에서):
    PYTHONPATH=src uv run python test/youngmin/chunking/build_chunks.py
    PYTHONPATH=src uv run python test/youngmin/chunking/build_chunks.py --name min200 --min-chars 200

입력: data/cineverse-dataset-schema/  (저장소 루트, works · documents · sections · extra/)
출력: test/youngmin/data/chunks_{name}/  (.gitignore 의 data/ 규칙으로 git 에 올라가지 않는다)

개인 브랜치(test/youngmin)의 시험용이다. 팀 공통 전략으로 정해진 것이 아니다.
청크의 text 는 문단 글자 그대로다. 고쳐 쓰지 않고, 짧은 문단만 이웃과 합친다.
모든 청크는 팀 Chunk · Section 그대로다 (biography · lore 는 PR #16 으로 팀 Section 에 들어왔다).
작품 · 문서는 데이터셋의 것을 그대로 쓰고(ID 를 새로 만들지 않는다) 여기서는 청크만 만든다.
아홉 원칙이 어디에 들어 있는지는 각 함수의 설명에 번호로 적었다.
"""

import argparse
import hashlib
import json
import re
import statistics
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from shared.core.models import Chunk, Document, Work
from shared.core.schemas import Section

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
# 공통 데이터셋은 저장소 루트의 data/ 에 둔다 (팀 번역 도구 src/tools/translation.py 와 같은 위치)
DATASET = ROOT / "data" / "cineverse-dataset-schema"

MIN_CHARS = 150  # 이보다 짧은 문단은 이웃 문단과 합친다 (원칙 3)
LONG_CHARS = 2000  # 보고서에서 "긴 청크" 로 세는 기준 (자르지는 않는다, 원칙 2)
TINY_CHARS = 30  # 이보다 짧은 조각은 이름만 남은 흔적이라 넣지 않는다 (예: "The Bowie.")

# 인물 문서에서 서사로 받는 절 (첫 단계 제목에 biography 가 들면)
BIOGRAPHY_HEAD = re.compile(r"biography", re.IGNORECASE)
# 세계관 문서 중 작품 속 설정을 다루는 것만 받는다 (목록 · 수상 · 제작사 · 페이즈 문서는 뺀다, 원칙 7)
LORE_TITLES = {
    "Features of the Marvel Cinematic Universe",
    "Marvel Cinematic Universe timeline",
    "Multiverse (Marvel Cinematic Universe)",
}
# 세계관 문서에서 빼는 절 종류 (제작 · 평가 · 참고)
LORE_SKIP_KINDS = {"creation", "reception", "release", "meta", "see_also"}

START_DATE = re.compile(r"\{\{\s*Start date\s*\|\s*(\d{4})", re.IGNORECASE)
POST_CREDIT = re.compile(r"\b(mid|post)-credits? scene", re.IGNORECASE)
MCU_SUFFIX = " (Marvel Cinematic Universe)"
# 본문에서 표는 한 줄로 평탄화되고 "+ 표 제목" 으로 시작한다 (원칙 7)
FLAT_TABLE = re.compile(r"^\+ ")
# 주어 없이 시작하는 문단 (틀이 지워진 흔적). 글자를 되살린 데이터셋에서는 0 이어야 한다
SUBJECTLESS = re.compile(r"^(is|are|was|were|\(|The (is|are|was|were|\())\b")


# 청크 하나의 재료: 절, 머리말, 문단들, 독립 항목인가, 부모 ID, 붙일 표시
Block = tuple[dict[str, Any], str, list[str], bool, str, list[str]]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def split_paragraphs(text: str) -> tuple[list[str], int]:
    """원칙 1 · 7 — 글쓴이가 그은 경계(줄바꿈)로 나눈다. 평탄화된 표는 뺀다.

    (문단 목록, 뺀 표의 수) 를 돌려준다.
    """
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    kept = [line for line in lines if not FLAT_TABLE.match(line)]
    return kept, len(lines) - len(kept)


def merge_short(paragraphs: list[str], min_chars: int = MIN_CHARS) -> list[str]:
    """원칙 3 — 이어지는 글(줄거리 · 서사): 짧은 문단을 다음 문단에, 마지막이면 앞 문단에 붙인다"""
    merged: list[str] = []
    carry = ""
    for paragraph in paragraphs:
        current = f"{carry} {paragraph}" if carry else paragraph
        if len(current) < min_chars:
            carry = current
        else:
            merged.append(current)
            carry = ""
    if carry:
        if merged:
            merged[-1] = f"{merged[-1]} {carry}"
        else:
            merged.append(carry)
    return merged


def pool_short(paragraphs: list[str], min_chars: int = MIN_CHARS) -> list[str]:
    """원칙 3 · 4 — 항목이 독립된 글(세계관 용어집): 짧은 항목끼리만 모은다.

    긴 항목에 옆 항목을 섞지 않는다. 모아도 짧으면 짧은 채로 둔다.
    """
    out: list[str] = []
    pool: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) >= min_chars:
            if pool:
                out.append(" ".join(pool))
                pool = []
            out.append(paragraph)
            continue
        pool.append(paragraph)
        if len(" ".join(pool)) >= min_chars:
            out.append(" ".join(pool))
            pool = []
    if pool:
        out.append(" ".join(pool))
    return out


def content_hash(chunk_id: str, header: str, text: str) -> str:
    """chunk_id · header · text 를 줄바꿈으로 이은 SHA-256 (architecture.md §4.2)"""
    return hashlib.sha256("\n".join((chunk_id, header, text)).encode("utf-8")).hexdigest()


def version_of(pairs: Iterable[tuple[str, str]]) -> str:
    """(chunk_id, content_hash) 를 정렬해 다시 해시한 값 (architecture.md §4.2). DB 행에서도 같은 함수로 잰다"""
    lines = sorted(f"{chunk_id}\t{content}" for chunk_id, content in pairs)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:16]


def index_version(chunks: list[Chunk]) -> str:
    """원칙 8 — 청크 묶음의 버전. 기준을 바꾸면 달라진다"""
    return version_of((c.chunk_id, c.content_hash) for c in chunks)


def index_files(name: str = "base") -> list[Path]:
    """고정 문서 컬렉션에 들어갈 파일 — 본문 청크 + 작품 카드 + 연결 카드.

    카드는 작품 표에서 나오므로 청킹 변형(min200 등)과 상관없이 같은 파일을 쓴다.
    """
    data = HERE.parent / "data"
    return [
        data / f"chunks_{name}" / "chunks.jsonl",
        data / "cards" / "cards.jsonl",
        data / "cards" / "connections.jsonl",
    ]


def read_index(name: str = "base") -> list[Chunk]:
    """색인에 넣을 청크 전부. 정답 키의 색인 버전과 적재한 DB 의 색인 버전이 이 묶음으로 같아진다"""
    chunks: list[Chunk] = []
    for path in index_files(name):
        if not path.exists():
            raise FileNotFoundError(
                f"{path} 가 없습니다. build_chunks · build_cards · build_connections 를 먼저 돌리세요"
            )
        with path.open(encoding="utf-8") as f:
            chunks += [Chunk.model_validate_json(line) for line in f if line.strip()]
    ids = [c.chunk_id for c in chunks]
    if len(set(ids)) != len(ids):
        raise ValueError("색인 안에서 chunk_id 가 겹칩니다")
    return chunks


def entities_in(text: str, links: list[dict[str, Any]]) -> list[str]:
    """절에 걸린 링크 중 이 문단에 글자가 나오는 것의 대상 문서 제목 (처음 나온 순)"""
    found: list[str] = []
    for link in links:
        anchor = link.get("anchor_text") or ""
        target = link.get("target_title") or ""
        if anchor and target and anchor in text and target not in found:
            found.append(target)
    return found


def work_header(
    title: str, year: int | None, kind: str, phase: str | None, order: int | None
) -> str:
    """원칙 6 — 문맥을 규칙으로 미리 붙인다. 빈 값은 구분점째 뺀다"""
    parts = [title, f"{year} {kind}" if year else kind]
    if phase:
        parts.append(f"Phase {phase}")
    if order:
        parts.append(f"timeline #{order}")
    return f"[{' · '.join(parts)}]"


def shown_year(work: Work, infobox: dict[str, Any] | None) -> int | None:
    """머리말과 카드에 적을 연도.

    데이터셋의 시즌 행은 year 가 시리즈 시작 연도다 (로키 시즌 2 도 2021). 그 작품의 문서 정보 상자에
    방영 시작일이 있으면 그 연도를 쓰고, 없으면 시즌 1 만 시리즈 연도를 쓰고 나머지는 비운다.
    연도가 빈 작품(넷플릭스 시기 시리즈)도 같은 방법으로 채운다.
    """
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


def text_block(section: dict[str, Any], header: str, independent: bool = False) -> Block:
    """절의 본문을 재료로. 부모는 그 절이다"""
    paragraphs, _ = split_paragraphs(section.get("text") or "")
    return (section, header, paragraphs, independent, f"s{section['section_index']}", [])


def episode_blocks(section: dict[str, Any], header: str) -> list[Block]:
    """에피소드 표의 줄마다 요약 하나를 재료로. 부모는 그 에피소드다 (원칙 1 — 에피소드가 경계다)"""
    blocks: list[Block] = []
    for position, episode in enumerate(section.get("episode_entries") or [], start=1):
        summary = (episode.get("summary") or "").strip()
        if not summary:
            continue
        number = episode.get("episode_number") or position
        name = (
            f"Episode {number}: {episode['title']}" if episode.get("title") else f"Episode {number}"
        )
        paragraphs, _ = split_paragraphs(summary)
        blocks.append(
            (
                section,
                f"{header[:-1]} · {name}]",
                paragraphs,
                False,
                f"s{section['section_index']}e{position}",
                ["episode_summary"],
            )
        )
    return blocks


def make_chunks(
    document: Document,
    section: Section,
    blocks: list[Block],
    *,
    title_ko: str | None,
    title_original: str,
    min_chars: int = MIN_CHARS,
    stats: Counter[str] | None = None,
) -> list[Chunk]:
    """같은 종류의 재료 묶음을 청크로. 번호는 문서 안에서 그 종류끼리 1부터 이어 센다.

    원칙 2 — 문단을 쪼개지 않는다. 원칙 4 — 겹침이 없고 문단은 절 · 에피소드 경계를 넘어 합치지 않는다.
    원칙 5 — 부모(절 또는 에피소드)를 가리키는 parent_id 를 남긴다.
    원칙 9 — 문서의 출처 값을 청크마다 복제한다.
    """
    pieces: list[tuple[dict[str, Any], str, str, str, list[str]]] = []
    for sec, header, paragraphs, independent, parent, flags in blocks:
        raw = [
            line for line in (sec.get("text") or "").split("\n") if FLAT_TABLE.match(line.strip())
        ]
        if stats is not None and not flags:
            stats["flat_tables_skipped"] += len(raw)
        join = pool_short if independent else merge_short
        for text in join(paragraphs, min_chars):
            if len(text) < TINY_CHARS:
                if stats is not None:
                    stats["tiny_dropped"] += 1
                continue
            pieces.append((sec, header, text, parent, flags))
    chunks: list[Chunk] = []
    for position, (sec, header, text, parent, flags) in enumerate(pieces, start=1):
        chunk_id = f"{document.doc_id}-{section}-p{position:02d}"
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
                entities=entities_in(text, sec.get("links") or []),
                flags=flags + (["post_credit"] if POST_CREDIT.search(text) else []),
                content_hash=content_hash(chunk_id, header, text),
                source_ids={
                    "team_document_id": document.source_ids["team_document_id"],
                    "team_section_index": str(sec["section_index"]),
                    "team_section_path": " > ".join(sec["section_path"]),
                    "parent_id": f"{document.doc_id}#{parent}",
                },
            )
        )
    return chunks


def make_parents(chunks: list[Chunk]) -> list[dict[str, Any]]:
    """원칙 5 — 작은 조각으로 찾고 큰 조각을 넘길 수 있게, 부모마다 청크 목록과 이은 글자를 낸다"""
    parents: dict[str, dict[str, Any]] = {}
    for chunk in chunks:
        parent_id = chunk.source_ids["parent_id"] or ""
        parent = parents.setdefault(
            parent_id,
            {
                "parent_id": parent_id,
                "doc_id": chunk.doc_id,
                "work_id": chunk.work_id,
                "section": str(chunk.section),
                "section_path": chunk.source_ids["team_section_path"],
                "header": chunk.header,
                "chunk_ids": [],
                "text": "",
            },
        )
        parent["chunk_ids"].append(chunk.chunk_id)
        parent["text"] = f"{parent['text']}\n{chunk.text}".strip()
    return list(parents.values())


def select_works(works: list[Work], works_meta: dict[str, dict[str, Any]]) -> dict[str, Work]:
    """색인 규칙 (architecture.md §3): 정식 작품 · 개봉 · 1차 수집이고 Wikipedia 문서가 있는 행.

    돌려주는 것: {문서 ID: 작품}
    """
    selected: dict[str, Work] = {}
    for work in works:
        meta = works_meta[work.work_id]
        if (
            work.is_canon
            and meta["status"] == "released"
            and meta["collect_wave"] == "1"
            and meta["doc_id"]
        ):
            selected[meta["doc_id"]] = work
    return selected


def work_chunks(
    document: Document,
    work: Work,
    year: int | None,
    sections: list[dict[str, Any]],
    min_chars: int,
    stats: Counter[str],
) -> list[Chunk]:
    """작품 문서: 첫머리는 synopsis, Plot · Premise 절과 에피소드 요약은 plot"""
    header = work_header(
        work.title_original, year, document.doc_type, work.phase, work.timeline_order
    )
    synopsis = [text_block(s, header) for s in sections if s["team_section"] == "synopsis"]
    plot: list[Block] = []
    for section in sections:
        if section["team_section"] == "plot" and section["text"]:
            plot.append(text_block(section, header))
        plot += episode_blocks(section, header)
    names = {"title_ko": work.title_ko, "title_original": work.title_original}
    chunks: list[Chunk] = []
    for kind, blocks in ((Section.SYNOPSIS, synopsis), (Section.PLOT, plot)):
        chunks += make_chunks(document, kind, blocks, min_chars=min_chars, stats=stats, **names)
    return chunks


def character_chunks(
    document: Document,
    title: str,
    sections: list[dict[str, Any]],
    min_chars: int,
    stats: Counter[str],
) -> list[Chunk]:
    """인물 문서: 첫머리는 intro, 서사 절은 biography"""
    base = f"[{display_name(title)} · MCU character"
    intro = [text_block(s, f"{base}]") for s in sections if s["team_section"] == "intro"]
    biography = [
        text_block(s, f"{base} · {' > '.join(s['section_path'])}]")
        for s in sections
        if s["text"] and BIOGRAPHY_HEAD.search(s["section_path"][0])
    ]
    chunks: list[Chunk] = []
    for kind, blocks in ((Section.INTRO, intro), (Section.BIOGRAPHY, biography)):
        chunks += make_chunks(
            document,
            kind,
            blocks,
            title_ko=None,
            title_original=title,
            min_chars=min_chars,
            stats=stats,
        )
    return chunks


def lore_chunks(
    document: Document,
    title: str,
    sections: list[dict[str, Any]],
    min_chars: int,
    stats: Counter[str],
) -> list[Chunk]:
    """세계관 문서: 첫머리와 본문 절을 lore 로. 항목이 독립된 글이라 짧은 것끼리만 모은다"""
    name = display_name(title)
    blocks = []
    for section in sections:
        if not section["text"] or section["section_kind"] in LORE_SKIP_KINDS:
            continue
        path = " > ".join(section["section_path"])
        header = f"[{name}]" if path == "(lead)" else f"[{name} · {path}]"
        blocks.append(text_block(section, header, independent=True))
    return make_chunks(
        document,
        Section.LORE,
        blocks,
        title_ko=None,
        title_original=title,
        min_chars=min_chars,
        stats=stats,
    )


def build(name: str = "base", min_chars: int = MIN_CHARS) -> dict[str, Any]:
    out = HERE.parent / "data" / f"chunks_{name}"
    stats: Counter[str] = Counter()
    works = [Work.model_validate(row) for row in load_jsonl(DATASET / "works.jsonl")]
    documents = {
        row["doc_id"]: Document.model_validate(row)
        for row in load_jsonl(DATASET / "documents.jsonl")
    }
    works_meta = {row["work_id"]: row for row in load_jsonl(DATASET / "extra" / "works_meta.jsonl")}
    docs_meta = {
        row["doc_id"]: row for row in load_jsonl(DATASET / "extra" / "documents_meta.jsonl")
    }
    sections: dict[str, list[dict[str, Any]]] = {}
    for row in load_jsonl(DATASET / "sections.jsonl"):
        sections.setdefault(row["doc_id"], []).append(row)

    by_doc = select_works(works, works_meta)
    chunks: list[Chunk] = []
    used: Counter[str] = Counter()
    for doc_id, document in documents.items():
        meta = docs_meta[doc_id]
        rows = sections.get(doc_id, [])
        if doc_id in by_doc:
            work = by_doc[doc_id]
            year = shown_year(work, meta["infobox"])
            made = work_chunks(document, work, year, rows, min_chars, stats)
        elif document.doc_type == "character" and meta["scope_status"] == "mcu":
            made = character_chunks(document, meta["title"], rows, min_chars, stats)
        elif meta["title"] in LORE_TITLES:
            made = lore_chunks(document, meta["title"], rows, min_chars, stats)
        else:
            continue
        used[document.doc_type] += 1
        if not made:
            stats["documents_without_chunks"] += 1
        chunks += made
    parents = make_parents(chunks)

    ids = [c.chunk_id for c in chunks]
    if len(set(ids)) != len(ids):
        raise ValueError("chunk_id 가 겹칩니다")

    out.mkdir(parents=True, exist_ok=True)
    with (out / "chunks.jsonl").open("w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(chunk.model_dump_json() + "\n")
    with (out / "parents.jsonl").open("w", encoding="utf-8") as f:
        for parent in parents:
            f.write(json.dumps(parent, ensure_ascii=False) + "\n")

    lengths = [len(c.text) for c in chunks]
    parent_lengths = [len(p["text"]) for p in parents]
    plotted = {c.doc_id for c in chunks if str(c.section) == "plot"}
    report = {
        "name": name,
        # 본문 청크만의 버전. 색인(카드 포함)의 버전은 check_gold.py · 적재 기록에 있다
        "chunks_version": index_version(chunks),
        "params": {"min_chars": min_chars, "overlap": 0},
        "documents": dict(used),
        "works": len(by_doc),
        "works_with_timeline_order": sum(w.timeline_order is not None for w in by_doc.values()),
        "chunks": len(chunks),
        "chunks_by_section": dict(Counter(str(c.section) for c in chunks)),
        "episode_summary_chunks": sum("episode_summary" in c.flags for c in chunks),
        "text_chars": {
            "min": min(lengths),
            "median": int(statistics.median(lengths)),
            "max": max(lengths),
            "under_min_chars": sum(n < min_chars for n in lengths),
            "from_200_to_1000_percent": round(
                100 * sum(200 <= n <= 1000 for n in lengths) / len(lengths), 1
            ),
            f"over_{LONG_CHARS}": sum(n > LONG_CHARS for n in lengths),
        },
        "subjectless_chunks": sum(bool(SUBJECTLESS.match(c.text)) for c in chunks),
        "flat_tables_skipped": stats["flat_tables_skipped"],
        "tiny_dropped": stats["tiny_dropped"],
        "parents": len(parents),
        "parent_chars_median": int(statistics.median(parent_lengths)),
        "chunks_with_entities": sum(bool(c.entities) for c in chunks),
        "post_credit_flags": sum("post_credit" in c.flags for c in chunks),
        "documents_without_chunks": stats["documents_without_chunks"],
        "works_without_plot": sorted(
            f"{work.work_id} {work.title_original}"
            for doc_id, work in by_doc.items()
            if doc_id not in plotted
        ),
    }
    (out / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="공통 데이터셋의 절을 팀 양식 청크로 만든다")
    parser.add_argument("--name", default="base", help="출력 폴더 이름 (data/chunks_{name})")
    parser.add_argument("--min-chars", type=int, default=MIN_CHARS, help="짧은 문단을 합치는 기준")
    args = parser.parse_args()
    print(json.dumps(build(args.name, args.min_chars), ensure_ascii=False, indent=2))
