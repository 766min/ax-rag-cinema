from retrieve.search import BM25Index, rrf_merge, tokenize, vector_candidates, vector_search


def test_rrf_rewards_agreement_between_rankings() -> None:
    fused = rrf_merge([("a", 0.9), ("b", 0.8), ("c", 0.7)], [("c", 5.0), ("d", 4.0)])
    assert [c.chunk_id for c in fused] == ["c", "a", "b", "d"]
    assert fused[0].rrf_score == round(1 / 63 + 1 / 61, 6)
    assert fused[0].vector_score == 0.7 and fused[0].bm25_score == 5.0
    assert fused[1].bm25_score is None


def test_rrf_ties_keep_first_ranking_order() -> None:
    assert [c.chunk_id for c in rrf_merge([("a", 0.5)], [("b", 1.0)])] == ["a", "b"]


def test_rrf_k_is_a_parameter() -> None:
    fused = rrf_merge([("a", 0.9)], [("a", 1.0)], k=10)
    assert fused[0].rrf_score == round(2 / 11, 6)


def test_bm25_matches_the_appended_english_name_only() -> None:
    index = BM25Index(
        [
            ("x-card", "[Iron Man · 2008 film]\nIron Man is a film released in 2008."),
            ("y-card", "[Thor · 2011 film]\nThor is a film released in 2011."),
            ("z-card", "[Echo · 2024 series]\nEcho is a TV series."),
        ]
    )
    assert tokenize("아이언맨 2는? Iron Man") == ["아이언맨", "2", "는", "iron", "man"]
    assert [cid for cid, _ in index.search("아이언맨은 언제 나왔어? Iron Man", k=5)] == ["x-card"]
    assert index.search("아이언맨은 언제 나왔어?", k=5) == []


class _Doc:
    def __init__(self, cid: str) -> None:
        self.metadata = {"chunk_id": cid}


class _Store:
    def similarity_search_with_score(self, query: str, k: int) -> list:
        return [(_Doc("v1"), 0.25), (_Doc("v2"), 0.5)][:k]


def test_vector_scores_are_cosine_similarity() -> None:
    hits = vector_search(_Store(), "q", 2)
    assert hits == [("v1", 0.75), ("v2", 0.5)]
    cands = vector_candidates(hits)
    assert [c.rank for c in cands] == [1, 2] and cands[0].bm25_score is None
