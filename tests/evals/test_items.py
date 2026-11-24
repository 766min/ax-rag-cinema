import pytest

from evals.items import check_ready, excerpt_core, gold_chunk_ids, map_citations, to_eval_item
from shared.core.models import Chunk, EvalItem, SourceId
from shared.core.schemas import Section

EXCERPT_TEXT = "Thanos wants to collect all six Infinity Stones to wipe out half of all life."


def _chunk(position: int, text: str, section_index: str = "1") -> Chunk:
    return Chunk(
        chunk_id=f"tt4154756-wikipedia-plot-p{position:02d}",
        doc_id="tt4154756-wikipedia",
        work_id="tt4154756",
        source_id=SourceId.WIKIPEDIA_EN,
        section=Section.PLOT,
        position=position,
        total=3,
        text=text,
        source_url="https://en.wikipedia.org/wiki/Avengers:_Infinity_War",
        source_ids={"team_document_id": "doc_c19334b35dac57b1", "section_index": section_index},
    )


CHUNKS = [_chunk(1, "Opening scene on the Asgardian ship."), _chunk(2, EXCERPT_TEXT)]
DOC = "doc_c19334b35dac57b1"


def _sections(*texts: str) -> dict[tuple[str, str], str]:
    """원문 절 = 청크들을 이어 붙인 글 (청크가 원문에서 잘려 나온 상황)."""
    return {(DOC, "1"): "\n".join(texts)}


SECTIONS = _sections(*(c.text for c in CHUNKS))


def _raw(excerpt: str) -> dict[str, object]:
    return {
        "query_id": "Q003",
        "question": "타노스는 왜 스톤을 모아?",
        "answerable": True,
        "expected_answer": "우주 생명 절반을 없애려고",
        "난이도": "초보",
        "채점 핵심 사실": "생명 절반 / 인피니티 스톤",
        "근거 문서·절·리비전": "[1] Avengers: Infinity War\ndoc_c19334b35dac57b1 / 섹션 1: Plot\nrevision 1",
        "원문 근거 발췌": f"[1] {excerpt}",
    }


def test_발췌가_든_청크만_정답_키로_고른다() -> None:
    raw = _raw(EXCERPT_TEXT[:60] + "…")
    assert gold_chunk_ids(raw, CHUNKS, SECTIONS) == ["tt4154756-wikipedia-plot-p02"]
    assert map_citations(raw, CHUNKS, SECTIONS)[0]["status"] == "contained"


def test_발췌를_못_찾으면_절_전체로_대신하지_않고_missing() -> None:
    raw = _raw("xx text that is not in any chunk at all, really yy")
    assert gold_chunk_ids(raw, CHUNKS, SECTIONS) == []
    result = map_citations(raw, CHUNKS, SECTIONS)[0]
    assert (result["status"], result["reason"]) == ("missing", "발췌가 원문 절에 없음")


def test_발췌가_청크_경계에_걸치면_split_으로_겹치는_청크_모두() -> None:
    part1 = "Thanos and his Black Order attack the Asgardian refugee ship near Earth"
    part2 = "Loki offers the Tesseract to save Thor but is killed by Thanos soon after"
    chunks = [_chunk(1, f"Intro. {part1}"), _chunk(2, f"{part2}. End.")]
    sections = _sections(*(c.text for c in chunks))
    result = map_citations(_raw(f"ab {part1} {part2} cd…"), chunks, sections)[0]

    assert result["status"] == "split"
    assert result["chunk_ids"] == [c.chunk_id for c in chunks]


def test_경계_문장이_어느_청크에도_통째로_없어도_위치로_찾는다() -> None:
    # 앞 60자 · 뒤 60자 방식으로는 못 찾던 경우 (경계가 두 조각 모두를 가로지름)
    text = "A" * 50 + " " + "B" * 50 + " " + "C" * 50
    chunks = [_chunk(1, text[:70]), _chunk(2, text[70:])]
    sections = _sections(text)
    sections[(DOC, "1")] = text
    result = map_citations(_raw(f"x {text} y"), chunks, sections)[0]
    assert result["status"] == "split"


def test_발췌_일부가_어느_청크에도_없으면_partial() -> None:
    kept = "Thanos and his Black Order attack the Asgardian refugee ship near Earth"
    dropped = "and this sentence was dropped from every chunk"
    chunks = [_chunk(1, kept)]
    sections = {(DOC, "1"): f"{kept} {dropped}"}
    result = map_citations(_raw(f"ab {kept} {dropped} cd"), chunks, sections)[0]
    assert (result["status"], result["chunk_ids"]) == ("partial", [chunks[0].chunk_id])
    assert result["uncovered_chars"] > 0


def test_발췌의_잘린_첫_단어와_마지막_단어는_뺀다() -> None:
    assert excerpt_core(".I.E.L.D. to power weapons, and the s…") == "to power weapons, and the"


def test_문항_변환() -> None:
    item = to_eval_item(_raw(EXCERPT_TEXT), CHUNKS, SECTIONS, "v2")
    assert (item.question_id, item.key_facts, item.gold_index_version) == (
        "Q003",
        ["생명 절반", "인피니티 스톤"],
        "v2",
    )


def test_근거_없음_문항은_정답_키가_없다() -> None:
    item = to_eval_item({**_raw(EXCERPT_TEXT), "answerable": False}, CHUNKS, SECTIONS, "v2")
    assert (item.gold_chunk_ids, item.gold_index_version) == ([], None)


def _item(**kw: object) -> EvalItem:
    base: dict[str, object] = {
        "question_id": "Q1",
        "question": "q",
        "answerable": True,
        "gold_chunk_ids": ["a"],
        "gold_index_version": "v2",
    }
    return EvalItem.model_validate({**base, **kw})


def test_정답_키_기준_색인이_없거나_다르면_평가를_시작하지_않는다() -> None:
    check_ready(
        [_item(), _item(answerable=False, gold_chunk_ids=[], gold_index_version=None)], "v2"
    )
    with pytest.raises(ValueError, match="gold_index_version"):
        check_ready([_item(gold_index_version=None)], "v2")
    with pytest.raises(ValueError, match="v1 기준"):
        check_ready([_item(gold_index_version="v1")], "v2")
    with pytest.raises(ValueError, match="정답 키 없음"):
        check_ready([_item(gold_chunk_ids=[])], "v2")
