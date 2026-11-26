"""검색 구성(A · B · C · C+ · A+ · B+)에 맞춰 1단계 검색 → 재정렬을 돌린다."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from retrieve.refine import Reranker, rerank
from retrieve.search import RRF_K, BM25Index, rrf_merge, vector_candidates, vector_search
from shared.core.models import Candidate, Chunk
from shared.core.schemas import RetrievalMode
from shared.core.timing import measure_time

# 사람이 쓰는 짧은 이름 → RetrievalMode (실행 기록 trace.retrieval_mode 에는 값 쪽이 들어간다)
MODES: dict[str, RetrievalMode] = {
    "A": RetrievalMode.VECTOR,
    "B": RetrievalMode.HYBRID,
    "C": RetrievalMode.HYBRID_RERANK,
    "C+": RetrievalMode.HYBRID_RERANK_REWRITE,
    "A+": RetrievalMode.VECTOR_REWRITE,
    "B+": RetrievalMode.HYBRID_REWRITE,
}
# 공식 비교표와 팀 추가 실험은 결과표를 따로 둔다
OFFICIAL_MODES = ("A", "B", "C", "C+")
EXTRA_MODES = ("A+", "B+")
# 서비스가 이 집합을 보고 영문 리라이트를 부를지 정한다
REWRITE_MODES = frozenset(
    {
        RetrievalMode.HYBRID_RERANK_REWRITE,
        RetrievalMode.VECTOR_REWRITE,
        RetrievalMode.HYBRID_REWRITE,
    }
)
RERANK_MODES = frozenset({RetrievalMode.HYBRID_RERANK, RetrievalMode.HYBRID_RERANK_REWRITE})
VECTOR_ONLY = frozenset({RetrievalMode.VECTOR, RetrievalMode.VECTOR_REWRITE})


def mode_of(name: str) -> RetrievalMode:
    """짧은 이름('C+')을 RetrievalMode 로 바꾼다. 모르는 이름이면 바로 오류."""
    if name not in MODES:
        raise ValueError(f"구성은 {', '.join(MODES)} 중 하나입니다: {name}")
    return MODES[name]


def mode_name(mode: RetrievalMode) -> str:
    """mode_of 의 반대: RetrievalMode → 짧은 이름."""
    return next(name for name, value in MODES.items() if value == mode)


# 같은 검색 방식에 영문 리라이트를 더한 구성 (A → A+ · B → B+ · C → C+)
_WITH_REWRITE = {
    RetrievalMode.VECTOR: RetrievalMode.VECTOR_REWRITE,
    RetrievalMode.HYBRID: RetrievalMode.HYBRID_REWRITE,
    RetrievalMode.HYBRID_RERANK: RetrievalMode.HYBRID_RERANK_REWRITE,
}


def with_rewrite(mode: RetrievalMode) -> RetrievalMode:
    """같은 검색 방식에 리라이트를 더한 구성. 이미 리라이트 구성이거나 짝이 없으면 그대로."""
    return _WITH_REWRITE.get(mode, mode)


def effective_mode(name: str, rewrite: bool = True) -> str:
    """실제로 돌릴 구성 이름. 리라이트가 켜져 있으면(기본) A · B · C 를 A+ · B+ · C+ 로 바꾼다."""
    mode = mode_of(name)
    return mode_name(with_rewrite(mode)) if rewrite else name


@dataclass(frozen=True)
class SearchConfig:
    """검색 설정. params() 를 실행 기록에 남겨 어떤 설정으로 돌렸는지 추적한다."""

    top_k: int = 5  # 생성 모델에 넘기는 수 · Hit@k 의 k
    pool: int = 20  # 결합 후보 깊이 = 각 검색기에서 가져오는 수 = 재정렬 입력 수 (임시값)
    rrf_k: int = RRF_K  # RRF 상수 (임시값 60)

    def params(self) -> dict[str, Any]:
        return {"top_k": self.top_k, "pool": self.pool, "rrf_k": self.rrf_k}


def search_text(chunk: Chunk) -> str:
    """벡터 · BM25 · 재정렬이 보는 글 = 머리말 + 본문. 적재 때 임베딩한 글(store.db.embed_input)과 같아야 한다."""
    return f"{chunk.header}\n{chunk.text}" if chunk.header else chunk.text


class Retriever:
    """컬렉션 하나를 검색한다. 청크 · 검색용 글 · BM25 색인 · 재정렬기를 들고 있다."""

    def __init__(
        self,
        store: Any,
        chunks: Sequence[Chunk],
        reranker: Reranker | None = None,
    ) -> None:
        self.store = store
        self.chunks = {c.chunk_id: c for c in chunks}
        self.texts = {cid: search_text(c) for cid, c in self.chunks.items()}
        self._bm25: BM25Index | None = None
        self.reranker = reranker

    @property
    def bm25(self) -> BM25Index:
        """처음 쓸 때 한 번만 만든다 (A 계열만 돌리면 만들지 않는다)."""
        if self._bm25 is None:
            self._bm25 = BM25Index(self.texts.items())
        return self._bm25

    @measure_time(label="retrieve")
    def first_stage(self, query: str, mode: RetrievalMode, config: SearchConfig) -> list[Candidate]:
        """1단계 검색. A 계열은 벡터 순위 그대로, 나머지는 벡터 + BM25 를 RRF 로 합친다."""
        vector = vector_search(self.store, query, config.pool)
        if mode in VECTOR_ONLY:
            return vector_candidates(vector)
        merged = rrf_merge(vector, self.bm25.search(query, config.pool), k=config.rrf_k)
        return merged[: config.pool]  # 두 목록을 합치면 최대 2×pool 이라 깊이만큼 자른다

    @measure_time(label="rerank")
    def _rerank(self, query: str, candidates: list[Candidate]) -> list[Candidate]:
        assert self.reranker is not None
        return rerank(query, candidates, self.texts, self.reranker)

    def search(self, query: str, mode: RetrievalMode, config: SearchConfig) -> list[Candidate]:
        """구성에 맞게 1단계 → (재정렬) 을 돌려 후보 전부를 최종 순위대로 돌려준다. 상위 top_k 가 생성 모델로 간다."""
        if mode == RetrievalMode.HYBRID_RERANK_ENRICHED:
            raise NotImplementedError("D 구성은 이번 취합 범위가 아닙니다")
        if mode in RERANK_MODES and self.reranker is None:
            raise ValueError(f"{mode} 구성은 재정렬 모델이 필요합니다")
        candidates = self.first_stage(query, mode, config)
        if mode in RERANK_MODES and candidates:
            candidates = self._rerank(query, candidates)
        # 벡터 DB 와 메모리의 청크 목록이 어긋나면(다른 색인을 섞어 읽음) 여기서 멈춘다
        unknown = [c.chunk_id for c in candidates if c.chunk_id not in self.chunks]
        if unknown:
            raise ValueError(f"벡터 검색이 BM25 코퍼스에 없는 청크를 돌려줬습니다: {unknown[:3]}")
        return candidates
