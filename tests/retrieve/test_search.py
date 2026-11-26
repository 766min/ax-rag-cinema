from retrieve.search import BM25Index, tokenize, vector_candidates, vector_search


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
