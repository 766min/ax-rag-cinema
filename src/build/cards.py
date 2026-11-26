# 작품 카드와 연결 카드 만드는 파일_영민
# 쓰는 폴더: scripts/build_index.py · scripts/chunk_stats.py · tests/integration

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from build.chunks import (
    Dataset,
    Recipe,
    build_chunks,
    content_hash,
    display_name,
    is_body_work,
    shown_year,
    work_header,
)
from shared.core.models import Chunk, Document, SourceId, Work
from shared.core.schemas import Section

# IMDb 형식(movie · tvSeries …) → 머리말에 쓰는 짧은 종류 이름
KIND = {
    "movie": "film",
    "tvSeries": "series",
    "tvMiniSeries": "series",
    "tvEpisode": "episode",
    "video": "video",
    "short": "short",
    "tvShort": "short",
    "tvSpecial": "special",
    "tvMovie": "special",
}
# 종류 이름 → 카드 문장에 쓰는 영어 명사 (예: series → 'TV series')
NOUN = {
    "film": "film",
    "series": "TV series",
    "season": "season",
    "episode": "TV episode",
    "short": "short film",
    "special": "TV special",
    "video": "video release",
    "title": "title",
}
# 정식 작품의 근거가 된 공식 문서 이름 (카드 문장에 출처로 적는다)
CANON_LABEL = {
    "marvel_timeline": "Marvel.com MCU Complete Timeline",
    "disney_release": "Disney+ Marvel Movies In Order",
}
# 출처 URL · 기준일. 카드의 줄마다 어디서 온 값인지 밝히기 위해 쓴다
IMDB_URL = "https://www.imdb.com/title/{tconst}/"
TIMELINE_URL = "https://www.marvel.com/articles/movies/mcu-timeline-order-disney-plus"
RELEASE_URL = "https://www.disneyplus.com/explore/articles/marvel-movies-in-order"
RELEASE_ASOF = "2026-11-04"
TIMELINE_ASOF = "2026-10-22"
# 연결 카드에 적는 선행작 후보의 최대 개수
PREREQUISITE_LIMIT = 5
FIRST_DATE = re.compile(
    r"\{\{\s*(?:Film date|Start date)\s*\|\s*(\d{4})\s*\|\s*(\d{1,2})\s*\|\s*(\d{1,2})", re.I
)


# [역할] 작품 종류 판정: 시즌이면 season, 문서가 있으면 문서 종류, 없으면 IMDb 형식에서 고른다
def kind_of(work: Work, document: Document | None) -> str:
    if work.season is not None:
        return "season"
    if document is not None and document.doc_type in NOUN:
        return document.doc_type
    return KIND.get(work.format, "title")


# [역할] 카드 첫 문장 '무엇이고 언제 나왔나'. 미개봉작은 개봉 전이라고 적는다
#   예) "Avengers: Endgame is a film released in 2019."
def base_sentence(work: Work, kind: str, year: int | None, status: str, series: str | None) -> str:
    noun = NOUN[kind]
    article = "an" if noun[0].lower() in "aeiou" else "a"
    what = (
        f"{article} {noun} of the TV series {series}"
        if kind == "season" and series
        else f"{article} {noun}"
    )
    if status != "released":
        return f"{work.title_original} is {what} that is not released yet" + (
            f" (planned {year})." if year else "."
        )
    return f"{work.title_original} is {what}" + (f" released in {year}." if year else ".")


# [역할] 정식(MCU 공식) 작품인지와 그 근거 문서 · 기준일을 한 문장으로
def canon_sentence(work: Work, meta: dict[str, Any]) -> str:
    if not work.is_canon:
        return "It is not part of the MCU official timeline."
    source = meta.get("canon_source")
    basis = ", ".join(
        x for x in (CANON_LABEL.get(source or "", source), meta.get("canon_asof")) if x
    )
    basis = f" ({basis})" if basis else ""
    if work.timeline_order is not None:
        return f"It is part of the MCU official timeline{basis}."
    return f"It is an official MCU title{basis}."


# [역할] 페이즈 · 사가 · 타임라인 순번 · 개봉 순번 문장들 (값이 있는 것만)
def order_sentences(work: Work, meta: dict[str, Any], timeline_total: int) -> list[str]:
    out: list[str] = []
    saga = meta.get("saga")
    if work.phase and saga:
        out.append(f"It belongs to Phase {work.phase} of {saga}.")
    elif work.phase:
        out.append(f"It belongs to Phase {work.phase}.")
    if work.timeline_order is not None:
        out.append(
            f"It is number {work.timeline_order} of {timeline_total} in the official timeline order."
        )
    if meta.get("release_order") is not None:
        out.append(f"It is film number {meta['release_order']} in release order.")
    return out


# [역할] Wikipedia 정보 상자에서 값이 하나뿐인 칸(시즌 수 · 회차 수 · 방송사 · 상영 시간)만 문장으로
def infobox_sentences(kind: str, infobox: dict[str, Any] | None) -> list[str]:
    """정보 상자에서 값이 하나인 칸만 옮긴다 (감독 · 각본은 정제값에 이름이 붙어 있어 쓰지 않는다)"""
    params = (infobox or {}).get("params") or {}

    # [역할] 정보 상자 칸의 정제된 글자 하나 꺼내기 (없으면 빈 글자)
    def value(key: str) -> str:
        return ((params.get(key) or {}).get("text") or "").strip()

    out: list[str] = []
    seasons, episodes = value("num_seasons"), value("num_episodes")
    network = value("network") or value("channel")
    runtime = value("runtime")
    if kind == "series" and seasons.isdigit() and episodes.isdigit():
        plural = "season" if seasons == "1" else "seasons"
        out.append(f"It has {seasons} {plural} and {episodes} episodes.")
    elif kind in ("series", "season") and episodes.isdigit():
        out.append(f"It has {episodes} episodes.")
    if kind in ("series", "season") and network:
        out.append(f"It was released on {network}.")
    if runtime:
        out.append(f"Running time: {runtime}.")
    return out


# [역할] 작품 한 행 → 작품 카드 Chunk 한 개
#   문장들을 이어 text 를 만들고, chunk_id 는 '{work_id}-card' 규칙을 따른다 (architecture.md §3)
def make_card(
    work: Work,
    meta: dict[str, Any],
    document: Document | None,
    infobox: dict[str, Any] | None,
    *,
    series: Work | None = None,
    seasons: list[int] | None = None,
    timeline_total: int = 0,
    header_on: bool = True,
) -> Chunk:
    kind = kind_of(work, document)
    year = shown_year(work, infobox)
    sentences = [
        base_sentence(work, kind, year, meta["status"], series.title_original if series else None),
        canon_sentence(work, meta),
        *order_sentences(work, meta, timeline_total),
    ]
    if seasons:
        listed = ", ".join(f"season {n}" for n in seasons)
        sentences.append(f"Its seasons are listed separately in the timeline: {listed}.")
    facts = infobox_sentences(kind, infobox)
    sentences += facts
    # 표시: 정식이 아님(non_canon) · 미개봉(upcoming) — 답변에서 주의를 줄 때 쓸 수 있다
    flags = [
        flag
        for flag, on in (
            ("non_canon", not work.is_canon),
            ("upcoming", meta["status"] != "released"),
        )
        if on
    ]
    text = " ".join(sentences)
    header = (
        work_header(work.title_original, year, kind, work.phase, work.timeline_order)
        if header_on
        else ""
    )
    chunk_id = f"{work.work_id}-card"
    # 정보 상자 문장을 썼을 때만 Wikipedia 문서를 출처로, 아니면 IMDb 주소를 출처로
    use_wiki = document is not None and bool(facts)
    return Chunk(
        chunk_id=chunk_id,
        doc_id=work.work_id,
        work_id=work.work_id,
        # 카드는 작품 표에서 나온다. SourceId 에 '작품 표' 가 없어 본문과 같은 값을 쓴다 (영민 원본과 같음)
        source_id=SourceId.WIKIPEDIA_EN,
        section=Section.CARD,
        position=1,
        total=1,
        text=text,
        header=header,
        title_ko=work.title_ko,
        title_original=work.title_original,
        source_url=document.source_url
        if use_wiki and document
        else IMDB_URL.format(tconst=work.source_ids["imdb_tconst"]),
        revid=document.revid if use_wiki and document else None,
        entities=[],
        flags=flags,
        content_hash=content_hash(chunk_id, header, text),
        source_ids={
            "imdb_tconst": work.source_ids["imdb_tconst"],
            "canon_source": meta.get("canon_source"),
            "canon_asof": meta.get("canon_asof"),
            "wikipedia_doc_id": document.doc_id if use_wiki and document else None,
        },
    )


# [역할] 작품 표의 모든 행에 작품 카드를 만든다 (정식이 아닌 작품 · 미개봉작 포함)
def build_cards(ds: Dataset, header_on: bool = True) -> list[Chunk]:
    by_id = {w.work_id: w for w in ds.works}
    timeline_total = sum(w.timeline_order is not None for w in ds.works)
    # 시리즈 → 그 시리즈의 시즌 번호들 (시리즈 카드에 '시즌은 따로 있다' 를 적기 위해)
    seasons: dict[str, list[int]] = {}
    for w in ds.works:
        if w.season is not None and w.parent_work_id:
            seasons.setdefault(w.parent_work_id, []).append(w.season)
    cards: list[Chunk] = []
    for work in ds.works:
        meta = ds.works_meta[work.work_id]
        doc_id = meta["doc_id"] or ""
        cards.append(
            make_card(
                work,
                meta,
                ds.documents.get(doc_id),
                (ds.docs_meta.get(doc_id) or {}).get("infobox"),
                series=by_id.get(work.parent_work_id or ""),
                seasons=sorted(seasons.get(work.work_id, [])),
                timeline_total=timeline_total,
                header_on=header_on,
            )
        )
    # 빈 값(None)이 문장에 글자로 새어 들어가면 검색 품질이 떨어지므로 여기서 멈춘다
    if any("None" in c.text or "None" in c.header for c in cards):
        raise ValueError("카드에 빈 값(None)이 글자로 들어갔습니다")
    return cards


# ---------- 연결 카드 ----------


@dataclass
# [역할] 선행작 후보 하나 = 작품 ID + 겹치는 인물 이름들
class Prerequisite:
    work_id: str
    shared: list[str]


# [역할] 정보 상자의 개봉일 · 방영 시작일 틀에서 날짜를 꺼낸다 (없으면 None)
def first_date(infobox: dict[str, Any] | None) -> date | None:
    params = (infobox or {}).get("params") or {}
    for key in ("released", "first_aired"):
        match = FIRST_DATE.search((params.get(key) or {}).get("raw") or "")
        if match:
            y, m, d = (int(part) for part in match.groups())
            return date(y, m, d)
    return None


# [역할] earlier 가 later 보다 먼저인가? 먼저면 거리, 아니면(또는 판단 불가) None
#   가장 자세한 값부터 비교한다: 개봉일 → 개봉 순번 → 연도
def gap(earlier: dict[str, Any], later: dict[str, Any]) -> int | None:
    """earlier 가 먼저 나왔으면 그 거리, 아니면 None. 개봉일 → 개봉 순번 → 연도 순으로 잰다"""
    for key in ("date", "release_order", "year"):
        a, b = earlier.get(key), later.get(key)
        if a is None or b is None:
            continue
        if key == "date":
            a, b = a.toordinal(), b.toordinal()
        return b - a if a < b else None
    return None


# [역할] 작품 문서의 출연진 절에서 링크된 MCU 인물 문서 제목들을 모은다
def cast_characters(sections: list[dict[str, Any]], character_titles: set[str]) -> set[str]:
    return {
        link["target_title"]
        for section in sections
        if section["section_kind"] == "cast"
        for link in section["links"]
        if link.get("target_title") in character_titles
    }


# [역할] 선행작 후보 고르기 — 먼저 나왔고 출연 인물이 겹치는 작품을
#   많이 겹칠수록 · 가까울수록 앞에 두고 최대 limit 편까지 (사람이 확인한 정답이 아니라 '후보')
def prerequisite_candidates(
    work_id: str, facts: dict[str, dict[str, Any]], limit: int = PREREQUISITE_LIMIT
) -> list[Prerequisite]:
    """먼저 나온 작품 중 출연 인물이 겹치는 것. 많이 겹칠수록, 가까울수록 앞"""
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


# [역할] 'previous = X; next = Y' 형태의 짧은 문구 (값이 있는 쪽만)
def side(previous: str | None, following: str | None) -> str:
    return "; ".join(f"{k} = {v}" for k, v in (("previous", previous), ("next", following)) if v)


# [역할] 작품 하나 → 연결 카드 Chunk (연결할 것이 없으면 None)
#   줄마다 출처를 괄호로 붙인다: 개봉 순서(Disney+) · 타임라인(Marvel.com) · 선행작 후보(Wikipedia 출연진)
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
    header_on: bool = True,
) -> Chunk | None:
    kind = kind_of(work, document)
    year = shown_year(work, infobox)
    parts = [f"{year} {kind}" if year else kind] + ([f"Phase {work.phase}"] if work.phase else [])
    lines: list[str] = []

    # [역할] 작품 ID → 제목 (미개봉작이면 '(not released yet)' 을 붙인다)
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
    header = (
        work_header(work.title_original, year, kind, work.phase, work.timeline_order)
        if header_on
        else ""
    )
    chunk_id = f"{work.work_id}-connection"
    url: str
    revid: int | None
    # 대표 출처 고르기: 타임라인 줄이 있으면 Marvel.com, 개봉 순서만 있으면 Disney+, 선행작만 있으면 작품 문서
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
        source_id=SourceId.WIKIPEDIA_EN,
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


# [역할] 본문 색인 규칙을 지키는 작품마다 연결 카드를 만든다
#   먼저 작품별 '사실표(facts)' 를 만들고 (날짜 · 순번 · 연도 · 상위 시리즈 · 출연 인물), 그 표로 선행작을 고른다
def build_connections(ds: Dataset, header_on: bool = True) -> list[Chunk]:
    character_titles = {
        meta["title"]
        for doc_id, meta in ds.docs_meta.items()
        if ds.documents[doc_id].doc_type == "character" and meta["scope_status"] == "mcu"
    }

    # [역할] 작품 ID → 그 작품 문서의 정보 상자
    def infobox(work_id: str) -> dict[str, Any] | None:
        doc_id = ds.works_meta[work_id]["doc_id"]
        return ds.docs_meta[doc_id]["infobox"] if doc_id else None

    titles = {w.work_id: w.title_original for w in ds.works}
    upcoming = {w.work_id for w in ds.works if ds.works_meta[w.work_id]["status"] != "released"}
    by_timeline = {w.timeline_order: w.work_id for w in ds.works if w.timeline_order is not None}
    by_release = {
        ds.works_meta[w.work_id]["release_order"]: w.work_id
        for w in ds.works
        if ds.works_meta[w.work_id].get("release_order") is not None
    }
    selected = [w for w in ds.works if is_body_work(w, ds.works_meta[w.work_id])]
    facts = {
        w.work_id: {
            "date": first_date(infobox(w.work_id)),
            "release_order": ds.works_meta[w.work_id].get("release_order"),
            "year": shown_year(w, infobox(w.work_id)),
            "parent": w.parent_work_id,
            "cast": cast_characters(
                ds.sections.get(ds.works_meta[w.work_id]["doc_id"] or "", []), character_titles
            ),
        }
        for w in selected
    }
    cards: list[Chunk] = []
    for work in selected:
        doc_id = ds.works_meta[work.work_id]["doc_id"]
        card = make_connection_card(
            work,
            ds.works_meta[work.work_id],
            infobox(work.work_id),
            ds.documents.get(doc_id or ""),
            titles=titles,
            by_timeline=by_timeline,
            by_release=by_release,
            upcoming=upcoming,
            prerequisites=prerequisite_candidates(work.work_id, facts),
            header_on=header_on,
        )
        if card is not None:
            cards.append(card)
    if any("None" in c.text for c in cards):
        raise ValueError("연결 카드에 빈 값(None)이 글자로 들어갔습니다")
    return cards


# [역할] 이 파일의 바깥 진입점. 고정 문서 컬렉션에 들어갈 전부를 한 목록으로 만든다
#   본문 청크 + 작품 카드 + 연결 카드 → 이 목록의 index_version 이 색인 버전이 된다
def build_index_chunks(
    ds: Dataset, recipe: Recipe, scope: str = "works+characters+lore"
) -> list[Chunk]:
    """고정 문서 컬렉션에 넣을 전부 = 본문 청크 + 작품 카드 + 연결 카드 (영민 read_index 와 같은 묶음)"""
    chunks = build_chunks(ds, recipe, scope)
    chunks += build_cards(ds, recipe.header)
    chunks += build_connections(ds, recipe.header)
    ids = [c.chunk_id for c in chunks]
    if len(set(ids)) != len(ids):
        raise ValueError("색인 안에서 chunk_id 가 겹칩니다")
    return chunks
