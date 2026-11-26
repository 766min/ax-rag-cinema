import pytest

from retrieve.pipeline import MODES, REWRITE_MODES, Retriever, SearchConfig, mode_of
from retrieve.refine import rerank
from shared.core.models import Candidate, Chunk, SourceId
from shared.core.schemas import RetrievalMode, Section


def chunk(cid: str, text: str) -> Chunk:
    return Chunk(
        chunk_id=f"tt0371746-wikipedia-plot-p{cid}",
        doc_id="tt0371746-wikipedia",
        work_id="tt0371746",
        source_id=SourceId.WIKIPEDIA_EN,
        section=Section.PLOT,
        position=int(cid),
        total=9,
        text=text,
        source_url="https://en.wikipedia.org/w/index.php?oldid=1",
    )


# V1 = 벡터에만 걸림 · BOTH = 벡터와 BM25 둘 다 · B1 = BM25 에만 걸림
V1, BOTH, B1 = chunk("01", "zzz"), chunk("02", "armor"), chunk("03", "armor suit")

# BM25Okapi 는 절반 넘는 문서에 나오는 낱말의 IDF 가 음수가 되므로, 관계없는 문서를 채운다
FILLER = [chunk(f"0{i}", f"other words {i}") for i in range(4, 9)]


class _Doc:
    """가짜 벡터 저장소의 문서."""

    def __init__(self, c: Chunk) -> None:
        self.metadata = {"chunk_id": c.chunk_id}


class _Store:
    """가짜 벡터 저장소. 언제나 V1, BOTH 순서를 돌려준다."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def similarity_search_with_score(self, query: str, k: int) -> list:
        self.queries.append(query)
        return [(_Doc(V1), 0.1), (_Doc(BOTH), 0.2)][:k]


class _Reranker:
    """가짜 재정렬기. 글이 길수록 높은 점수를 주고, 받은 개수를 seen 에 남긴다."""

    name = "fake"

    def __init__(self) -> None:
        self.seen: list[int] = []

    def score(self, query: str, texts: list[str]) -> list[float]:
        self.seen.append(len(texts))
        return [len(t) / 100 for t in texts]


def retriever(reranker: _Reranker | None = None) -> Retriever:
    return Retriever(_Store(), [V1, BOTH, B1, *FILLER], reranker)


def test_each_mode_adds_one_stage() -> None:
    """A → B → C 로 갈수록 한 단계씩 더해지고, C 는 후보 전부를 재정렬한다."""
    cfg = SearchConfig(top_k=2, pool=20)

    a = retriever().search("armor", MODES["A"], cfg)
    assert [c.chunk_id for c in a] == [V1.chunk_id, BOTH.chunk_id]
    assert a[0].bm25_score is None

    b = retriever().search("armor", MODES["B"], cfg)
    assert [c.chunk_id for c in b] == [BOTH.chunk_id, V1.chunk_id, B1.chunk_id]
    assert b[0].vector_score == 0.8
    assert b[0].bm25_score
    assert b[0].rrf_score

    rr = _Reranker()
    c = retriever(rr).search("armor", MODES["C"], cfg)
    assert [x.chunk_id for x in c] == [B1.chunk_id, BOTH.chunk_id, V1.chunk_id]
    assert [x.rank_before_rerank for x in c] == [3, 1, 2]
    assert rr.seen == [3]  # C 는 B 의 전체 결합 후보(상위 2개로 자르기 전)를 받는다


def test_c_and_b_share_first_stage_candidates() -> None:
    """C 의 재정렬 전 순위는 B 의 순위와 같다 (같은 후보를 다시 매길 뿐)."""
    cfg = SearchConfig(top_k=5, pool=20)
    b = retriever().search("armor", MODES["B"], cfg)
    c = retriever(_Reranker()).search("armor", MODES["C"], cfg)

    assert {x.chunk_id for x in b} == {x.chunk_id for x in c}
    assert sorted(x.rank_before_rerank or 0 for x in c) == [x.rank for x in b]


def test_pool_and_rrf_k_are_parameters() -> None:
    """pool=1 · rrf_k=10 이 결과에 반영된다."""
    b = retriever().search("armor", MODES["B"], SearchConfig(pool=1, rrf_k=10))

    # 벡터 1위 V1 과 BM25 1위 BOTH 가 1/11 로 같고, 동점은 벡터 순위표가 먼저다
    assert [x.chunk_id for x in b] == [V1.chunk_id]
    assert b[0].rrf_score == round(1 / 11, 6)


def test_rerank_modes_need_a_reranker() -> None:
    with pytest.raises(ValueError, match="재정렬"):
        retriever().search("q", MODES["C+"], SearchConfig())


def test_mode_names_and_rewrite_set() -> None:
    """리라이트 구성은 C+ · A+ · B+ 셋이다."""
    assert mode_of("A+") == RetrievalMode.VECTOR_REWRITE

    rewrite = {m for m in MODES.values() if m in REWRITE_MODES}
    assert rewrite == {MODES["C+"], MODES["A+"], MODES["B+"]}

    with pytest.raises(ValueError):
        mode_of("D")


def test_rerank_keeps_previous_rank() -> None:
    cands = [
        Candidate(chunk_id="a", rank=1, rank_before_rerank=None, vector_score=None,
                  bm25_score=None, rrf_score=None, rerank_score=None),
        Candidate(chunk_id="b", rank=2, rank_before_rerank=None, vector_score=None,
                  bm25_score=None, rrf_score=None, rerank_score=None),
    ]  # fmt: skip

    out = rerank("q", cands, {"a": "x", "b": "xxxx"}, _Reranker())

    got = [(c.chunk_id, c.rank, c.rank_before_rerank) for c in out]
    assert got == [("b", 1, 2), ("a", 2, 1)]
