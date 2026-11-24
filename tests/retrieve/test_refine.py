import pytest

from retrieve.refine import rerank
from shared.core.models import Candidate


class LengthReranker:
    """본문이 길수록 점수가 높은 가짜 재정렬기."""

    name = "fake-length"

    def score(self, query: str, texts: list[str]) -> list[float]:
        return [float(len(t)) for t in texts]


def test_재정렬은_전후_순위와_점수를_남긴다() -> None:
    candidates = [Candidate(chunk_id="a", rank=1, rrf_score=0.03), Candidate(chunk_id="b", rank=2)]
    out = rerank("q", candidates, {"a": "x", "b": "xxx"}, LengthReranker())

    assert [(c.chunk_id, c.rank, c.rank_before_rerank, c.rerank_score) for c in out] == [
        ("b", 1, 2, 3.0),
        ("a", 2, 1, 1.0),
    ]
    assert out[1].rrf_score == 0.03  # 원래 점수는 유지


def test_점수_개수가_다르면_막는다() -> None:
    class Broken:
        name = "broken"

        def score(self, query: str, texts: list[str]) -> list[float]:
            return [1.0]

    with pytest.raises(ValueError, match="개수"):
        rerank(
            "q",
            [Candidate(chunk_id="a", rank=1), Candidate(chunk_id="b", rank=2)],
            {"a": "x", "b": "y"},
            Broken(),
        )
