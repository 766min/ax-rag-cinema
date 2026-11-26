"""공통 데이터셋의 작품 표 → 연결 카드 (팀 Chunk, section = connection).

실행 (저장소 루트에서, build_cards.py 다음에):
    PYTHONPATH=src:test/youngmin/chunking uv run python test/youngmin/chunking/build_connections.py

연결 카드는 "이 작품 앞뒤에 무엇이 있나" 를 한 청크에 모은다 (architecture.md §3).
    - 개봉 순서의 앞뒤 영화   (Disney+ 「Marvel Movies In Order」)
    - 타임라인의 앞뒤 작품     (Marvel.com 「MCU Complete Timeline」)
    - 선행작 후보             (먼저 나온 작품 중 주요 인물이 겹치는 것, Wikipedia 출연진 절)
줄마다 출처를 괄호로 붙인다. 선행작은 출연진이 겹치는 정도로 고른 **후보**이고 사람이 확인한 정답이 아니다.
본문 청크와 같은 색인 규칙을 따른다 — 정식 작품 · 개봉 · 1차 수집인 작품에만 만든다.

입력: data/cineverse-dataset-schema/  (저장소 루트)
출력: test/youngmin/data/cards/connections.jsonl · connections_report.json
"""

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any

from build_cards import CANON_LABEL, kind_of
from build_chunks import (
    DATASET,
    HERE,
    content_hash,
    display_name,
    load_jsonl,
    shown_year,
    work_header,
)

from shared.core.models import Chunk, Document, SourceId, Work
from shared.core.schemas import Section

OUT = HERE.parent / "data" / "cards"
TIMELINE_URL = "https://www.marvel.com/articles/movies/mcu-timeline-order-disney-plus"
RELEASE_URL = "https://www.disneyplus.com/explore/articles/marvel-movies-in-order"
RELEASE_ASOF = "2026-11-04"
TIMELINE_ASOF = "2026-10-22"
PREREQUISITE_LIMIT = 5
# 정보 상자의 개봉일 · 방영 시작일 틀 ({{Film date|2008|4|14|…}} · {{Start date|2021|6|9}})
FIRST_DATE = re.compile(
    r"\{\{\s*(?:Film date|Start date)\s*\|\s*(\d{4})\s*\|\s*(\d{1,2})\s*\|\s*(\d{1,2})", re.I
)


@dataclass
class Prerequisite:
    work_id: str
    shared: list[str]  # 겹치는 인물 (문서 제목에서 "(Marvel Cinematic Universe)" 를 뗀 이름)


def first_date(infobox: dict[str, Any] | None) -> date | None:
    params = (infobox or {}).get("params") or {}
    for key in ("released", "first_aired"):
        match = FIRST_DATE.search((params.get(key) or {}).get("raw") or "")
        if match:
            return date(*(int(part) for part in match.groups()))
    return None


def gap(earlier: dict[str, Any], later: dict[str, Any]) -> int | None:
    """earlier 가 later 보다 먼저 나왔으면 그 거리, 아니면 None.

    둘 다 가진 값 중 가장 자세한 것으로 잰다 — 개봉일 → 개봉 순번 → 연도.
    같은 해에 나온 두 작품처럼 앞뒤를 가릴 수 없으면 선행작으로 치지 않는다.
    """
    for key in ("date", "release_order", "year"):
        a, b = earlier.get(key), later.get(key)
        if a is None or b is None:
            continue
        if key == "date":
            a, b = a.toordinal(), b.toordinal()
        return b - a if a < b else None
    return None


def cast_characters(sections: list[dict[str, Any]], character_titles: set[str]) -> set[str]:
    """출연진 절이 링크한 MCU 인물 문서 (Cast · Cast and characters)"""
    return {
        link["target_title"]
        for section in sections
        if section["section_kind"] == "cast"
        for link in section["links"]
        if link.get("target_title") in character_titles
    }


def prerequisite_candidates(
    work_id: str, facts: dict[str, dict[str, Any]], limit: int = PREREQUISITE_LIMIT
) -> list[Prerequisite]:
    """먼저 나온 작품 중 출연진의 MCU 인물이 겹치는 것. 많이 겹칠수록, 가까울수록 앞에 둔다.

    시즌과 그 시리즈 전체 행은 서로 선행작으로 치지 않는다 (시리즈 전체가 그 시즌을 품고 있다).
    같은 시리즈의 앞 시즌은 선행작이다.
    """
    mine = facts[work_id]["cast"]
    parent = facts[work_id].get("parent")
    found: list[tuple[int, int, Prerequisite]] = []
    for other_id, other in facts.items():
        if other_id in (work_id, parent) or other.get("parent") == work_id or not mine:
            continue
        distance = gap(other, facts[work_id])
        shared = sorted(display_name(t) for t in mine & other["cast"])
        if distance is not None and shared:
            found.append((-len(shared), distance, Prerequisite(other_id, shared)))
    found.sort(key=lambda item: item[:2])
    return [item[2] for item in found[:limit]]


def side(previous: str | None, following: str | None) -> str:
    return "; ".join(f"{k} = {v}" for k, v in (("previous", previous), ("next", following)) if v)


def make_connection_card(
    work: Work,
    meta: dict[str, Any],
    infobox: dict[str, Any] | None,
    document: Document | None,
    *,
    titles: dict[str, str],
    by_timeline: dict[int, str],
    by_release: dict[int, str],
    upcoming: set[str],
    prerequisites: list[Prerequisite],
) -> Chunk | None:
    """연결할 것이 하나도 없으면 None. 줄마다 출처를 괄호로 붙인다"""
    kind = kind_of(work, document)
    year = shown_year(work, infobox)
    parts = [f"{year} {kind}" if year else kind] + ([f"Phase {work.phase}"] if work.phase else [])
    lines: list[str] = []

    def name(work_id: str | None) -> str | None:
        if not work_id:
            return None
        return titles[work_id] + (" (not released yet)" if work_id in upcoming else "")

    order = meta.get("release_order")
    if order is not None:
        release = side(name(by_release.get(order - 1)), name(by_release.get(order + 1)))
        if release:
            lines.append(
                f"Release order: {release}. (Disney+ Marvel Movies In Order, {RELEASE_ASOF})"
            )
    if work.timeline_order is not None:
        t = work.timeline_order
        timeline = side(name(by_timeline.get(t - 1)), name(by_timeline.get(t + 1)))
        if timeline:
            label = CANON_LABEL["marvel_timeline"]
            lines.append(f"Timeline order: {timeline}. ({label}, {TIMELINE_ASOF})")
    if prerequisites:
        items = []
        for p in prerequisites:
            names = ", ".join(p.shared[:3]) + (", …" if len(p.shared) > 3 else "")
            items.append(f"{titles[p.work_id]} ({names})")
        lines.append(
            "Earlier works sharing main characters (candidates): "
            + "; ".join(items)
            + ". (Wikipedia cast sections)"
        )
    if not lines:
        return None

    text = "\n".join([f"[Connection card] {work.title_original} ({' · '.join(parts)})", *lines])
    header = work_header(work.title_original, year, kind, work.phase, work.timeline_order)
    chunk_id = f"{work.work_id}-connection"
    # 대표 출처: 타임라인 줄이 있으면 Marvel.com, 개봉 순서 줄만 있으면 Disney+, 선행작만 있으면 그 작품 문서
    if work.timeline_order is not None and any(line.startswith("Timeline") for line in lines):
        url, revid = TIMELINE_URL, None
    elif any(line.startswith("Release") for line in lines):
        url, revid = RELEASE_URL, None
    else:
        url = document.source_url if document else TIMELINE_URL
        revid = document.revid if document else None
    return Chunk(
        chunk_id=chunk_id,
        doc_id=work.work_id,
        work_id=work.work_id,
        source_id=SourceId.WIKIPEDIA_EN,  # 카드와 같은 사정 — 작품 표에 맞는 출처 값이 없다
        section=Section.CONNECTION,
        position=1,
        total=1,
        text=text,
        header=header,
        title_ko=work.title_ko,
        title_original=work.title_original,
        source_url=url,
        revid=revid,
        entities=list(dict.fromkeys(n for p in prerequisites for n in p.shared)),
        flags=["prerequisite_candidates"] if prerequisites else [],
        content_hash=content_hash(chunk_id, header, text),
        source_ids={
            "timeline_url": TIMELINE_URL if work.timeline_order is not None else None,
            "release_url": RELEASE_URL if order is not None else None,
            "cast_doc_id": document.doc_id if prerequisites and document else None,
        },
    )


def build() -> dict[str, Any]:
    works = [Work.model_validate(row) for row in load_jsonl(DATASET / "works.jsonl")]
    documents = {
        row["doc_id"]: Document.model_validate(row)
        for row in load_jsonl(DATASET / "documents.jsonl")
    }
    works_meta = {row["work_id"]: row for row in load_jsonl(DATASET / "extra" / "works_meta.jsonl")}
    docs_meta = {
        row["doc_id"]: row for row in load_jsonl(DATASET / "extra" / "documents_meta.jsonl")
    }
    character_titles = {
        meta["title"]
        for doc_id, meta in docs_meta.items()
        if documents[doc_id].doc_type == "character" and meta["scope_status"] == "mcu"
    }
    sections: dict[str, list[dict[str, Any]]] = {}
    for row in load_jsonl(DATASET / "sections.jsonl"):
        sections.setdefault(row["doc_id"], []).append(row)

    def infobox(work_id: str) -> dict[str, Any] | None:
        doc_id = works_meta[work_id]["doc_id"]
        return docs_meta[doc_id]["infobox"] if doc_id else None

    titles = {w.work_id: w.title_original for w in works}
    upcoming = {w.work_id for w in works if works_meta[w.work_id]["status"] != "released"}
    by_timeline = {w.timeline_order: w.work_id for w in works if w.timeline_order is not None}
    by_release = {
        works_meta[w.work_id]["release_order"]: w.work_id
        for w in works
        if works_meta[w.work_id].get("release_order") is not None
    }
    # 본문 청크와 같은 색인 규칙 (architecture.md §3)
    selected = [
        w
        for w in works
        if w.is_canon
        and works_meta[w.work_id]["status"] == "released"
        and works_meta[w.work_id]["collect_wave"] == "1"
    ]
    facts = {
        w.work_id: {
            "date": first_date(infobox(w.work_id)),
            "release_order": works_meta[w.work_id].get("release_order"),
            "year": shown_year(w, infobox(w.work_id)),
            "parent": w.parent_work_id,
            "cast": cast_characters(
                sections.get(works_meta[w.work_id]["doc_id"] or "", []), character_titles
            ),
        }
        for w in selected
    }

    cards: list[Chunk] = []
    skipped: list[str] = []
    for work in selected:
        doc_id = works_meta[work.work_id]["doc_id"]
        card = make_connection_card(
            work,
            works_meta[work.work_id],
            infobox(work.work_id),
            documents.get(doc_id or ""),
            titles=titles,
            by_timeline=by_timeline,
            by_release=by_release,
            upcoming=upcoming,
            prerequisites=prerequisite_candidates(work.work_id, facts),
        )
        if card is None:
            skipped.append(f"{work.work_id} {work.title_original}")
        else:
            cards.append(card)
    if any("None" in card.text for card in cards):
        raise ValueError("연결 카드에 빈 값(None)이 글자로 들어갔습니다")

    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "connections.jsonl").open("w", encoding="utf-8") as f:
        for card in cards:
            f.write(card.model_dump_json() + "\n")
    report = {
        "selected_works": len(selected),
        "connection_cards": len(cards),
        "lines": dict(
            Counter(line.split(":")[0] for card in cards for line in card.text.split("\n")[1:])
        ),
        "with_prerequisites": sum("prerequisite_candidates" in card.flags for card in cards),
        "works_with_cast": sum(bool(f["cast"]) for f in facts.values()),
        "works_with_date": sum(f["date"] is not None for f in facts.values()),
        "skipped_no_connection": skipped,
    }
    (OUT / "connections_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))
