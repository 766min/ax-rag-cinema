"""평가 실행 (architecture §8): 문항마다 파이프라인을 돌려 RunRecord 로 남긴다."""

from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel

from evals.items import check_ready
from evals.recorder import Recorder, measure
from retrieval.normalize import ALIAS_VERSION, AliasNormalizer
from retrieval.pipeline import Retriever, SearchConfig, run_question
from retrieval.refine import Reranker
from shared.core.logger import get_logger
from shared.core.models import EvalItem, EvidenceSource, RunRecord, Trace
from shared.core.schemas import AnswerStatus, RetrievalMode

logger = get_logger(__name__)


@dataclass
class RunSetup:
    """한 번의 실행에서 바뀌지 않는 것들 (RunRecord 의 버전 · 모델 칸)."""

    index_version: str
    embedding_model: str
    llm_model: str
    prompt_version: str
    config: SearchConfig
    extra_params: dict[str, Any] = field(default_factory=dict)  # 레시피 · 데이터셋 · 문항 ID 등


def run_eval(
    items: list[EvalItem],
    mode: RetrievalMode,
    recorder: Recorder,
    retriever: Retriever,
    normalizer: AliasNormalizer,
    llm: BaseChatModel,
    setup: RunSetup,
    reranker: Reranker | None = None,
) -> list[RunRecord]:
    """문항을 차례로 돌리고 한 줄씩 기록한다. 정답 키가 이번 색인과 안 맞으면 시작하지 않는다."""
    check_ready(items, setup.index_version)
    records: list[RunRecord] = []
    for item in items:
        with measure() as m:
            result = run_question(
                item.question, mode, retriever, normalizer, llm, setup.config, reranker
            )
        status = result.answer.status
        record = RunRecord(
            run_id=recorder.run_id,
            question_id=item.question_id,
            status=status,
            candidates=result.candidates,
            cited_chunk_ids=result.answer.cited_chunk_ids,
            answer=result.answer.answer or None,
            error=result.error,
            latency_ms=m.latency_ms,
            tokens=m.tokens,
            embedding_model=setup.embedding_model,
            llm_model=setup.llm_model,
            prompt_version=setup.prompt_version,
            alias_version=ALIAS_VERSION,
            params={
                **setup.config.as_params(),
                "reranker": getattr(reranker, "name", None),
                **setup.extra_params,
            },
            trace=Trace(
                query_ko=item.question,
                query_normalized=result.query_normalized,
                query_en="",  # 리라이트(C+리라이트)는 아직 없음
                rewrite_failed=False,
                retrieval_mode=mode,
                evidence_source=(
                    EvidenceSource.DOCUMENTS
                    if status == AnswerStatus.ANSWERED
                    else EvidenceSource.NONE
                ),
                web_called=False,  # 평가는 웹 검색을 끈다 (§8)
                web_cache_hit=False,
                web_failed=False,
                index_version=setup.index_version,
            ),
        )
        recorder.write(record)
        records.append(record)
        logger.info("%s %s %s %dms", item.question_id, mode, status, m.latency_ms.get("total", 0))
    return records
