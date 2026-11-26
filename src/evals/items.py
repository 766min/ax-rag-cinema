import json
import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from build.chunks import index_version
from shared.core.models import Chunk, EvalItem

# 근거 [n] 마다: 원본 문서 ID · 절 번호 · 판 번호
EVIDENCE = re.compile(r"\[(\d+)\] .+?\n(doc_[0-9a-f]+) / 섹션 (\d+): .+?\nrevision (\d+)")
# 발췌 [n] 마다: 발췌 글
EXCERPT = re.compile(r"\[(\d+)\] (.+?)(?=\n\n\[\d+\] |\Z)", re.DOTALL)


def squash(text: str) -> str:
    """영문자 · 숫자만 남긴다. 평가셋 발췌는 글자를 되살리기 전 판이라 그대로 찾으면 어긋난다"""
    return re.sub(r"[^0-9a-z]+", "", text.lower())


def probes(excerpt: str, width: int = 32) -> list[str]:
    """발췌의 처음 · 가운데 · 끝 토막 (발췌가 문단 둘에 걸치면 토막마다 다른 청크에 맞는다)"""
    body = squash(excerpt.replace("…", ""))
    if len(body) <= width:
        return [body] if body else []
    middle = (len(body) - width) // 2
    return [body[:width], body[middle : middle + width], body[-width:]]


def read_rows(path: str | Path) -> list[dict[str, Any]]:
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text.startswith("["):
        return list(json.loads(text))
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def is_question_set_row(row: dict[str, Any]) -> bool:
    return "query_id" in row and "근거 문서·절·리비전" in row


def evidence_of(row: dict[str, Any]) -> list[dict[str, Any]]:
    """근거 [n] 마다 원본 문서 ID · 절 번호 · 판 · 발췌"""
    excerpts = dict(EXCERPT.findall(row.get("원문 근거 발췌") or ""))
    return [
        {"n": int(n), "doc": doc, "section": section, "revision": int(rev), "excerpt": excerpts.get(n, "")}
        for n, doc, section, rev in EVIDENCE.findall(row.get("근거 문서·절·리비전") or "")
    ]  # fmt: skip


def map_gold(
    row: dict[str, Any], chunks: Sequence[Chunk]
) -> tuple[list[str], list[dict[str, Any]]]:
    """발췌 토막이 들어 있는 같은 절의 청크 ID 들 (+ 근거별 대조 결과)"""
    # 청크를 (원본 문서 ID, 절 번호) 로 묶어 두면 근거마다 후보를 빨리 찾는다
    by_section: dict[tuple[str | None, str | None], list[Chunk]] = {}
    for c in chunks:
        key = (c.source_ids.get("team_document_id"), c.source_ids.get("team_section_index"))
        by_section.setdefault(key, []).append(c)
    gold: list[str] = []
    report: list[dict[str, Any]] = []
    for ev in evidence_of(row):
        candidates = by_section.get((ev["doc"], ev["section"]), [])
        needles = probes(ev["excerpt"])
        hits = [c.chunk_id for c in candidates if any(n in squash(c.text) for n in needles)]
        whole = squash(ev["excerpt"].replace("…", ""))
        in_one = [c.chunk_id for c in candidates if whole and whole in squash(c.text)]
        report.append({**{k: ev[k] for k in ("n", "doc", "section")}, "section_in_chunks": bool(candidates),
                       "chunk_ids": hits, "whole_excerpt_in_one_chunk": bool(in_one)})  # fmt: skip
        gold += [cid for cid in hits if cid not in gold]
    return gold, report


def to_items(
    rows: Iterable[dict[str, Any]], chunks: Sequence[Chunk]
) -> tuple[list[EvalItem], list[dict[str, Any]]]:
    """평가셋 행 → EvalItem. question_set 형식이면 정답 키를 이 청크 묶음에 잇고 그 색인 버전에 묶는다"""
    version = index_version(chunks)
    items: list[EvalItem] = []
    report: list[dict[str, Any]] = []
    for row in rows:
        if not is_question_set_row(row):
            items.append(EvalItem.model_validate(row))
            continue
        gold, evidence = map_gold(row, chunks)
        gold = gold if row["answerable"] else []
        report.append({"question_id": row["query_id"], "evidence": evidence, "gold": gold})
        items.append(
            EvalItem(
                question_id=row["query_id"],
                question=row["question"],
                answerable=row["answerable"],
                expected_answer=row.get("expected_answer"),
                key_facts=[
                    f.strip() for f in (row.get("채점 핵심 사실") or "").split("/") if f.strip()
                ],
                difficulty=row.get("난이도"),
                split=None,
                gold_chunk_ids=gold,
                gold_index_version=version,
            )
        )
    return items, report


def excerpts_by_question(rows: Iterable[dict[str, Any]]) -> dict[str, list[str]]:
    """문항별 정답 발췌 (채점 · '모델 입력에 정답 문장이 있었나' 에 쓴다. 생성 경로에는 넘기지 않는다)"""
    return {
        r["query_id"]: [e["excerpt"] for e in evidence_of(r)]
        for r in rows
        if is_question_set_row(r)
    }


def check_ready(items: Sequence[EvalItem], version: str) -> None:
    """정답 키의 색인 버전과 지금 색인이 다르면 평가를 시작하지 않는다 (check_ready)"""
    other = {i.gold_index_version for i in items if i.gold_chunk_ids} - {version}
    if other:
        raise ValueError(
            f"정답 키는 색인 {sorted(map(str, other))} 기준인데 지금 색인은 {version} 입니다"
        )


def load_queries(path: str | Path) -> dict[str, str]:
    """저장한 영문 검색어. results/2026-11-25-minji-query-en.json 형식({'queries': {id: {'query_en'}}})
    또는 JSONL({'question_id', 'query_en'})"""
    text = Path(path).read_text(encoding="utf-8").strip()
    if text.startswith("{") and '"queries"' in text[:200]:
        data = json.loads(text)["queries"]
        return {qid: row["query_en"] for qid, row in data.items() if row.get("query_en")}
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    return {r["question_id"]: r["query_en"] for r in rows if r.get("query_en")}
