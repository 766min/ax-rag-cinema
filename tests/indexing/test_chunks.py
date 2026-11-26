"""원문 위치를 보존하는 청킹 (src/build/chunks.py). 데이터셋 · DB · API 없이 돈다."""

from build.chunks import (
    RECIPES,
    Block,
    Recipe,
    chunk_spans,
    content_hash,
    index_version,
    make_chunks,
    paragraph_runs,
    sentence_spans,
)
from shared.core.models import Document, SourceId
from shared.core.schemas import Section

SHIELD = "He joins S.H.I.E.L.D. Howard Stark leads it. Dr. Strange agrees. Then they leave."


def texts(text: str, spans: list) -> list[str]:
    return [text[u.start : u.end] for u in spans]


def test_sentences_keep_initials_and_abbreviations() -> None:
    # 이니셜 뒤 대문자는 문장 끝인지 애매하므로 자르지 않는다
    assert texts(SHIELD, sentence_spans(SHIELD)) == [
        "He joins S.H.I.E.L.D. Howard Stark leads it.",
        "Dr. Strange agrees.",
        "Then they leave.",
    ]


def test_paragraph_runs_break_at_flat_tables() -> None:
    text = "First para.\n+ Table title a b c\nSecond para.\n\nThird para."
    runs = paragraph_runs(text)
    assert [texts(text, run) for run in runs] == [["First para."], ["Second para.", "Third para."]]


def test_spans_pack_paragraphs_and_stay_under_max() -> None:
    text = "\n".join(f"Paragraph number {i} has some words." for i in range(10))
    spans = chunk_spans(text, max_chars=120)
    assert all(u.end - u.start <= 120 for u, _ in spans)
    joined = "\n".join(text[u.start : u.end] for u, _ in spans)
    assert joined == text


def test_long_paragraph_splits_at_sentence_boundaries() -> None:
    para = " ".join(f"Sentence {i} is here." for i in range(30))
    spans = chunk_spans(para, max_chars=100)
    for unit, _ in spans:
        piece = para[unit.start : unit.end]
        assert piece.startswith("Sentence") and piece.endswith("here.")


def test_overlap_prepends_whole_sentences_without_moving_new_content() -> None:
    para = " ".join(f"Sentence {i} is here." for i in range(30))
    plain = chunk_spans(para, max_chars=100)
    over = chunk_spans(para, max_chars=100, overlap=1)
    assert [u.end for u, _ in plain] == [u.end for u, _ in over]
    assert over[0][1] == 0 and all(added > 0 for _, added in over[1:])
    second = para[over[1][0].start : over[1][0].end]
    previous_last = sentence_spans(para, plain[0][0].start, plain[0][0].end)[-1]
    assert second.startswith(para[previous_last.start : previous_last.end])


def test_hard_split_sentence_gets_no_overlap() -> None:
    long_sentence = "word, " * 60 + "end."
    spans = chunk_spans(long_sentence, max_chars=80, overlap=1)
    assert len(spans) > 1 and all(added == 0 for _, added in spans)
    assert all(u.end - u.start <= 80 for u, _ in spans)


def doc() -> Document:
    return Document(
        doc_id="tt0371746-wikipedia",
        work_id="tt0371746",
        source_id=SourceId.WIKIPEDIA_EN,
        source_url="https://en.wikipedia.org/w/index.php?oldid=1",
        revid=1,
        doc_type="film",
        source_ids={"team_document_id": "doc_x"},
    )


def section_row(text: str) -> dict:
    return {"section_index": 3, "section_path": ["Plot"], "text": text, "links": []}


def test_make_chunks_keeps_offsets_ids_and_hash() -> None:
    text = "\n".join(f"Tony Stark builds suit number {i} in the cave." for i in range(40))
    row = section_row(text)
    block = Block(row, "[Iron Man · 2008 film]", text, "s3")
    chunks = make_chunks(
        doc(),
        Section.PLOT,
        [block],
        RECIPES["s800"],
        title_ko="아이언맨",
        title_original="Iron Man",
    )
    assert [c.chunk_id for c in chunks] == [
        f"tt0371746-wikipedia-plot-p{i:02d}" for i in range(1, len(chunks) + 1)
    ]
    for c in chunks:
        start, end = int(c.source_ids["char_start"]), int(c.source_ids["char_end"])
        assert text[start:end] == c.text
        assert c.source_ids["parent_id"] == "tt0371746-wikipedia#s3"
        assert c.content_hash == content_hash(c.chunk_id, c.header, c.text)
        assert c.source_ids["license"] is None and c.source_ids["fetched_at"] is None


def test_header_off_changes_hash_and_version_but_not_text() -> None:
    text = "Tony Stark is captured. He escapes in a suit."
    block = Block(section_row(text), "[Iron Man · 2008 film]", text, "s3")
    names = {"title_ko": None, "title_original": "Iron Man"}
    on = make_chunks(doc(), Section.PLOT, [block], RECIPES["s800"], **names)
    off = make_chunks(doc(), Section.PLOT, [block], RECIPES["s800-nohdr"], **names)
    assert [c.text for c in on] == [c.text for c in off]
    assert off[0].header == "" and on[0].header
    assert index_version(on) != index_version(off)


def test_episode_blocks_are_not_merged_across_episodes() -> None:
    first = "Episode one summary sentence that is long enough to keep."
    second = "Episode two summary sentence that is long enough to keep."
    row = section_row("")
    blocks = [
        Block(row, "[S · Episode 1]", first, "s3e1", ["episode_summary"]),
        Block(row, "[S · Episode 2]", second, "s3e2", ["episode_summary"]),
    ]
    chunks = make_chunks(
        doc(), Section.PLOT, blocks, Recipe("t", max_chars=800), title_ko=None, title_original="S"
    )
    assert [c.text for c in chunks] == [first, second]
    assert [c.source_ids["parent_id"] for c in chunks] == [
        "tt0371746-wikipedia#s3e1",
        "tt0371746-wikipedia#s3e2",
    ]


def test_ck09_overlap_differs_by_section() -> None:
    ck09 = RECIPES["ck09"]
    assert ck09.overlap_for(Section.SYNOPSIS) == 0 and ck09.overlap_for(Section.INTRO) == 0
    assert ck09.overlap_for(Section.PLOT) == 2
    assert ck09.overlap_for(Section.BIOGRAPHY) == 1 and ck09.overlap_for(Section.LORE) == 1
