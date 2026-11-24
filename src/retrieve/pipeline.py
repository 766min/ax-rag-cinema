"""구성별 실행 (architecture §5): 이름 사전 치환 → 검색 → (재정렬) → 답변.

단계마다 @measure_time 라벨을 붙여 evals.recorder 가 시간 · 토큰을 단계별로 모은다.
라벨은 RunRecord.latency_ms 설명과 같은 retrieve · rerank · generate 를 쓴다.
"""

from dataclasses import dataclass
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.vectorstores import VectorStore

from answer import AnswerResult, generate_answer
from retrieve.normalize import AliasNormalizer
from retrieve.refine import Reranker, rerank
from retrieve.search import BM25Index, Hit, rrf_merge, vector_candidates, vector_search
from shared.core.models import Candidate
from shared.core.schemas import AnswerStatus, RetrievalMode
from shared.core.timing import measure_time

SUPPORTED_MODES = (RetrievalMode.VECTOR, RetrievalMode.HYBRID, RetrievalMode.HYBRID_RERANK)


@dataclass
class SearchConfig:
    top_k: int = 5  # 답변 문맥 · Hit@k 에 쓰는 상위 개수
    pool: int = 20  # 각 검색기에서 가져오는 후보 수 (RRF · 재정렬 입력)

    def as_params(self) -> dict[str, Any]:
        return {"top_k": self.top_k, "pool": self.pool}


@dataclass
class PipelineResult:
    query_normalized: str
    candidates: list[Candidate]
    answer: AnswerResult
    error: str | None = None


class Retriever:
    """한 컬렉션을 검색한다. BM25 색인과 본문은 처음에 한 번만 만든다."""

    def __init__(self, store: VectorStore, corpus: dict[str, str]) -> None:
        self.store = store
        self.texts = corpus  # chunk_id → 색인 글 (머리말 + 본문)
        self.bm25 = BM25Index(corpus.items())

    def _vector(self, query: str, k: int) -> list[Hit]:
        return vector_search(self.store, query, k)

    @measure_time(label="retrieve")
    def search(self, query: str, mode: RetrievalMode, config: SearchConfig) -> list[Candidate]:
        if mode == RetrievalMode.VECTOR:
            return vector_candidates(self._vector(query, config.pool))
        return rrf_merge(self._vector(query, config.pool), self.bm25.search(query, config.pool))


@measure_time(label="rerank")
def _rerank(
    query: str, candidates: list[Candidate], texts: dict[str, str], reranker: Reranker
) -> list[Candidate]:
    return rerank(query, candidates, texts, reranker)


@measure_time(label="generate")
def _generate(question: str, contexts: list[tuple[str, str]], llm: BaseChatModel) -> AnswerResult:
    return generate_answer(question, contexts, llm)


def run_question(
    question: str,
    mode: RetrievalMode,
    retriever: Retriever,
    normalizer: AliasNormalizer,
    llm: BaseChatModel,
    config: SearchConfig,
    reranker: Reranker | None = None,
) -> PipelineResult:
    """질문 하나를 끝까지 돌린다. 검색 · 생성 실패는 예외 대신 상태로 돌려준다 (§7)."""
    if mode not in SUPPORTED_MODES:
        raise NotImplementedError(f"아직 구현하지 않은 구성입니다: {mode}")
    if mode == RetrievalMode.HYBRID_RERANK and reranker is None:
        raise ValueError("C 구성(hybrid_rerank)은 reranker 가 필요합니다")

    query = normalizer.normalize(question)
    try:
        candidates = retriever.search(query, mode, config)
        if reranker is not None and mode == RetrievalMode.HYBRID_RERANK:
            candidates = _rerank(query, candidates, retriever.texts, reranker)
    except Exception as exc:
        failed = AnswerResult(AnswerStatus.RETRIEVAL_ERROR, "")
        return PipelineResult(query, [], failed, error=f"{type(exc).__name__}: {exc}")

    contexts = [(c.chunk_id, retriever.texts[c.chunk_id]) for c in candidates[: config.top_k]]
    try:
        answer = _generate(question, contexts, llm)
    except Exception as exc:
        failed = AnswerResult(AnswerStatus.GENERATION_ERROR, "")
        return PipelineResult(query, candidates, failed, error=f"{type(exc).__name__}: {exc}")
    return PipelineResult(query, candidates, answer)
