import sys
from types import SimpleNamespace
from typing import Any

import pytest

from retrieve.refine import CrossEncoderReranker, pick_device, rerank
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


def fake_torch(cuda: bool, mps: bool) -> Any:
    return SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: cuda),
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: mps)),
    )


@pytest.mark.parametrize(
    ("cuda", "mps", "expected"),
    [(True, False, "cuda"), (True, True, "cuda"), (False, True, "mps"), (False, False, "cpu")],
)
def test_pick_device_prefers_gpu(
    cuda: bool, mps: bool, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NVIDIA GPU(cuda) → Apple GPU(mps) → CPU 순으로 고른다."""
    monkeypatch.delenv("RERANKER_DEVICE", raising=False)
    assert pick_device(fake_torch(cuda, mps)) == expected


def test_pick_device_respects_argument_then_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RERANKER_DEVICE", "cpu")
    assert pick_device(fake_torch(True, False)) == "cpu"
    assert pick_device(fake_torch(True, False), "cuda:1") == "cuda:1"


def test_reranker_without_optional_group_explains_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """재정렬 그룹을 설치하지 않았으면, 재정렬기를 만들 때만 설치 방법을 알려 준다."""
    # sys.modules 에 None 을 넣으면 그 모듈 import 가 ImportError 가 된다 (설치 안 된 상태 흉내)
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    with pytest.raises(RuntimeError, match="--group rerank"):
        CrossEncoderReranker("any-model")
