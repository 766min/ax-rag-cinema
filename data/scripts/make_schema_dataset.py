"""팀 저장소(dev)의 데이터 모델에 맞춘 공유용 데이터셋을 만든다.

handoff 형식의 파일은 팀 모델(src/shared/core/models.py)로 읽히지 않는다 — 작품 ID 가 cnt_…,
문서 ID 가 doc_…, is_canon 이 문자열이기 때문이다. 이 스크립트는 같은 재료를 팀 모델로 다시 짠다.

  works.jsonl      Work      — IMDb 목록 132편 + 공식 순서 자료의 시즌 행 (architecture.md §3)
  documents.jsonl  Document  — 문서 850개. ID 는 {work_id}-wikipedia 또는 wp-{pageid}
  aliases.jsonl    Alias
  sections.jsonl   본문 (글자를 되살린 판). 팀 모델에 본문을 담는 표가 없어 doc_id 로 잇는다
  extra/           팀 모델에 칸이 없는 값 (개봉 순번 · 사가 · 범위 판정 · 정보 상자 · 인물)

먼저 make_share_dataset.py 를 돌려 글자를 되살린 판을 만들어 둔다. 실행 (저장소 루트에서):
    PYTHONPATH=src uv run python test/youngmin/chunking/make_schema_dataset.py
"""

import argparse
import csv
import hashlib
import json
import re
import shutil
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from shared.core.models import Alias, Document, SourceId, Work
from shared.core.schemas import Section

HERE = Path(__file__).resolve().parent
DATASET = HERE.parent / "cineverse-dataset" / "data"
RESTORED = HERE.parent / "data" / "share" / "cineverse-dataset-restored"
OFFICIAL = HERE / "official"
NAME = "cineverse-dataset-schema"
OUT = HERE.parent / "data" / "share" / NAME

ASOF = {"marvel_timeline": "2026-10-22", "disney_release": "2026-11-04"}
DOC_TYPE = {"movie": "film", "tv_series": "series", "tv_season": "season", "tv_episode": "episode"}
WORK_DOC_TYPES = {"film", "series", "season", "episode", "short", "special"}
PLOT_HEADS = {"Plot", "Premise"}
PHASE = {"One": "1", "Two": "2", "Three": "3", "Four": "4", "Five": "5", "Six": "6"}
PHASE_CATEGORY = re.compile(r"Marvel Cinematic Universe: Phase (\w+) ")
SEASON_TITLE = re.compile(r"^(.*) season (\d+)$")
_PREFIX = re.compile(
    r"^(a marvel (studios|television) special presentation\s*[:—-]\s*|"
    r"marvel (studios|television|animation)'s?\s+|"
    r"marvel studios one[- ]shot:\s*|marvel one[- ]shot:\s*)"
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def norm(title: str) -> str:
    """공식 문서의 제목 표기를 맞춘다 (따옴표 · 말줄임표 · 'Marvel Studios'' 머리말)"""
    text = " ".join(title.casefold().replace("’", "'").replace("…", "...").split())
    return _PREFIX.sub("", text)


def to_int(value: Any) -> int | None:
    return int(value) if str(value or "").strip() else None


def phase_of(categories: list[str]) -> str | None:
    """Wikipedia 분류에 적힌 Phase. 여러 Phase 에 걸친 문서(시리즈 전체 등)는 비운다"""
    found = {
        PHASE[match.group(1)]
        for category in categories
        if (match := PHASE_CATEGORY.match(category)) and match.group(1) in PHASE
    }
    return found.pop() if len(found) == 1 else None


def build_work_rows() -> list[dict[str, Any]]:
    """작품 표 (architecture.md §3). 2026-11-23 에 검증한 규칙 그대로다.

    - 한 행 = IMDb 목록의 작품 하나. 타임라인이 시즌별로 적은 시리즈는 시즌마다 한 행 (tt…-S1)
    - timeline_order 는 Marvel.com 타임라인, phase · saga · release_order 는 Disney+ 개봉 순서 문서
    - is_canon 은 두 공식 문서 중 하나에 올라 있는가
    달라진 것 하나: 시즌 행을 가진 시리즈도 시리즈 전체 행을 남긴다 (row_kind = series_parent).
    시즌 행의 parent_work_id 와 시리즈 문서의 work_id 가 가리킬 행이 있어야 하기 때문이다.
    """
    imdb = {row["tconst"]: row for row in load_csv(DATASET / "imdb" / "summary.csv")}
    timeline = load_csv(OFFICIAL / "marvel_timeline_2026-10-22.csv")
    release = {norm(r["title"]): r for r in load_csv(OFFICIAL / "disney_release_2026-11-04.csv")}
    title_map = load_csv(OFFICIAL / "title_map.csv")
    order = {(norm(r["title"]), r["season"]): int(r["order"]) for r in timeline}
    on_timeline = {key[0] for key in order}

    for m in title_map:  # IMDb 목록에 없는 것은 2차 수집분(넷플릭스 시기 시리즈)뿐이어야 한다
        if m["tconst"] in imdb:
            continue
        if m["wave"] != "2":
            raise ValueError(f"IMDb 목록에 없는 작품입니다: {m['official_title']} {m['tconst']}")
        imdb[m["tconst"]] = {
            "tconst": m["tconst"],
            "tmdb_id": "",
            "title_ko": m["official_title"],
            "title_original": m["official_title"],
            "year": "",
            "titleType": "tvSeries",
            "genres": "",
        }
    entries: dict[str, list[dict[str, str]]] = {}
    for m in title_map:
        entries.setdefault(m["tconst"], []).append(m)

    rows: list[dict[str, Any]] = []
    for tconst, row in imdb.items():
        listed = entries.get(tconst, [])
        by_season = [m for m in listed if m["season"]]
        year = to_int(row.get("year"))
        variants: list[tuple[dict[str, str] | None, str]] = [(m, "season") for m in by_season]
        if by_season:
            variants.insert(0, (by_season[0], "series_parent"))
        else:
            variants = [(listed[0] if listed else None, "work")]
        for entry, row_kind in variants:
            season = to_int(entry["season"]) if entry and row_kind == "season" else None
            disney_only = entry is not None and norm(entry["official_title"]) not in on_timeline
            upcoming = year is not None and (year >= 2027 or (year >= 2026 and disney_only))
            rel: dict[str, str] | None = (
                release.get(norm(entry["official_title"])) if entry else None
            )
            timeline_order = None
            if entry and row_kind != "series_parent":
                timeline_order = order.get((norm(entry["official_title"]), entry["season"]))
            suffix = f" season {season}" if season else ""
            rows.append(
                {
                    "work_id": f"{tconst}-S{season}" if season else tconst,
                    "tconst": tconst,
                    "row_kind": row_kind,
                    "title_ko": (row["title_ko"] or row["title_original"])
                    + (f" 시즌 {season}" if season else ""),
                    "title_original": row["title_original"] + suffix,
                    "format": row.get("titleType") or "unknown",
                    "year": year,
                    "season": season,
                    "parent_work_id": tconst if season else None,
                    "phase": rel["phase"] if rel and rel["phase"] else None,
                    "timeline_order": timeline_order,
                    "is_canon": entry is not None,
                    "canon_source": entry["canon_source"] if entry else None,
                    "canon_asof": ASOF.get(entry["canon_source"]) if entry else None,
                    "saga": (rel["saga"] or None) if rel else None,
                    "release_order": int(rel["order"]) if rel and rel["kind"] == "movie" else None,
                    "status": "upcoming" if upcoming else "released",
                    "collect_wave": "none" if (entry is None or upcoming) else entry["wave"],
                    "fandom_page": (entry["wiki_page"] or None) if entry else None,
                    "tmdb_id": to_int(row.get("tmdb_id")),
                    "is_animation": "Animation" in (row.get("genres") or "").split(","),
                }
            )
    return rows


def link_documents(
    work_rows: list[dict[str, Any]], docs: list[dict[str, Any]]
) -> tuple[dict[str, str], dict[str, str], dict[str, dict[str, Any]]]:
    """문서와 작품을 잇는다.

    돌려주는 것: {문서: 그 문서 전체가 다루는 작품}, {문서: 관련 작품(시리즈)}, {문서: 콘텐츠 레코드}
    문서 전체가 한 작품일 때만 주인으로 삼는다. 한 문서 안의 에피소드 줄이나 절로만 대응된 작품은
    그 문서의 주인이 아니다.
    """
    work_ids = {row["work_id"] for row in work_rows}
    seasons = {
        (norm(row["title_original"].rsplit(" season ", 1)[0]), row["season"]): row["work_id"]
        for row in work_rows
        if row["row_kind"] == "season"
    }
    contents = load_jsonl(DATASET / "processed" / "contents.jsonl")
    for name in ("contents_no_mcu_evidence", "contents_non_mcu", "contents_non_screen"):
        contents += load_jsonl(DATASET / "processed" / "excluded" / f"{name}.jsonl")
    by_content = {c["content_id"]: c for c in contents}
    content_of = {c["document_id"]: c for c in contents}

    owner: dict[str, str] = {}
    tconst_of: dict[str, str] = {}  # 콘텐츠 → IMDb ID (문서 전체가 그 작품일 때)
    for row in load_jsonl(DATASET / "mapped" / "primary_contents.jsonl"):
        if row["wikipedia"]["target"] not in ("content", "document"):
            continue
        tconst = row["external_ids"]["imdb_tconst"]
        document_id = row["wikipedia"]["document_id"]
        if document_id in owner or tconst not in work_ids:
            raise ValueError(f"문서의 주인이 둘이거나 작품 행이 없습니다: {document_id} {tconst}")
        owner[document_id] = tconst
        tconst_of[row["content_id"]] = tconst
    for content in contents:  # 시즌 문서 — 제목이 공식 순서 자료의 "시리즈 + 시즌 번호" 와 같을 때
        match = SEASON_TITLE.match(content["title"])
        if content.get("content_type") != "tv_season" or not match:
            continue
        work_id = seasons.get((norm(match.group(1)), int(match.group(2))))
        if not work_id:
            continue
        owner[content["document_id"]] = work_id
        parent = by_content.get(content.get("parent_content_id") or "")
        series = work_id.split("-S")[0]
        if parent and parent["content_id"] not in tconst_of:  # IMDb 목록에 없던 2차 수집 시리즈
            tconst_of[parent["content_id"]] = series
            owner.setdefault(parent["document_id"], series)

    related: dict[str, str] = {}
    known = {d["document_id"] for d in docs}
    for content in contents:  # 에피소드 · 시즌 문서가 속한 시리즈
        seen: set[str] = set()
        node: dict[str, Any] | None = content
        while node and node["content_id"] not in seen:
            seen.add(node["content_id"])
            if node["content_id"] in tconst_of:
                if content["document_id"] in known:
                    related[content["document_id"]] = tconst_of[node["content_id"]]
                break
            node = by_content.get(node.get("parent_content_id") or "")
    for row in load_jsonl(RESTORED / "characters.jsonl"):  # 인물 문서의 범위 판정
        content_of.setdefault(row["document_id"], {"scope_status": row["scope_status"]})
    for row in load_jsonl(DATASET / "processed" / "excluded" / "characters_non_mcu.jsonl"):
        content_of.setdefault(row["document_id"], {"scope_status": row["scope_status"]})
    return owner, related, content_of


def team_section(doc_type: str, section_path: list[str]) -> str | None:
    """이 절이 팀 Section 의 어느 값에 해당하나. 해당하는 값이 없으면 None (본문은 그대로 있다)"""
    if doc_type in WORK_DOC_TYPES:
        if section_path == ["(lead)"]:
            return Section.SYNOPSIS.value
        if section_path[0] in PLOT_HEADS:
            return Section.PLOT.value
    if doc_type == "character" and section_path == ["(lead)"]:
        return Section.INTRO.value
    return None


def build() -> dict[str, Any]:
    docs = load_jsonl(RESTORED / "documents.jsonl")
    work_rows = build_work_rows()
    owner, related, content_of = link_documents(work_rows, docs)
    doc_of_work = {work_id: document_id for document_id, work_id in owner.items()}
    by_document = {d["document_id"]: d for d in docs}
    handoff = {w["source_ids"]["imdb_tconst"]: w for w in load_jsonl(RESTORED / "works.jsonl")}
    mapped = {
        row["external_ids"]["imdb_tconst"]: row
        for row in load_jsonl(DATASET / "mapped" / "primary_contents.jsonl")
    }

    works: list[Work] = []
    works_meta: list[dict[str, Any]] = []
    for row in work_rows:
        document_id = doc_of_work.get(row["work_id"])
        doc = by_document.get(document_id or "")
        match = mapped.get(row["tconst"]) if row["row_kind"] != "season" else None
        content = content_of.get(document_id or "")
        works.append(
            Work(
                work_id=row["work_id"],
                title_ko=row["title_ko"],
                title_original=row["title_original"],
                format=row["format"],
                year=row["year"],
                season=row["season"],
                parent_work_id=row["parent_work_id"],
                phase=row["phase"] or (phase_of(doc["categories"]) if doc else None),
                timeline_order=row["timeline_order"],
                is_canon=row["is_canon"],
                source_ids={
                    "imdb_tconst": row["tconst"],
                    "team_content_id": (content or {}).get("content_id")
                    or (match["content_id"] if match else None),
                    # 문서 전체가 이 작품이 아니어도(에피소드 줄 · 절) 대응된 문서를 적는다
                    "wikipedia_document_id": document_id
                    or (match["wikipedia"]["document_id"] if match else None),
                },
            )
        )
        old = handoff.get(row["tconst"], {}) if row["row_kind"] != "season" else {}
        works_meta.append(
            {
                "work_id": row["work_id"],
                "row_kind": row["row_kind"],
                "doc_id": f"{row['work_id']}-wikipedia" if document_id else None,
                "wikipedia_target": match["wikipedia"]["target"] if match else None,
                "scope_status": match["scope_status"] if match else None,
                **{
                    key: row[key]
                    for key in (
                        "canon_source",
                        "canon_asof",
                        "saga",
                        "release_order",
                        "status",
                        "collect_wave",
                        "fandom_page",
                        "tmdb_id",
                        "is_animation",
                    )
                },
                "wikipedia_page": old.get("wiki_page"),
                "release_date_raw": old.get("release_date"),
            }
        )

    documents: list[Document] = []
    documents_meta: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    doc_id_of: dict[str, str] = {}
    for doc in docs:
        work_id = owner.get(doc["document_id"])
        content = content_of.get(doc["document_id"])
        kind = (content or {}).get("content_type") or "other"
        doc_type = DOC_TYPE.get(kind, kind) if doc["doc_kind"] == "content" else doc["doc_kind"]
        doc_id = f"{work_id}-wikipedia" if work_id else f"wp-{doc['pageid']}"
        doc_id_of[doc["document_id"]] = doc_id
        documents.append(
            Document(
                doc_id=doc_id,
                work_id=work_id,
                source_id=SourceId.WIKIPEDIA_EN,
                source_url=doc["permalink"],  # 판 번호가 박힌 주소
                license="CC BY-SA 4.0",
                revid=doc["revision_id"],
                fetched_at=doc["fetched_at"],
                doc_type=doc_type,
                source_ids={
                    "team_document_id": doc["document_id"],
                    "team_content_id": (content or {}).get("content_id"),
                    "pageid": str(doc["pageid"]),
                    "wikidata_qid": doc.get("wikidata_qid"),
                    "article_url": doc["source_url"],
                },
            )
        )
        documents_meta.append(
            {
                "doc_id": doc_id,
                "title": doc["title"],
                "related_work_id": work_id or related.get(doc["document_id"]),
                "scope_status": (content or {}).get("scope_status"),
                "review_status": doc["review_status"],
                "revision_timestamp": doc["revision_timestamp"],
                "text_sha256": doc["text_sha256"],
                "text_length": doc["text_length"],
                "text_restored": doc.get("text_restored"),
                "categories": doc["categories"],
                "infobox": doc["infobox"],
            }
        )
        for section in doc["sections"]:
            if not section["text"] and not section["episode_entries"]:
                continue
            sections.append(
                {
                    "doc_id": doc_id,
                    "section_index": section["section_index"],
                    "team_section": team_section(doc_type, section["section_path"]),
                    "section_kind": section["section_kind"],
                    "level": section["level"],
                    "section_path": section["section_path"],
                    "text": section["text"],
                    "start_char": section["start_char"],
                    "end_char": section["end_char"],
                    "links": section["links"],
                    "episode_entries": section["episode_entries"],
                }
            )

    aliases = [Alias(**row) for row in load_jsonl(RESTORED / "aliases.jsonl")]
    characters = [
        {"doc_id": doc_id_of[row["document_id"]], **row}
        for row in load_jsonl(RESTORED / "characters.jsonl")
    ]

    if OUT.exists():
        shutil.rmtree(OUT)
    write_jsonl(OUT / "works.jsonl", [w.model_dump(mode="json") for w in works])
    write_jsonl(OUT / "documents.jsonl", [d.model_dump(mode="json") for d in documents])
    write_jsonl(OUT / "aliases.jsonl", [a.model_dump(mode="json") for a in aliases])
    write_jsonl(OUT / "sections.jsonl", sections)
    write_jsonl(OUT / "extra" / "works_meta.jsonl", works_meta)
    write_jsonl(OUT / "extra" / "documents_meta.jsonl", documents_meta)
    write_jsonl(OUT / "extra" / "characters.jsonl", characters)
    for name in (
        "marvel_timeline_2026-10-22.csv",
        "disney_release_2026-11-04.csv",
        "title_map.csv",
    ):
        shutil.copyfile(OFFICIAL / name, OUT / "extra" / name)
    (OUT / "scripts").mkdir()
    for script in ("restore_text.py", "make_share_dataset.py", "make_schema_dataset.py"):
        shutil.copyfile(HERE / script, OUT / "scripts" / script)
    shutil.copyfile(HERE / "validate_schema_dataset.py", OUT / "validate.py")
    shutil.copyfile(HERE / "schema_README.md", OUT / "README.md")

    return {
        "works": len(works),
        "works_by_kind": dict(Counter(row["row_kind"] for row in work_rows)),
        "works_is_canon": sum(w.is_canon is True for w in works),
        "works_with_timeline_order": sum(w.timeline_order is not None for w in works),
        "works_with_phase": sum(w.phase is not None for w in works),
        "works_with_document": sum(m["doc_id"] is not None for m in works_meta),
        "documents": len(documents),
        "documents_by_type": dict(Counter(d.doc_type for d in documents)),
        "documents_with_work": sum(d.work_id is not None for d in documents),
        "documents_related_to_work": sum(m["related_work_id"] is not None for m in documents_meta),
        "aliases": len(aliases),
        "sections": len(sections),
        "sections_by_team_section": dict(Counter(str(s["team_section"]) for s in sections)),
        "characters": len(characters),
    }


def main() -> None:
    from validate_schema_dataset import validate

    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", type=Path, default=OUT.parent / f"{NAME}-2026-11-25.zip")
    args = parser.parse_args()

    report = build()
    report["validated"] = validate(OUT)
    (OUT / "BUILD_REPORT.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    files = sorted(p for p in OUT.rglob("*") if p.is_file())
    (OUT / "SHA256SUMS.txt").write_text(
        "".join(
            f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(OUT).as_posix()}\n"
            for p in files
        ),
        encoding="utf-8",
    )
    args.zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(p for p in OUT.rglob("*") if p.is_file()):
            z.write(p, f"{NAME}/{p.relative_to(OUT).as_posix()}")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"zip: {args.zip}  ({args.zip.stat().st_size / 1_000_000:.1f} MB)")


if __name__ == "__main__":
    main()
