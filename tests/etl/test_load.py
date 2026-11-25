from typing import Any

from etl.load import (
    count_tokens,
    fetch_existing,
    index_chunks,
    load_corpus,
    meta_values,
    plan,
    row_id,
    to_document,
)
from shared.core.models import Chunk, SourceId
from shared.core.schemas import Section


def _chunk(position: int, text: str = "body") -> Chunk:
    return Chunk(
        chunk_id=f"tt0371746-wikipedia-plot-p{position:02d}",
        doc_id="tt0371746-wikipedia",
        work_id="tt0371746",
        source_id=SourceId.WIKIPEDIA_EN,
        section=Section.PLOT,
        position=position,
        total=3,
        text=text,
        header="Iron Man > Plot",
        source_url="https://en.wikipedia.org/wiki/Iron_Man",
        content_hash=f"hash-{position}-{text}",
    )


def test_문서는_머리말_본문이고_메타데이터에_색인_버전() -> None:
    doc = to_document(_chunk(1, "Tony builds a suit."), "v2")

    assert doc.page_content == "Iron Man > Plot\nTony builds a suit."
    assert doc.metadata["chunk_id"] == "tt0371746-wikipedia-plot-p01"
    assert doc.metadata["index_version"] == "v2"
    assert "text" not in doc.metadata  # 본문은 page_content 에만


def test_해시와_버전이_같으면_건너뛰고_사라진_청크는_지운다() -> None:
    c1, c2 = _chunk(1), _chunk(2, "changed")
    existing = {
        c1.chunk_id: (c1.content_hash, "v2"),  # 그대로 → 건너뜀
        c2.chunk_id: ("old-hash", "v2"),  # 내용 바뀜 → 다시
        "tt0371746-wikipedia-plot-p09": ("x", "v2"),  # 이번엔 없음 → 삭제
    }
    to_add, to_delete = plan([c1, c2], existing, "v2")

    assert [c.chunk_id for c in to_add] == [c2.chunk_id]
    assert to_delete == ["tt0371746-wikipedia-plot-p09"]


def test_색인_버전이_바뀌면_다시_넣는다() -> None:
    c1 = _chunk(1)
    to_add, _ = plan([c1], {c1.chunk_id: (c1.content_hash, "v1")}, "v2")
    assert to_add == [c1]


class _Cursor:
    def __init__(self, rows: list[tuple[Any, ...]], tables: bool = True) -> None:
        self.rows = rows
        self.tables = tables
        self.params: Any = None
        self.queries = 0

    def execute(self, sql: str, params: Any = None) -> None:
        self.params = params
        self.queries += 1

    def fetchone(self) -> tuple[bool]:
        return (self.tables,)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows


class _Conn:
    def __init__(self, rows: list[tuple[Any, ...]], tables: bool = True) -> None:
        self.cur = _Cursor(rows, tables)

    def cursor(self) -> _Cursor:
        return self.cur


def test_기존_해시를_컬렉션_이름으로_조회() -> None:
    conn = _Conn([("chunk-a", "h1", "v2")])  # SQL 이 메타데이터의 논리 chunk_id 를 돌려준다
    assert fetch_existing(conn, "mcu_movies") == {"chunk-a": ("h1", "v2")}
    assert conn.cur.params == ("mcu_movies",)


# --- index_chunks: 실제 쓰기 · 삭제 경로 (가짜 store, DB 없음) -----------------------


class _Store:
    """delete · add_documents 호출만 기록하는 가짜 벡터 스토어."""

    def __init__(self) -> None:
        self.deleted: list[tuple[list[str], bool]] = []
        self.added: list[list[str]] = []

    def delete(self, ids: list[str], collection_only: bool = False) -> None:
        self.deleted.append((ids, collection_only))

    def add_documents(self, docs: list[Any], ids: list[str]) -> None:
        assert [d.metadata["chunk_id"] for d in docs] == [i.split(":", 1)[1] for i in ids]
        self.added.append(ids)


def test_바뀐_청크만_넣고_사라진_청크는_이_컬렉션에서만_지운다() -> None:
    c1, c2 = _chunk(1), _chunk(2, "changed")
    existing = [
        (c1.chunk_id, c1.content_hash, "v2"),  # 그대로
        (c2.chunk_id, "old-hash", "v2"),  # 바뀜
        ("tt0371746-wikipedia-plot-p09", "x", "v2"),  # 사라짐
    ]
    store = _Store()
    stats = index_chunks([c1, c2], store, _Conn(existing), "movies_ck03", "v2")  # type: ignore[arg-type]

    assert stats == {"total": 2, "added": 1, "skipped": 1, "deleted": 1}
    assert store.deleted == [(["movies_ck03:tt0371746-wikipedia-plot-p09"], True)]
    assert store.added == [["movies_ck03:tt0371746-wikipedia-plot-p02"]]


def test_배치로_나눠_넣는다() -> None:
    chunks = [_chunk(1), _chunk(2), _chunk(3)]
    store = _Store()
    index_chunks(chunks, store, _Conn([]), "c", "v2", batch_size=2)  # type: ignore[arg-type]

    assert [len(batch) for batch in store.added] == [2, 1]
    assert store.deleted == []  # 지울 것이 없으면 delete 를 부르지 않음


def test_다시_돌리면_아무것도_하지_않는다() -> None:
    chunks = [_chunk(1), _chunk(2)]
    existing = [(c.chunk_id, c.content_hash, "v2") for c in chunks]
    store = _Store()
    stats = index_chunks(chunks, store, _Conn(existing), "c", "v2")  # type: ignore[arg-type]

    assert stats["added"] == 0 and stats["skipped"] == 2
    assert store.added == [] and store.deleted == []


def test_행_ID는_컬렉션마다_다르다() -> None:
    assert row_id("movies_ck02", "a") != row_id("movies_ck03", "a")


def test_색인_설정_메타데이터를_청크마다_남긴다() -> None:
    doc = to_document(
        _chunk(1), "v2", {"embedding_model": "emb-x", "embedding_input": "header+text"}
    )
    assert (doc.metadata["embedding_model"], doc.metadata["embedding_input"]) == (
        "emb-x",
        "header+text",
    )


def test_토큰_수는_tiktoken으로_센다() -> None:
    encoding, counts = count_tokens(["hello world", ""], "text-embedding-3-small")
    assert encoding == "cl100k_base"
    assert counts[0] > 0 and counts[1] == 0


def test_새_DB에_PGVector_테이블이_없으면_기존_행_0개로_본다() -> None:
    conn = _Conn([("should-not-be-read", "h", "v")], tables=False)
    assert fetch_existing(conn, "c") == {}
    assert meta_values(conn, "c", "embedding_model") == set()
    assert load_corpus(conn, "c") == {}
