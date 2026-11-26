from retrieve.search import BM25Index, tokenize


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
