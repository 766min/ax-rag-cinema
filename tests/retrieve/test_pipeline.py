import pytest
from fakes import FakeLLM, FakeStore

from answer import CitedAnswer
from retrieve.normalize import AliasNormalizer
from retrieve.pipeline import Retriever, SearchConfig, run_question
from shared.core.models import Alias, AliasKind
from shared.core.schemas import AnswerStatus, RetrievalMode

CORPUS = {"a": "Thanos collects the Infinity Stones", "b": "Wakanda is a nation", "c": "Iron Man"}
NORMALIZER = AliasNormalizer([Alias(alias_ko="타노스", canonical_en="Thanos", kind=AliasKind.WORK)])
CONFIG = SearchConfig(top_k=2, pool=3)


def _retriever(fail: bool = False) -> Retriever:
    return Retriever(FakeStore([("b", 0.9), ("a", 0.8), ("c", 0.1)], fail=fail), CORPUS)  # type: ignore[arg-type]


def test_A는_벡터_순위_그대로_답한다() -> None:
    llm = FakeLLM()
    result = run_question("타노스는?", RetrievalMode.VECTOR, _retriever(), NORMALIZER, llm, CONFIG)  # type: ignore[arg-type]

    assert result.query_normalized == "타노스 (Thanos)는?"
    assert [c.chunk_id for c in result.candidates] == ["b", "a", "c"]
    assert result.answer.status == AnswerStatus.ANSWERED
    assert result.answer.cited_chunk_ids == ["b"]  # 문맥 [1] = 1위 청크


def test_B는_BM25와_합쳐_순위가_바뀐다() -> None:
    result = run_question(
        "타노스는?",
        RetrievalMode.HYBRID,
        _retriever(),
        NORMALIZER,
        FakeLLM(),
        CONFIG,  # type: ignore[arg-type]
    )
    assert result.candidates[0].chunk_id == "a"  # 벡터 2위지만 BM25 에서 'thanos' 가 겹쳐 1위
    assert result.candidates[0].bm25_score is not None


def test_검색이_실패하면_retrieval_error() -> None:
    llm = FakeLLM()
    result = run_question("q", RetrievalMode.VECTOR, _retriever(fail=True), NORMALIZER, llm, CONFIG)  # type: ignore[arg-type]

    assert result.answer.status == AnswerStatus.RETRIEVAL_ERROR
    assert result.error and "ConnectionError" in result.error
    assert llm.calls == 0  # 검색이 실패하면 모델을 부르지 않음


def test_생성이_실패하면_generation_error() -> None:
    result = run_question(
        "q",
        RetrievalMode.VECTOR,
        _retriever(),
        NORMALIZER,
        FakeLLM(fail=True),
        CONFIG,  # type: ignore[arg-type]
    )
    assert result.answer.status == AnswerStatus.GENERATION_ERROR
    assert result.candidates  # 검색 결과는 남는다


def test_인용이_없으면_no_evidence() -> None:
    llm = FakeLLM(CitedAnswer(answer="모르겠어요", cited_numbers=[]))
    result = run_question("q", RetrievalMode.VECTOR, _retriever(), NORMALIZER, llm, CONFIG)  # type: ignore[arg-type]
    assert result.answer.status == AnswerStatus.NO_EVIDENCE


def test_C는_재정렬기가_없으면_막는다() -> None:
    with pytest.raises(ValueError, match="reranker"):
        run_question("q", RetrievalMode.HYBRID_RERANK, _retriever(), NORMALIZER, FakeLLM(), CONFIG)  # type: ignore[arg-type]
