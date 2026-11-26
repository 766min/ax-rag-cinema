"""청킹 규칙 확인. 실행: uv run pytest test/youngmin/chunking -q  (저장소 루트에서)"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_chunks import (  # noqa: E402
    content_hash,
    entities_in,
    episode_blocks,
    make_chunks,
    make_parents,
    merge_short,
    pool_short,
    select_works,
    split_paragraphs,
    text_block,
    work_header,
)
from check_gold import probes, squash  # noqa: E402

from shared.core.models import Document, SourceId, Work  # noqa: E402
from shared.core.schemas import Section  # noqa: E402

LONG = "a" * 200
DOC = Document(
    doc_id="tt0371746-wikipedia",
    work_id="tt0371746",
    source_id=SourceId.WIKIPEDIA_EN,
    source_url="https://en.wikipedia.org/w/index.php?oldid=1",
    revid=1,
    doc_type="film",
    source_ids={"team_document_id": "doc_x"},
)


def section(index: int, text: str, path: list[str] | None = None, **more: object) -> dict:
    return {
        "section_index": index,
        "section_path": path or ["Plot"],
        "text": text,
        "links": [],
        **more,
    }


def plot(blocks: list, **kwargs: object) -> list:
    return make_chunks(DOC, Section.PLOT, blocks, title_ko=None, title_original="x", **kwargs)


# ---------- 원칙 1 · 7: 경계로 나누고 평탄화된 표는 뺀다


def test_paragraphs_split_on_single_newline() -> None:
    assert split_paragraphs("one\n\n two \nthree") == (["one", "two", "three"], 0)


def test_flattened_table_is_skipped() -> None:
    assert split_paragraphs("+ Content 1943 Captain America\nprose") == (["prose"], 1)


# ---------- 원칙 2 · 3 · 4: 문단을 쪼개지 않고, 짧은 것만 합친다


def test_short_paragraph_joins_the_next_one() -> None:
    assert merge_short(["short", LONG]) == [f"short {LONG}"]


def test_short_last_paragraph_joins_the_previous_one() -> None:
    assert merge_short([LONG, "tail"]) == [f"{LONG} tail"]


def test_text_is_not_rewritten() -> None:
    text = "Tony Stark, who has inherited Stark Industries, tours Afghanistan. " * 3
    assert merge_short([text.strip()]) == [text.strip()]


def test_independent_items_pool_only_with_other_short_items() -> None:
    a, b = "x" * 80, "y" * 80
    assert pool_short([a, b, LONG, "tail " * 8]) == [f"{a} {b}", LONG, ("tail " * 8)]


def test_pool_never_mixes_a_short_item_into_a_long_one() -> None:
    assert pool_short(["short item", LONG]) == ["short item", LONG]


def test_name_only_remnant_is_dropped() -> None:
    stats: Counter[str] = Counter()
    chunks = make_chunks(
        DOC,
        Section.LORE,
        [text_block(section(1, f"The Bowie.\n{LONG}"), "[h]", independent=True)],
        title_ko=None,
        title_original="x",
        stats=stats,
    )
    assert [c.text for c in chunks] == [LONG]
    assert stats["tiny_dropped"] == 1


# ---------- 원칙 4 · 5 · 9: 겹침 없음, 부모 가리키기, 출처 값 복제


def test_ids_count_up_across_sections_but_do_not_merge_across_them() -> None:
    chunks = plot(
        [text_block(section(1, f"{LONG}\ntail"), "[h]"), text_block(section(2, LONG), "[h]")]
    )
    assert [c.chunk_id for c in chunks] == [
        "tt0371746-wikipedia-plot-p01",
        "tt0371746-wikipedia-plot-p02",
    ]
    assert chunks[0].text == f"{LONG} tail"  # 짧은 꼬리는 같은 절의 앞 문단에 붙는다
    assert [c.source_ids["team_section_index"] for c in chunks] == ["1", "2"]
    assert all(c.total == 2 and c.revid == 1 and c.source_url == DOC.source_url for c in chunks)


def test_chunks_do_not_overlap() -> None:
    one, two = "b" * 200, "c" * 200
    assert [c.text for c in plot([text_block(section(1, f"{one}\n{two}"), "[h]")])] == [one, two]


def test_parent_holds_the_whole_section() -> None:
    chunks = plot(
        [
            text_block(section(1, f"{LONG}\n{'b' * 200}"), "[h]"),
            text_block(section(2, "c" * 200), "[h]"),
        ]
    )
    parents = make_parents(chunks)
    assert [p["parent_id"] for p in parents] == ["tt0371746-wikipedia#s1", "tt0371746-wikipedia#s2"]
    assert parents[0]["chunk_ids"] == [c.chunk_id for c in chunks[:2]]
    assert parents[0]["text"] == f"{LONG}\n{'b' * 200}"
    assert chunks[0].source_ids["parent_id"] == "tt0371746-wikipedia#s1"


# ---------- 에피소드 요약: 에피소드 하나가 경계이고 부모다


def test_each_episode_summary_is_its_own_block() -> None:
    episodes = [
        {"title": "Glorious Purpose", "episode_number": 1, "summary": LONG},
        {"title": "No summary yet", "episode_number": 2, "summary": ""},
        {"title": "Lamentis", "episode_number": 3, "summary": f"short\n{LONG}"},
    ]
    blocks = episode_blocks(
        section(4, "", ["Episodes"], episode_entries=episodes), "[Loki season 1]"
    )
    chunks = plot(blocks)
    assert [c.header for c in chunks] == [
        "[Loki season 1 · Episode 1: Glorious Purpose]",
        "[Loki season 1 · Episode 3: Lamentis]",
    ]
    assert chunks[1].text == f"short {LONG}"  # 짧은 문단은 같은 에피소드 안에서만 합친다
    assert [c.source_ids["parent_id"] for c in chunks] == [
        "tt0371746-wikipedia#s4e1",
        "tt0371746-wikipedia#s4e3",
    ]
    assert all(c.flags == ["episode_summary"] for c in chunks)


# ---------- 원칙 6: 머리말


def test_header_drops_empty_parts() -> None:
    assert work_header("Iron Man", 2008, "film", "1", 5) == (
        "[Iron Man · 2008 film · Phase 1 · timeline #5]"
    )
    assert work_header("Daredevil", None, "series", None, None) == "[Daredevil · series]"


def test_post_credit_scene_is_flagged() -> None:
    text = f"{LONG}\nIn a post-credits scene, Nick Fury visits Stark. {LONG}"
    assert [c.flags for c in plot([text_block(section(1, text), "[h]")])] == [[], ["post_credit"]]


def test_entities_are_link_targets_whose_anchor_is_in_the_paragraph() -> None:
    links = [
        {"anchor_text": "Tony Stark", "target_title": "Tony Stark (Marvel Cinematic Universe)"},
        {"anchor_text": "Thanos", "target_title": "Thanos (Marvel Cinematic Universe)"},
    ]
    assert entities_in("Tony Stark tours Afghanistan.", links) == [
        "Tony Stark (Marvel Cinematic Universe)"
    ]


# ---------- 색인 규칙 (architecture.md §3)


def test_only_canon_released_first_wave_works_with_a_document_are_indexed() -> None:
    def work(work_id: str, is_canon: bool = True) -> Work:
        return Work(
            work_id=work_id, title_ko="가", title_original="A", format="movie", is_canon=is_canon
        )

    def meta(status: str = "released", wave: str = "1", doc: bool = True) -> dict:
        return {"status": status, "collect_wave": wave, "doc_id": "d" if doc else None}

    works = [work("tt1"), work("tt2", is_canon=False), work("tt3"), work("tt4"), work("tt5")]
    metas = {
        "tt1": {**meta(), "doc_id": "tt1-wikipedia"},
        "tt2": meta(),
        "tt3": meta(status="upcoming"),
        "tt4": meta(wave="2"),
        "tt5": meta(doc=False),
    }
    assert list(select_works(works, metas)) == ["tt1-wikipedia"]


# ---------- 원칙 8: 색인이 바뀌면 정답 키를 다시 잇는다


def test_hash_changes_with_text_and_is_stable() -> None:
    assert content_hash("id", "[h]", "a") == content_hash("id", "[h]", "a")
    assert content_hash("id", "[h]", "a") != content_hash("id", "[h]", "b")


def test_excerpt_matches_text_with_restored_apostrophes() -> None:
    # 발췌는 글자를 되살리기 전 것(아포스트로피 빠짐), 청크는 되살린 글자
    excerpt = "Using the Darkholds power, Harkness demonstrates her magic to Wanda"
    chunk = "Using the Darkhold's power, Harkness demonstrates her magic to Wanda Maximoff."
    assert any(needle in squash(chunk) for needle in probes(excerpt))
