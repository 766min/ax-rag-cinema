"""새 데이터(영민님 schema 묶음, 2026-11-25)를 읽고 S2 청크 묶음을 만든다. 네트워크를 쓰지 않는다.

S2 = 어젯밤 R2 와 같은 범위를 새 데이터로 만든 것:
- 작품 줄거리: MCU 문서의 Plot 절(team_section = plot) + 에피소드 표의 요약 한 줄씩
- 인물 · 인물 목록 · 세계관 문서의 작품 속 섹션 (R2_SECTION_KINDS, 본문 있는 것만)
- 범위: documents_meta 의 scope_status 가 mcu 이거나 판정이 없는 문서 (세계관 문서 등).
  MCU 아님(non_mcu) · 근거 없음(no_mcu_evidence) · 코믹스 등(non_screen)은 뺀다.

평가셋 근거는 옛 문서 ID(doc_…)로 적혀 있어서, source_ids.team_document_id 로 새 ID 에 잇는다 (절 번호는 같다).
"""

import dataclasses
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from experiments.chunking.chunker import (
    R2_SECTION_KINDS,
    ExpChunk,
    _numbered,
    _pieces,
    section_header,
    split_paragraphs,
)
from experiments.chunking.data import PROJECT_ROOT, Question, SectionRef, read_jsonl

SCHEMA_DIR = PROJECT_ROOT / "data" / "schema"
EXCLUDED_SCOPES = frozenset({"non_mcu", "no_mcu_evidence", "non_screen"})
EXTRA_DOC_TYPES = frozenset({"character", "character_list", "universe"})


@dataclass
class Schema:
    documents: dict[str, dict[str, Any]]  # doc_id -> 문서 (팀 Document 모양)
    meta: dict[str, dict[str, Any]]  # doc_id -> extra/documents_meta
    sections: list[dict[str, Any]]
    aliases: list[dict[str, Any]]
    old_to_new: dict[str, str]  # 옛 문서 ID(doc_…) -> 새 문서 ID

    def in_scope(self, doc_id: str) -> bool:
        return self.meta[doc_id].get("scope_status") not in EXCLUDED_SCOPES

    def title(self, doc_id: str) -> str:
        return str(self.meta[doc_id]["title"])


def load_schema(base: Path = SCHEMA_DIR) -> Schema:
    if not base.exists():
        raise FileNotFoundError(f"{base} 가 없습니다. 새 데이터 묶음을 data/schema 에 풀어 주세요.")
    documents = {d["doc_id"]: d for d in read_jsonl(base / "documents.jsonl")}
    meta = {m["doc_id"]: m for m in read_jsonl(base / "extra" / "documents_meta.jsonl")}
    if set(meta) != set(documents):
        raise ValueError("documents 와 documents_meta 의 문서가 다릅니다")
    old_to_new: dict[str, str] = {}
    for d in documents.values():
        old = (d.get("source_ids") or {}).get("team_document_id")
        if not old or old in old_to_new:
            raise ValueError(f"옛 문서 ID 가 없거나 겹칩니다: {d['doc_id']}")
        old_to_new[old] = d["doc_id"]
    return Schema(
        documents=documents,
        meta=meta,
        sections=list(read_jsonl(base / "sections.jsonl")),
        aliases=list(read_jsonl(base / "aliases.jsonl")),
        old_to_new=old_to_new,
    )


def remap_questions(questions: list[Question], schema: Schema) -> list[Question]:
    """평가셋 근거의 옛 문서 ID 를 새 문서 ID 로 바꾼다. 못 찾으면 멈춘다 (채점이 조용히 틀어지지 않게)."""
    out = []
    for q in questions:
        refs = []
        for r in q.gold_refs:
            if r.doc_id not in schema.old_to_new:
                raise ValueError(f"{q.question_id}: 새 데이터에 없는 근거 문서 {r.doc_id}")
            refs.append(SectionRef(schema.old_to_new[r.doc_id], r.section_index))
        out.append(dataclasses.replace(q, gold_refs=refs))
    return out


def _units(schema: Schema) -> list[tuple[SectionRef, str, str, str]]:
    """청크로 자를 재료: (원문 섹션, 문서 종류, 머리말, 글). 문서 · 절 순서를 지킨다."""
    out: list[tuple[SectionRef, str, str, str]] = []
    for s in schema.sections:
        doc_id = s["doc_id"]
        if not schema.in_scope(doc_id):
            continue
        doc = schema.documents[doc_id]
        ref = SectionRef(doc_id, s["section_index"])
        title = schema.title(doc_id)
        if doc["doc_type"] in EXTRA_DOC_TYPES:
            if s["section_kind"] in R2_SECTION_KINDS and s["text"].strip():
                header = section_header({"title": title}, s)
                out.append((ref, doc["doc_type"], header, s["text"]))
            continue
        # 줄거리 절: 팀 분류(team_section)가 plot 이거나, 수집 분류(section_kind)가 plot 인 것
        # ("Story" · "Premise" · "Scene summary" 처럼 제목이 달라 team_section 이 비어 있는 줄거리 절 포함)
        if (s["team_section"] == "plot" or s["section_kind"] == "plot") and s["text"].strip():
            out.append((ref, doc["doc_type"], f"[{title} · plot]", s["text"]))
        for e in s.get("episode_entries") or []:
            summary = (e.get("summary") or "").strip()
            if summary:
                name = e.get("title") or ""
                header = f"[{title} · {name} · plot]" if name else f"[{title} · plot]"
                out.append((ref, doc["doc_type"], header, summary))
    return out


def s2_chunks(schema: Schema, max_chars: int, overlap: bool = False) -> list[ExpChunk]:
    counter: dict[SectionRef, int] = defaultdict(int)
    out = []
    for ref, doc_type, header, text in _units(schema):
        doc = schema.documents[ref.doc_id]
        for k, (piece, ov) in enumerate(_pieces(text, max_chars, overlap)):
            out.append(
                ExpChunk(
                    chunk_id=_numbered(ref.doc_id, ref, counter),
                    doc_id=ref.doc_id,
                    refs=[ref],
                    doc_kind=doc_type,
                    title=schema.title(ref.doc_id),
                    header=header,
                    text=piece,
                    overlap_chars=ov,
                    first_in_unit=k == 0,
                    source_url=doc["source_url"],
                    revid=doc.get("revid"),
                )
            )
    ids = [c.chunk_id for c in out]
    if len(set(ids)) != len(ids):
        raise ValueError("S2: chunk_id 가 겹칩니다")
    return out


def schema_paragraphs(schema: Schema) -> dict[SectionRef, set[str]]:
    """원문 섹션마다 문단 모음 (에피소드 요약 포함). 경계 검사(cut_report)에 쓴다."""
    paras: dict[SectionRef, set[str]] = defaultdict(set)
    for s in schema.sections:
        ref = SectionRef(s["doc_id"], s["section_index"])
        paras[ref].update(split_paragraphs(s["text"]))
        for e in s.get("episode_entries") or []:
            paras[ref].update(split_paragraphs(e.get("summary") or ""))
    return paras


def schema_section_texts(schema: Schema) -> list[str]:
    """발췌 확인 조각에서 상투 문장을 가려낼 때 쓰는 전체 원문 (범위와 상관없이 전부)."""
    texts = [s["text"] for s in schema.sections if s["text"].strip()]
    texts += [
        e["summary"]
        for s in schema.sections
        for e in (s.get("episode_entries") or [])
        if (e.get("summary") or "").strip()
    ]
    return texts


def is_extra_doc(schema: Schema, doc_id: str) -> bool:
    return schema.documents[doc_id]["doc_type"] in EXTRA_DOC_TYPES
