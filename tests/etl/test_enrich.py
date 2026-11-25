from fakes import FakeLLM, FakeStore

from etl.dataset import Dataset
from etl.enrich import enriched_index_texts, enrichment_text, map_enrichment
from etl.load import index_text, to_document
from retrieval.normalize import AliasNormalizer
from retrieval.pipeline import Retriever, SearchConfig, run_question
from shared.core.models import Chunk, SourceId
from shared.core.schemas import RetrievalMode, Section

PLOT = (
    "Tony Stark builds a suit in a cave. He escapes and returns home. "
    "Obadiah Stane betrays him and steals the reactor. Stark defeats Stane in a final battle."
)


def _chunk(position: int, text: str, section: Section = Section.PLOT) -> Chunk:
    return Chunk(
        chunk_id=f"tt0371746-wikipedia-{section}-p{position:02d}",
        doc_id="tt0371746-wikipedia",
        work_id="tt0371746",
        source_id=SourceId.WIKIPEDIA_EN,
        section=section,
        position=position,
        total=3,
        text=text,
        header="Iron Man > Plot",
        source_url="https://en.wikipedia.org/w/index.php?oldid=1",
        source_ids={"team_document_id": "doc_x", "section_index": "1"},
    )


def _row(chunk_id: str, text: str, summary: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "doc_id": "doc_x",
        "position": chunk_id[-1],
        "text": text,
        "summary_original": summary,
        "events": [{"event": "Stane betrays Stark", "cause": "greed", "consequence": None}],
        "entities": ["Obadiah Stane"],
    }


def _data() -> Dataset:
    return Dataset(works={}, documents={}, titles={}, scope=[], sections={("doc_x", "1"): PLOT})


def test_원문_위치가_겹치는_가공본만_붙이고_원문은_그대로_둔다() -> None:
    first = _chunk(1, "Tony Stark builds a suit in a cave. He escapes and returns home.")
    second = _chunk(2, "Obadiah Stane betrays him and steals the reactor. Stark defeats Stane.")
    intro = _chunk(3, "Iron Man is a film.", Section.INTRO)
    # 가공본은 글자 복원 전 본문 (아포스트로피 · 공백이 달라도 영문자 · 숫자로 찾는다)
    rows = [
        _row(
            "doc_x-plot-p1",
            "Obadiah  Stane betrays him and steals the reactor. Stark defeats Stane in a final",
            "Stane betrays.",
        ),
        _row("doc_x-plot-p2", "Text from an episode summary that is not in the section.", "x"),
    ]
    mappings, stats = map_enrichment(_data(), [first, second, intro], {"doc_x": rows})

    assert list(mappings) == [second.chunk_id]  # 겹치는 청크만, plot 절만
    assert stats["enriched_rows_located"] == 1
    assert stats["enriched_rows_not_located"] == ["doc_x-plot-p2"]

    texts = enriched_index_texts([first, second, intro], mappings, {"doc_x": rows})
    assert texts[first.chunk_id] == index_text(first)  # 가공본 없는 청크는 C 와 같은 글
    assert texts[second.chunk_id].startswith(index_text(second))
    assert "[LLM-derived context, not source text]" in texts[second.chunk_id]
    assert "Summary: Stane betrays." in texts[second.chunk_id]
    assert second.text == "Obadiah Stane betrays him and steals the reactor. Stark defeats Stane."


def test_가공_정보는_요약_사건_개체를_한_번씩() -> None:
    text = enrichment_text([_row("a1", "", "S."), _row("a2", "", "S.")])
    assert text.count("Summary: S.") == 1
    assert "Event: Stane betrays Stark greed" in text
    assert text.endswith("Entities: Obadiah Stane")


def test_색인_글은_바꿀_수_있고_메타데이터의_원문_칸은_비운다() -> None:
    chunk = _chunk(1, "Original.")
    doc = to_document(chunk, "v", page_content="Original. + LLM context")
    assert doc.page_content == "Original. + LLM context"
    assert "text" not in doc.metadata


def test_D는_가공된_글로_검색하고_답변_문맥에는_원문만_넣는다() -> None:
    class SeeContext(FakeLLM):
        def with_structured_output(self, schema):  # noqa: ANN001, ANN201
            runnable = super().with_structured_output(schema)
            outer = self

            def capture(payload):  # noqa: ANN001, ANN202
                outer.seen = payload
                return runnable.invoke(payload)

            from langchain_core.runnables import RunnableLambda

            return RunnableLambda(capture)

    store = FakeStore([("a", 0.9)])
    retriever = Retriever(
        store,  # type: ignore[arg-type]
        {"a": "Original. [LLM-derived context] secret"},
        context={"a": "Original."},
    )
    llm = SeeContext()
    run_question(
        "질문",
        RetrievalMode.VECTOR,
        retriever,
        AliasNormalizer([]),
        llm,  # type: ignore[arg-type]
        SearchConfig(top_k=1, pool=1),
    )
    context = llm.seen.to_messages()[0].content
    assert "Original." in context and "LLM-derived" not in context
