"""Codex 가 만든 평가 문항을 검증한다 (평가셋 확장 4단계). 청킹 결과 없이 공통 데이터셋만 본다.

    uv run python scripts/check_eval_gen.py tmp/eval_gen_*.jsonl
    uv run python scripts/check_eval_gen.py tmp/eval_gen_plot.jsonl --out tmp/eval_gen_plot_passed.jsonl

입력 형식은 docs/experiments/eval_set_codex_prompts.md 의 [공통 규칙] 출력 형식이다.

검사 (오류 = 탈락, 경고 = 통과하되 '검증경고' 칸에 남김)
    오류  필수 칸 · 정해진 값 목록 · eval_track 과 질문유형의 짝
          채점 핵심 사실 3~4개 (함정은 첫 사실이 '전제 바로잡기:')
          근거 doc_id · section_index 가 실제로 있음, section_path 일치
          발췌가 그 절 text 에 그대로 있음 (공백만 정리해서 비교) · works.jsonl 근거는 값이 일치
          query_id 중복 · 기존 문항과 거의 같은 질문 · variant_of 등 참조 문항이 없음
    경고  발췌 길이 40~300자 밖 · 팀 섹션(team_section) 밖 절 · 근거 문서 수와 난이도가 안 맞음
          기존 문항과 비슷한 질문

통과한 문항에는 아래를 채운다.
    근거          표준 모양 {doc_id, team_doc_id, section_index, section_path, doc_type, team_section, 발췌}
    근거 문서·절·리비전 · 원문 근거 발췌   팀 question_set.jsonl 과 같은 글 형식
    qid · question_ko · category            scripts/eval_retrieval.py 호환 칸
정답 청크(gold · primary_gold)는 붙이지 않는다. 청킹마다 scripts/attach_chunks.py 로 붙인다.

출력
    --out     (기본 tmp/eval_gen_passed.jsonl)  통과 문항 → build_eval_set.py --extra 로 합친다
    --failed  (기본 tmp/eval_gen_failed.jsonl)  탈락 문항 + '검증오류' 칸
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "cineverse-dataset-schema"

LOCS = ["줄거리", "시놉시스", "인물", "세계관·사물", "메타", "추천·가치관", "문서에 없음"]
LEVELS = ["초보", "중수", "고수", "함정"]
TYPES = ["사실", "관계", "인과", "시간선", "비교·가치관", "추천", "근거 검증"]
ANSWER_ONLY = {"추천", "비교·가치관"}
METHODS = {"①", "②", "③", "④", "⑤"}
REQUIRED = [
    "query_id",
    "question",
    "expected_answer",
    "answerable",
    "난이도",
    "채점 핵심 사실",
    "근거위치",
    "질문유형",
    "eval_track",
    "근거",
]
DUP_ERR, DUP_WARN = 0.6, 0.45


def read_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def section_text(s: dict) -> str:
    """절 본문 + 에피소드 요약. 시리즈 · 시즌 문서의 'Episodes' 절은 text 가 비어 있고
    회차 요약이 episode_entries[].summary 에만 있다 (최종 색인은 이것을 줄거리 청크로 만든다)."""
    parts = [s.get("text") or ""]
    parts += [e.get("summary") or "" for e in s.get("episode_entries") or []]
    return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------- 코퍼스


class Corpus:
    """공통 데이터셋만 읽는다. 청킹 결과는 읽지 않는다 (청크는 attach_chunks.py 가 붙인다)."""

    def __init__(self):
        self.docs = {d["doc_id"]: d for d in read_jsonl(DATA / "documents.jsonl")}
        self.sections = {
            (s["doc_id"], s["section_index"]): s for s in read_jsonl(DATA / "sections.jsonl")
        }
        self.sec_ws = {k: ws(section_text(s)) for k, s in self.sections.items()}
        self.works = {w["work_id"]: w for w in read_jsonl(DATA / "works.jsonl")}

    def title(self, doc_id: str) -> str:
        url = self.docs[doc_id].get("source_ids", {}).get("article_url") or ""
        return unquote(url.rsplit("/", 1)[-1]).replace("_", " ") or doc_id

    def team_doc_id(self, doc_id: str) -> str | None:
        return (self.docs.get(doc_id) or {}).get("source_ids", {}).get("team_document_id")

    def evidence(self, doc_id: str, section_index: int, excerpt: str) -> dict:
        """근거 한 항목의 표준 모양. 본체에는 청크 ID 를 넣지 않는다."""
        sec = self.sections[(doc_id, section_index)]
        return {
            "doc_id": doc_id,
            "team_doc_id": self.team_doc_id(doc_id),
            "section_index": section_index,
            "section_path": " > ".join(sec["section_path"]),
            "doc_type": self.docs[doc_id]["doc_type"],
            "team_section": sec.get("team_section"),
            "발췌": excerpt,
            "발췌_확인": bool(ws(excerpt)) and ws(excerpt) in self.sec_ws[(doc_id, section_index)],
        }


# ---------------------------------------------------------------- 중복


def norm_q(q: str) -> str:
    q = re.sub(r"\((스포|스포일러)\)", "", q or "")
    return re.sub(r"[\s《》<>「」『』\"'“”‘’?!.,·:()\-]", "", q)


def jaccard(a: str, b: str) -> float:
    x = {a[i : i + 2] for i in range(len(a) - 1)}
    y = {b[i : i + 2] for i in range(len(b) - 1)}
    return len(x & y) / len(x | y) if x and y else 0.0


# ---------------------------------------------------------------- 문항 하나 검사


def check_row(r: dict, corpus: Corpus) -> tuple[list[str], list[str]]:
    err, warn = [], []
    for k in REQUIRED:
        if k not in r or r[k] is None or r[k] == "":
            if not (k == "근거" and r.get("근거위치") in ("문서에 없음", "추천·가치관")):
                err.append(f"필수 칸 없음: {k}")
    if err:
        return err, warn
    if r["근거위치"] not in LOCS:
        err.append(f"근거위치 값 오류: {r['근거위치']}")
    if r["난이도"] not in LEVELS:
        err.append(f"난이도 값 오류: {r['난이도']}")
    if r["질문유형"] not in TYPES:
        err.append(f"질문유형 값 오류: {r['질문유형']}")
    want_track = "answer_only" if r["질문유형"] in ANSWER_ONLY else "retrieval"
    if r["eval_track"] != want_track:
        err.append(f"eval_track 은 {want_track} 이어야 함 (질문유형 {r['질문유형']})")
    if r.get("늘리기방법") and r["늘리기방법"] not in METHODS:
        warn.append(f"늘리기방법 값 이상: {r['늘리기방법']}")
    if r["난이도"] == "함정" and r["질문유형"] != "근거 검증":
        warn.append("함정인데 질문유형이 근거 검증이 아님")

    facts = [f.strip() for f in str(r["채점 핵심 사실"]).split(" / ") if f.strip()]
    if not r.get("variant_of") and not 3 <= len(facts) <= 4:
        err.append(f"채점 핵심 사실 {len(facts)}개 (3~4개여야 함)")
    if r["난이도"] == "함정" and r.get("answerable", True):
        if not facts or not facts[0].startswith("전제 바로잡기"):
            err.append("함정 문항의 첫 핵심 사실이 '전제 바로잡기:' 로 시작하지 않음")
        if not r.get("전제 오류"):
            warn.append("함정 문항에 전제 오류 칸이 비어 있음")

    docs_used = set()
    for i, e in enumerate(r.get("근거") or [], start=1):
        tag = f"근거[{i}]"
        ex = e.get("발췌") or ""
        if e.get("doc_id") == "works.jsonl":
            w = corpus.works.get(e.get("work_id"))
            if not w:
                err.append(f"{tag} works.jsonl 에 work_id 없음: {e.get('work_id')}")
            elif str(w.get(e.get("field"))) != str(ex).strip():
                err.append(
                    f"{tag} works.jsonl {e.get('work_id')}.{e.get('field')} = {w.get(e.get('field'))!r}, 발췌 {ex!r}"
                )
            docs_used.add(e.get("work_id"))
            continue
        doc_id, idx = e.get("doc_id"), e.get("section_index")
        if doc_id not in corpus.docs:
            err.append(f"{tag} doc_id 없음: {doc_id}")
            continue
        sec = corpus.sections.get((doc_id, idx))
        if not sec:
            err.append(f"{tag} {doc_id} 에 section_index {idx} 없음")
            continue
        docs_used.add(doc_id)
        path = " > ".join(sec["section_path"])
        given = e.get("section_path")
        given = " > ".join(given) if isinstance(given, list) else (given or "")
        if ws(given) != ws(path):
            err.append(f"{tag} section_path 불일치: 적은 값 {given!r}, 실제 {path!r}")
        if not ws(ex):
            err.append(f"{tag} 발췌 없음")
        elif ws(ex) not in corpus.sec_ws[(doc_id, idx)]:
            err.append(f"{tag} 발췌가 {doc_id}#{idx} 본문에 그대로 없음: {ex[:50]!r}…")
        if ex and not 40 <= len(ex) <= 300:
            warn.append(f"{tag} 발췌 길이 {len(ex)}자 (40~300 권장)")
        if not sec.get("team_section") and r["eval_track"] == "retrieval":
            warn.append(
                f"{tag} 팀 섹션(plot/synopsis/intro) 밖 절: {doc_id}#{idx} {path} — 청킹에 따라 못 찾을 수 있음"
            )

    if r["eval_track"] == "retrieval" and r.get("answerable", True) and not (r.get("근거") or []):
        err.append("retrieval 문항인데 근거가 없음")
    n = len(docs_used)
    expect = {"초보": (1, 1), "중수": (2, 2), "고수": (3, 99)}.get(r["난이도"])
    if expect and n and not expect[0] <= n <= expect[1]:
        warn.append(f"근거 문서 {n}곳인데 난이도 {r['난이도']}")
    return err, warn


def render_text(r: dict, corpus: Corpus) -> None:
    """팀 question_set.jsonl 과 같은 '근거 문서·절·리비전' · '원문 근거 발췌' 글을 만든다."""
    refs, exs = [], []
    for i, e in enumerate(r.get("근거") or [], start=1):
        if e.get("doc_id") == "works.jsonl":
            refs.append(f"[{i}] works.jsonl\n{e.get('work_id')} / {e.get('field')}")
        else:
            d = corpus.docs[e["doc_id"]]
            sec = corpus.sections[(e["doc_id"], e["section_index"])]
            # 팀 question_set.jsonl 과 같게 팀 문서 ID(doc_…)를 쓴다. 팀 평가 코드(src/evals/items.py)는
            # 이 글에서 doc_… 만 읽는다. 데이터셋 ID(wp-… · tt…-wikipedia)는 구조화된 '근거' 칸에 있다
            shown = e.get("team_doc_id") or corpus.team_doc_id(e["doc_id"]) or e["doc_id"]
            refs.append(
                f"[{i}] {corpus.title(e['doc_id'])}\n{shown} / 섹션 {e['section_index']}: "
                f"{' > '.join(sec['section_path'])}\nrevision {d.get('revid')}\n{d.get('source_url')}"
            )
        exs.append(f"[{i}] {e.get('발췌', '')}")
    r["근거 문서·절·리비전"] = "\n\n".join(refs)
    r["원문 근거 발췌"] = "\n\n".join(exs)


# ---------------------------------------------------------------- main


def expand(patterns: list[str]) -> list[Path]:
    out = []
    for p in patterns:
        hits = glob.glob(p) if any(ch in p for ch in "*?[") else [p]
        out += [Path(h) for h in sorted(hits)]
    skip = {"eval_gen_passed.jsonl", "eval_gen_failed.jsonl"}
    return [
        p
        for p in out
        if p.name not in skip
        and not p.name.endswith("_passed.jsonl")
        and not p.name.endswith("_failed.jsonl")
    ]


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("files", nargs="+", help="생성 파일 (glob 가능)")
    ap.add_argument(
        "--against",
        default=str(ROOT / "tmp" / "eval_merged.jsonl"),
        help="기존 문항 (중복·참조 확인)",
    )
    ap.add_argument("--out", default=str(ROOT / "tmp" / "eval_gen_passed.jsonl"))
    ap.add_argument("--failed", default=str(ROOT / "tmp" / "eval_gen_failed.jsonl"))
    args = ap.parse_args()

    files = expand(args.files)
    if not files:
        sys.exit("검사할 파일이 없다")
    corpus = Corpus()
    rows = []
    for f in files:
        for i, r in enumerate(read_jsonl(f), start=1):
            r["_file"], r["_line"] = f.name, i
            rows.append(r)
    existing = read_jsonl(Path(args.against)) if Path(args.against).exists() else []
    # 이미 --extra 로 합친 뒤 같은 생성 문항을 다시 검사하는 경우, 자기 자신과 겹친다고 보지 않는다
    current = {r.get("query_id") for r in rows}
    existing = [
        r for r in existing if not (r.get("작성자") == "codex" and r["query_id"] in current)
    ]
    known_ids = {r["query_id"] for r in existing}
    seen_q = [(norm_q(r["question"]), r["query_id"]) for r in existing if r.get("question")]
    all_ids = known_ids | {r.get("query_id") for r in rows}
    id_count = Counter(r.get("query_id") for r in rows)

    passed, failed = [], []
    for r in rows:
        err, warn = check_row(r, corpus)
        qid = r.get("query_id")
        if id_count[qid] > 1 or qid in known_ids:
            err.append(f"query_id 중복: {qid}")
        for k in ("variant_of", "분해_of", "참고_문항"):
            if r.get(k) and r[k] not in all_ids:
                (err if k != "참고_문항" else warn).append(f"{k} 가 가리키는 문항 없음: {r[k]}")
        for ref in r.get("엮은_문항") or []:
            if ref not in all_ids:
                err.append(f"엮은_문항 이 가리키는 문항 없음: {ref}")
        if not r.get("variant_of") and r.get("question"):
            nq = norm_q(r["question"])
            best = max(((jaccard(nq, q), oid) for q, oid in seen_q), default=(0.0, None))
            if best[0] >= DUP_ERR:
                err.append(f"기존 문항 {best[1]} 과 거의 같은 질문 (유사도 {best[0]:.2f})")
            elif best[0] >= DUP_WARN:
                warn.append(
                    f"기존 문항 {best[1]} 과 비슷함 (유사도 {best[0]:.2f}) — 같은 사실인지 확인"
                )

        src = f"{r.pop('_file')}:{r.pop('_line')}"
        if err:
            failed.append({**r, "검증오류": err, "검증경고": warn, "검증위치": src})
            continue
        if not r.get("variant_of") and r.get("question"):
            seen_q.append((norm_q(r["question"]), qid))
        # 근거를 표준 모양으로 (청크 ID 는 넣지 않는다. 청크는 attach_chunks.py 가 청킹마다 붙인다)
        r["근거"] = [
            e
            if e.get("doc_id") == "works.jsonl"
            else corpus.evidence(e["doc_id"], e["section_index"], e.get("발췌", ""))
            for e in r.get("근거") or []
        ]
        render_text(r, corpus)
        for k in ("gold", "primary_gold"):
            r.pop(k, None)
        r.update(
            {
                "출처": src.split(":")[0],
                "원본_id": qid,
                "작성자": r.get("작성자") or "codex",
                "분류방식": "생성",
                "검증경고": warn,
                "근거확인": {
                    "문서_찾음": True,
                    "발췌_확인": True,
                    "팀섹션_없는_근거": [
                        f"{e['doc_id']}#{e['section_index']} {e['section_path']}"
                        for e in r["근거"]
                        if e.get("doc_id") != "works.jsonl" and not e["team_section"]
                    ],
                },
                "qid": qid,
                "question_ko": r["question"],
                "category": "unanswerable" if not r.get("answerable", True) else r["근거위치"],
            }
        )
        r.setdefault("question_en", None)
        r.setdefault("variant_of", None)
        passed.append(r)

    for path, data in ((args.out, passed), (args.failed, failed)):
        with open(path, "w", encoding="utf-8") as f:
            for r in data:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"검사 {len(rows)} (파일 {len(files)}개) → 통과 {len(passed)} · 탈락 {len(failed)}")
    print(f"  {args.out}\n  {args.failed}")
    reasons = Counter(re.sub(r"[:(].*", "", e).strip() for r in failed for e in r["검증오류"])
    if reasons:
        print("\n탈락 이유")
        for k, v in reasons.most_common():
            print(f"  {v:3d}  {k}")
    warns = Counter(
        re.sub(r"[:(].*|\d+자.*|\d+곳.*", "", w).strip() for r in passed for w in r["검증경고"]
    )
    if warns:
        print("\n통과했지만 경고")
        for k, v in warns.most_common():
            print(f"  {v:3d}  {k}")
    cells = Counter((r["근거위치"], r["난이도"]) for r in passed if not r.get("variant_of"))
    if cells:
        print("\n통과 문항 칸별 (변형 제외)")
        for (loc, lv), v in sorted(cells.items()):
            print(f"  {loc} · {lv}: {v}")
    print("\n청크 ID 는 붙이지 않았다. 채점할 청킹 결과로 scripts/attach_chunks.py 를 돌린다.")


if __name__ == "__main__":
    main()
