"""부모 문맥 (src/retrieve/parent.py)."""

import pytest

from retrieve.parent import chunk_blocks, parent_blocks
from shared.core.models import Chunk, SourceId
from shared.core.schemas import Section

PARENT = "d#s1"
# 100자 문단 10개, 줄바꿈으로 구분 (원문 1,009자)
SOURCE = "\n".join(chr(ord("a") + i) * 100 for i in range(10))


def chunk(i: int, overlap: str = "0", parent: str | None = PARENT) -> Chunk:
    """i 번째 문단과 정확히 같은 위치를 가리키는 청크"""
    start = i * 101
    ids = {"char_start": str(start), "char_end": str(start + 100), "overlap_chars": overlap}
    if parent:
        ids["parent_id"] = parent
    return Chunk(
        chunk_id=f"tt0371746-wikipedia-plot-p{i + 1:02d}",
        doc_id="tt0371746-wikipedia",
        work_id="tt0371746",
        source_id=SourceId.WIKIPEDIA_EN,
        section=Section.PLOT,
        position=i + 1,
        total=10,
        text=SOURCE[start : start + 100],
        header="[h]",
        source_url="https://example.org",
        source_ids=ids,
    )


def card() -> Chunk:
    return Chunk(
        chunk_id="tt0371746-card",
        doc_id="tt0371746",
        work_id="tt0371746",
        source_id=SourceId.WIKIPEDIA_EN,
        section=Section.CARD,
        position=1,
        total=1,
        text="card text",
        source_url="https://example.org",
    )


def test_small_parent_is_passed_whole() -> None:
    [block] = parent_blocks([chunk(3)], {PARENT: SOURCE}, max_chars=2000)
    assert (
        block.text == SOURCE and block.span == (0, len(SOURCE)) and block.ids == [chunk(3).chunk_id]
    )


def test_large_parent_is_cut_from_source_around_the_hit() -> None:
    [block] = parent_blocks([chunk(5)], {PARENT: SOURCE}, max_chars=302)
    lo, hi = block.span or (0, 0)
    assert hi - lo <= 302 and lo <= 5 * 101 and hi >= 5 * 101 + 100
    assert block.text == "…\n" + SOURCE[lo:hi] + "\n…"
    assert SOURCE[lo:hi] == "\n".join(ch * 100 for ch in "efg")  # 줄 경계에 맞춰 앞뒤 한 문단씩


def test_hits_in_one_parent_become_one_block_in_rank_order() -> None:
    blocks = parent_blocks([chunk(2), card(), chunk(0)], {PARENT: SOURCE}, max_chars=2000)
    assert [b.ids for b in blocks] == [[chunk(2).chunk_id, chunk(0).chunk_id], ["tt0371746-card"]]
    assert blocks[1].text == "card text" and blocks[1].span is None


def test_far_apart_hits_are_joined_without_pretending_to_be_contiguous() -> None:
    [block] = parent_blocks([chunk(0), chunk(9)], {PARENT: SOURCE}, max_chars=250)
    assert block.contiguous is False
    assert block.text == SOURCE[0:100] + "\n…\n" + SOURCE[909:1009]


def test_overlapping_index_is_refused() -> None:
    with pytest.raises(ValueError, match="겹침"):
        parent_blocks([chunk(1, overlap="12")], {PARENT: SOURCE})


def test_chunk_blocks_keep_one_chunk_each() -> None:
    blocks = chunk_blocks([chunk(1), card()])
    assert [b.ids for b in blocks] == [[chunk(1).chunk_id], ["tt0371746-card"]]
    assert blocks[0].render().startswith(f"[{chunk(1).chunk_id}] [h]\n")
