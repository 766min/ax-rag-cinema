"""raw/ 의 원문에서 "글자를 담은 틀" 을 되살려 정제본 절을 다시 만든다.

정제본은 틀을 통째로 지운다. 대부분은 각주 · 꾸밈이라 괜찮지만, 본문 글자를 담은 틀까지 지워져
`{{Visible anchor|Peotor}} is an orangutan` 이 `is an orangutan` 이 된다. 이 스크립트는 그런 틀만
글자로 바꾼 뒤, 수집 묶음이 쓴 것과 같은 함수(parse_wikitext)로 다시 정제한다.

실행 (저장소 루트에서):
    PYTHONPATH=src:test/youngmin/cineverse-dataset/code/src \
      uv run --with mwparserfromhell==0.7.2 python test/youngmin/chunking/restore_text.py

mwparserfromhell 은 이 실행에만 임시로 쓰고 팀 pyproject.toml 에는 넣지 않는다.
출력: test/youngmin/data/restored/sections.jsonl (바뀐 절만) · report.json
"""

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
DATASET = HERE.parent / "cineverse-dataset" / "data"
OUT = HERE.parent / "data" / "restored"

# 아포스트로피를 바로 넣으면 앞의 기울임 표시(작은따옴표 둘)와 붙어 굵게 표시로 읽히고 절 구분이 깨진다.
# 그래서 정제가 끝날 때까지 자리 표시 글자로 두었다가 마지막에 바꾼다
APOSTROPHE = ""

# (이름, 틀 모양, 바꿀 글자). 틀 안에 다른 틀이 든 경우는 건드리지 않는다
TEXT_TEMPLATES: list[tuple[str, re.Pattern[str], str]] = [
    (
        "visible anchor",
        re.compile(
            r"\{\{\s*(?:visible anchor|vanchor)\s*\|\s*([^{}|]+?)\s*(?:\|[^{}]*)?\}\}", re.I
        ),
        r"\1",
    ),
    ("'s", re.compile(r"\{\{\s*'s\s*\}\}"), f"{APOSTROPHE}s"),
    ("'", re.compile(r"\{\{\s*'\s*\}\}"), APOSTROPHE),
    ("nbsp", re.compile(r"\{\{\s*nbsp\s*\}\}", re.I), " "),
    (
        "spaced dash",
        re.compile(r"\{\{\s*(?:snd|spnd|spaced ndash|spaced en dash)\s*\}\}", re.I),
        " – ",
    ),
    ("en dash", re.compile(r"\{\{\s*(?:ndash|en dash)\s*\}\}", re.I), "–"),
    ("em dash", re.compile(r"\{\{\s*(?:mdash|em dash)\s*\}\}", re.I), "—"),
    ("abbr", re.compile(r"\{\{\s*abbr\s*\|\s*([^{}|]+?)\s*\|[^{}]*\}\}", re.I), r"\1"),
]

# 주어 없이 시작하는 문단 (틀이 지워진 흔적)
SUBJECTLESS = re.compile(r"^(is|are|was|were|\(|The (is|are|was|were|\())\b")


def restore_templates(wikitext: str) -> tuple[str, Counter[str]]:
    """글자를 담은 틀을 그 글자로 바꾼다. 무엇을 몇 번 바꿨는지도 돌려준다.

    아포스트로피는 자리 표시 글자로 남는다. 정제한 뒤 finish() 로 바꾼다.
    """
    counts: Counter[str] = Counter()
    for name, pattern, replacement in TEXT_TEMPLATES:
        wikitext, n = pattern.subn(replacement, wikitext)
        if n:
            counts[name] += n
    return wikitext, counts


def finish(text: str) -> str:
    """정제가 끝난 글자에서 자리 표시 글자를 아포스트로피로 바꾼다"""
    return text.replace(APOSTROPHE, "'")


def subjectless(text: str) -> int:
    return sum(bool(SUBJECTLESS.match(line.strip())) for line in text.split("\n") if line.strip())


def documents_path() -> Path:
    """handoff 에서 받은 통합 층(integrated/)이 있으면 그것을, 없으면 수집 묶음의 정제본을 읽는다.

    두 파일의 절 본문과 판 번호는 850개 모두 같다 (2026-11-25 확인).
    """
    integrated = DATASET / "integrated" / "documents.jsonl"
    return integrated if integrated.exists() else DATASET / "processed" / "documents.jsonl"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def main() -> None:
    from cineverse_ingest.mw.wikitext import parse_wikitext  # 수집 묶음의 정제 함수

    raw_files = {tuple(path.stem.split("_")): path for path in (DATASET / "raw").glob("*/*.json")}
    documents = load_jsonl(documents_path())
    rows: list[dict[str, Any]] = []
    totals: Counter[str] = Counter()
    report: dict[str, Any] = {
        "documents": len(documents),
        "documents_with_text_templates": 0,
        "structure_changed": [],  # 절 수가 달라져 건너뛴 문서
        "documents_changed": 0,
        "sections_changed": 0,
        "subjectless_before": 0,
        "subjectless_after": 0,
    }
    for doc in documents:
        path = raw_files[(str(doc["pageid"]), str(doc["revision_id"]))]
        page = json.loads(path.read_text(encoding="utf-8"))["page"]
        restored, counts = restore_templates(page["revisions"][0]["slots"]["main"]["content"])
        if not counts:
            continue
        report["documents_with_text_templates"] += 1
        old = doc["sections"]
        new = parse_wikitext(restored)["sections"]
        if len(new) != len(old):
            report["structure_changed"].append(doc["title"])
            continue
        totals += counts
        changed = 0
        for before, after in zip(old, new, strict=True):
            text = finish(after["text"])
            if text == before["text"]:
                continue
            changed += 1
            report["subjectless_before"] += subjectless(before["text"])
            report["subjectless_after"] += subjectless(text)
            rows.append(
                {
                    "document_id": doc["document_id"],
                    "section_index": before["section_index"],
                    "section_path": [finish(part) for part in after["section_path"]],
                    "text": text,
                    "links": [
                        {**link, "anchor_text": finish(link["anchor_text"])}
                        for link in after["links"]
                    ],
                }
            )
        if changed:
            report["documents_changed"] += 1
            report["sections_changed"] += changed

    report["templates_restored"] = dict(totals)
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "sections.jsonl").open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    (OUT / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
