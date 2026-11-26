"""handoff 데이터셋과 평가셋(question_set.jsonl)을 읽는다. 네트워크를 쓰지 않는다."""

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HANDOFF_DIR = PROJECT_ROOT / "data" / "handoff"
QUESTION_SET = PROJECT_ROOT / "question_set.jsonl"
# 민지 추가 함정 문항 (공식 평가셋 아님, 결과도 본 문항과 따로 본다)
TRICK_SET = Path(__file__).resolve().parent / "trick_questions.jsonl"

# question_set.jsonl 의 한국어 칸 이름 (명석님 평가셋)
F_DIFFICULTY = "난이도"
F_KEY_FACTS = "채점 핵심 사실"
F_EVIDENCE = "근거 문서·절·리비전"
F_EXCERPT = "원문 근거 발췌"

_REF_PATTERN = re.compile(r"(doc_[0-9a-f]{16})\s*/\s*섹션\s*(\d+)")
_REVISION_PATTERN = re.compile(r"revision\s+(\d+)")
_EXCERPT_SPLIT = re.compile(r"\[(\d+)\]\s*")


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


@dataclass(frozen=True)
class SectionRef:
    """원문 섹션 하나 (문서 ID + documents.jsonl 의 section_index). 청킹 방식과 무관한 정답 단위"""

    doc_id: str
    section_index: int


@dataclass
class Question:
    """평가 문항 한 개와 정답 근거"""

    question_id: str
    question: str
    answerable: bool
    expected_answer: str
    key_facts: list[str]
    difficulty: str | None
    gold_refs: list[SectionRef]
    gold_revisions: list[int]
    excerpts: dict[int, str] = field(default_factory=dict)  # 근거 번호(1부터) -> 발췌문


def parse_question(row: dict[str, Any]) -> Question:
    evidence = row.get(F_EVIDENCE) or ""
    refs = [SectionRef(d, int(i)) for d, i in _REF_PATTERN.findall(evidence)]
    revisions = [int(r) for r in _REVISION_PATTERN.findall(evidence)]
    parts = _EXCERPT_SPLIT.split(row.get(F_EXCERPT) or "")
    excerpts = {int(parts[i]): parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}
    facts_raw = row.get(F_KEY_FACTS) or ""
    key_facts = [f.strip() for f in facts_raw.split(" / ") if f.strip()]
    return Question(
        question_id=row["query_id"],
        question=row["question"],
        answerable=bool(row["answerable"]),
        expected_answer=row.get("expected_answer") or "",
        key_facts=key_facts,
        difficulty=row.get(F_DIFFICULTY),
        gold_refs=refs,
        gold_revisions=revisions,
        excerpts=excerpts,
    )


def load_questions(path: Path = QUESTION_SET) -> list[Question]:
    return [parse_question(r) for r in read_jsonl(path)]


@dataclass
class Handoff:
    documents: dict[str, dict[str, Any]]  # doc_id -> 문서
    plots: dict[str, dict[str, Any]]  # plot_id -> 줄거리
    chunks: list[dict[str, Any]]  # 학용님 청크 (R0)
    aliases: list[dict[str, Any]]


def load_handoff(base: Path = HANDOFF_DIR) -> Handoff:
    if not base.exists():
        raise FileNotFoundError(
            f"{base} 가 없습니다. cineverse-integrated-dataset-handoff.zip 을 data/ 에 풀어 주세요."
        )
    documents = {d["doc_id"]: d for d in read_jsonl(base / "documents.jsonl")}
    plots = {p["plot_id"]: p for p in read_jsonl(base / "source_plots.jsonl")}
    chunks = list(read_jsonl(base / "chunks.jsonl"))
    aliases = list(read_jsonl(base / "aliases.jsonl"))
    return Handoff(documents=documents, plots=plots, chunks=chunks, aliases=aliases)


def section_of_plot(plot: dict[str, Any], document: dict[str, Any]) -> SectionRef:
    """줄거리 하나가 들어 있는 원문 섹션을 찾는다.

    줄거리(plot)는 글자 위치(start_char)로, 에피소드 요약은 위치가 없어서 섹션 경로로 찾는다.
    둘 다 실패하거나 후보가 여럿이면 에러를 낸다 (채점 기준이 조용히 틀어지지 않게).
    """
    start, end = plot.get("start_char"), plot.get("end_char")
    sections = document["sections"]
    if start is not None and end is not None:
        hits = [
            s
            for s in sections
            if s["start_char"] is not None
            and s["end_char"] is not None
            and s["start_char"] <= start
            and end <= s["end_char"]
        ]
        if hits:
            inner = min(hits, key=lambda s: s["end_char"] - s["start_char"])
            return SectionRef(document["doc_id"], inner["section_index"])
    by_path = [s for s in sections if s["section_path"] == plot["section_path"]]
    if len(by_path) == 1:
        return SectionRef(document["doc_id"], by_path[0]["section_index"])
    raise ValueError(
        f"줄거리 {plot['plot_id']} 의 섹션을 하나로 정할 수 없습니다 (경로 후보 {len(by_path)}개)"
    )
