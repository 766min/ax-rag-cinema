# ══════════════════════════════════════════════════════════════════════════════
# [학습 안내] tests/retrieve/test_refine.py — 재정렬 테스트
#
# gather/tests/retrieve/test_pipeline.py 에서 재정렬 관련 테스트를 분리한 파일이다.
# ══════════════════════════════════════════════════════════════════════════════
import pytest

from retrieve.refine import rerank
from shared.core.models import Candidate, Chunk, SourceId
from shared.core.schemas import Section


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


V1, BOTH, B1 = chunk("01", "zzz"), chunk("02", "armor"), chunk("03", "armor suit")


class _Reranker:
    name = "fake"

    def __init__(self) -> None:
        self.seen: list[int] = []

    def score(self, query: str, texts: list[str]) -> list[float]:
        self.seen.append(len(texts))
        return [len(t) / 100 for t in texts]


def test_rerank_keeps_previous_rank() -> None:
    cands = [
        Candidate(chunk_id="a", rank=1, rank_before_rerank=None, vector_score=None,
                  bm25_score=None, rrf_score=None, rerank_score=None),
        Candidate(chunk_id="b", rank=2, rank_before_rerank=None, vector_score=None,
                  bm25_score=None, rrf_score=None, rerank_score=None),
    ]  # fmt: skip
    out = rerank("q", cands, {"a": "x", "b": "xxxx"}, _Reranker())
    assert [(c.chunk_id, c.rank, c.rank_before_rerank) for c in out] == [
        ("b", 1, 2),
        ("a", 2, 1),
    ]


def test_rerank_empty_candidates() -> None:
    assert rerank("q", [], {}, _Reranker()) == []


def test_rerank_rejects_mismatched_scores() -> None:
    class BadReranker:
        name = "bad"

        def score(self, query: str, texts: list[str]) -> list[float]:
            return []

    candidates = [
        Candidate(chunk_id="a", rank=1, rank_before_rerank=None, vector_score=None,
                  bm25_score=None, rrf_score=None, rerank_score=None),
    ]  # fmt: skip
    with pytest.raises(ValueError, match="점수 개수"):
        rerank("q", candidates, {"a": "x"}, BadReranker())
