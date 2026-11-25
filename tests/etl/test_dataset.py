import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from etl.chunking import RECIPES
from etl.dataset import (
    DATASET_DIR,
    build_chunks,
    collection_for,
    dataset_fingerprint,
    index_version,
    load_dataset,
    scope_documents,
)
from evals.items import load_eval_items, map_citations, read_question_set
from shared.core.schemas import Section

CK01 = RECIPES["CK01"]
LONG = "A" * 600


# --- 가짜 데이터셋 (실제 파일과 같은 칸, 내용은 임의) ----------------------------------


def _work(work_id: str, is_canon: bool = True, **kw: Any) -> dict[str, Any]:
    return {
        "work_id": work_id,
        "title_ko": "아이언맨",
        "title_original": "Iron Man",
        "format": "movie",
        "year": 2008,
        "season": None,
        "parent_work_id": None,
        "phase": "1",
        "timeline_order": 5,
        "is_canon": is_canon,
        "source_ids": {},
        **kw,
    }


def _work_meta(work_id: str, row_kind: str = "work", wave: str = "1") -> dict[str, Any]:
    return {"work_id": work_id, "row_kind": row_kind, "status": "released", "collect_wave": wave}


def _doc(doc_id: str, doc_type: str, work_id: str | None, team_id: str) -> dict[str, Any]:
    return {
        "doc_id": doc_id,
        "work_id": work_id,
        "source_id": "wikipedia_en",
        "source_url": "https://en.wikipedia.org/w/index.php?oldid=1",
        "license": "CC BY-SA 4.0",
        "revid": 1,
        "fetched_at": "2026-11-23T22:05:08Z",
        "doc_type": doc_type,
        "source_ids": {"team_document_id": team_id},
    }


def _section(doc_id: str, index: int, kind: str, team: str | None, text: str = LONG):
    return {
        "doc_id": doc_id,
        "section_index": index,
        "team_section": team,
        "section_kind": kind,
        "section_path": ["(lead)" if kind == "lead" else kind.title()],
        "text": text,
    }


def _write(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _fake_dataset(root: Path) -> Path:
    works = [
        _work("tt0371746"),  # 색인 작품
        _work("tt0000001", is_canon=False),  # 정식 아님 → 본문 제외
        _work("tt9140554"),  # 시리즈 전체 행 → 제외
    ]
    meta = [
        _work_meta("tt0371746"),
        _work_meta("tt0000001"),
        _work_meta("tt9140554", row_kind="series_parent"),
    ]
    docs = [
        _doc("tt0371746-wikipedia", "film", "tt0371746", "doc_film"),
        _doc("tt0000001-wikipedia", "film", "tt0000001", "doc_noncanon"),
        _doc("tt9140554-wikipedia", "series", "tt9140554", "doc_parent"),
        _doc("wp-1", "character", None, "doc_char"),
        _doc("wp-2", "character", None, "doc_char_other"),  # MCU 아님 → 제외
        _doc("wp-3", "universe", None, "doc_world"),
        _doc("wp-4", "episode", None, "doc_ep"),  # 범위 밖 종류
    ]
    docs_meta = [
        {"doc_id": d["doc_id"], "title": f"T {d['doc_id']}", "scope_status": "mcu"} for d in docs
    ]
    docs_meta[4]["scope_status"] = "non_mcu"
    sections = [
        _section("tt0371746-wikipedia", 0, "lead", "synopsis"),
        _section("tt0371746-wikipedia", 1, "plot", "plot", f"{LONG}\n{LONG}"),
        _section("tt0371746-wikipedia", 2, "reception", None),
        _section("tt0000001-wikipedia", 0, "lead", "synopsis"),
        _section("tt9140554-wikipedia", 0, "lead", "synopsis"),
        _section("wp-1", 0, "lead", "intro"),
        _section("wp-1", 3, "biography", None),
        _section("wp-1", 9, "reception", None),
        _section("wp-2", 0, "lead", "intro"),
        _section("wp-3", 0, "lead", None),
        _section("wp-3", 23, "other", None),
        _section("wp-4", 0, "lead", None),
    ]
    _write(root / "works.jsonl", works)
    _write(root / "extra" / "works_meta.jsonl", meta)
    _write(root / "documents.jsonl", docs)
    _write(root / "extra" / "documents_meta.jsonl", docs_meta)
    _write(root / "sections.jsonl", sections)
    names = ["works.jsonl", "documents.jsonl", "sections.jsonl"]
    (root / "SHA256SUMS.txt").write_text(
        "".join(f"{hashlib.sha256((root / n).read_bytes()).hexdigest()}  {n}\n" for n in names)
    )
    return root


def test_색인_범위는_팀_규칙_작품과_MCU_인물_세계관_문서(tmp_path: Path) -> None:
    data = load_dataset(_fake_dataset(tmp_path))

    assert [d.doc_id for d in scope_documents(data)] == ["tt0371746-wikipedia", "wp-1", "wp-3"]
    assert [(d.doc_id, s["section_index"], sec) for d, s, sec in data.scope] == [
        ("tt0371746-wikipedia", 0, Section.SYNOPSIS),
        ("tt0371746-wikipedia", 1, Section.PLOT),
        ("wp-1", 0, Section.INTRO),
        ("wp-1", 3, Section.BIOGRAPHY),
        ("wp-3", 0, Section.LORE),
        ("wp-3", 23, Section.LORE),
    ]
    # 범위 밖 절도 원문은 남긴다 (정답 근거가 범위 밖이면 missing 으로 드러나게)
    assert data.sections[("doc_ep", "0")] == LONG


def test_청크_ID는_팀_규칙이고_문서_안_순번이며_원래_ID를_남긴다(tmp_path: Path) -> None:
    chunks = build_chunks(load_dataset(_fake_dataset(tmp_path)), CK01)

    assert [c.chunk_id for c in chunks] == [
        "tt0371746-wikipedia-synopsis-p01",
        "tt0371746-wikipedia-plot-p02",
        "tt0371746-wikipedia-plot-p03",
        "wp-1-intro-p01",
        "wp-1-biography-p02",
        "wp-3-lore-p01",
        "wp-3-lore-p02",
    ]
    film, char = chunks[0], chunks[4]
    assert (film.work_id, film.title_ko, film.total) == ("tt0371746", "아이언맨", 3)
    assert (char.work_id, char.title_original) == (None, "T wp-1")
    assert char.header == "T wp-1 > Biography"
    assert char.source_ids == {"team_document_id": "doc_char", "section_index": "3"}
    assert all(c.content_hash for c in chunks)


def test_체크섬이_다르면_멈추고_같으면_목록_파일의_해시를_쓴다(tmp_path: Path) -> None:
    root = _fake_dataset(tmp_path)
    fp = dataset_fingerprint(root)
    assert fp["sha256"] == hashlib.sha256((root / "SHA256SUMS.txt").read_bytes()).hexdigest()
    assert fp["files"] == "3"
    assert index_version(CK01, fp) == f"CK01-para-m150-min10@{fp['sha256'][:8]}"
    assert collection_for("mcu_movies", CK01, fp) == f"mcu_movies_ck01_{fp['sha256'][:8]}"

    (root / "works.jsonl").write_text("{}\n")
    with pytest.raises(ValueError, match="works.jsonl"):
        dataset_fingerprint(root)


# --- 실데이터 확인 (내 컴퓨터에서만, data/ 가 없으면 건너뜀) ---------------------------

needs_data = pytest.mark.skipif(
    not DATASET_DIR.exists(), reason="팀 데이터셋이 없음 (data/ 는 git 제외)"
)


@needs_data
def test_실데이터_색인_범위는_문서_156개() -> None:
    data = load_dataset()
    kinds = Counter(d.doc_type for d in scope_documents(data))
    assert sum(kinds.values()) == 156
    assert kinds["character"] == 63 and kinds["universe"] == 29


@needs_data
@pytest.mark.parametrize("recipe", list(RECIPES.values()), ids=list(RECIPES))
def test_실데이터_근거_55건이_모두_청크에_매핑되고_Q019는_정답키가_없다(recipe) -> None:
    data = load_dataset()
    chunks = build_chunks(data, recipe)
    assert len({c.chunk_id for c in chunks}) == len(chunks)

    citations = [
        c for raw in read_question_set() for c in map_citations(raw, chunks, data.sections)
    ]
    assert len(citations) == 55
    assert [c for c in citations if c["status"] in ("missing", "partial")] == []

    items = {i.question_id: i for i in load_eval_items(chunks, data.sections, "v")}
    assert all(i.gold_chunk_ids for i in items.values() if i.answerable)
    # Q019 는 근거 없음 문항 (코믹스 질문) — 인용은 있지만 정답 키는 비어야 한다
    assert not items["Q019"].answerable and items["Q019"].gold_chunk_ids == []
    assert any(c["question_id"] == "Q019" for c in citations)
