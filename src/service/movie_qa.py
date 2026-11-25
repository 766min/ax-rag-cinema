"""Service flow: Korean question -> English search -> English draft -> Korean answer.

The retriever and English answer generator are supplied by their owners when those
modules land. This module does not change the A/B/C/C+ evaluation routes.
"""

from collections.abc import Awaitable, Callable, Iterable, Sequence

from pydantic import BaseModel, Field

from shared.core.models import Alias
from shared.core.schemas import AnswerStatus
from tools.translation import (
    Rewrite,
    load_aliases,
    model_translate,
    rewrite_movie_query,
    translate_answer_to_korean,
)


class Evidence(BaseModel):
    chunk_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source_url: str = Field(min_length=1)


class EnglishDraft(BaseModel):
    answer_en: str = Field(min_length=1)
    cited_chunk_ids: list[str] = Field(min_length=1)


Retrieve = Callable[[str], Awaitable[Sequence[Evidence]]]
GenerateEnglish = Callable[[str, Sequence[Evidence]], Awaitable[EnglishDraft]]


async def answer_movie_question(
    query_ko: str,
    *,
    retrieve: Retrieve,
    generate_en: GenerateEnglish,
    aliases: Iterable[Alias] | None = None,
    query_translator: Rewrite = model_translate,
    answer_translator: Rewrite = model_translate,
) -> dict:
    """Run the service path with explicit, replaceable retrieval and generation ports."""
    alias_list = list(aliases) if aliases is not None else load_aliases()
    translation = await rewrite_movie_query(query_ko, alias_list, query_translator)
    search_query = translation["query_en"] or translation["query_normalized"]
    result = {
        **translation,
        "search_query": search_query,
        "answer_en": "",
        "answer_ko": "",
        "cited_chunk_ids": [],
        "sources": [],
        "translation_failed": False,
    }

    try:
        evidence = list(await retrieve(search_query))
    except Exception as exc:
        return {**result, "status": AnswerStatus.RETRIEVAL_ERROR, "error": str(exc)}

    if not evidence:
        return {
            **result,
            "status": AnswerStatus.NO_EVIDENCE,
            "answer_ko": "확인 가능한 근거를 찾지 못했습니다.",
        }

    try:
        draft = await generate_en(search_query, evidence)
        evidence_by_id = {item.chunk_id: item for item in evidence}
        if any(chunk_id not in evidence_by_id for chunk_id in draft.cited_chunk_ids):
            raise ValueError("검색 근거에 없는 청크가 답변 인용에 포함되었습니다.")
    except Exception as exc:
        return {**result, "status": AnswerStatus.GENERATION_ERROR, "error": str(exc)}

    result["answer_en"] = draft.answer_en
    translated = await translate_answer_to_korean(draft.answer_en, alias_list, answer_translator)
    if translated["translation_failed"]:
        return {
            **result,
            "status": AnswerStatus.GENERATION_ERROR,
            "translation_failed": True,
            "error": translated["error"],
        }

    cited = list(dict.fromkeys(draft.cited_chunk_ids))
    return {
        **result,
        "status": AnswerStatus.ANSWERED,
        "answer_ko": translated["answer_ko"],
        "cited_chunk_ids": cited,
        "sources": [
            {"chunk_id": chunk_id, "source_url": evidence_by_id[chunk_id].source_url}
            for chunk_id in cited
        ],
    }
