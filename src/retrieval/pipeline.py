"""구성별 실행 (architecture §5): 이름 사전 치환 → 검색 → (재정렬) → 답변.

단계마다 @measure_time 라벨을 붙여 evals.recorder 가 시간 · 토큰을 단계별로 모은다.
라벨은 RunRecord.latency_ms 설명과 같은 retrieve · rerank · generate 를 쓴다.
"""

from dataclasses import dataclass
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.vectorstores import VectorStore

from generation.answer import AnswerResult, generate_answer
from retrieval.normalize import AliasNormalizer
from retrieval.refine import Reranker, rerank
from retrieval.search import BM25Index, Hit, rrf_merge, vector_candidates, vector_search
from shared.core.models import Candidate
from shared.core.schemas import AnswerStatus, RetrievalMode
from shared.core.timing import measure_time

SUPPORTED_MODES = (
    RetrievalMode.VECTOR,
    RetrievalMode.HYBRID,
    RetrievalMode.HYBRID_RERANK,
    RetrievalMode.HYBRID_RERANK_REWRITE,
    RetrievalMode.HYBRID_RERANK_ENRICHED,  # D: 검색 방식은 C 와 같고 색인(컬렉션)만 다르다
)
RERANK_MODES = (
    RetrievalMode.HYBRID_RERANK,
    RetrievalMode.HYBRID_RERANK_REWRITE,
    RetrievalMode.HYBRID_RERANK_ENRICHED,
)


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

    def __init__(
        self,
        store: VectorStore,
        corpus: dict[str, str],
        context: dict[str, str] | None = None,
    ) -> None:
        self.store = store
        self.texts = corpus  # chunk_id → 색인 글 (BM25 · 재정렬 입력)
        self.bm25 = BM25Index(corpus.items())
        # 답변 문맥 글. D(가공 색인)는 색인 글에 LLM 가공 정보가 붙어 있어 원문만 따로 준다
        self.context = context or corpus

    def _vector(self, query: str, k: int) -> list[Hit]:
        return vector_search(self.store, query, k)

    @measure_time(label="retrieve")
    def search(self, query: str, mode: RetrievalMode, config: SearchConfig) -> list[Candidate]:
        if mode == RetrievalMode.VECTOR:
            return vector_candidates(self._vector(query, config.pool))
        # RRF 는 두 목록을 합쳐 최대 2×pool 개가 되므로 pool 개로 자른다 (재정렬 입력 = 후보 20개)
        merged = rrf_merge(self._vector(query, config.pool), self.bm25.search(query, config.pool))
        return merged[: config.pool]


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
    search_query: str | None = None,
) -> PipelineResult:
    """질문 하나를 끝까지 돌린다. 검색 · 생성 실패는 예외 대신 상태로 돌려준다 (§7).

    search_query: 미리 만든 검색어 (V2 진단의 질의 방식). 주면 이름 사전 치환 대신 이 글로 검색한다.
    답변 생성에는 언제나 원래 한국어 질문을 쓴다.
    """
    if mode not in SUPPORTED_MODES:
        raise NotImplementedError(f"아직 구현하지 않은 구성입니다: {mode}")
    if mode in RERANK_MODES and reranker is None:
        raise ValueError(f"{mode} 구성은 reranker 가 필요합니다")
    if mode == RetrievalMode.HYBRID_RERANK_REWRITE and search_query is None:
        raise ValueError("C+리라이트는 미리 만든 영어 검색어(search_query)가 필요합니다")

    query = search_query if search_query is not None else normalizer.normalize(question)
    try:
        candidates = retriever.search(query, mode, config)
        if reranker is not None and mode in RERANK_MODES:
            candidates = _rerank(query, candidates, retriever.texts, reranker)
    except Exception as exc:
        failed = AnswerResult(AnswerStatus.RETRIEVAL_ERROR, "")
        return PipelineResult(query, [], failed, error=f"{type(exc).__name__}: {exc}")

    contexts = [(c.chunk_id, retriever.context[c.chunk_id]) for c in candidates[: config.top_k]]
    try:
        answer = _generate(question, contexts, llm)
    except Exception as exc:
        failed = AnswerResult(AnswerStatus.GENERATION_ERROR, "")
        return PipelineResult(query, candidates, failed, error=f"{type(exc).__name__}: {exc}")
    return PipelineResult(query, candidates, answer)
