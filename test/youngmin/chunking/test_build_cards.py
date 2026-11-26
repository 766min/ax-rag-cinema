"""작품 카드 규칙 확인. 실행: uv run pytest test/youngmin/chunking -q  (저장소 루트에서)"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_cards import infobox_sentences, make_card  # noqa: E402
from build_chunks import shown_year  # noqa: E402

from shared.core.models import Document, SourceId, Work  # noqa: E402

TIMELINE = {"canon_source": "marvel_timeline", "canon_asof": "2026-10-22"}


def work(**overrides: object) -> Work:
    fields: dict[str, object] = {
        "work_id": "tt4154796",
        "title_ko": "어벤져스: 엔드게임",
        "title_original": "Avengers: Endgame",
        "format": "movie",
        "year": 2019,
        "phase": "3",
        "timeline_order": 44,
        "is_canon": True,
        "source_ids": {"imdb_tconst": "tt4154796"},
    }
    fields.update(overrides)
    return Work(**fields)


def meta(**overrides: object) -> dict:
    return {"status": "released", "saga": None, "release_order": None, **TIMELINE, **overrides}


def test_canon_film_card_states_basis_and_order() -> None:
    card = make_card(
        work(), meta(saga="The Infinity Saga", release_order=21), None, None, timeline_total=81
    )
    assert card.chunk_id == "tt4154796-card" and card.doc_id == "tt4154796"
    assert card.text == (
        "Avengers: Endgame is a film released in 2019. "
        "It is part of the MCU official timeline (Marvel.com MCU Complete Timeline, 2026-10-22). "
        "It belongs to Phase 3 of The Infinity Saga. "
        "It is number 44 of 81 in the official timeline order. "
        "It is film number 21 in release order."
    )
    assert card.header == "[Avengers: Endgame · 2019 film · Phase 3 · timeline #44]"
    assert card.flags == [] and card.source_url == "https://www.imdb.com/title/tt4154796/"


def test_non_canon_and_upcoming_cards_say_so() -> None:
    old = make_card(
        work(is_canon=False, phase=None, timeline_order=None, year=1996, format="tvSeries"),
        meta(canon_source=None, canon_asof=None),
        None,
        None,
    )
    assert old.text == (
        "Avengers: Endgame is a TV series released in 1996. "
        "It is not part of the MCU official timeline."
    )
    assert old.flags == ["non_canon"]
    soon = make_card(
        work(timeline_order=None, year=2027, phase="6"),
        meta(status="upcoming", canon_source="disney_release", canon_asof="2026-11-04"),
        None,
        None,
    )
    assert "is a film that is not released yet (planned 2027)." in soon.text
    assert "an official MCU title (Disney+ Marvel Movies In Order, 2026-11-04)" in soon.text
    assert soon.flags == ["upcoming"]


def test_season_card_names_its_series_and_uses_its_own_year() -> None:
    series = work(work_id="tt9140554", title_original="Loki", format="tvSeries", year=2021)
    season = work(
        work_id="tt9140554-S2",
        title_original="Loki season 2",
        format="tvSeries",
        year=2021,  # 데이터셋은 시리즈 시작 연도를 적는다
        season=2,
        parent_work_id="tt9140554",
        phase="5",
        timeline_order=69,
    )
    box = {"params": {"first_aired": {"raw": "{{Start date|2023|10|5}}", "text": ""}}}
    assert shown_year(season, box) == 2023
    assert shown_year(season, None) is None  # 시즌 2 이상은 근거가 없으면 비운다
    assert shown_year(season.model_copy(update={"season": 1}), None) == 2021
    card = make_card(season, meta(), None, box, series=series, timeline_total=81)
    assert card.text.startswith("Loki season 2 is a season of the TV series Loki released in 2023.")
    parent = make_card(series, meta(), None, None, seasons=[1, 2])
    assert "Its seasons are listed separately in the timeline: season 1, season 2." in parent.text


def test_only_single_valued_infobox_fields_are_used() -> None:
    box = {
        "params": {
            "director": {"text": "Anna BodenRyan Fleck"},  # 이름이 붙어 있어 쓰지 않는다
            "runtime": {"text": "41–56 minutes"},
            "num_seasons": {"text": "2"},
            "num_episodes": {"text": "12"},
            "network": {"text": "Disney+"},
        }
    }
    assert infobox_sentences("series", box) == [
        "It has 2 seasons and 12 episodes.",
        "It was released on Disney+.",
        "Running time: 41–56 minutes.",
    ]
    assert infobox_sentences("film", box) == ["Running time: 41–56 minutes."]
    doc = Document(
        doc_id="tt9140554-wikipedia",
        work_id="tt9140554",
        source_id=SourceId.WIKIPEDIA_EN,
        source_url="https://en.wikipedia.org/w/index.php?oldid=7",
        revid=7,
        doc_type="series",
    )
    card = make_card(work(work_id="tt9140554", format="tvSeries"), meta(), doc, box)
    # 정보 상자 값을 실은 카드는 그 Wikipedia 판을 출처로 적는다
    assert card.revid == 7 and card.source_url.endswith("oldid=7")
