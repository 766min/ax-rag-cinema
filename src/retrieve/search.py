import re
from collections.abc import Iterable
from typing import Any

from rank_bm25 import BM25Okapi

from shared.core.models import Candidate

# 검색기 하나의 결과 한 줄
Hit = tuple[str, float]  # (chunk_id, 점수)

# RRF 상수 k. 클수록 순위 차이의 영향이 완만해진다 (팀 합의 전 임시값)
RRF_K = 60

# BM25 토큰 규칙. 어간 추출 · 불용어 처리는 하지 않는다
_TOKEN = re.compile(r"[a-z0-9]+|[가-힣]+")
# 실행 기록에 남기는 토큰 규칙 설명
TOKENIZER = "regex [a-z0-9]+|[가-힣]+ (lowercase, no stemming)"


def tokenize(text: str) -> list[str]:
    """영어 소문자 · 숫자 · 한글 덩어리. 문서는 영어라 한국어 낱말은 거의 걸리지 않는다"""
    return _TOKEN.findall(text.lower())


class BM25Index:
    """청크 전체로 한 번 만드는 BM25 색인"""

    def __init__(self, docs: Iterable[tuple[str, str]]) -> None:
        pairs = list(docs)
        self._ids = [chunk_id for chunk_id, _ in pairs]
        self._bm25 = BM25Okapi([tokenize(text) for _, text in pairs])

    def search(self, query: str, k: int) -> list[Hit]:
        """점수가 0 보다 큰 것만 상위 k 개 (한국어만 남은 질문은 0건일 수 있다)"""
        scores = self._bm25.get_scores(tokenize(query))
        ranked = sorted(zip(self._ids, scores, strict=True), key=lambda x: x[1], reverse=True)
        return [(chunk_id, float(score)) for chunk_id, score in ranked[:k] if score > 0]


def vector_search(store: Any, query: str, k: int) -> list[Hit]:
    """PGVector 코사인 거리 → 유사도 (1 - 거리). 클수록 가깝다"""
    found = store.similarity_search_with_score(query, k=k)
    return [(doc.metadata["chunk_id"], round(1 - float(distance), 4)) for doc, distance in found]


def vector_candidates(hits: list[Hit]) -> list[Candidate]:
    """A 구성: 벡터 순위 그대로. BM25 · RRF · 재정렬 칸은 비운다"""
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


def rrf_merge(vector_hits: list[Hit], bm25_hits: list[Hit], k: int = RRF_K) -> list[Candidate]:
    """B 구성: 두 순위를 1/(k + 순위) 의 합으로 합친다. 원래 점수는 척도가 달라 더하지 않고 기록만 한다"""
    rrf: dict[str, float] = {}
    vector_score: dict[str, float] = {}
    bm25_score: dict[str, float] = {}
    for rank, (chunk_id, score) in enumerate(vector_hits, start=1):
        rrf[chunk_id] = rrf.get(chunk_id, 0.0) + 1 / (k + rank)
        vector_score[chunk_id] = score
    for rank, (chunk_id, score) in enumerate(bm25_hits, start=1):
        rrf[chunk_id] = rrf.get(chunk_id, 0.0) + 1 / (k + rank)
        bm25_score[chunk_id] = round(score, 4)
    # 안정 정렬: 동점은 먼저 나온 순 (벡터 순위표가 먼저)
    ordered = sorted(rrf.items(), key=lambda x: x[1], reverse=True)
    return [
        Candidate(
            chunk_id=chunk_id,
            rank=rank,
            rank_before_rerank=None,
            vector_score=vector_score.get(chunk_id),
            bm25_score=bm25_score.get(chunk_id),
            rrf_score=round(score, 6),
            rerank_score=None,
        )
        for rank, (chunk_id, score) in enumerate(ordered, start=1)
    ]
