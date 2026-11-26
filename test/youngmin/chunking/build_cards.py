"""공통 데이터셋의 작품 표 → 작품 카드 (팀 Chunk, section = card).

실행 (저장소 루트에서):
    PYTHONPATH=src uv run python test/youngmin/chunking/build_cards.py

카드는 작품 표의 **모든 행**에 하나씩 만든다 (architecture.md §3). 본문이 색인되지 않는 작품 —
정식이 아닌 작품, 미개봉작, 문서가 없는 작품 — 을 물었을 때 검색되는 것이 이 카드다.
글자는 표의 값을 정해진 문장 틀에 넣어 만든다. LLM 을 쓰지 않는다.

입력: data/cineverse-dataset-schema/  (저장소 루트, works · documents · extra/)
출력: test/youngmin/data/cards/cards.jsonl · report.json
"""

import json
from collections import Counter
from typing import Any

from build_chunks import DATASET, HERE, content_hash, load_jsonl, shown_year, work_header

from shared.core.models import Chunk, Document, SourceId, Work
from shared.core.schemas import Section

OUT = HERE.parent / "data" / "cards"

# 머리말에 쓰는 종류 (본문 청크의 머리말과 같은 낱말). 문서가 있으면 문서의 종류를 쓴다
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
# 문장에 쓰는 이름
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
# 정식 작품의 근거가 된 공식 문서 (architecture.md §2)
CANON_LABEL = {
    "marvel_timeline": "Marvel.com MCU Complete Timeline",
    "disney_release": "Disney+ Marvel Movies In Order",
}
IMDB_URL = "https://www.imdb.com/title/{tconst}/"


def kind_of(work: Work, document: Document | None) -> str:
    if work.season is not None:
        return "season"
    if document is not None and document.doc_type in NOUN:
        return document.doc_type
    return KIND.get(work.format, "title")


def base_sentence(work: Work, kind: str, year: int | None, status: str, series: str | None) -> str:
    """무엇이고 언제 나왔나. 미개봉작은 개봉 전이라고 적는다"""
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


def canon_sentence(work: Work, meta: dict[str, Any]) -> str:
    """정식 여부와 그 근거 (공식 문서 이름 · 기준일)"""
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


def infobox_sentences(kind: str, infobox: dict[str, Any] | None) -> list[str]:
    """Wikipedia 정보 상자에서 숫자 · 채널처럼 값이 하나인 칸만 옮긴다.

    감독 · 각본 같은 이름 칸은 정제값에서 여러 이름이 붙어 있어("Anna BodenRyan Fleck") 쓰지 않는다.
    그 값은 작품 문서의 synopsis 청크에 문장으로 들어 있다.
    """
    params = (infobox or {}).get("params") or {}

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


def make_card(
    work: Work,
    meta: dict[str, Any],
    document: Document | None,
    infobox: dict[str, Any] | None,
    *,
    series: Work | None = None,
    seasons: list[int] | None = None,
    timeline_total: int = 0,
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
    flags = [
        flag
        for flag, on in (
            ("non_canon", not work.is_canon),
            ("upcoming", meta["status"] != "released"),
        )
        if on
    ]
    text = " ".join(sentences)
    header = work_header(work.title_original, year, kind, work.phase, work.timeline_order)
    chunk_id = f"{work.work_id}-card"
    use_wiki = document is not None and bool(facts)
    return Chunk(
        chunk_id=chunk_id,
        doc_id=work.work_id,
        work_id=work.work_id,
        # 카드는 문서가 아니라 작품 표에서 나온다. 출처 값에 "작품 표" 가 없어 본문과 같은 값을 쓴다
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


def build() -> dict[str, Any]:
    works = [Work.model_validate(row) for row in load_jsonl(DATASET / "works.jsonl")]
    by_id = {work.work_id: work for work in works}
    documents = {
        row["doc_id"]: Document.model_validate(row)
        for row in load_jsonl(DATASET / "documents.jsonl")
    }
    works_meta = {row["work_id"]: row for row in load_jsonl(DATASET / "extra" / "works_meta.jsonl")}
    infoboxes = {
        row["doc_id"]: row["infobox"]
        for row in load_jsonl(DATASET / "extra" / "documents_meta.jsonl")
    }
    timeline_total = sum(work.timeline_order is not None for work in works)
    seasons: dict[str, list[int]] = {}
    for work in works:
        if work.season is not None and work.parent_work_id:
            seasons.setdefault(work.parent_work_id, []).append(work.season)

    cards: list[Chunk] = []
    for work in works:
        meta = works_meta[work.work_id]
        doc_id = meta["doc_id"]
        cards.append(
            make_card(
                work,
                meta,
                documents.get(doc_id or ""),
                infoboxes.get(doc_id or ""),
                series=by_id.get(work.parent_work_id or ""),
                seasons=sorted(seasons.get(work.work_id, [])),
                timeline_total=timeline_total,
            )
        )
    if any("None" in card.text or "None" in card.header for card in cards):
        raise ValueError("카드에 빈 값(None)이 글자로 들어갔습니다")

    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "cards.jsonl").open("w", encoding="utf-8") as f:
        for card in cards:
            f.write(card.model_dump_json() + "\n")
    lengths = [len(card.text) for card in cards]
    report = {
        "cards": len(cards),
        "canon": sum("non_canon" not in card.flags for card in cards),
        "flags": dict(Counter(flag for card in cards for flag in card.flags)),
        "with_wikipedia_facts": sum(card.revid is not None for card in cards),
        "with_timeline_order": sum("official timeline order" in card.text for card in cards),
        "with_phase": sum("belongs to Phase" in card.text for card in cards),
        "without_year": sum(
            "released in" not in card.text and "planned" not in card.text for card in cards
        ),
        "text_chars": {"min": min(lengths), "max": max(lengths)},
    }
    (OUT / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))
