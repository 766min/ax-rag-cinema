"""평가 문항 읽기: question_set.jsonl(골든셋) → EvalItem.

골든셋은 근거를 '원본 doc_… / 섹션 N' 과 원문 발췌로 적어 두었다. 같은 절의 청크 중 발췌가 들어 있는
청크를 정답 키(gold_chunk_ids)로 고르고, 못 찾으면 그 절의 청크 전체를 정답 키로 쓴다.
"""

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from build.handoff import PROJECT_ROOT, find_chunk_ids
from shared.core.models import Chunk, EvalItem

QUESTION_SET = PROJECT_ROOT / "question_set.jsonl"

_CITE = re.compile(r"\[(\d+)\][^\[]*?(doc_[0-9a-f]{16}) / 섹션 (\d+)")
_EXCERPT = re.compile(r"\[(\d+)\]\s*(.*?)(?=\n\s*\[\d+\]|\Z)", re.S)
_FRAGMENT = slice(10, 50)  # 발췌는 문장 중간에서 시작하고 끝이 … 로 잘려 있어 가운데 40자로 찾는다


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def gold_chunk_ids(raw: dict[str, Any], chunks: list[Chunk]) -> list[str]:
    """골든셋 한 문항의 근거 → 정답 chunk_id 목록 (순서 유지, 중복 없음)."""
    texts = {c.chunk_id: _squash(c.text) for c in chunks}
    excerpts = {int(n): _squash(t) for n, t in _EXCERPT.findall(raw.get("원문 근거 발췌", ""))}
    gold: list[str] = []
    for number, doc, section in _CITE.findall(raw.get("근거 문서·절·리비전", "")):
        in_section = find_chunk_ids(chunks, doc, int(section))
        fragment = excerpts.get(int(number), "")[_FRAGMENT]
        matched = [cid for cid in in_section if fragment and fragment in texts[cid]]
        for chunk_id in matched or in_section:
            if chunk_id not in gold:
                gold.append(chunk_id)
    return gold


def to_eval_item(raw: dict[str, Any], chunks: list[Chunk], index_version: str) -> EvalItem:
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
        gold_chunk_ids=gold_chunk_ids(raw, chunks) if answerable else [],
        gold_index_version=index_version if answerable else None,
    )


def load_eval_items(
    chunks: list[Chunk], index_version: str, path: Path = QUESTION_SET
) -> list[EvalItem]:
    with path.open(encoding="utf-8") as f:
        raws = [json.loads(line) for line in f if line.strip()]
    return [to_eval_item(raw, chunks, index_version) for raw in raws]


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
