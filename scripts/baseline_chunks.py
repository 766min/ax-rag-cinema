"""기준선 청킹: 최대 800자 · 문장 경계 · 겹침 없음 (팀 인사이트 취합 2026-11-25 의 비교 기준선).

평가셋의 `경계 걸침` 칸(정답 발췌가 이 청킹에서 청크 두 개 이상에 걸치는가)을 판정하는 데 쓴다.
공통 데이터셋 sections.jsonl 만 있으면 메모리에서 바로 만든다(파일이 없어도 평가셋 병합이 돈다).
모든 절을 자른다 — 어느 절을 색인에 넣을지는 최종 코드가 정할 일이라, 여기서는 경계만 본다.

    uv run python scripts/baseline_chunks.py                 # tmp/chunks_baseline800.jsonl 로 저장
    uv run python scripts/baseline_chunks.py --max-chars 1000

자르는 법: 절 text 를 문장으로 나누고, 이어 붙여 max-chars 를 넘기 직전에 끊는다.
한 문장이 max-chars 보다 길면 그 문장 하나를 청크로 둔다(문장 중간에서 자르지 않는다).
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "cineverse-dataset-schema"
ABBR = re.compile(r"(\b[A-Z]|\b(?:Dr|Mr|Mrs|Ms|St|Jr|Sr|Lt|Col|Gen|Sgt|No|vs|Vol))\.$")


def sentences(text: str) -> list[str]:
    """문장 나누기. 'Samuel L.' · 'Dr.' · 'S.H.I.E.L.D.' 뒤에서는 끊지 않는다."""
    out: list[str] = []
    for para in (text or "").split("\n"):
        sents: list[str] = []
        for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\"“(])", para.strip()):
            if sents and ABBR.search(sents[-1]):
                sents[-1] = f"{sents[-1]} {s}"
            elif s.strip():
                sents.append(s)
        out += sents
    return out


def chunk_section(doc_id: str, idx: int, text: str, max_chars: int = 800) -> list[dict]:
    chunks, cur = [], ""
    for s in sentences(text):
        if cur and len(cur) + 1 + len(s) > max_chars:
            chunks.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}" if cur else s
    if cur:
        chunks.append(cur)
    return [
        {"chunk_id": f"{doc_id}#{idx}-c{n:02d}", "doc_id": doc_id, "section_index": idx, "text": t}
        for n, t in enumerate(chunks, start=1)
    ]


def build(max_chars: int = 800, sections_path: Path = DATA / "sections.jsonl") -> list[dict]:
    out = []
    with open(sections_path, encoding="utf-8") as f:
        for line in f:
            s = json.loads(line)
            out += chunk_section(s["doc_id"], s["section_index"], s.get("text") or "", max_chars)
            # 'Episodes' 절의 회차 요약은 회차 경계를 넘지 않게 회차마다 따로 자른다
            for n, ep in enumerate(s.get("episode_entries") or [], start=1):
                pieces = chunk_section(
                    s["doc_id"], s["section_index"], ep.get("summary") or "", max_chars
                )
                for k, c in enumerate(pieces, start=1):
                    c["chunk_id"] = f"{s['doc_id']}#{s['section_index']}-e{n:02d}-c{k:02d}"
                    out.append(c)
    return out


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--max-chars", type=int, default=800)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    chunks = build(args.max_chars)
    out = Path(args.out or ROOT / "tmp" / f"chunks_baseline{args.max_chars}.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    lens = sorted(len(c["text"]) for c in chunks)
    print(
        f"청크 {len(chunks)}개 · 중앙값 {lens[len(lens) // 2]}자 · {args.max_chars}자 초과(긴 한 문장) "
        f"{sum(1 for n in lens if n > args.max_chars)}개 → {out}"
    )


if __name__ == "__main__":
    main()
