"""평가셋의 '문장 정답'을 한 청킹 결과의 청크 ID 로 바꾼다 (청크 어댑터).

평가셋은 정답을 청크 번호가 아니라 문서 · 절 · 원문 발췌로 적는다. 청킹이 정해지면 이 스크립트로
그 청킹 결과에서 발췌를 담은 청크를 찾아 gold_chunk_ids(EvalItem 칸)를 채운다.

    uv run python scripts/attach_chunks.py --chunks <청킹 결과>.jsonl --index-version <색인 버전>
    uv run python scripts/attach_chunks.py --chunks x.jsonl --id-field id --text-field content --doc-field meta.doc

입력
    --eval      평가셋 (기본 tmp/eval_set.json — build_eval_set.py 출력. .json 배열 · .jsonl 둘 다 읽음)
    --chunks    한 줄에 청크 하나인 jsonl
    --id-field / --text-field / --doc-field   청크의 ID · 본문 · 문서 ID 칸 (기본 chunk_id · text · doc_id,
                점으로 중첩 칸 지정. --doc-field "" 이면 문서를 따지지 않는다).
                문서 ID 는 데이터셋 doc_id(wp-… · tt…-wikipedia) · 팀 문서 ID(doc_…) 둘 다 알아본다.
    --index-version  gold_index_version 에 적을 값 (청킹 결과의 index_version)

찾는 방법
    발췌와 청크를 영문자 · 숫자만 남기고 소문자로 바꿔 비교한다.
    청크 하나가 발췌를 통째로 담거나, 발췌가 청크 경계에 걸리면 그 청크들이 발췌를 빈틈없이 덮을 때 찾은 것으로 본다.

출력 (--out, 기본 tmp/eval_set_<청킹 파일 이름>.json — 입력과 같은 형식)
    각 문항에 gold_chunk_ids · gold_index_version 을 채운다. answerable=false 문항은 비운다.
    화면에 발췌를 찾은 비율과 못 찾은 근거 목록을 낸다. 못 찾은 문항은 gold_chunk_ids 가 비어
    EvalItem.hit_at 이 오류를 내므로 조용히 0 점으로 섞이지 않는다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIN_OVERLAP = 30  # 경계에 걸린 발췌를 맞출 때 쓰는 기준 조각 길이 (정규화 후 글자 수)


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def get_field(obj, dotted: str):
    for k in dotted.split("."):
        obj = obj.get(k) if isinstance(obj, dict) else None
    return obj


def load_rows(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix == ".json":
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def save_rows(path: Path, rows: list[dict]) -> None:
    if path.suffix == ".json":
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        path.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
        )


class ChunkIndex:
    """청킹 결과를 문서별로 묶어 둔 것. build_eval_set.py 도 기준선 청킹의 경계 걸침 판정에 쓴다."""

    def __init__(self, raw: list[dict], id_f="chunk_id", text_f="text", doc_f="doc_id"):
        self.use_doc = bool(doc_f) and bool(raw) and get_field(raw[0], doc_f) is not None
        self.ids = [str(get_field(c, id_f)) for c in raw]
        self.texts = [norm(get_field(c, text_f)) for c in raw]
        self.by_doc = defaultdict(list)
        for i, c in enumerate(raw):
            self.by_doc[str(get_field(c, doc_f)) if self.use_doc else ""].append(i)
        self.every = list(range(len(raw)))

    def locate(self, e: dict) -> tuple[str, list[str]]:
        """근거 한 항목 → ('통째' | '걸침' | '없음', 청크 ID 목록)"""
        cand = (
            self.by_doc.get(e.get("doc_id") or "", [])
            + self.by_doc.get(e.get("team_doc_id") or "", [])
            if self.use_doc
            else self.every
        )
        ex = norm(e.get("발췌"))
        full = [self.ids[i] for i in cand if ex in self.texts[i]]
        if full:
            return "통째", full
        hit = find_chunks(ex, cand, self.ids, self.texts)
        return ("걸침", hit) if hit else ("없음", [])


def find_chunks(ex: str, cand: list[int], ids: list[str], texts: list[str]) -> list[str]:
    """정규화한 발췌 ex 를 담은 청크 ID. 경계에 걸리면 발췌를 빈틈없이 덮는 청크들을 돌려준다."""
    full = [ids[i] for i in cand if ex in texts[i]]
    if full or len(ex) < 2 * MIN_OVERLAP:
        return full
    # 발췌 안의 기준 조각을 청크에서 찾아 청크를 발췌 위에 정렬하고, 겹치는 부분이 같은지 본다
    L = MIN_OVERLAP
    anchors = sorted(set(range(0, len(ex) - L + 1, L // 2)) | {len(ex) - L})
    spans, used = [], []
    for i in cand:
        t = texts[i]
        for k in anchors:
            p = t.find(ex[k : k + L])
            if p == -1:
                continue
            o = k - p
            lo, hi = max(0, o), min(len(ex), o + len(t))
            if ex[lo:hi] == t[lo - o : hi - o]:
                spans.append((lo, hi))
                used.append(ids[i])
                break
    if not spans:
        return []
    spans.sort()
    start, reach = spans[0]
    for a, b in spans[1:]:
        if a > reach:
            return []
        reach = max(reach, b)
    # 맨 앞 · 맨 끝의 L자 미만 조각은 기준 조각을 못 세워 남을 수 있다
    return used if start <= L and len(ex) - reach <= L else []


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--eval", default=str(ROOT / "tmp" / "eval_set.json"))
    ap.add_argument("--chunks", required=True)
    ap.add_argument("--id-field", default="chunk_id")
    ap.add_argument("--text-field", default="text")
    ap.add_argument("--doc-field", default="doc_id")
    ap.add_argument("--index-version", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    eval_path, chunk_path = Path(args.eval), Path(args.chunks)
    for p in (eval_path, chunk_path):
        if not p.exists():
            sys.exit(f"{p} 없음")
    rows = load_rows(eval_path)
    raw = [
        json.loads(line)
        for line in chunk_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if (
        not raw
        or get_field(raw[0], args.id_field) is None
        or get_field(raw[0], args.text_field) is None
    ):
        sys.exit(
            f"{chunk_path}: 첫 줄에 {args.id_field!r} · {args.text_field!r} 칸이 없다. 있는 칸: {sorted(raw[0]) if raw else []}"
        )
    idx = ChunkIndex(raw, args.id_field, args.text_field, args.doc_field)
    if args.doc_field and not idx.use_doc:
        print(f"! 칸 {args.doc_field!r} 이 없어 문서를 따지지 않고 찾는다")
    ids = idx.ids

    n_ev = n_found = 0
    missing = []
    for r in rows:
        gold = []
        if r.get("answerable", True):
            for e in r.get("근거") or []:
                if not e.get("발췌") or e.get("doc_id") == "works.jsonl":
                    continue
                _, hit = idx.locate(e)
                n_ev += 1
                n_found += bool(hit)
                if not hit:
                    missing.append(
                        (
                            r["query_id"],
                            f"{e.get('doc_id')}#{e.get('section_index')} {e.get('section_path')}",
                        )
                    )
                gold += hit
        r["gold_chunk_ids"] = list(dict.fromkeys(gold))
        r["gold_index_version"] = args.index_version

    out = (
        Path(args.out)
        if args.out
        else ROOT
        / "tmp"
        / f"eval_set_{chunk_path.parent.name if chunk_path.stem == 'chunks' else chunk_path.stem}{eval_path.suffix}"
    )
    save_rows(out, rows)
    no_gold = [
        r["query_id"]
        for r in rows
        if r.get("answerable", True)
        and r.get("eval_track") != "answer_only"
        and r.get("근거")
        and not r["gold_chunk_ids"]
    ]
    print(f"청크 {len(ids)}개 · {chunk_path}")
    print(
        f"발췌 찾은 비율 {n_found / n_ev:.1%} ({n_found}/{n_ev})"
        if n_ev
        else "발췌가 있는 근거가 없다"
    )
    print(f"gold_chunk_ids 가 빈 검색 문항 {len(no_gold)}개: {', '.join(no_gold) or '없음'}")
    if missing:
        print("못 찾은 근거:")
        for q, where in missing:
            print(f"  {q:<12} {where}")
    print(f"→ {out}")


if __name__ == "__main__":
    main()
