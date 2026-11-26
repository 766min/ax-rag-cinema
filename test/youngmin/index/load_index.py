"""색인(본문 청크 + 작품 카드 + 연결 카드)을 고정 문서 컬렉션에 증분 적재한다 (architecture.md §4.2).

실행 (저장소 루트에서, 청크 · 카드 · 연결 카드를 만든 뒤에):
    PYTHONPATH=src:test/youngmin/chunking uv run python test/youngmin/index/load_index.py

- 팀 공통 부품을 그대로 쓴다: 컬렉션 이름 · 모델 · 키는 shared.core.config (.env),
  저장소 · 임베딩 · DB 연결은 shared.core.vector_store, 기록은 shared.core.logger · timing.
- 청크 하나 = 행 하나. 행 ID 는 chunk_id, 본문(document)은 청크의 text 그대로,
  나머지 칸은 메타데이터(JSONB). 임베딩은 "머리말 + 본문" 으로 만든다 (§3).
- content_hash 가 같은 청크는 임베딩을 다시 하지 않는다. 결과는 inserted · updated ·
  unchanged_skipped · deleted 로 남긴다. 같은 적재를 두 번 돌리면 두 번째는 전부 건너뛴다.

출력: test/youngmin/data/index/{index}/manifest.json (DB · 컬렉션 · 색인 버전 · 건수 · 적재 결과)
색인 변형(예: min300)은 DB 를 따로 만들어 DATABASE_URL 환경변수로 가리킨 뒤 --index min300 으로 넣는다.
"""

import json
import sys
from collections import Counter
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shared.core.logger import get_logger
from shared.core.models import Chunk
from shared.core.timing import measure_time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "chunking"))

from build_chunks import read_index, version_of  # noqa: E402

OUT = HERE.parent / "data" / "index"  # 색인마다 OUT/{name}/manifest.json
log = get_logger(__name__)

# langchain_postgres 의 표 (PGVector 가 처음 만들 때 생긴다)
EXISTING_ROWS = """
SELECT e.id, e.cmetadata, e.document
FROM langchain_pg_embedding e
JOIN langchain_pg_collection c ON e.collection_id = c.uuid
WHERE c.name = %s
"""
# 행 ID(chunk_id)는 컬렉션과 상관없는 전역 키다. 덮어쓸 때 collection_id 는 바뀌지 않으므로,
# 다른 컬렉션에 같은 ID 가 있으면 그 행을 조용히 덮어쓰게 된다
FOREIGN_IDS = """
SELECT e.id, c.name
FROM langchain_pg_embedding e
JOIN langchain_pg_collection c ON e.collection_id = c.uuid
WHERE c.name <> %s AND e.id = ANY(%s)
"""
REFRESH_ROW = """
UPDATE langchain_pg_embedding SET cmetadata = %s, document = %s
WHERE id = %s
"""


def embed_input(chunk: Chunk) -> str:
    """임베딩 · BM25 가 보는 글자 — 머리말 + 본문 (§3). 저장하는 본문은 text 그대로다"""
    return f"{chunk.header}\n{chunk.text}" if chunk.header else chunk.text


def metadata(chunk: Chunk) -> dict[str, Any]:
    """text 를 뺀 청크의 칸 전부. Chunk.model_validate({**metadata, "text": document}) 로 되돌아간다"""
    return chunk.model_dump(mode="json", exclude={"text"})


@dataclass
class Plan:
    insert: list[Chunk] = field(default_factory=list)
    update: list[Chunk] = field(default_factory=list)
    refresh: list[Chunk] = field(default_factory=list)  # 해시가 같고 메타데이터만 다르다
    unchanged: list[Chunk] = field(default_factory=list)
    delete: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {
            "inserted": len(self.insert),
            "updated": len(self.update),
            "unchanged_skipped": len(self.unchanged) + len(self.refresh),
            "metadata_refreshed": len(self.refresh),
            "deleted": len(self.delete),
        }


def plan_load(chunks: list[Chunk], existing: dict[str, tuple[dict[str, Any], str]]) -> Plan:
    """새 청크와 DB 의 행을 chunk_id 로 맞대어 넷으로 가른다 (§4.2 의 표)"""
    plan = Plan()
    for chunk in chunks:
        row = existing.get(chunk.chunk_id)
        if row is None:
            plan.insert.append(chunk)
        elif row[0].get("content_hash") != chunk.content_hash:
            plan.update.append(chunk)
        elif row[0] != metadata(chunk) or row[1] != chunk.text:
            plan.refresh.append(chunk)
        else:
            plan.unchanged.append(chunk)
    ids = {chunk.chunk_id for chunk in chunks}
    plan.delete = sorted(set(existing) - ids)
    return plan


def fetch_rows(conn: Any, collection: str) -> dict[str, tuple[dict[str, Any], str]]:
    with conn.cursor() as cur:
        cur.execute(EXISTING_ROWS, (collection,))
        return {row[0]: (row[1] or {}, row[2] or "") for row in cur.fetchall()}


@measure_time(label="embed")
def embed(texts: list[str]) -> list[list[float]]:
    from shared.core.vector_store import get_embeddings

    return get_embeddings().embed_documents(texts)


@measure_time(label="upsert")
def upsert(store: Any, chunks: list[Chunk], vectors: list[list[float]]) -> None:
    store.add_embeddings(
        texts=[c.text for c in chunks],
        embeddings=vectors,
        metadatas=[metadata(c) for c in chunks],
        ids=[c.chunk_id for c in chunks],
    )


def load(name: str = "base", collection: str | None = None) -> dict[str, Any]:
    from psycopg.types.json import Jsonb

    from shared.core.config import settings
    from shared.core.vector_store import get_raw_db_connection, get_vector_store

    collection = collection or settings.MOVIE_COLLECTION
    chunks = read_index(name)
    file_version = version_of((c.chunk_id, c.content_hash) for c in chunks)
    log.info("적재 시작: 색인 %s · 청크 %d개 → 컬렉션 %s", name, len(chunks), collection)

    store = get_vector_store(collection)  # 표와 컬렉션이 없으면 여기서 만든다
    with closing(get_raw_db_connection()) as conn:
        existing = fetch_rows(conn, collection)
        plan = plan_load(chunks, existing)
        log.info("적재 계획: %s", plan.counts())

        changed = plan.insert + plan.update
        if changed:
            with conn.cursor() as cur:
                cur.execute(FOREIGN_IDS, (collection, [c.chunk_id for c in changed]))
                clash = cur.fetchall()
            if clash:
                raise ValueError(
                    f"다른 컬렉션에 같은 chunk_id 가 있어 덮어쓰게 됩니다: {clash[:5]}"
                )
            log.info(
                "임베딩: %d개 (%s, %d자)",
                len(changed),
                settings.EMBEDDING_MODEL,
                sum(len(embed_input(c)) for c in changed),
            )
            upsert(store, changed, embed([embed_input(c) for c in changed]))
        if plan.refresh:
            with conn.cursor() as cur:
                cur.executemany(
                    REFRESH_ROW, [(Jsonb(metadata(c)), c.text, c.chunk_id) for c in plan.refresh]
                )
            conn.commit()
        if plan.delete:
            store.delete(ids=plan.delete, collection_only=True)

        after = fetch_rows(conn, collection)
    db_version = version_of((cid, row[0].get("content_hash", "")) for cid, row in after.items())
    if len(after) != len(chunks) or db_version != file_version:
        raise ValueError(
            f"적재 뒤 확인 실패: DB {len(after)}행 · 버전 {db_version}, "
            f"파일 {len(chunks)}개 · 버전 {file_version}"
        )

    from urllib.parse import urlsplit

    report = {
        # 색인 변형은 DB 를 나눠 적재한다 (행 ID 가 전역 키라 같은 DB 의 다른 컬렉션에 넣을 수 없다)
        "database": urlsplit(settings.DATABASE_URL).path.lstrip("/"),
        "collection": collection,
        "index": name,
        "index_version": db_version,
        "embedding_model": settings.EMBEDDING_MODEL,
        "rows": len(after),
        "rows_by_section": dict(Counter(row[0].get("section") for row in after.values())),
        "load": plan.counts(),
        "loaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    (OUT / name).mkdir(parents=True, exist_ok=True)
    (OUT / name / "manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info("적재 끝: %s · 색인 버전 %s", plan.counts(), db_version)
    return report


if __name__ == "__main__":
    import argparse

    from shared.core.logger import setup_logging

    parser = argparse.ArgumentParser(description="색인 적재")
    parser.add_argument("--index", default="base", help="청크 변형 이름 (data/chunks_{index})")
    setup_logging(log_file="logs/youngmin.log")
    print(json.dumps(load(parser.parse_args().index), ensure_ascii=False, indent=2))
