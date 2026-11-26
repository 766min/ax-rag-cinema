"""적재 계획과 시험 실행의 규칙 확인 (DB · API 없이). 실행: uv run pytest test/youngmin -q"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "chunking"))

from build_chunks import index_version, version_of  # noqa: E402
from eval_run import cited_ids  # noqa: E402
from load_index import embed_input, metadata, plan_load  # noqa: E402
from retrieval import Corpus, Hit, Searcher, rrf, tokenize  # noqa: E402

from shared.core.models import Chunk, SourceId  # noqa: E402
from shared.core.schemas import Section  # noqa: E402


def chunk(position: int, text: str = "a" * 200, header: str = "[Iron Man · 2008 film]") -> Chunk:
    return Chunk(
        chunk_id=f"tt0371746-wikipedia-plot-p{position:02d}",
        doc_id="tt0371746-wikipedia",
        work_id="tt0371746",
        source_id=SourceId.WIKIPEDIA_EN,
        section=Section.PLOT,
        position=position,
        total=max(3, position),
        text=text,
        header=header,
        source_url="https://en.wikipedia.org/w/index.php?oldid=1",
        revid=1,
        content_hash=f"h{position}-{len(text)}",
    )


def row(c: Chunk) -> tuple[dict, str]:
    return metadata(c), c.text


def test_plan_splits_new_changed_same_and_gone() -> None:
    same, changed, new = chunk(1), chunk(2, text="b" * 300), chunk(3)
    revised = chunk(1).model_copy(update={"revid": 2})  # 해시는 같고 판 번호만 바뀌었다
    existing = {
        same.chunk_id: row(revised),
        changed.chunk_id: row(chunk(2)),
        "tt0371746-wikipedia-plot-p09": row(chunk(9)),
    }
    plan = plan_load([same, changed, new], existing)
    assert [c.chunk_id for c in plan.insert] == [new.chunk_id]
    assert [c.chunk_id for c in plan.update] == [changed.chunk_id]
    assert [c.chunk_id for c in plan.refresh] == [same.chunk_id]  # 임베딩 없이 메타데이터만 맞춘다
    assert plan.delete == ["tt0371746-wikipedia-plot-p09"]
    assert plan.counts() == {
        "inserted": 1,
        "updated": 1,
        "unchanged_skipped": 1,
        "metadata_refreshed": 1,
        "deleted": 1,
    }


def test_second_load_skips_everything() -> None:
    chunks = [chunk(1), chunk(2)]
    plan = plan_load(chunks, {c.chunk_id: row(c) for c in chunks})
    assert plan.counts()["unchanged_skipped"] == 2 and not (plan.insert or plan.update)


def test_row_round_trips_to_the_same_chunk() -> None:
    c = chunk(1)
    meta, document = row(c)
    assert "text" not in meta
    assert Chunk.model_validate({**meta, "text": document}) == c


def test_embedding_sees_header_but_stored_text_does_not() -> None:
    c = chunk(1, text="Tony Stark builds a suit.")
    assert embed_input(c) == "[Iron Man · 2008 film]\nTony Stark builds a suit."
    assert row(c)[1] == "Tony Stark builds a suit."


def test_db_version_matches_file_version() -> None:
    chunks = [chunk(2), chunk(1)]
    pairs = [(c.chunk_id, c.content_hash) for c in reversed(chunks)]
    assert version_of(pairs) == index_version(chunks)  # 순서와 상관없다


def test_citations_outside_the_candidates_are_dropped() -> None:
    candidates = ["tt0371746-wikipedia-plot-p01", "tt0371746-card"]
    answer = (
        "Stark builds a suit [tt0371746-wikipedia-plot-p01]. "
        "It is from 2008 [tt0371746-card, tt9999999-card]. Again [tt0371746-wikipedia-plot-p01]."
    )
    assert cited_ids(answer, candidates) == ["tt0371746-wikipedia-plot-p01", "tt0371746-card"]
    assert cited_ids("NO_EVIDENCE", candidates) == []


# ---------- 검색 부품 (B · C)


def test_rrf_rewards_agreement_between_rankings() -> None:
    fused = rrf([["a", "b", "c"], ["c", "d"]])
    assert [cid for cid, _ in fused] == ["c", "a", "b", "d"]  # 두 순위표에 다 나온 c 가 1위
    assert fused[0][1] == 1 / 63 + 1 / 61


def test_rrf_ties_keep_first_ranking_order() -> None:
    assert [cid for cid, _ in rrf([["a"], ["b"]])] == ["a", "b"]


def test_bm25_matches_the_appended_english_name() -> None:
    corpus = Corpus(
        [
            (
                "x-card",
                "Iron Man is a film released in 2008.",
                {"header": "[Iron Man · 2008 film]"},
            ),
            ("y-card", "Thor is a film released in 2011.", {"header": "[Thor · 2011 film]"}),
            ("z-card", "Echo is a TV series.", {"header": "[Echo · 2024 series]"}),
        ]
    )
    assert tokenize("아이언맨 2는? Iron Man") == ["아이언맨", "2는", "iron", "man"]
    assert [cid for cid, _ in corpus.bm25_search("아이언맨은 언제 나왔어? Iron Man", k=5)] == [
        "x-card"
    ]


class _Doc:
    def __init__(self, cid: str) -> None:
        self.metadata = {"chunk_id": cid, "header": ""}
        self.page_content = cid


class _Store:
    def similarity_search_with_score(self, query: str, k: int) -> list:
        return [(_Doc("v1"), 0.1), (_Doc("both"), 0.2)][:k]


class _Reranker:
    name = "fake"

    def scores(self, query: str, texts: list[str]) -> list[float]:
        return [{"v1": 0.1, "both": 0.5, "b1": 0.9}[t] for t in texts]


def test_each_mode_adds_one_stage() -> None:
    corpus = Corpus([("both", "both", {}), ("b1", "b1", {})])
    corpus.bm25_search = lambda q, k: [("b1", 2.0), ("both", 1.0)]  # type: ignore[method-assign]
    a = Searcher("A", _Store()).retrieve("q")
    assert [h.chunk_id for h in a] == ["v1", "both"] and a[0].bm25_score is None
    b = Searcher("B", _Store(), corpus).retrieve("q")
    assert [h.chunk_id for h in b] == ["both", "v1", "b1"]  # 벡터 2위 + BM25 2위 > 각 1위 하나씩
    assert b[0].vector_score == 0.8 and b[0].bm25_score == 1.0 and b[0].rrf_score
    c = Searcher("C", _Store(), corpus, _Reranker()).retrieve("q")  # type: ignore[arg-type]
    assert [h.chunk_id for h in c] == ["b1", "both", "v1"]
    assert [h.rank_before_rerank for h in c] == [3, 1, 2]
    assert isinstance(c[0], Hit)


# ---------- 답변 채점


def test_verdict_must_cover_every_key_fact() -> None:
    from judge import parse_verdict

    good = 'ok {"facts": [{"n": 1, "verdict": "met"}, {"n": 2, "verdict": "Partial"}], "contradiction": false}'
    assert parse_verdict(good, 2)["verdicts"] == ["met", "partial"]
    for bad in (
        '{"facts": [{"n": 1, "verdict": "met"}]}',
        '{"facts": [{"verdict": "yes"}]}',
        "no json",
    ):
        try:
            parse_verdict(bad, 2 if "yes" not in bad else 1)
        except ValueError:
            continue
        raise AssertionError(f"모양이 틀린 판정을 받아들였습니다: {bad}")


def test_correct_means_every_fact_met_and_no_contradiction() -> None:
    from judge import score

    assert score(["met", "met"], False) == {"passed": True, "fact_rate": 1.0}
    assert score(["met", "partial"], False) == {"passed": False, "fact_rate": 0.75}
    assert score(["met", "met"], True)["passed"] is False


# ---------- 원칙 5: 작게 찾고 크게 넘긴다


def test_window_grows_around_the_hit_until_the_limit() -> None:
    from retrieval import window

    members = ["p1", "p2", "p3", "p4", "p5"]
    texts = {m: m * 50 for m in members}  # 100자씩, 줄바꿈 1자
    assert window(members, texts, ["p3"], 1000) == "\n".join(
        texts[m] for m in members
    )  # 전부 들어간다
    small = window(members, texts, ["p3"], 302)  # p3 + 앞뒤 하나씩 = 302자
    assert small == "…\n" + "\n".join(texts[m] for m in ["p2", "p3", "p4"]) + "\n…"
    far = window(members, texts, ["p1", "p5"], 250)  # 검색된 둘만으로 넘치면 둘만 잇는다
    assert far == texts["p1"] + "\n…\n" + texts["p5"]


def test_hits_in_one_section_become_one_block_and_cards_stay_whole() -> None:
    from retrieval import parent_blocks

    def meta(parent: str | None, position: int) -> dict:
        return {"header": "[h]", "position": position, "source_ids": {"parent_id": parent}}

    corpus = Corpus(
        [
            ("d-plot-p01", "a" * 50, meta("d#s1", 1)),
            ("d-plot-p02", "b" * 50, meta("d#s1", 2)),
            ("d-plot-p03", "c" * 50, meta("d#s1", 3)),
            ("tt1-card", "card text", meta(None, 1)),
        ]
    )
    top = [corpus.hits["d-plot-p03"], corpus.hits["tt1-card"], corpus.hits["d-plot-p01"]]
    blocks = parent_blocks(top, corpus, 1000)
    assert [b.ids for b in blocks] == [["d-plot-p03", "d-plot-p01"], ["tt1-card"]]
    assert blocks[0].text == "\n".join(["a" * 50, "b" * 50, "c" * 50])  # 부모 전체, 문서 순서대로
    assert blocks[1].text == "card text"
