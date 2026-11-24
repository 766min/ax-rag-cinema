"""청크 적재 · 임베딩 (architecture §4.2 증분 적재).

- DB 행 ID 는 '{컬렉션}:{chunk_id}' 로 컬렉션마다 나눈다. langchain_pg_embedding.id 는 컬렉션과 상관없는
  전역 기본키라서, chunk_id 를 그대로 쓰면 같은 chunk_id 를 가진 다른 컬렉션(정제본 D, 청킹 실험 등)이
  행을 덮어쓴다. 논리적 chunk_id 는 메타데이터에 두고, 조회도 메타데이터의 chunk_id 로 한다
- 메타데이터는 Chunk 칸 그대로 (본문은 page_content 에만 둔다)
- content_hash 와 색인 버전이 같은 청크는 다시 임베딩하지 않는다
- 이번 색인에 없는 청크(청킹 기준이 바뀌어 사라진 것)는 이 컬렉션 안에서만 지운다
"""

from typing import Any, Protocol

from langchain_core.documents import Document
from langchain_core.vectorstores import VectorStore

from shared.core.logger import get_logger
from shared.core.models import Chunk

logger = get_logger(__name__)


class DBConnection(Protocol):
    """cursor() 만 있으면 된다. vector_store.DBConnection 과 같은 모양이지만, 그 모듈은 import 할 때
    .env 를 읽어(config.settings) CI 에서 테스트가 깨지므로 여기서 따로 둔다."""

    def cursor(self) -> Any: ...


_EXISTING_SQL = """
    SELECT e.cmetadata->>'chunk_id', e.cmetadata->>'content_hash', e.cmetadata->>'index_version'
    FROM langchain_pg_embedding e
    JOIN langchain_pg_collection c ON e.collection_id = c.uuid
    WHERE c.name = %s
"""


def row_id(collection: str, chunk_id: str) -> str:
    """DB 행 ID. 컬렉션이 다르면 같은 chunk_id 라도 다른 행이 된다."""
    return f"{collection}:{chunk_id}"


def index_text(chunk: Chunk) -> str:
    """임베딩 · BM25 가 보는 글 = 머리말 + 본문 (models.Chunk.header 설명)."""
    return f"{chunk.header}\n{chunk.text}" if chunk.header else chunk.text


def to_document(chunk: Chunk, index_version: str) -> Document:
    metadata: dict[str, Any] = chunk.model_dump(mode="json", exclude={"text"})
    metadata["index_version"] = index_version
    return Document(page_content=index_text(chunk), metadata=metadata)


def fetch_existing(conn: DBConnection, collection: str) -> dict[str, tuple[str, str]]:
    """컬렉션에 이미 있는 chunk_id → (content_hash, index_version). 컬렉션이 없으면 빈 dict.

    튜터님 예제와 달리 오류를 삼키지 않는다: 조회가 실패하면 전부 다시 임베딩하게 되기 때문이다.
    """
    cur = conn.cursor()
    cur.execute(_EXISTING_SQL, (collection,))
    return {row[0]: (row[1], row[2]) for row in cur.fetchall()}


def plan(
    chunks: list[Chunk], existing: dict[str, tuple[str, str]], index_version: str
) -> tuple[list[Chunk], list[str]]:
    """새로 넣을 청크와 지울 chunk_id 를 고른다 (DB 를 건드리지 않는 순수 함수)."""
    to_add = [c for c in chunks if existing.get(c.chunk_id) != (c.content_hash, index_version)]
    current = {c.chunk_id for c in chunks}
    to_delete = sorted(chunk_id for chunk_id in existing if chunk_id not in current)
    return to_add, to_delete


def index_chunks(
    chunks: list[Chunk],
    store: VectorStore,
    conn: DBConnection,
    collection: str,
    index_version: str,
    batch_size: int = 200,
) -> dict[str, int]:
    """증분 적재. 반환값: 전체 · 추가 · 건너뜀 · 삭제 개수."""
    existing = fetch_existing(conn, collection)
    to_add, to_delete = plan(chunks, existing, index_version)

    if to_delete:
        # collection_only=True: 다른 컬렉션의 행은 ID 가 겹쳐도 지우지 않는다
        store.delete(ids=[row_id(collection, cid) for cid in to_delete], collection_only=True)
    for start in range(0, len(to_add), batch_size):
        batch = to_add[start : start + batch_size]
        store.add_documents(
            [to_document(c, index_version) for c in batch],
            ids=[row_id(collection, c.chunk_id) for c in batch],
        )
        logger.info("적재 %d / %d", start + len(batch), len(to_add))

    stats = {
        "total": len(chunks),
        "added": len(to_add),
        "skipped": len(chunks) - len(to_add),
        "deleted": len(to_delete),
    }
    logger.info("[%s] 색인 %s 적재 결과: %s", collection, index_version, stats)
    return stats


def load_corpus(conn: DBConnection, collection: str) -> dict[str, tuple[str, dict[str, Any]]]:
    """컬렉션의 청크 전체 chunk_id → (색인 글, 메타데이터). BM25 색인과 답변 문맥에 쓴다."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT e.cmetadata->>'chunk_id', e.document, e.cmetadata
        FROM langchain_pg_embedding e
        JOIN langchain_pg_collection c ON e.collection_id = c.uuid
        WHERE c.name = %s
        """,
        (collection,),
    )
    return {row[0]: (row[1], row[2] or {}) for row in cur.fetchall()}
