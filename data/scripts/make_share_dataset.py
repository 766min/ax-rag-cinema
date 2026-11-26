"""팀 공유용 묶음을 만든다 — integrated 층에서 원문(raw/)의 글자를 되살린 판.

restore_text.py 와 같은 규칙으로 글자를 되살리되, 바뀐 절만 따로 두지 않고
documents · source_plots · characters 세 파일에 직접 반영한다. 절의 글자가 바뀌면
글자 위치(start_char · end_char)와 문서 해시도 함께 바뀌므로 같이 다시 계산한다.
ID(doc_ · cnt_ · plt_ · chr_)와 판 번호는 건드리지 않는다. 바뀌지 않은 줄은 원래 줄 그대로 쓴다.

실행 (저장소 루트에서):
    PYTHONPATH=src:test/youngmin/cineverse-dataset/code/src \
      uv run --with mwparserfromhell==0.7.2 python test/youngmin/chunking/make_share_dataset.py

출력: test/youngmin/data/share/cineverse-dataset-restored/  +  같은 이름의 zip (--zip 으로 위치 지정)
"""

import argparse
import hashlib
import json
import shutil
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from restore_text import APOSTROPHE, DATASET, finish, restore_templates

HERE = Path(__file__).resolve().parent
SOURCE = DATASET / "integrated"
NAME = "cineverse-dataset-restored"
OUT = HERE.parent / "data" / "share" / NAME
COPIED = ["works.jsonl", "aliases.jsonl", "disney_release_order.csv"]
BASIS_NOTE = "; text-bearing templates restored from the same revision wikitext (2026-11-25)"


def finish_all(value: Any) -> Any:
    """중첩된 값 안의 모든 문자열에서 자리 표시 글자를 아포스트로피로 바꾼다"""
    if isinstance(value, str):
        return finish(value)
    if isinstance(value, list):
        return [finish_all(v) for v in value]
    if isinstance(value, dict):
        return {k: finish_all(v) for k, v in value.items()}
    return value


def read_lines(path: Path) -> list[tuple[str, dict[str, Any]]]:
    """(원래 줄, 읽은 값). 바뀌지 않은 줄을 원래 글자 그대로 다시 쓰기 위해 줄도 함께 둔다"""
    with path.open(encoding="utf-8") as f:
        return [(line.rstrip("\n"), json.loads(line)) for line in f if line.strip()]


def write_lines(path: Path, rows: list[str]) -> None:
    path.write_text("".join(row + "\n" for row in rows), encoding="utf-8")


def dump(row: dict[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False)


def section_summary(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """수집 코드가 인물 레코드의 sections 칸을 만드는 식 그대로 (글자가 있는 절의 위치 목록)"""
    return [
        {
            "section_path": s["section_path"],
            "section_kind": s["section_kind"],
            "start_char": s["start_char"],
            "end_char": s["end_char"],
        }
        for s in sections
        if s["text"]
    ]


def lead_text(sections: list[dict[str, Any]]) -> str:
    return next((s["text"] for s in sections if s["section_path"] == ["(lead)"]), "")


def linked_titles(section: dict[str, Any]) -> list[str]:
    return sorted({link["target_title"] for link in section["links"]})


def restore_documents(report: dict[str, Any]) -> tuple[list[str], dict[str, tuple[Any, Any]]]:
    """문서 파일을 되살린다. 돌려주는 것: 쓸 줄들, 바뀐 문서의 {document_id: (옛 절, 새 절)}"""
    from cineverse_ingest.mw.wikitext import parse_wikitext  # 수집 묶음의 정제 함수

    raw_files = {tuple(p.stem.split("_")): p for p in (DATASET / "raw").glob("*/*.json")}
    lines: list[str] = []
    changed: dict[str, tuple[Any, Any]] = {}
    totals: Counter[str] = Counter()
    for line, doc in read_lines(SOURCE / "documents.jsonl"):
        path = raw_files[(str(doc["pageid"]), str(doc["revision_id"]))]
        page = json.loads(path.read_text(encoding="utf-8"))["page"]
        wikitext = page["revisions"][0]["slots"]["main"]["content"]
        base = parse_wikitext(wikitext)
        # 되살리기 전의 원문을 같은 함수로 정제하면 받은 파일과 똑같아야 한다. 아니면 정제 함수가 다른 것이다
        if base["sections"] != doc["sections"] or base["text_sha256"] != doc["text_sha256"]:
            raise ValueError(f"정제 함수가 받은 파일을 재현하지 못합니다: {doc['title']}")
        restored, counts = restore_templates(wikitext)
        parsed = parse_wikitext(restored) if counts else base
        sections = finish_all(parsed["sections"])
        if not counts or sections == doc["sections"]:
            lines.append(line)
            continue
        if len(sections) != len(doc["sections"]):
            raise ValueError(f"절 수가 달라졌습니다: {doc['title']}")
        full = finish(parsed["text"])
        changed[doc["document_id"]] = (doc["sections"], sections)
        totals += counts
        report["sections_changed"] += sum(
            old["text"] != new["text"] for old, new in zip(doc["sections"], sections, strict=True)
        )
        doc = {
            **doc,
            "sections": sections,
            "text_sha256": hashlib.sha256(full.encode("utf-8")).hexdigest(),
            "text_length": len(full),
            "text_basis": doc["text_basis"] + BASIS_NOTE,
            "text_restored": dict(counts),
        }
        lines.append(dump(doc))
    report["documents"] = len(lines)
    report["documents_changed"] = len(changed)
    report["templates_restored"] = dict(totals)
    return lines, changed


def restore_plots(changed: dict[str, tuple[Any, Any]], report: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for line, row in read_lines(SOURCE / "source_plots.jsonl"):
        if row["document_id"] not in changed:
            lines.append(line)
            continue
        old_sections, new_sections = changed[row["document_id"]]
        new_row = None
        if row["start_char"] is not None:  # 절 줄거리 — 글자 위치로 절을 찾는다
            for old, new in zip(old_sections, new_sections, strict=True):
                if (old["start_char"], old["text"]) == (row["start_char"], row["text"]):
                    new_row = {
                        **row,
                        "section_path": new["section_path"],
                        "text": new["text"],
                        "start_char": new["start_char"],
                        "end_char": new["end_char"],
                        "linked_titles": linked_titles(new),
                    }
                    break
        else:  # 에피소드 요약 — 절 안의 같은 자리 에피소드를 찾는다
            for old, new in zip(old_sections, new_sections, strict=True):
                for old_ep, new_ep in zip(
                    old["episode_entries"], new["episode_entries"], strict=True
                ):
                    if (old_ep.get("summary"), old_ep.get("title")) == (
                        row["text"],
                        row["episode_title"],
                    ):
                        new_row = {
                            **row,
                            "text": new_ep["summary"],
                            "section_path": new["section_path"],
                        }
                        break
                if new_row:
                    break
        if new_row is None:
            raise ValueError(f"줄거리의 절을 찾지 못했습니다: {row['plot_id']}")
        if new_row == row:
            lines.append(line)
        else:
            report["plots_changed"] += 1
            lines.append(dump(new_row))
    report["plots"] = len(lines)
    return lines


def restore_characters(changed: dict[str, tuple[Any, Any]], report: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for line, row in read_lines(SOURCE / "characters.jsonl"):
        if row["document_id"] not in changed:
            lines.append(line)
            continue
        old_sections, new_sections = changed[row["document_id"]]
        new_row = None
        if row["sections"] == section_summary(old_sections):  # 문서 전체가 인물인 레코드
            if row["introduction"] != lead_text(old_sections):
                raise ValueError(f"인물 소개가 첫머리와 다릅니다: {row['character_id']}")
            new_row = {
                **row,
                "introduction": lead_text(new_sections),
                "sections": section_summary(new_sections),
            }
        elif len(row["sections"]) == 1:  # 목록 문서의 한 절에서 나온 후보
            for old, new in zip(old_sections, new_sections, strict=True):
                if section_summary([old]) == row["sections"]:
                    if row["introduction"] != old["text"][:1500]:
                        raise ValueError(f"인물 소개가 절과 다릅니다: {row['character_id']}")
                    new_row = {
                        **row,
                        "introduction": new["text"][:1500],
                        "sections": section_summary([new]),
                    }
                    break
        if new_row is None:
            raise ValueError(f"인물의 절을 찾지 못했습니다: {row['character_id']}")
        if new_row == row:
            lines.append(line)
        else:
            report["characters_changed"] += 1
            lines.append(dump(new_row))
    report["characters"] = len(lines)
    return lines


def verify(out: Path) -> dict[str, int]:
    """쓴 파일을 다시 읽어 서로 맞는지 본다. 하나라도 어긋나면 멈춘다"""
    docs = {d["document_id"]: d for _, d in read_lines(out / "documents.jsonl")}
    checked: Counter[str] = Counter()
    for doc in docs.values():
        texts = [s["text"] for s in doc["sections"] if s["text"]]
        full = "\n\n".join(texts)
        assert hashlib.sha256(full.encode("utf-8")).hexdigest() == doc["text_sha256"], doc["title"]
        assert len(full) == doc["text_length"], doc["title"]
        for s in doc["sections"]:
            if s["text"]:
                assert full[s["start_char"] : s["end_char"]] == s["text"], doc["title"]
                checked["section_offsets"] += 1
            assert APOSTROPHE not in json.dumps(s, ensure_ascii=False), doc["title"]
        checked["documents"] += 1
    for _, plot in read_lines(out / "source_plots.jsonl"):
        doc = docs[plot["document_id"]]
        if plot["start_char"] is not None:
            full = "\n\n".join(s["text"] for s in doc["sections"] if s["text"])
            assert full[plot["start_char"] : plot["end_char"]] == plot["text"], plot["plot_id"]
        else:
            summaries = [e.get("summary") for s in doc["sections"] for e in s["episode_entries"]]
            assert plot["text"] in summaries, plot["plot_id"]
        checked["plots"] += 1
    for _, ch in read_lines(out / "characters.jsonl"):
        doc = docs[ch["document_id"]]
        full = "\n\n".join(s["text"] for s in doc["sections"] if s["text"])
        first = ch["sections"][0] if ch["sections"] else None
        if first is not None:
            piece = full[first["start_char"] : first["end_char"]]
            assert ch["introduction"] in (piece, piece[:1500], lead_text(doc["sections"])), ch[
                "character_id"
            ]
        checked["characters"] += 1
    return dict(checked)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", type=Path, default=OUT.parent / f"{NAME}-2026-11-25.zip")
    args = parser.parse_args()

    report: dict[str, Any] = Counter()
    doc_lines, changed = restore_documents(report)
    plot_lines = restore_plots(changed, report)
    character_lines = restore_characters(changed, report)

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    write_lines(OUT / "documents.jsonl", doc_lines)
    write_lines(OUT / "source_plots.jsonl", plot_lines)
    write_lines(OUT / "characters.jsonl", character_lines)
    for name in COPIED:
        shutil.copyfile(SOURCE / name, OUT / name)
    (OUT / "scripts").mkdir()
    for script in ("restore_text.py", "make_share_dataset.py"):
        shutil.copyfile(HERE / script, OUT / "scripts" / script)
    shutil.copyfile(HERE / "share_README.md", OUT / "README.md")

    report = {**report, "verified": verify(OUT)}
    (OUT / "RESTORE_REPORT.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    files = sorted(p for p in OUT.rglob("*") if p.is_file())
    sums = "".join(
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(OUT).as_posix()}\n"
        for p in files
    )
    (OUT / "SHA256SUMS.txt").write_text(sums, encoding="utf-8")

    args.zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(p for p in OUT.rglob("*") if p.is_file()):
            z.write(p, f"{NAME}/{p.relative_to(OUT).as_posix()}")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"zip: {args.zip}  ({args.zip.stat().st_size / 1_000_000:.1f} MB)")


if __name__ == "__main__":
    main()
