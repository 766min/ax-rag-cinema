from fakes import FakeStore

from retrieval.search import BM25Index, rrf_merge, tokenize, vector_candidates, vector_search


def test_토큰은_영어_소문자와_한글() -> None:
    assert tokenize("Iron Man (아이언맨) 2008!") == ["iron", "man", "아이언맨", "2008"]


def test_BM25는_단어가_겹치는_청크를_먼저() -> None:
    # 문서가 2개뿐이면 BM25 의 IDF 가 0 이 되므로 3개로 만든다
    index = BM25Index(
        [
            ("a", "Thanos collects the Infinity Stones"),
            ("b", "Wakanda is a nation"),
            ("c", "Tony Stark builds a suit"),
        ]
    )
    hits = index.search("infinity stones", k=5)
    assert [cid for cid, _ in hits] == ["a"]  # 점수 0 인 청크는 빠짐


def test_벡터_검색은_chunk_id와_점수() -> None:
    store = FakeStore([("a", 0.9), ("b", 0.5)])
    assert vector_search(store, "q", k=1) == [("a", 0.9)]  # type: ignore[arg-type]
    assert [c.rank for c in vector_candidates([("a", 0.9), ("b", 0.5)])] == [1, 2]


def test_RRF는_두_순위를_합치고_점수를_따로_남긴다() -> None:
    merged = rrf_merge([("a", 0.9), ("b", 0.8)], [("b", 12.0), ("c", 3.0)])

    assert [c.chunk_id for c in merged] == ["b", "a", "c"]  # b 는 양쪽에 있어 1위
    b = merged[0]
    assert (b.rank, b.vector_score, b.bm25_score) == (1, 0.8, 12.0)
    assert b.rrf_score is not None and b.rrf_score > merged[1].rrf_score  # type: ignore[operator]
    assert merged[2].vector_score is None  # 벡터에 없던 후보
