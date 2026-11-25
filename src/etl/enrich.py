"""D 구성(LLM 가공 색인, architecture §5): 팀 가공본을 CK05 청크의 색인 글에만 덧붙인다.

- 가공본: data/local/cineverse_handoff/enriched/enriched_chunks.jsonl (861건, plot 절만, 문서 313개)
  method=openai_structured_enrichment · model=gpt-6-luna · schema=team-v1-enriched-1. 프롬프트는 전달받지 못함
- 원문 · chunk_id 는 C(CK05)와 같다. 덧붙인 글은 임베딩 · BM25 · 재정렬 입력에만 쓰고, 답변 문맥 · 정답 키 ·
  근거 회수율은 원문으로만 잰다 (가공 문장이 있다고 근거를 찾은 것으로 치지 않는다)
- 가공본의 청크 경계(팀 초안)와 CK05 경계가 달라서, 같은 원래 문서(doc_…)의 plot 절 안에서 글자 위치가
  겹치는 가공본을 붙인다. 가공본은 글자 복원 전 본문이라 영문자 · 숫자만 남겨 위치를 찾는다
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from etl.dataset import PROJECT_ROOT, Dataset
from shared.core.models import Chunk
from shared.core.schemas import Section

ENRICHED_PATH = (
    PROJECT_ROOT / "data" / "local" / "cineverse_handoff" / "enriched" / "enriched_chunks.jsonl"
)
ANCHOR = 40  # 위치를 찾을 때 쓰는 앞 · 뒤 글자 수 (영문자 · 숫자만 센 길이)
MIN_OVERLAP = 50  # 이만큼 겹쳐야 붙인다 (경계에서 몇 글자 스치는 것은 빼려고)
_NON_ALNUM = re.compile(r"[^0-9a-z]")


def alnum(text: str) -> str:
    return _NON_ALNUM.sub("", text.lower())


def enrichment_version(path: Path = ENRICHED_PATH) -> str:
    """가공본 파일 SHA-256 앞 8자 + 스키마 (색인 버전에 붙는다)."""
    return f"enriched-team-v1-enriched-1@{hashlib.sha256(path.read_bytes()).hexdigest()[:8]}"


def load_enrichment(path: Path = ENRICHED_PATH) -> dict[str, list[dict[str, Any]]]:
    """원래 문서 ID(doc_…) → 그 문서의 가공본 (position 순)."""
    by_doc: dict[str, list[dict[str, Any]]] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                row = json.loads(line)
                by_doc.setdefault(row["doc_id"], []).append(row)
    for rows in by_doc.values():
        rows.sort(key=lambda r: int(r["position"]))
    return by_doc


def _span(haystack: str, needle: str) -> tuple[int, int] | None:
    """needle(영문자 · 숫자만)의 haystack 안 구간. 통째로 없으면 앞 · 뒤 ANCHOR 글자로 찾는다."""
    if not needle:
        return None
    pos = haystack.find(needle)
    if pos != -1:
        return pos, pos + len(needle)
    head = haystack.find(needle[:ANCHOR])
    tail = haystack.find(needle[-ANCHOR:], max(head, 0))
    if head == -1 or tail == -1:
        return None
    return head, tail + ANCHOR


@dataclass
class Mapping:
    """CK05 청크 하나에 붙인 가공본과 겹친 정도."""

    enriched_ids: list[str] = field(default_factory=list)
    overlap_chars: list[int] = field(
        default_factory=list
    )  # 가공본마다 겹친 글자 수 (영문자 · 숫자)


def map_enrichment(
    data: Dataset, chunks: list[Chunk], by_doc: dict[str, list[dict[str, Any]]]
) -> tuple[dict[str, Mapping], dict[str, Any]]:
    """plot 청크마다 겹치는 가공본을 찾는다. 반환: (chunk_id → Mapping, 연결 통계)."""
    mappings: dict[str, Mapping] = {}
    unlocated: list[str] = []
    located: set[str] = set()
    for chunk in chunks:
        team_id = chunk.source_ids.get("team_document_id") or ""
        rows = by_doc.get(team_id)
        if chunk.section != Section.PLOT or not rows:
            continue
        section = alnum(data.sections[(team_id, chunk.source_ids["section_index"] or "")])
        mine = _span(section, alnum(chunk.text))
        if mine is None:
            continue
        mapping = Mapping()
        for row in rows:
            theirs = _span(section, alnum(row["text"]))
            if theirs is None:
                unlocated.append(row["chunk_id"])
                continue
            located.add(row["chunk_id"])
            overlap = min(mine[1], theirs[1]) - max(mine[0], theirs[0])
            if overlap >= MIN_OVERLAP:
                mapping.enriched_ids.append(row["chunk_id"])
                mapping.overlap_chars.append(overlap)
        if mapping.enriched_ids:
            mappings[chunk.chunk_id] = mapping
    in_scope = {
        c.source_ids.get("team_document_id")
        for c in chunks
        if c.section == Section.PLOT and c.source_ids.get("team_document_id") in by_doc
    }
    stats = {
        "plot_chunks": sum(c.section == Section.PLOT for c in chunks),
        "plot_chunks_enriched": len(mappings),
        "chunks_enriched_total": len(mappings),
        "chunks_total": len(chunks),
        "enriched_rows_in_scope_docs": sum(len(by_doc[d]) for d in in_scope if d),
        "enriched_rows_located": len(located),
        "enriched_rows_not_located": sorted(set(unlocated) - located),
    }
    return mappings, stats


def enrichment_text(rows: list[dict[str, Any]]) -> str:
    """색인 글에 덧붙일 가공 정보 (요약 · 사건 · 개체). 원문과 구분되게 표시를 붙인다."""
    summaries = list(
        dict.fromkeys(r["summary_original"].strip() for r in rows if r.get("summary_original"))
    )
    events: list[str] = []
    entities: list[str] = []
    for r in rows:
        for e in _as_list(r.get("events")):
            parts = [e.get("event"), e.get("cause"), e.get("consequence")]
            events.append(" ".join(p.strip() for p in parts if p))
        entities += [str(x) for x in _as_list(r.get("entities"))]
    lines = ["[LLM-derived context, not source text]"]
    lines += [f"Summary: {s}" for s in summaries]
    lines += [f"Event: {e}" for e in dict.fromkeys(events) if e]
    if entities:
        lines.append("Entities: " + ", ".join(dict.fromkeys(entities)))
    return "\n".join(lines)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def enriched_index_texts(
    chunks: list[Chunk], mappings: dict[str, Mapping], by_doc: dict[str, list[dict[str, Any]]]
) -> dict[str, str]:
    """chunk_id → D 의 색인 글 (머리말 + 원문 + 가공 정보). 가공본이 없는 청크는 C 와 같은 글."""
    from etl.load import index_text

    by_id = {row["chunk_id"]: row for rows in by_doc.values() for row in rows}
    texts: dict[str, str] = {}
    for chunk in chunks:
        base = index_text(chunk)
        mapping = mappings.get(chunk.chunk_id)
        if mapping is None:
            texts[chunk.chunk_id] = base
        else:
            rows = [by_id[i] for i in mapping.enriched_ids]
            texts[chunk.chunk_id] = f"{base}\n\n{enrichment_text(rows)}"
    return texts


def enriched_index_version(base_version: str, path: Path = ENRICHED_PATH) -> str:
    """D 색인 버전 = 원문 색인 버전 + 가공본 버전 (예: CK05-…@24ee6397+enriched-…@504af75e)."""
    return f"{base_version}+{enrichment_version(path)}"


@dataclass
class EnrichedIndex:
    """D 색인 하나: 컬렉션 · 버전 · 청크별 색인 글 · 연결 통계."""

    collection: str
    version: str
    page_texts: dict[str, str]
    stats: dict[str, Any]


def build_enriched_index(
    data: Dataset, chunks: list[Chunk], collection: str, base_version: str
) -> EnrichedIndex:
    by_doc = load_enrichment()
    mappings, stats = map_enrichment(data, chunks, by_doc)
    return EnrichedIndex(
        collection=collection,
        version=enriched_index_version(base_version),
        page_texts=enriched_index_texts(chunks, mappings, by_doc),
        stats=stats
        | {"enriched_file_sha256": hashlib.sha256(ENRICHED_PATH.read_bytes()).hexdigest()},
    )
