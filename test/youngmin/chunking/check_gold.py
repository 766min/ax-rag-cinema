"""평가셋(question_set.jsonl)의 근거를 만든 청크에 잇는다 (정답 키 후보, 원칙 8).

실행 (저장소 루트에서, build_chunks · build_cards · build_connections 다음에):
    PYTHONPATH=src uv run python test/youngmin/chunking/check_gold.py
    PYTHONPATH=src uv run python test/youngmin/chunking/check_gold.py --name min200

평가셋은 근거를 "원본 문서 ID(doc_…) / 절 번호 / 발췌" 로 적었다. 청크의 source_ids 로 같은 절을 찾고,
발췌 글자가 들어 있는 청크를 정답 키 후보로 낸다. 청킹 기준을 바꾸면 번호가 밀리므로 색인마다 다시 돌린다.
후보일 뿐이고 확정은 평가셋 담당이 한다.

출력: data/chunks_{name}/gold_candidates.jsonl (근거별 대조) · eval_items.jsonl (팀 EvalItem 형식)
"""

import argparse
import json
import re
from pathlib import Path
from typing import Any

from build_chunks import index_version, read_index

from shared.core.models import EvalItem

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DATASET = ROOT / "data" / "cineverse-dataset-schema"

EVIDENCE = re.compile(r"\[(\d+)\] .+?\n(doc_[0-9a-f]+) / 섹션 (\d+): .+?\nrevision (\d+)")
EXCERPT = re.compile(r"\[(\d+)\] (.+?)(?=\n\n\[\d+\] |\Z)", re.DOTALL)


def squash(text: str) -> str:
    """글자와 숫자만 남긴다. 발췌는 글자를 되살리기 전 것이고 청크는 되살린 글자라서다"""
    return re.sub(r"[^0-9a-z]+", "", text.lower())


def probes(excerpt: str, width: int = 32) -> list[str]:
    """발췌의 처음 · 가운데 · 끝 토막. 발췌가 문단 둘에 걸치면 토막마다 다른 청크에 맞는다"""
    body = squash(excerpt)
    if len(body) <= width:
        return [body] if body else []
    middle = (len(body) - width) // 2
    return [body[:width], body[middle : middle + width], body[-width:]]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def check(name: str = "base") -> dict[str, Any]:
    out = HERE.parent / "data" / f"chunks_{name}"
    chunks = load_jsonl(out / "chunks.jsonl")
    documents = load_jsonl(DATASET / "documents.jsonl")
    # 정답 키는 색인 전체(본문 청크 + 작품 카드 + 연결 카드)의 버전에 묶는다. 적재한 DB 의 버전과 같아야 Hit@k 를 잰다
    version = index_version(read_index(name))
    revid = {d["source_ids"]["team_document_id"]: d["revid"] for d in documents}
    by_section: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for chunk in chunks:
        ids = chunk["source_ids"]
        by_section.setdefault((ids["team_document_id"], ids["team_section_index"]), []).append(
            chunk
        )

    rows = []
    items: list[EvalItem] = []
    for q in load_jsonl(ROOT / "question_set.jsonl"):
        excerpts = dict(EXCERPT.findall(q.get("원문 근거 발췌") or ""))
        evidence = []
        gold: list[str] = []
        for number, doc, section, revision in EVIDENCE.findall(q["근거 문서·절·리비전"]):
            candidates = by_section.get((doc, section), [])
            needles = probes(excerpts.get(number, ""))
            hits = [
                c["chunk_id"]
                for c in candidates
                if any(needle in squash(c["text"]) for needle in needles)
            ]
            evidence.append(
                {
                    "n": int(number),
                    "team_document_id": doc,
                    "section_index": int(section),
                    "section_in_chunks": bool(candidates),
                    "revision_matches": revid.get(doc) == int(revision),
                    "chunk_ids": hits,
                }
            )
            gold += [cid for cid in hits if cid not in gold]
        gold = gold if q["answerable"] else []
        rows.append(
            {
                "query_id": q["query_id"],
                "answerable": q["answerable"],
                "question": q["question"],
                "evidence": evidence,
                "gold_chunk_ids_candidate": gold,
            }
        )
        items.append(
            EvalItem(
                question_id=q["query_id"],
                question=q["question"],
                answerable=q["answerable"],
                expected_answer=q.get("expected_answer"),
                key_facts=[
                    f.strip() for f in (q.get("채점 핵심 사실") or "").split("/") if f.strip()
                ],
                difficulty=q.get("난이도"),
                split=None,
                gold_chunk_ids=gold,
                gold_index_version=version,
            )
        )

    with (out / "gold_candidates.jsonl").open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    with (out / "eval_items.jsonl").open("w", encoding="utf-8") as f:
        for item in items:
            f.write(item.model_dump_json() + "\n")
    found = [e for r in rows for e in r["evidence"]]
    return {
        "name": name,
        "index_version": version,
        "questions": len(rows),
        "evidence_items": len(found),
        "section_in_chunks": sum(e["section_in_chunks"] for e in found),
        "excerpt_matched_to_chunk": sum(bool(e["chunk_ids"]) for e in found),
        "revision_mismatch": sum(not e["revision_matches"] for e in found),
        "gold_chunks": sum(len(r["gold_chunk_ids_candidate"]) for r in rows),
        "answerable_without_candidate": [
            r["query_id"] for r in rows if r["answerable"] and not r["gold_chunk_ids_candidate"]
        ],
        "unmatched": [
            f"{r['query_id']}[{e['n']}] {e['team_document_id']} 섹션 {e['section_index']}"
            for r in rows
            for e in r["evidence"]
            if not e["chunk_ids"]
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="평가셋 근거를 청크에 잇는다")
    parser.add_argument("--name", default="base", help="청크 폴더 이름 (data/chunks_{name})")
    print(json.dumps(check(parser.parse_args().name), ensure_ascii=False, indent=2))
