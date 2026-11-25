import json
import re
from pathlib import Path
from typing import Any

import pytest

from etl.handoff import (
    DEFAULT_RECIPE,
    HANDOFF_DIR,
    PROJECT_ROOT,
    RECIPES,
    ChunkRecipe,
    build,
    collection_for,
    dataset_fingerprint,
    find_chunk_ids,
    index_version,
    load_handoff,
    split_text,
)
from shared.core.schemas import Section

# --- 가짜 원본 데이터 (실제 handoff 와 같은 칸, 내용은 임의) --------------------------


def _work(work_id: str, doc: str, content_type: str | None = "movie", **kw: Any) -> dict[str, Any]:
    return {
        "work_id": work_id,
        "title_ko": "아이언맨",
        "title_original": "Iron Man",
        "format": None,
        "content_type": content_type,
        "season": None,
        "release_date": "{{Film date|2008|05|02}}",
        "phase": 1,
        "timeline_order": 3,
        "is_canon": "unknown",
        "source_ids": {"imdb_tconst": work_id, "wikipedia_document_id": doc},
        **kw,
    }


def _section(index: int, kind: str, text: str, path: str = "Plot") -> dict[str, Any]:
    return {"section_index": index, "section_kind": kind, "section_path": [path], "text": text}


def _doc(doc_id: str, doc_type: str, pageid: int, sections: list[dict[str, Any]], **kw: Any):
    return {
        "doc_id": doc_id,
        "doc_type": doc_type,
        "pageid": pageid,
        "title": kw.pop("title", "Iron Man (2008 film)"),
        "source_url": "https://en.wikipedia.org/wiki/x",
        "license": "CC-BY-SA-4.0",
        "revid": 1,
        "fetched_at": "2026-11-23T22:05:08+00:00",
        "wikidata_qid": "Q1",
        "sections": sections,
        **kw,
    }


LONG = "A" * 600  # 두 개를 합치면 기본 레시피(CK03) 크기 1000 을 넘는 문단
SHORT = "short line"
CK01 = RECIPES["CK01"]


# --- split_text (RecursiveCharacterTextSplitter) ------------------------------


def test_짧은_문단들은_한_청크로_합친다() -> None:
    assert split_text("first line\nsecond line") == ["first line\nsecond line"]


def test_긴_글은_청크_크기_안으로_나눈다() -> None:
    text = " ".join(f"Sentence number {i} is here." for i in range(200))  # 약 5,800자
    pieces = split_text(text)

    assert len(pieces) > 1
    assert all(len(p) <= DEFAULT_RECIPE.size for p in pieces)


def test_빈_글은_청크가_없다() -> None:
    assert split_text("") == []
    assert split_text("\n\n") == []


# --- 레시피 -------------------------------------------------------------------


def test_레시피_목록과_기본값() -> None:
    assert list(RECIPES) == ["CK01", "CK02", "CK03", "CK04"]
    assert [(r.size, r.overlap) for r in RECIPES.values()] == [
        (0, 0),
        (500, 100),
        (1000, 100),
        (1500, 100),
    ]
    assert DEFAULT_RECIPE is RECIPES["CK03"]
    assert DEFAULT_RECIPE.version == "handoff-CK03-rcts1000-o100-min10"
    assert CK01.version == "handoff-CK01-para-m150-min10"


def test_CK01_150자_미만_문단은_다음_문단과_합친다() -> None:
    assert split_text(f"{SHORT}\n{LONG}\n{LONG}", CK01) == [f"{SHORT}\n{LONG}", LONG]


def test_CK01_마지막_짧은_문단은_앞에_붙고_길이_상한은_없다() -> None:
    huge = "B" * 5000
    assert split_text(f"{huge}\n{SHORT}", CK01) == [f"{huge}\n{SHORT}"]


def test_레시피마다_크기_상한을_지킨다() -> None:
    text = " ".join(f"Sentence number {i} is here." for i in range(300))
    for recipe in (RECIPES["CK02"], RECIPES["CK03"], RECIPES["CK04"]):
        assert all(len(p) <= recipe.size for p in split_text(text, recipe))


def test_짧은_조각은_버리고_무엇을_버렸는지_알려준다() -> None:
    recipe = ChunkRecipe("T", "paragraph", merge_min=1, min_chars=10)
    kept, dropped = recipe.split("W.\nThis line is long enough.")
    assert (kept, dropped) == (["This line is long enough."], ["W."])


def test_데이터셋_체크섬은_숨김파일과_zip을_빼고_계산한다(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text(
        json.dumps({"dataset": "d", "schema_version": "s", "generated_at": "g"})
    )
    (tmp_path / "works.jsonl").write_text("{}\n")
    before = dataset_fingerprint(tmp_path)
    (tmp_path / ".DS_Store").write_text("x")
    (tmp_path / "raw.zip").write_text("x")
    assert dataset_fingerprint(tmp_path) == before
    (tmp_path / "works.jsonl").write_text("{} \n")  # 내용이 바뀌면 값도 바뀜
    assert dataset_fingerprint(tmp_path)["sha256"] != before["sha256"]
    assert index_version(CK01, before) == f"{CK01.version}@{before['sha256'][:8]}"


def test_컬렉션은_레시피마다_따로() -> None:
    assert collection_for("mcu_movies", RECIPES["CK02"]) == "mcu_movies_ck02"


# --- build: ID 규칙과 범위 ------------------------------------------------------


def _sample() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    works = [
        _work("tt0371746", "doc_film"),
        _work("tt9999999", "doc_film", content_type="tv_episode"),  # 같은 문서를 공유하는 에피소드
        _work("cnt_abc", "doc_tv", content_type="tv_season", title_ko=None),  # IMDb ID 없음
    ]
    docs = [
        _doc(
            "doc_film",
            "content",
            100,
            [
                _section(0, "lead", LONG, "(lead)"),
                _section(1, "plot", f"{LONG}\n{LONG}"),
                _section(2, "reception", LONG, "Reception"),  # 자르지 않는 절
            ],
        ),
        _doc("doc_char", "character", 200, [_section(0, "lead", LONG, "(lead)")], title="Tony"),
        _doc("doc_tv", "content", 300, [_section(1, "plot", LONG)], title="Agent Carter s1"),
        _doc("doc_list", "character_list", 400, [_section(0, "lead", LONG)]),
    ]
    return works, docs


def test_작품_문서는_work_id_wikipedia_인물은_wp_pageid() -> None:
    works, documents, chunks = build(*_sample())

    assert [w.work_id for w in works] == ["tt0371746", "tt9999999"]  # cnt_ 작품 제외
    assert {d.doc_id: d.work_id for d in documents} == {
        "tt0371746-wikipedia": "tt0371746",  # 에피소드가 아니라 영화가 대표
        "wp-200": None,
    }
    assert [c.chunk_id for c in chunks] == [
        "tt0371746-wikipedia-synopsis-p01",
        "tt0371746-wikipedia-plot-p02",
        "tt0371746-wikipedia-plot-p03",
        "wp-200-intro-p01",
    ]


def test_원본_ID와_절_번호를_남긴다() -> None:
    _, documents, chunks = build(*_sample())

    assert documents[0].source_ids["team_document_id"] == "doc_film"
    assert chunks[1].source_ids == {"team_document_id": "doc_film", "section_index": "1"}
    assert find_chunk_ids(chunks, "doc_film", 1) == [
        "tt0371746-wikipedia-plot-p02",
        "tt0371746-wikipedia-plot-p03",
    ]


def test_청크_칸이_채워진다() -> None:
    _, _, chunks = build(*_sample())
    first = chunks[0]

    assert first.section == Section.SYNOPSIS
    assert (first.position, first.total) == (1, 3)
    assert first.header == "Iron Man (2008 film) > (lead)"
    assert len(first.content_hash) == 64
    assert first.title_ko == "아이언맨"


def test_레시피를_바꾸면_청크가_달라지지만_원문_위치는_남는다() -> None:
    _, _, ck01 = build(*_sample(), recipe=CK01)
    _, _, ck02 = build(*_sample(), recipe=RECIPES["CK02"])

    assert len(ck02) > len(ck01)  # 600자 문단이 500자 레시피에서는 둘로 나뉨
    for chunks in (ck01, ck02):
        assert all(c.source_ids.get("section_index") for c in chunks)
        assert len({c.chunk_id for c in chunks}) == len(chunks)


def test_Work_값을_팀_모델에_맞춘다() -> None:
    works, _, _ = build(*_sample())
    w = works[0]

    assert (w.year, w.phase, w.is_canon, w.format) == (2008, "1", None, "movie")


# --- 실데이터 확인 (내 컴퓨터에서만, data/ 가 없으면 건너뜀) ---------------------------

_QUESTION_SET = PROJECT_ROOT / "question_set.jsonl"
_CITE = re.compile(r"(doc_[0-9a-f]{16}) / 섹션 (\d+)")


@pytest.mark.skipif(not HANDOFF_DIR.exists(), reason="handoff 데이터가 없음 (data/ 는 git 제외)")
def test_실데이터_골든셋_근거_절이_모두_청크에_있다() -> None:
    _, documents, chunks = load_handoff()
    missing = []
    for line in _QUESTION_SET.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        for doc, section in _CITE.findall(item["근거 문서·절·리비전"]):
            if not find_chunk_ids(chunks, doc, int(section)):
                missing.append((item["query_id"], doc, section))

    assert missing == []
    assert len({c.chunk_id for c in chunks}) == len(chunks)
    assert len({d.doc_id for d in documents}) == len(documents)
