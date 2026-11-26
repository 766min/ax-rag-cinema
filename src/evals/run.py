from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from evals.recorder import Recorder, measure
from retrieve.pipeline import MODES, VECTOR_ONLY, Retriever, SearchConfig
from service.answer import make_retrieve
from service.movie_qa import GenerateEnglish, answer_movie_question
from shared.core.models import Alias, Chunk, EvalItem, EvidenceSource, RunRecord, Trace
from shared.core.schemas import AnswerStatus
from tools.translation import Rewrite, model_translate


@dataclass
class RunMeta:
    index_version: str
    embedding_model: str
    llm_model: str
    prompt_version: str
    alias_version: str | None
    # 실행 전체에 같은 값 (색인 · 재정렬 · 문맥 …)
    params: dict[str, Any] = field(default_factory=dict)


def input_text(read: Sequence[Mapping[str, Any]], chunks: Mapping[str, Chunk],
               sources: Mapping[str, str] | None = None) -> str:  # fmt: skip
    """모델이 읽은 글을 기록(read)에서 되살린다 — '모델 입력에 정답 문장이 있었나' 채점용"""
    parts: list[str] = []
    for block in read:
        span, parent = block.get("span"), block.get("parent_id")
        if span and parent and sources and parent in sources:
            parts.append(sources[parent][span[0] : span[1]])
        else:
            parts += [chunks[cid].text for cid in block["ids"] if cid in chunks]
    return "\n".join(parts)


async def run_questions(
    items: Sequence[EvalItem],
    *,
    mode_name: str,
    retriever: Retriever,
    generate_en: GenerateEnglish,
    aliases: list[Alias],
    config: SearchConfig,
    recorder: Recorder,
    meta: RunMeta,
    queries: Mapping[str, str] | None = None,
    query_translator: Rewrite = model_translate,
    answer_translator: Rewrite = model_translate,
) -> list[RunRecord]:
    mode = MODES[mode_name]
    # 구성에 맞는 검색 함수를 한 번 만들어 모든 문항에 쓴다
    retrieve = make_retrieve(retriever, mode, config)
    records: list[RunRecord] = []
    for item in items:
        # 1) 이 블록 안의 rewrite · retrieve · rerank · generate · translate 시간과 토큰이 measured 에 모인다
        with measure() as measured:
            result = await answer_movie_question(
                item.question,
                retrieve=retrieve,
                generate_en=generate_en,
                aliases=aliases,
                query_translator=query_translator,
                answer_translator=answer_translator,
                mode=mode,
                top_k=config.top_k,
                query_en=(queries or {}).get(item.question_id),
            )
        # 2) 결과 → RunRecord. 평가 중에 웹 근거가 나오면 설정이 잘못된 것이라 멈춘다
        status = result["status"]
        if status == AnswerStatus.WEB_UNVERIFIED:
            raise RuntimeError("평가 실행에서 웹 근거가 나왔습니다 — 웹 보강이 꺼져 있어야 합니다")
        candidates = result["candidates"]
        # RunRecord 의 검사(validator)가 규칙을 강제한다: 인용 ⊆ 후보, answered 면 인용 필수, 오류면 error 필수
        record = RunRecord(
            run_id=recorder.run_id,
            question_id=item.question_id,
            status=status,
            candidates=candidates,
            cited_chunk_ids=result["cited_chunk_ids"],
            answer=result["answer_en"] or None,
            error=result.get("error") if status in (AnswerStatus.RETRIEVAL_ERROR, AnswerStatus.GENERATION_ERROR) else None,
            latency_ms=measured.latency_ms,
            tokens=measured.tokens,
            embedding_model=meta.embedding_model,
            llm_model=meta.llm_model,
            prompt_version=meta.prompt_version,
            alias_version=meta.alias_version,
            params={
                **meta.params,
                **config.params(),
                "mode": mode_name,
                "search_query": result["search_query"],
                "query_source": result.get("query_source"),
                "answer_ko": result["answer_ko"],
                "partial": result["partial"],
                "no_evidence_reason": result["no_evidence_reason"],
                "error_stage": result["error_stage"],
                "dropped_citations": result["dropped_citations"],
                "read": result["read"],
                "bm25_zero": None if mode in VECTOR_ONLY else all(c.bm25_score is None for c in candidates),
            },
            trace=Trace(
                query_ko=result["query_ko"],
                query_normalized=result["query_normalized"],
                query_en=result["query_en"],
                rewrite_failed=result["rewrite_failed"],
                retrieval_mode=mode,
                evidence_source=EvidenceSource.DOCUMENTS if result["cited_chunk_ids"] else EvidenceSource.NONE,
                web_called=False,
                web_cache_hit=False,
                web_failed=False,
                index_version=meta.index_version,
            ),
        )  # fmt: skip
        # 3) 한 줄 쓰기
        recorder.write(record)
        records.append(record)
    return records
