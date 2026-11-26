# src/store/db.py — 청크를 벡터 DB(PostgreSQL + pgvector)에 넣는 파일 (증분 적재)_영민


from collections import Counter
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit
from build.chunks import version_of
from shared.core.logger import get_logger
from shared.core.models import Chunk
from shared.core.timing import measure_time

log = get_logger(__name__)

# 한 컬렉션의 모든 행 (ID · 메타데이터 · 본문)
EXISTING_ROWS = """
SELECT e.id, e.cmetadata, e.document
FROM langchain_pg_embedding e
JOIN langchain_pg_collection c ON e.collection_id = c.uuid
WHERE c.name = %s
"""
# 다른 컬렉션에 같은 ID 가 있는지 확인
FOREIGN_IDS = """
SELECT e.id, c.name
FROM langchain_pg_embedding e
JOIN langchain_pg_collection c ON e.collection_id = c.uuid
WHERE c.name <> %s AND e.id = ANY(%s)
"""
# 임베딩은 그대로 두고 메타데이터 · 본문만 고침
REFRESH_ROW = "UPDATE langchain_pg_embedding SET cmetadata = %s, document = %s WHERE id = %s"
# 새 DB 에는 아직 표가 없다 (PGVector 가 첫 적재 때 만든다) — 그 경우를 구분한다
TABLES_EXIST = "SELECT to_regclass('langchain_pg_embedding') IS NOT NULL"


# [역할] 임베딩할 글 = 머리말 + 본문. DB 에 '저장' 하는 본문은 text 만
def embed_input(chunk: Chunk) -> str:
    """임베딩 · BM25 가 보는 글자 — 머리말 + 본문. 저장하는 본문은 text 그대로"""
    return f"{chunk.header}\n{chunk.text}" if chunk.header else chunk.text


# [역할] 청크 → DB 메타데이터(JSONB). text 는 본문 칸에 따로 들어가므로 빼고, 임베딩 모델 이름을 더한다
def metadata(chunk: Chunk, embedding_model: str = "") -> dict[str, Any]:
    """text 를 뺀 청크의 칸 + 임베딩 모델. Chunk.model_validate({**meta, 'text': doc}) 로 되돌아간다"""
    meta = chunk.model_dump(mode="json", exclude={"text"})
    if embedding_model:
        meta["embedding_model"] = embedding_model
    return meta


# [역할] DB 행(메타데이터 + 본문) → 청크로 되돌리기 (metadata() 의 반대)
def row_to_chunk(meta: dict[str, Any], document: str) -> Chunk:
    return Chunk.model_validate({**meta, "text": document})


@dataclass
# [역할] 적재 계획. 청크를 다섯 갈래로 나눠 담는다 (아직 DB 를 바꾸지 않은 상태)
class Plan:
    insert: list[Chunk] = field(default_factory=list)
    update: list[Chunk] = field(default_factory=list)
    refresh: list[Chunk] = field(default_factory=list)  # 해시가 같고 메타데이터만 다르다
    unchanged: list[Chunk] = field(default_factory=list)
    delete: list[str] = field(default_factory=list)

    # [역할] 계획을 숫자로 요약 — 적재 결과로 남기는 값 (architecture.md §4.2)
    def counts(self) -> dict[str, int]:
        return {
            "inserted": len(self.insert),
            "updated": len(self.update),
            "unchanged_skipped": len(self.unchanged) + len(self.refresh),
            "metadata_refreshed": len(self.refresh),
            "deleted": len(self.delete),
        }

    # [역할] 이 계획이 기존 행을 고치거나 지우는가? (그렇다면 allow_changes 가 필요하다)
    def changes_existing(self) -> bool:
        return bool(self.update or self.refresh or self.delete)


# [역할] 새 청크와 DB 행을 chunk_id 로 맞대어 계획을 세운다
def plan_load(
    chunks: Sequence[Chunk],
    existing: dict[str, tuple[dict[str, Any], str]],
    embedding_model: str = "",
) -> Plan:
    """새 청크와 DB 행을 chunk_id 로 맞대어 신규 · 갱신 · 메타데이터만 · 그대로 · 삭제로 가른다"""
    plan = Plan()
    for chunk in chunks:
        row = existing.get(chunk.chunk_id)
        if row is None:
            plan.insert.append(chunk)
        elif row[0].get("content_hash") != chunk.content_hash:
            plan.update.append(chunk)
        elif row[0] != metadata(chunk, embedding_model) or row[1] != chunk.text:
            plan.refresh.append(chunk)
        else:
            plan.unchanged.append(chunk)
    plan.delete = sorted(set(existing) - {c.chunk_id for c in chunks})
    return plan


# [역할] 다른 임베딩 모델로 만든 행이 섞여 있으면 멈춤
def check_embedding_model(existing: dict[str, tuple[dict[str, Any], str]], model: str) -> None:
    """다른 임베딩 모델로 만든 행과 섞지 않는다 (서인 index_dataset 의 검사)"""
    others = {meta.get("embedding_model") for meta, _ in existing.values()} - {model}
    if others:
        raise ValueError(f"컬렉션에 다른 임베딩 모델의 행이 있습니다: {sorted(map(str, others))}")


# [역할] 컬렉션의 행을 {chunk_id: (메타데이터, 본문)} 으로 읽음
def fetch_rows(conn: Any, collection: str) -> dict[str, tuple[dict[str, Any], str]]:
    with conn.cursor() as cur:
        cur.execute(TABLES_EXIST)
        if not cur.fetchone()[0]:
            return {}
        cur.execute(EXISTING_ROWS, (collection,))
        return {row[0]: (row[1] or {}, row[2] or "") for row in cur.fetchall()}


# [역할] DB 에 실제로 들어 있는 행으로 색인 버전 계산
def db_index_version(rows: dict[str, tuple[dict[str, Any], str]]) -> str:
    return version_of((cid, meta.get("content_hash", "")) for cid, (meta, _) in rows.items())


# [역할] 컬렉션 전체를 청크 목록으로 (검색기 · BM25 코퍼스를 만들 때)
def read_collection(conn: Any, collection: str) -> list[Chunk]:
    """컬렉션의 모든 행을 청크로 (BM25 코퍼스 · 부모 문맥 · 정답 키 대조에 쓴다)"""
    return [row_to_chunk(meta, doc) for meta, doc in fetch_rows(conn, collection).values()]


# [역할] 'postgresql+psycopg://…/rag_db_gather' → 'rag_db_gather'
def database_name(url: str) -> str:
    return urlsplit(url).path.lstrip("/")


# [역할] 실제 임베딩 호출 (OpenAI). 'embed' 단계 시간으로 기록
@measure_time(label="embed")
def _embed(texts: list[str]) -> list[list[float]]:
    from shared.core.vector_store import get_embeddings

    return get_embeddings().embed_documents(texts)


# [역할] 이 파일의 진입점 — 증분 적재 전체 (위 '전체 흐름' 1~5)
def load(
    chunks: Sequence[Chunk],
    collection: str,
    *,
    allow_changes: bool = False,
    dry_run: bool = False,
    connect: Callable[[], Any] | None = None,
    store: Any = None,
    embed: Callable[[list[str]], list[list[float]]] | None = None,
    embedding_model: str | None = None,
    database: str | None = None,
) -> dict[str, Any]:
    """증분 적재. 돌려주는 값: DB · 컬렉션 · 색인 버전 · 행 수 · 적재 결과.

    connect · store · embed · embedding_model · database 는 테스트에서 바꿔 끼운다
    (기본은 shared.core.vector_store 와 .env 설정).
    """
    # 설정을 받지 않았을 때만 .env 설정(shared.core.config)을 읽는다
    if embedding_model is None or database is None:
        from shared.core.config import settings

        embedding_model = embedding_model or settings.EMBEDDING_MODEL
        database = database or database_name(settings.DATABASE_URL)
    model = embedding_model
    if connect is None or store is None:
        from shared.core.vector_store import get_raw_db_connection, get_vector_store

        connect = connect or get_raw_db_connection
        if store is None and not dry_run:
            store = get_vector_store(collection)  # 표와 컬렉션이 없으면 여기서 만든다
    embed = embed or _embed
    # 파일 쪽 색인 버전 (적재가 끝난 뒤 DB 쪽 버전과 비교할 값)
    file_version = version_of((c.chunk_id, c.content_hash) for c in chunks)
    with closing(connect()) as conn:
        existing = fetch_rows(conn, collection)
        if existing:
            check_embedding_model(existing, model)
        plan = plan_load(chunks, existing, model)
        log.info("적재 계획 %s: %s", collection, plan.counts())
        # 안전 검사 ①: 기존 색인을 바꾸는 적재는 명시적으로 허용했을 때만 (실험 색인 보호)
        if existing and plan.changes_existing() and not allow_changes:
            raise ValueError(
                f"컬렉션 {collection} 의 기존 행을 바꾸거나 지우게 됩니다 {plan.counts()}. "
                "의도한 것이면 allow_changes(--allow-changes)를 켜세요"
            )
        # 안전 검사 ②: 새로 넣을 ID 가 같은 DB 의 다른 컬렉션에 있으면 멈춘다 (전역 행 ID 덮어쓰기 방지)
        changed = plan.insert + plan.update
        if changed:
            with conn.cursor() as cur:
                cur.execute(TABLES_EXIST)
                clash = []
                if cur.fetchone()[0]:
                    cur.execute(FOREIGN_IDS, (collection, [c.chunk_id for c in changed]))
                    clash = cur.fetchall()
            if clash:
                raise ValueError(
                    f"같은 DB 의 다른 컬렉션에 같은 chunk_id 가 있어 덮어쓰게 됩니다: {clash[:5]}. "
                    "색인 변형은 DB 를 나눠 넣으세요"
                )
        # dry_run: 계획만 보고 끝낸다 (임베딩 비용 없이 몇 개가 바뀌는지 확인)
        if dry_run:
            return {
                "dry_run": True,
                "database": database,
                "collection": collection,
                "load": plan.counts(),
                "file_index_version": file_version,
                "embed_chars": sum(len(embed_input(c)) for c in changed),
            }
        if changed:
            # 실행 ①: 바뀐 청크만 임베딩해서 넣는다. 같은 ID 는 덮어쓴다(upsert)
            vectors = embed([embed_input(c) for c in changed])
            store.add_embeddings(
                texts=[c.text for c in changed],
                embeddings=vectors,
                metadatas=[metadata(c, model) for c in changed],
                ids=[c.chunk_id for c in changed],
            )
        # 실행 ②: 임베딩 없이 메타데이터만 고친다 (Jsonb: 파이썬 dict 를 Postgres JSONB 로 넘기는 포장)
        if plan.refresh:
            from psycopg.types.json import Jsonb

            with conn.cursor() as cur:
                cur.executemany(
                    REFRESH_ROW,
                    [(Jsonb(metadata(c, model)), c.text, c.chunk_id) for c in plan.refresh],
                )
            conn.commit()
        # 실행 ③: 새 결과에 없는 청크를 이 컬렉션에서만 지운다
        if plan.delete:
            store.delete(ids=plan.delete, collection_only=True)
        after = fetch_rows(conn, collection)
    # 적재 뒤 확인: DB 행 수 · 색인 버전이 파일과 다르면 적재가 틀린 것이므로 멈춘다
    db_version = db_index_version(after)
    if len(after) != len(chunks) or db_version != file_version:
        raise ValueError(
            f"적재 뒤 확인 실패: DB {len(after)}행 · 버전 {db_version}, "
            f"파일 {len(chunks)}개 · 버전 {file_version}"
        )
    return {
        "database": database,
        "collection": collection,
        "index_version": db_version,
        "embedding_model": model,
        "rows": len(after),
        "rows_by_section": dict(Counter(str(m.get("section")) for m, _ in after.values())),
        "load": plan.counts(),
    }
