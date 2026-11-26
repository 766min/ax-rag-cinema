import hashlib
import json
from contextlib import closing
from dataclasses import dataclass
from typing import Any

from build.chunks import Dataset, dataset_fingerprint, parent_sources
from retrieve.pipeline import MODES, RERANK_MODES, Retriever
from retrieve.refine import CrossEncoderReranker
from retrieve.search import TOKENIZER
from service.answer import PROMPT_VERSION, make_generate_en
from service.movie_qa import GenerateEnglish
from shared.core.models import Alias, Chunk
from store.db import database_name, db_index_version, fetch_rows, row_to_chunk


@dataclass
class Runtime:
    """실행 진입점(ask · run_eval)이 같이 쓰는 묶음: 검색기 · 생성기 · 별칭 · 청크 · 부모 원문 · 색인 버전."""

    retriever: Retriever
    generate_en: GenerateEnglish
    aliases: list[Alias]
    chunks: dict[str, Chunk]
    sources: dict[str, str] | None
    index_version: str
    info: dict[str, Any]  # 실행 기록 params 에 그대로 넣는 값


def open_runtime(
    modes: list[str], *, context: str = "chunk", parent_chars: int = 3000,
    collection: str | None = None, dataset: str | None = None, reranker_model: str | None = None,
) -> Runtime:  # fmt: skip
    """DB 에서 색인을 읽어 검색기 · 생성기 · 실행 정보를 한 번에 만든다."""
    # 불러오는 순간 .env 가 필요해서 함수 안에서 import 한다 (테스트가 이 파일을 불러도 깨지지 않게)
    from shared.core.config import settings
    from shared.core.vector_store import get_llm, get_raw_db_connection, get_vector_store
    from tools.translation import load_aliases

    collection = collection or settings.MOVIE_COLLECTION
    with closing(get_raw_db_connection()) as conn:
        rows = fetch_rows(conn, collection)
    if not rows:
        raise RuntimeError(
            f"{database_name(settings.DATABASE_URL)} 의 컬렉션 {collection} 이 비었습니다. "
            "scripts/build_index.py 로 먼저 적재하세요"
        )
    chunks = [row_to_chunk(meta, doc) for meta, doc in rows.values()]

    # 색인과 설정의 임베딩 모델이 다르면 검색 결과가 엉망이 되므로 바로 멈춘다
    models = {meta.get("embedding_model") for meta, _ in rows.values()}
    if models != {settings.EMBEDDING_MODEL}:
        raise RuntimeError(
            f"색인의 임베딩 모델 {models} 과 설정 {settings.EMBEDDING_MODEL} 이 다릅니다"
        )

    # 재정렬 모델 · 부모 원문은 무거워서 필요한 구성일 때만 불러온다
    needs_rerank = any(MODES[m] in RERANK_MODES for m in modes)
    reranker = CrossEncoderReranker(reranker_model) if needs_rerank else None
    sources = parent_sources(Dataset.load(dataset)) if context == "parent" else None
    retriever = Retriever(get_vector_store(collection), chunks, reranker)

    # 어느 DB · 컬렉션 · 데이터 판 · 문맥 · 재정렬 모델로 돌렸는지 실행 기록에 남긴다
    info = {
        "database": database_name(settings.DATABASE_URL),
        "collection": collection,
        "dataset": dataset_fingerprint(dataset),
        "context": context,
        "parent_chars": parent_chars if context == "parent" else None,
        "reranker": reranker.info if reranker else None,
        "bm25_tokenizer": TOKENIZER,
    }
    return Runtime(
        retriever=retriever,
        generate_en=make_generate_en(
            get_llm(), context=context, sources=sources, parent_chars=parent_chars
        ),
        aliases=load_aliases(),
        chunks={c.chunk_id: c for c in chunks},
        sources=sources,
        index_version=db_index_version(rows),
        info=info,
    )


def run_meta_values() -> dict[str, str]:
    """실행 기록의 고정 칸(임베딩 모델 · 생성 모델 · 프롬프트 판) 값."""
    from shared.core.config import settings

    return {
        "embedding_model": settings.EMBEDDING_MODEL,
        "llm_model": settings.LLM_MODEL,
        "prompt_version": PROMPT_VERSION,
    }


def alias_version(aliases: list[Alias]) -> str:
    """이름 사전의 판 = 내용 해시 앞 12자. 사전이 바뀌면 달라진다 (실행 기록 alias_version 칸)."""
    raw = json.dumps(
        [a.model_dump(mode="json") for a in aliases], ensure_ascii=False, sort_keys=True
    )
    return "aliases-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
