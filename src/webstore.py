"""웹 검색 결과 적재 · 재조회 (architecture.md §6 의 2 · 6단계).

Brave 결과를 팀 양식(websearch.to_team_format)으로 바꾼 뒤
- 청크는 웹 컬렉션(WEB_COLLECTION)에 넣고 (같은 URL = 같은 chunk_id → 덮어씀)
- "이 질문으로 검색한 적이 있는가" 는 검색 기록 표(web_search_log)에 lookup_key 로 남긴다.

검색 기록을 따로 두는 이유
- 결과가 0건인 검색도 "이미 찾아봤다" 로 남겨야 Brave 를 다시 부르지 않는다 (§6 판정 기준).
- 같은 URL 이 다른 질문에서 다시 나오면 청크 메타데이터의 lookup_key 는 마지막 질문 것으로 덮인다.
  질문 → 청크 목록을 기록 표가 들고 있어야 앞 질문으로도 다시 찾을 수 있다.

평가 실행기는 이 모듈을 쓰지 않는다 (§6 지키는 선).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from langchain_core.documents import Document as LangChainDocument

from shared.core.logger import get_logger
from shared.core.models import Chunk, Document, SourceId
from websearch import BRAVE_LICENSE, WebResult, WebSearchOutcome, to_vector_document

logger = get_logger(__name__)

DEFAULT_TTL_DAYS = 30

_CHUNK_FIELDS = set(Chunk.model_fields)
_RESULT_META_FIELDS = (
    "lookup_key",
    "work_id",
    "query_ko",
    "query_en",
    "rank",
    "page_age",
    "language",
    "content_hash",
    "fetched_at",
)


@dataclass(frozen=True)
class SearchRecord:
    """lookup_key 하나의 검색 기록"""

    lookup_key: str
    query_ko: str
    query_en: str
    work_id: str | None
    chunk_ids: list[str]
    searched_at: datetime


class SearchLog(Protocol):
    """검색 기록 저장소. 운영은 PgSearchLog, 테스트는 메모리 구현을 쓴다"""

    def get(self, key: str) -> SearchRecord | None: ...
    def put(self, record: SearchRecord) -> None: ...


class VectorCollection(Protocol):
    """웹 컬렉션에서 쓰는 PGVector 기능만 (add_documents 는 같은 id 면 덮어쓴다)"""

    def add_documents(self, documents: list[LangChainDocument], **kwargs: Any) -> list[str]: ...
    def get_by_ids(self, ids: Any, /) -> list[LangChainDocument]: ...


class PgSearchLog:
    """Postgres 의 web_search_log 표. 처음 쓸 때 표를 만든다"""

    TABLE = "web_search_log"

    def __init__(self, connect: Any = None) -> None:
        if connect is None:
            from shared.core.vector_store import get_raw_db_connection

            connect = get_raw_db_connection
        self._connect = connect
        self._ready = False

    def _ensure_table(self, conn: Any) -> None:
        if self._ready:
            return
        with conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.TABLE} (
                    lookup_key  TEXT PRIMARY KEY,
                    query_ko    TEXT NOT NULL,
                    query_en    TEXT NOT NULL,
                    work_id     TEXT,
                    chunk_ids   JSONB NOT NULL,
                    searched_at TIMESTAMPTZ NOT NULL
                )
                """
            )
        conn.commit()
        self._ready = True

    def get(self, key: str) -> SearchRecord | None:
        import json

        conn = self._connect()
        try:
            self._ensure_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT lookup_key, query_ko, query_en, work_id, chunk_ids, searched_at "
                    f"FROM {self.TABLE} WHERE lookup_key = %s",
                    (key,),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        chunk_ids = row[4] if isinstance(row[4], list) else json.loads(row[4])
        return SearchRecord(row[0], row[1], row[2], row[3], list(chunk_ids), row[5])

    def put(self, record: SearchRecord) -> None:
        import json

        conn = self._connect()
        try:
            self._ensure_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self.TABLE}
                        (lookup_key, query_ko, query_en, work_id, chunk_ids, searched_at)
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                    ON CONFLICT (lookup_key) DO UPDATE SET
                        query_ko = EXCLUDED.query_ko,
                        query_en = EXCLUDED.query_en,
                        work_id = EXCLUDED.work_id,
                        chunk_ids = EXCLUDED.chunk_ids,
                        searched_at = EXCLUDED.searched_at
                    """,
                    (
                        record.lookup_key,
                        record.query_ko,
                        record.query_en,
                        record.work_id,
                        json.dumps(record.chunk_ids),
                        record.searched_at,
                    ),
                )
            conn.commit()
        finally:
            conn.close()


def from_vector_document(doc: LangChainDocument) -> WebResult:
    """웹 컬렉션에서 꺼낸 행을 websearch 가 만든 모양(WebResult)으로 되돌린다"""
    meta = dict(doc.metadata)
    chunk = Chunk.model_validate({k: v for k, v in meta.items() if k in _CHUNK_FIELDS})
    fetched_raw = meta.get("fetched_at")
    fetched = datetime.fromisoformat(fetched_raw) if isinstance(fetched_raw, str) else None
    document = Document(
        doc_id=chunk.doc_id,
        work_id=chunk.work_id,
        source_id=SourceId.BRAVE,
        source_url=chunk.source_url,
        license=BRAVE_LICENSE,
        revid=0,
        fetched_at=fetched,
        doc_type="web",
        source_ids={"brave_title": chunk.title_original},
    )
    metadata = {k: meta.get(k) for k in _RESULT_META_FIELDS}
    return WebResult(document=document, chunk=chunk, metadata=metadata)


@dataclass
class SaveReport:
    inserted: int = 0
    updated: int = 0
    unchanged_skipped: int = 0


class WebStore:
    """웹 컬렉션 + 검색 기록. websearch.search_web_for_question 이 store 로 받는다"""

    def __init__(
        self,
        collection: VectorCollection,
        log: SearchLog,
        *,
        ttl_days: int = DEFAULT_TTL_DAYS,
        now: Any = None,
    ) -> None:
        self.collection = collection
        self.log = log
        self.ttl = timedelta(days=ttl_days)
        self._now = now or (lambda: datetime.now(UTC))

    def lookup(self, key: str) -> list[WebResult] | None:
        """검색한 적이 있으면 그때 결과(0건일 수 있음), 없거나 기한이 지났으면 None"""
        record = self.log.get(key)
        if record is None:
            return None
        searched = record.searched_at
        if searched.tzinfo is None:
            searched = searched.replace(tzinfo=UTC)
        if self._now() - searched > self.ttl:
            logger.info("[webstore] 기한 지남 → 다시 검색 (key=%s)", key)
            return None
        if not record.chunk_ids:
            return []
        found = {
            d.metadata.get("chunk_id"): d for d in self.collection.get_by_ids(record.chunk_ids)
        }
        if not found:
            # 기록은 있는데 청크가 모두 지워졌다 (컬렉션을 비운 경우 등) → 다시 검색한다
            logger.info("[webstore] 기록의 청크가 없음 → 다시 검색 (key=%s)", key)
            return None
        # 기록 순서(= Brave 순위) 그대로. 일부만 지워졌으면 남은 것만
        return [from_vector_document(found[cid]) for cid in record.chunk_ids if cid in found]

    def save(self, outcome: WebSearchOutcome, *, query_ko: str, work_id: str | None) -> SaveReport:
        """청크를 웹 컬렉션에 넣고(내용이 같으면 다시 임베딩하지 않음) 검색 기록을 남긴다"""
        report = SaveReport()
        ids = [r.chunk.chunk_id for r in outcome.results]
        existing = {
            d.metadata.get("chunk_id"): d.metadata.get("content_hash")
            for d in (self.collection.get_by_ids(ids) if ids else [])
        }
        to_add: list[LangChainDocument] = []
        add_ids: list[str] = []
        for result in outcome.results:
            cid = result.chunk.chunk_id
            if cid in existing and existing[cid] == result.chunk.content_hash:
                report.unchanged_skipped += 1
                continue
            if cid in existing:
                report.updated += 1
            else:
                report.inserted += 1
            to_add.append(to_vector_document(result))
            add_ids.append(cid)
        if to_add:
            self.collection.add_documents(to_add, ids=add_ids)
        self.log.put(
            SearchRecord(
                lookup_key=outcome.lookup_key,
                query_ko=query_ko,
                query_en=outcome.query_en,
                work_id=work_id,
                chunk_ids=ids,
                searched_at=self._now(),
            )
        )
        logger.info(
            "[webstore] key=%s inserted=%d updated=%d unchanged=%d",
            outcome.lookup_key,
            report.inserted,
            report.updated,
            report.unchanged_skipped,
        )
        return report


_DEFAULT_STORE: WebStore | None = None


def default_store() -> WebStore:
    """WEB_STORE=true 일 때 서비스가 쓰는 저장소 (웹 컬렉션 + Postgres 검색 기록)"""
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        from shared.core.config import settings
        from shared.core.vector_store import get_vector_store

        _DEFAULT_STORE = WebStore(
            get_vector_store(settings.WEB_COLLECTION),
            PgSearchLog(),
            ttl_days=settings.WEB_STORE_TTL_DAYS,
        )
    return _DEFAULT_STORE
