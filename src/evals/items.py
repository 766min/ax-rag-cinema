"""평가 문항 읽기: question_set.jsonl(골든셋) → EvalItem.

골든셋은 근거를 '원본 doc_… / 섹션 N' 과 원문 발췌(약 170자, 단어 중간에서 시작하고 … 로 끝남)로 적어 두었다.
인용마다 발췌의 원문 절 안 위치를 찾고, 그 구간과 겹치는 청크를 정답 키(gold_chunk_ids)로 삼는다.
절 전체를 정답으로 대신 쓰지 않는다 (Hit@5 가 부풀려지지 않게).

인용 하나의 매핑 결과(status):
- contained: 발췌 구간 전체가 한 청크 안에 있음 (overlap 으로 여러 청크에 있으면 그 청크들)
- split: 발췌 구간이 여러 청크에 나뉘어 빈틈없이 덮임 → 겹치는 청크 전부
- partial: 겹치는 청크는 있지만 발췌 일부가 어느 청크에도 없음 (버린 조각 등) → 겹치는 청크, 검수 필요
- missing: 발췌가 원문 절에 없거나 겹치는 청크가 없음 → 정답 없음
"""

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from etl.chunking import chunk_spans, normalize_ws
from etl.dataset import PROJECT_ROOT
from shared.core.models import Chunk, EvalItem

QUESTION_SET = PROJECT_ROOT / "question_set.jsonl"

_CITE = re.compile(r"\[(\d+)\][^\[]*?(doc_[0-9a-f]{16}) / 섹션 (\d+)")
_EXCERPT = re.compile(r"\[(\d+)\]\s*(.*?)(?=\n\s*\[\d+\]|\Z)", re.S)

Sections = dict[
    tuple[str, str], str
]  # (원본 doc_…, 절 번호) → 절 원문 (etl.dataset.Dataset.sections)


def excerpt_core(excerpt: str) -> str:
    """발췌에서 잘린 첫 단어와 마지막 단어, 말줄임표를 뺀 확실한 본문."""
    words = normalize_ws(excerpt.rstrip("…").rstrip()).split(" ")
    return " ".join(words[1:-1])


def map_citations(
    raw: dict[str, Any], chunks: list[Chunk], sections: Sections
) -> list[dict[str, Any]]:
    """골든셋 한 문항의 인용마다 매핑 결과 (status, chunk_ids, 발췌 위치 정보)."""
    excerpts = {int(n): t for n, t in _EXCERPT.findall(raw.get("원문 근거 발췌", ""))}
    results: list[dict[str, Any]] = []
    for number, doc, section in _CITE.findall(raw.get("근거 문서·절·리비전", "")):
        in_section = sorted(
            (
                c
                for c in chunks
                if c.source_ids.get("team_document_id") == doc
                and c.source_ids.get("section_index") == section
            ),
            key=lambda c: c.position,
        )
        core = excerpt_core(excerpts.get(int(number), ""))
        located = _locate(core, sections.get((doc, section)), in_section)
        results.append(
            {
                "question_id": raw["query_id"],
                "citation": int(number),
                "team_document_id": doc,
                "section_index": int(section),
                "section_chunks": len(in_section),
                "excerpt_core_chars": len(core),
                **located,
            }
        )
    return results


def _locate(core: str, section_text: str | None, chunks: list[Chunk]) -> dict[str, Any]:
    if not core or section_text is None:
        return {"status": "missing", "reason": "발췌 또는 원문 절 없음", "chunk_ids": []}
    source = normalize_ws(section_text)
    pos = source.find(core)
    if pos == -1:
        return {"status": "missing", "reason": "발췌가 원문 절에 없음", "chunk_ids": []}
    lo, hi = pos, pos + len(core)
    spans = chunk_spans(section_text, [c.text for c in chunks])
    hits = [(c, s) for c, s in zip(chunks, spans, strict=True) if s and s[0] < hi and lo < s[1]]
    if not hits:
        return {"status": "missing", "reason": "발췌 구간과 겹치는 청크 없음", "chunk_ids": []}
    # 발췌 안에서 각 청크가 덮는 구간 [시작, 끝) — 근거 회수율(evidence_recall)이 이걸로 계산한다
    pieces = [
        {"chunk_id": c.chunk_id, "start": max(s0, lo) - lo, "end": min(s1, hi) - lo}
        for c, (s0, s1) in hits
    ]
    evidence = {"excerpt": core, "pieces": pieces}
    whole = [c.chunk_id for c, s in hits if s[0] <= lo and hi <= s[1]]
    if whole:
        return {"status": "contained", "chunk_ids": whole, **evidence}
    covered: set[int] = set()
    for _, (s0, s1) in hits:
        covered.update(range(max(s0, lo), min(s1, hi)))
    # 청크 사이 경계의 공백 한 칸은 splitter 가 떼어 내므로 빈틈으로 세지 않는다
    gap = sum(1 for i in range(lo, hi) if i not in covered and source[i] != " ")
    ids = [c.chunk_id for c, _ in hits]
    if gap == 0:
        return {"status": "split", "chunk_ids": ids, **evidence}
    return {"status": "partial", "uncovered_chars": gap, "chunk_ids": ids, **evidence}


def gold_chunk_ids(raw: dict[str, Any], chunks: list[Chunk], sections: Sections) -> list[str]:
    """골든셋 한 문항의 근거 → 정답 chunk_id 목록 (순서 유지, 중복 없음). missing 인용은 빠진다."""
    gold: list[str] = []
    for citation in map_citations(raw, chunks, sections):
        for chunk_id in citation["chunk_ids"]:
            if chunk_id not in gold:
                gold.append(chunk_id)
    return gold


def to_eval_item(
    raw: dict[str, Any], chunks: list[Chunk], sections: Sections, index_version: str
) -> EvalItem:
    answerable = bool(raw["answerable"])
    facts = raw.get("채점 핵심 사실") or ""
    return EvalItem(
        question_id=raw["query_id"],
        question=raw["question"],
        answerable=answerable,
        expected_answer=raw.get("expected_answer"),
        key_facts=[f.strip() for f in facts.split("/") if f.strip()],
        difficulty=raw.get("난이도"),
        split=None,  # dev · test 구분은 eval/*.json 이 정해지면 채운다
        gold_chunk_ids=gold_chunk_ids(raw, chunks, sections) if answerable else [],
        gold_index_version=index_version if answerable else None,
    )


def read_question_set(path: Path = QUESTION_SET) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_eval_items(
    chunks: list[Chunk], sections: Sections, index_version: str, path: Path = QUESTION_SET
) -> list[EvalItem]:
    return [to_eval_item(raw, chunks, sections, index_version) for raw in read_question_set(path)]


def check_ready(items: Iterable[EvalItem], index_version: str) -> None:
    """평가 전 확인 (#14 리뷰 의견): 답변 가능 문항의 정답 키와 기준 색인이 이번 실행과 맞아야 한다.

    하나라도 어긋나면 평가를 시작하지 않는다. 조용히 Hit@k 가 0 으로 섞이는 것을 막기 위해서다.
    """
    problems = []
    for item in items:
        if not item.answerable:
            continue
        if not item.gold_chunk_ids:
            problems.append(f"{item.question_id}: 정답 키 없음")
        elif item.gold_index_version is None:
            problems.append(f"{item.question_id}: 정답 키 기준 색인(gold_index_version) 없음")
        elif item.gold_index_version != index_version:
            problems.append(
                f"{item.question_id}: 정답 키는 {item.gold_index_version} 기준, 이번 색인은 {index_version}"
            )
    if problems:
        raise ValueError("평가를 시작할 수 없습니다:\n" + "\n".join(problems))
