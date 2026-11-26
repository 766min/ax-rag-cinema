"""연결 카드 규칙 확인. 실행: uv run pytest test/youngmin/chunking -q  (저장소 루트에서)"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_connections import (  # noqa: E402
    Prerequisite,
    cast_characters,
    first_date,
    gap,
    make_connection_card,
    prerequisite_candidates,
)

from shared.core.models import Work  # noqa: E402

IRON_MAN_2 = Work(
    work_id="tt1228705",
    title_ko="아이언맨 2",
    title_original="Iron Man 2",
    format="movie",
    year=2010,
    phase="1",
    timeline_order=10,
    is_canon=True,
)
TITLES = {
    "tt0371746": "Iron Man",
    "tt1228705": "Iron Man 2",
    "tt0800369": "Thor",
    "tt9": "Doomsday",
}


def card(meta: dict, prerequisites: list[Prerequisite] | None = None, **kwargs: object):
    return make_connection_card(
        IRON_MAN_2,
        {"release_order": 3, **meta},
        None,
        None,
        titles=TITLES,
        by_timeline=kwargs.get("by_timeline", {9: "tt0371746", 11: "tt0800369"}),  # type: ignore[arg-type]
        by_release=kwargs.get("by_release", {2: "tt0371746", 4: "tt9"}),  # type: ignore[arg-type]
        upcoming={"tt9"},
        prerequisites=prerequisites or [],
    )


def test_every_line_names_its_source() -> None:
    chunk = card(
        {}, [Prerequisite("tt0371746", ["James Rhodes", "Pepper Potts", "Tony Stark", "X"])]
    )
    assert chunk is not None
    assert chunk.chunk_id == "tt1228705-connection" and chunk.doc_id == "tt1228705"
    assert chunk.text.split("\n") == [
        "[Connection card] Iron Man 2 (2010 film · Phase 1)",
        "Release order: previous = Iron Man; next = Doomsday (not released yet). "
        "(Disney+ Marvel Movies In Order, 2026-11-04)",
        "Timeline order: previous = Iron Man; next = Thor. "
        "(Marvel.com MCU Complete Timeline, 2026-10-22)",
        "Earlier works sharing main characters (candidates): "
        "Iron Man (James Rhodes, Pepper Potts, Tony Stark, …). (Wikipedia cast sections)",
    ]
    assert chunk.flags == ["prerequisite_candidates"]
    assert chunk.entities == ["James Rhodes", "Pepper Potts", "Tony Stark", "X"]
    assert chunk.source_url.startswith("https://www.marvel.com/")


def test_a_card_with_nothing_to_connect_is_not_made() -> None:
    lonely = make_connection_card(
        IRON_MAN_2.model_copy(update={"timeline_order": None}),
        {"release_order": None},
        None,
        None,
        titles=TITLES,
        by_timeline={},
        by_release={},
        upcoming=set(),
        prerequisites=[],
    )
    assert lonely is None


def test_earlier_means_the_most_precise_shared_value() -> None:
    a = {"date": date(2008, 5, 2), "release_order": 1, "year": 2008}
    b = {"date": date(2010, 5, 7), "release_order": 3, "year": 2010}
    assert gap(a, b) == (date(2010, 5, 7) - date(2008, 5, 2)).days
    assert gap(b, a) is None
    # 날짜가 없으면 개봉 순번, 그것도 없으면 연도. 같은 해는 앞뒤를 가리지 않는다
    assert (
        gap({"date": None, "release_order": 1, "year": 2008}, {"release_order": 3, "year": 2010})
        == 2
    )
    assert gap({"year": 2021}, {"year": 2021}) is None


def test_prerequisites_rank_by_shared_characters_then_distance() -> None:
    facts = {
        "late": {"date": date(2019, 4, 26), "cast": {"A", "B", "C"}},
        "far_two": {"date": date(2008, 1, 1), "cast": {"A", "B"}},
        "near_two": {"date": date(2018, 1, 1), "cast": {"A", "B"}},
        "one": {"date": date(2018, 6, 1), "cast": {"C"}},
        "after": {"date": date(2020, 1, 1), "cast": {"A", "B", "C"}},
        "none": {"date": date(2017, 1, 1), "cast": {"Z"}},
    }
    found = prerequisite_candidates("late", facts)
    assert [p.work_id for p in found] == ["near_two", "far_two", "one"]
    assert found[0].shared == ["A", "B"]


def test_cast_is_links_to_mcu_character_pages_in_cast_sections() -> None:
    sections = [
        {
            "section_kind": "cast",
            "links": [
                {"target_title": "Tony Stark (Marvel Cinematic Universe)"},
                {"target_title": "Robert Downey Jr."},  # 배우 문서는 인물이 아니다
            ],
        },
        {"section_kind": "plot", "links": [{"target_title": "Thor (Marvel Cinematic Universe)"}]},
    ]
    titles = {"Tony Stark (Marvel Cinematic Universe)", "Thor (Marvel Cinematic Universe)"}
    assert cast_characters(sections, titles) == {"Tony Stark (Marvel Cinematic Universe)"}


def test_first_date_reads_film_and_series_templates() -> None:
    film = {"params": {"released": {"raw": "{{Film date|2008|4|14|Sydney|2008|5|2|US}}"}}}
    series = {"params": {"first_aired": {"raw": "{{Start date|2021|6|9}}"}}}
    assert first_date(film) == date(2008, 4, 14)
    assert first_date(series) == date(2021, 6, 9)
    assert first_date({"params": {"released": {"raw": "2016–2018"}}}) is None


def test_a_season_and_its_whole_series_are_not_prerequisites_of_each_other() -> None:
    facts = {
        "loki": {"date": date(2021, 6, 9), "cast": {"Loki", "Sylvie"}},
        "loki-S1": {"date": date(2021, 6, 9), "cast": {"Loki", "Sylvie"}, "parent": "loki"},
        "loki-S2": {"date": date(2023, 10, 5), "cast": {"Loki", "Sylvie"}, "parent": "loki"},
    }
    assert [p.work_id for p in prerequisite_candidates("loki-S2", facts)] == ["loki-S1"]
    assert prerequisite_candidates("loki", facts) == []  # 시즌 2 는 나중이고, 시즌 1 은 같은 날이다
