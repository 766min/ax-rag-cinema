"""재정렬 (C 구성, architecture §5). 지금은 인터페이스만 둔다.

모델(다국어 Cross-Encoder 등)은 아직 고르지 않았다. 어떤 재정렬기든 Reranker 모양만 맞추면
rerank() 와 평가 기록(재정렬 전후 순위 · 점수 · 시간 · 토큰)을 그대로 쓸 수 있다.
- 시간: pipeline 이 rerank() 호출을 @measure_time(label="rerank") 로 감싼다
- 토큰: LLM 재정렬기라면 LangChain 호출이 recorder 에 rerank_input · rerank_output 으로 잡힌다
"""

from typing import Protocol

from shared.core.models import Candidate


class Reranker(Protocol):
    """질문과 후보 본문 쌍마다 관련도 점수를 낸다 (클수록 관련 높음)."""

    name: str

    def score(self, query: str, texts: list[str]) -> list[float]: ...


def rerank(
    query: str,
    candidates: list[Candidate],
    texts: dict[str, str],
    reranker: Reranker,
) -> list[Candidate]:
    """후보를 재정렬기 점수 순으로 다시 매긴다. 이전 순위는 rank_before_rerank 에 남긴다."""
    if not candidates:
        return []
    scores = reranker.score(query, [texts[c.chunk_id] for c in candidates])
    if len(scores) != len(candidates):
        raise ValueError(f"재정렬 점수 개수가 후보와 다릅니다: {len(scores)} != {len(candidates)}")
    ordered = sorted(zip(candidates, scores, strict=True), key=lambda x: x[1], reverse=True)
    return [
        c.model_copy(update={"rank": rank, "rank_before_rerank": c.rank, "rerank_score": score})
        for rank, (c, score) in enumerate(ordered, start=1)
    ]
