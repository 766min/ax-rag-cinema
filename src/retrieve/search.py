import re
from collections.abc import Iterable
from typing import Any

from rank_bm25 import BM25Okapi

from shared.core.models import Candidate

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


def vector_search(store: Any, query: str, k: int) -> list[Hit]:
    found = store.similarity_search_with_score(query, k=k)
    return [(doc.metadata["chunk_id"], round(1 - float(distance), 4)) for doc, distance in found]


def vector_candidates(hits: list[Hit]) -> list[Candidate]:
    return [
        Candidate(
            chunk_id=chunk_id,
            rank=rank,
            rank_before_rerank=None,
            vector_score=score,
            bm25_score=None,
            rrf_score=None,
            rerank_score=None,
        )
        for rank, (chunk_id, score) in enumerate(hits, start=1)
    ]
