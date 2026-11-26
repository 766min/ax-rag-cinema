import re
from collections.abc import Iterable

from rank_bm25 import BM25Okapi

Hit = tuple[str, float]

_TOKEN = re.compile(r"[a-z0-9]+|[가-힣]+")
TOKENIZER = "regex [a-z0-9]+|[가-힣]+ (lowercase, no stemming)"


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


class BM25Index:
    def __init__(self, docs: Iterable[tuple[str, str]]) -> None:
        pairs = list(docs)
        self._ids = [chunk_id for chunk_id, _ in pairs]
        self._bm25 = BM25Okapi([tokenize(text) for _, text in pairs])

    def search(self, query: str, k: int) -> list[Hit]:
        scores = self._bm25.get_scores(tokenize(query))
        ranked = sorted(zip(self._ids, scores, strict=True), key=lambda x: x[1], reverse=True)
        return [(chunk_id, float(score)) for chunk_id, score in ranked[:k] if score > 0]
