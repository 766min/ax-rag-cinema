import asyncio
from types import SimpleNamespace
from typing import Any

from retrieve.parent import chunk_blocks
from retrieve.pipeline import MODES, Retriever, SearchConfig
from service.answer import INSTRUCTION_KO, SYSTEM, make_generate_en, make_retrieve, parse_draft
from service.answer_cache import AnswerCache
from service.movie_qa import Evidence
from shared.core.models import Alias, Candidate, Chunk, SourceId
from shared.core.schemas import Section

ALIASES = [Alias(alias_ko="아이언맨", canonical_en="Iron Man", kind="work", target_id="tt0371746")]
Q = "아이언맨은 누구야?"


def chunk(n: int) -> Chunk:
    return Chunk(
        chunk_id=f"tt0371746-wikipedia-plot-p{n:02d}",
        doc_id="tt0371746-wikipedia",
        work_id="tt0371746",
        source_id=SourceId.WIKIPEDIA_EN,
        section=Section.PLOT,
        position=n,
        total=9,
        text=f"Tony Stark fact number {n}.",
        header="[Iron Man · 2008 film]",
        title_ko="아이언맨",
        title_original="Iron Man",
        source_url="https://en.wikipedia.org/w/index.php?oldid=7",
        revid=7,
        source_ids={
            "parent_id": "tt0371746-wikipedia#s1",
            "char_start": str(n * 30),
            "char_end": str(n * 30 + 25),
            "team_section_path": "Plot",
            "fetched_at": "2026-11-23T00:00:00+00:00",
            "license": "CC BY-SA 4.0",
            "overlap_chars": "0",
        },
    )


def ev(n: int, rank: int) -> Evidence:
    c = chunk(n)
    cand = Candidate(
        chunk_id=c.chunk_id,
        rank=rank,
        rank_before_rerank=None,
        vector_score=0.5,
        bm25_score=None,
        rrf_score=None,
        rerank_score=None,
    )
    return Evidence(
        chunk_id=c.chunk_id,
        text=c.text,
        source_url=c.source_url,
        chunk=c,
        candidate=cand,
    )


class FakeLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[list[Any]] = []

    async def ainvoke(self, messages: list[Any]) -> Any:
        self.prompts.append(messages)
        return SimpleNamespace(content=self.reply)


def test_prompt_states_the_team_instruction() -> None:
    assert INSTRUCTION_KO in SYSTEM
    assert "NO_EVIDENCE" in SYSTEM


def test_parse_draft_keeps_allowed_citations_and_coverage() -> None:
    blocks = chunk_blocks([chunk(1), chunk(2)])
    raw = (
        f"Stark builds a suit [{chunk(1).chunk_id}, tt9999999-card]. "
        f"Again [{chunk(1).chunk_id}].\n"
        "COVERAGE: partial"
    )
    draft = parse_draft(raw, blocks)
    assert draft.cited_chunk_ids == [chunk(1).chunk_id]
    assert draft.dropped_citations == ["tt9999999-card"]
    assert draft.partial is True
    assert "COVERAGE" not in draft.answer_en
    assert parse_draft("NO_EVIDENCE", blocks).no_evidence is True


def test_parent_context_cites_retrieved_chunk_ids_only() -> None:
    source = "".join(f"Tony Stark fact number {n}.".ljust(30) for n in range(0, 9))
    llm = FakeLLM(f"Stark. [{chunk(2).chunk_id}]\nCOVERAGE: full")
    gen = make_generate_en(
        llm,
        context="parent",
        sources={"tt0371746-wikipedia#s1": source},
    )
    draft = asyncio.run(gen(Q, [ev(2, 1), ev(3, 2)]))
    assert draft.cited_chunk_ids == [chunk(2).chunk_id]
    assert draft.read[0]["ids"] == [chunk(2).chunk_id, chunk(3).chunk_id]
    human = llm.prompts[0][1].content
    assert human.startswith(f"Question (Korean): {Q}")
    assert "Tony Stark fact number 8." in human


def test_answer_cache_miss_hit_and_version_change() -> None:
    versions = {
        "index_version": "v1",
        "llm_model": "m",
        "prompt_version": "p3-gather",
    }
    cache = AnswerCache(versions, {"top_k": 5})
    key = cache.key(Q, "A")
    assert cache.get(key) is None
    value = {"status": "answered", "answer": "Stark."}
    cache.put(key, value)
    assert cache.get(key) == value
    assert cache.stats["miss"] == 1
    assert cache.stats["hit"] == 1
    assert cache.stats["put"] == 1
    changed = AnswerCache({**versions, "index_version": "v2"}, {"top_k": 5})
    assert cache.key(Q, "A") != changed.key(Q, "A")


def test_make_retrieve_turns_candidates_into_evidence() -> None:
    class Store:
        def similarity_search_with_score(self, query: str, k: int) -> list:
            return [(SimpleNamespace(metadata={"chunk_id": chunk(2).chunk_id}), 0.25)]

    retriever = Retriever(Store(), [chunk(1), chunk(2)])
    found = asyncio.run(make_retrieve(retriever, MODES["A"], SearchConfig())("q"))
    assert [e.chunk_id for e in found] == [chunk(2).chunk_id]
    assert found[0].chunk == chunk(2)
    assert found[0].candidate is not None
    assert found[0].candidate.rank == 1
