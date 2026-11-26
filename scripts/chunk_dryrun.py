"""시험용 청킹 (dry-run) — docs/architecture.md §3 규칙을 sections.jsonl 에 적용해 본다.

결과는 tmp/chunks_dryrun.jsonl 에만 쓴다 (results/ 는 건드리지 않는다).

    uv run python scripts/chunk_dryrun.py            # 기본: ID 중복 수정판
    uv run python scripts/chunk_dryrun.py --naive    # 튜토리얼 그대로 (섹션마다 p01 부터)
"""

import json
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path("data/cineverse-dataset-schema")
DATA = ROOT / "sections.jsonl"
OUT = Path("tmp/chunks_dryrun.jsonl")
MIN_LEN = 150
TARGET_SECTIONS = {"plot", "synopsis", "intro"}


def load_jsonl(path: Path):
    with path.open(encoding="utf-8") as f:
        for line in f:
            yield json.loads(line)


def load_sections():
    yield from load_jsonl(DATA)


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in text.split("\n") if p.strip()]


def merge_short(paras: list[str]) -> list[str]:
    out: list[str] = []
    for p in paras:
        if out and len(out[-1]) < MIN_LEN:
            out[-1] = out[-1] + "\n" + p  # 앞 문단이 짧으면 이어 붙임
        else:
            out.append(p)
    if len(out) > 1 and len(out[-1]) < MIN_LEN:  # 마지막이 짧으면 앞에 붙임
        last = out.pop()
        out[-1] += "\n" + last
    return out


def make_header(doc: dict, work: dict | None) -> str:
    """[제목 · 연도 · 형식 · Phase · timeline #] — 임베딩/BM25 용, text 에는 넣지 않는다."""
    if not work:
        return ""
    parts = [
        work.get("title_original") or "",
        str(work.get("year") or ""),
        work.get("format") or "",
    ]
    if work.get("phase"):
        parts.append(f"Phase {work['phase']}")
    if work.get("timeline_order"):
        parts.append(f"timeline #{work['timeline_order']}")
    return "[" + " · ".join(p for p in parts if p) + "]"


def build_chunks(naive: bool) -> list[dict]:
    docs = {d["doc_id"]: d for d in load_jsonl(ROOT / "documents.jsonl")}
    works = {w["work_id"]: w for w in load_jsonl(ROOT / "works.jsonl")}

    chunks: list[dict] = []
    next_pos: Counter = Counter()  # (doc_id, section) -> 지금까지 쓴 번호
    for s in load_sections():
        sec = s["team_section"]
        if sec not in TARGET_SECTIONS:
            continue
        doc = docs[s["doc_id"]]
        work = works.get(doc.get("work_id") or "")
        parts = merge_short(paragraphs(s["text"]))
        key = (s["doc_id"], sec)
        start = 0 if naive else next_pos[key]
        for i, t in enumerate(parts, start=start + 1):
            chunks.append(
                {
                    "chunk_id": f"{s['doc_id']}-{sec}-p{i:02d}",
                    "doc_id": s["doc_id"],
                    "work_id": doc.get("work_id"),
                    "source_id": doc.get("source_id"),
                    "doc_type": doc.get("doc_type"),
                    "section": sec,
                    "section_path": s["section_path"],
                    "position": i,
                    "text": t,
                    "header": make_header(doc, work),
                }
            )
        next_pos[key] = start + len(parts)

    # total 은 (doc, section) 단위 전체 개수
    totals = Counter((c["doc_id"], c["section"]) for c in chunks)
    for c in chunks:
        c["total"] = totals[(c["doc_id"], c["section"])]
    return chunks


def report(chunks: list[dict]) -> dict:
    lens = sorted(len(c["text"]) for c in chunks)
    ids = Counter(c["chunk_id"] for c in chunks)
    dup = {k: v for k, v in ids.items() if v > 1}
    q = statistics.quantiles(lens, n=10)
    stats = {
        "chunks": len(chunks),
        "docs": len({c["doc_id"] for c in chunks}),
        "by_section": dict(Counter(c["section"] for c in chunks)),
        "len_min": lens[0],
        "len_p10": round(q[0]),
        "len_median": round(statistics.median(lens)),
        "len_p90": round(q[-1]),
        "len_max": lens[-1],
        "under_min_len": sum(1 for n in lens if n < MIN_LEN),
        "over_2000": sum(1 for n in lens if n > 2000),
        "dup_ids": len(dup),
        "dup_examples": list(dup)[:5],
        "with_header": sum(1 for c in chunks if c["header"]),
        "empty_text": sum(1 for c in chunks if not c["text"].strip()),
        "header_in_text": sum(1 for c in chunks if c["header"] and c["header"] in c["text"]),
    }
    return stats


if __name__ == "__main__":
    naive = "--naive" in sys.argv
    chunks = build_chunks(naive)
    stats = report(chunks)
    if not naive:
        OUT.parent.mkdir(exist_ok=True)
        with OUT.open("w", encoding="utf-8") as f:
            for c in chunks:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(
        json.dumps({"mode": "naive" if naive else "fixed", **stats}, ensure_ascii=False, indent=2)
    )
