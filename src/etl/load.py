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


def to_document(
    chunk: Chunk,
    index_version: str,
    index_meta: dict[str, str] | None = None,
    page_content: str | None = None,
) -> Document:
    """index_meta: 임베딩 모델 · 입력 구성 등 색인 설정 (청크마다 같이 저장해 섞임을 막는다).

    page_content: 색인 글을 바꿀 때만 (D 가공 색인). 없으면 머리말 + 본문.
    """
    metadata: dict[str, Any] = chunk.model_dump(mode="json", exclude={"text"})
    metadata["index_version"] = index_version
    metadata.update(index_meta or {})
    return Document(page_content=page_content or index_text(chunk), metadata=metadata)


def count_tokens(texts: list[str], model: str) -> tuple[str, list[int]]:
    """임베딩 입력 토큰 수 추정 (tiktoken). API 가 보고한 값이 아니라 같은 토크나이저로 센 값이다.

    모델 이름으로 토크나이저를 못 찾으면 cl100k_base 로 센다. 반환: (쓴 인코딩 이름, 글마다 토큰 수).
    """
    import tiktoken

    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        encoding = tiktoken.get_encoding("cl100k_base")
    return encoding.name, [len(encoding.encode(text)) for text in texts]


def _tables_exist(cur: Any) -> bool:
    """PGVector 테이블이 있는가. 처음 적재하기 전의 새 DB 에는 아직 없다 (PGVector 가 첫 적재 때 만든다)."""
    cur.execute("SELECT to_regclass('langchain_pg_embedding') IS NOT NULL")
    row = cur.fetchone()
    return bool(row and row[0])


def fetch_existing(conn: DBConnection, collection: str) -> dict[str, tuple[str, str]]:
    """컬렉션에 이미 있는 chunk_id → (content_hash, index_version). 테이블 · 컬렉션이 없으면 빈 dict.

    튜터님 예제와 달리 오류를 삼키지 않는다: 조회가 실패하면 전부 다시 임베딩하게 되기 때문이다.
    테이블이 없는 경우만 미리 확인해서 '기존 행 0개'로 본다.
    """
    cur = conn.cursor()
    if not _tables_exist(cur):
        return {}
    cur.execute(_EXISTING_SQL, (collection,))
    return {row[0]: (row[1], row[2]) for row in cur.fetchall()}


def meta_values(conn: DBConnection, collection: str, key: str) -> set[str | None]:
    """컬렉션 행들의 메타데이터 key 값 종류 (예: embedding_model). 다른 설정이 섞였는지 볼 때 쓴다."""
    cur = conn.cursor()
    if not _tables_exist(cur):
        return set()
    cur.execute(
        """
        SELECT DISTINCT e.cmetadata->>%s
        FROM langchain_pg_embedding e
        JOIN langchain_pg_collection c ON e.collection_id = c.uuid
        WHERE c.name = %s
        """,
        (key, collection),
    )
    return {row[0] for row in cur.fetchall()}


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
    index_meta: dict[str, str] | None = None,
    page_texts: dict[str, str] | None = None,
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
            [
                to_document(c, index_version, index_meta, (page_texts or {}).get(c.chunk_id))
                for c in batch
            ],
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
    if not _tables_exist(cur):
        return {}
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
