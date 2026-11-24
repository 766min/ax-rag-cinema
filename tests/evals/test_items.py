import pytest

from evals.items import check_ready, gold_chunk_ids, to_eval_item
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
        total=2,
        text=text,
        source_url="https://en.wikipedia.org/wiki/Avengers:_Infinity_War",
        source_ids={"team_document_id": "doc_c19334b35dac57b1", "section_index": section_index},
    )


CHUNKS = [_chunk(1, "Opening scene on the Asgardian ship."), _chunk(2, EXCERPT_TEXT)]


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
    assert gold_chunk_ids(_raw(EXCERPT_TEXT[:60] + "…"), CHUNKS) == ["tt4154756-wikipedia-plot-p02"]


def test_발췌를_못_찾으면_절_전체를_정답_키로() -> None:
    assert gold_chunk_ids(_raw("text that is not in any chunk at all, really."), CHUNKS) == [
        "tt4154756-wikipedia-plot-p01",
        "tt4154756-wikipedia-plot-p02",
    ]


def test_문항_변환() -> None:
    item = to_eval_item(_raw(EXCERPT_TEXT), CHUNKS, "v2")
    assert (item.question_id, item.key_facts, item.gold_index_version) == (
        "Q003",
        ["생명 절반", "인피니티 스톤"],
        "v2",
    )


def test_근거_없음_문항은_정답_키가_없다() -> None:
    item = to_eval_item({**_raw(EXCERPT_TEXT), "answerable": False}, CHUNKS, "v2")
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
