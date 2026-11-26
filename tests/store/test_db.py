# 증분 적재(src/store/db.py) 테스트_영민

from typing import Any
import pytest
from build.chunks import index_version, version_of
from shared.core.models import Chunk, SourceId
from shared.core.schemas import Section
from store.db import embed_input, load, metadata, plan_load, row_to_chunk

MODEL = "text-embedding-3-small"


# 테스트용 청크
def chunk(position: int, text: str = "a" * 200, header: str = "[Iron Man · 2008 film]") -> Chunk:
    return Chunk(
        chunk_id=f"tt0371746-wikipedia-plot-p{position:02d}",
        doc_id="tt0371746-wikipedia",
        work_id="tt0371746",
        source_id=SourceId.WIKIPEDIA_EN,
        section=Section.PLOT,
        position=position,
        total=max(3, position),
        text=text,
        header=header,
        source_url="https://en.wikipedia.org/w/index.php?oldid=1",
        revid=1,
        content_hash=f"h{position}-{len(text)}",
    )


# 청크가 DB 에 들어갔을 때의 행 모양 (메타데이터, 본문)
def row(c: Chunk) -> tuple[dict, str]:
    return metadata(c, MODEL), c.text


# [검증] 계획이 다섯 갈래를 맞게 나눔
def test_plan_splits_new_changed_same_and_gone() -> None:
    same, changed, new = chunk(1), chunk(2, text="b" * 300), chunk(3)
    revised = chunk(1).model_copy(update={"revid": 2})
    existing = {
        same.chunk_id: row(revised),
        changed.chunk_id: row(chunk(2)),
        "tt0371746-wikipedia-plot-p09": row(chunk(9)),
    }
    plan = plan_load([same, changed, new], existing, MODEL)
    assert [c.chunk_id for c in plan.insert] == [new.chunk_id]
    assert [c.chunk_id for c in plan.update] == [changed.chunk_id]
    assert [c.chunk_id for c in plan.refresh] == [same.chunk_id]
    assert plan.delete == ["tt0371746-wikipedia-plot-p09"]


# [검증] 같은 청크로 다시 계획하면 전부 건너뜀
def test_second_load_skips_everything() -> None:
    chunks = [chunk(1), chunk(2)]
    plan = plan_load(chunks, {c.chunk_id: row(c) for c in chunks}, MODEL)
    assert plan.counts()["unchanged_skipped"] == 2 and not (plan.insert or plan.update)


# [검증] 청크 → DB 행 → 청크 왕복이 같음 
def test_row_round_trips_to_the_same_chunk() -> None:
    c = chunk(1)
    meta, document = row(c)
    assert "text" not in meta
    assert row_to_chunk(meta, document) == c


# [검증] 임베딩은 머리말 + 본문, 저장 본문은 원문만
def test_embedding_sees_header_but_stored_text_does_not() -> None:
    c = chunk(1, text="Tony Stark builds a suit.")
    assert embed_input(c) == "[Iron Man · 2008 film]\nTony Stark builds a suit."
    assert row(c)[1] == "Tony Stark builds a suit."


# [검증] DB 쪽 버전 계산과 파일 쪽 버전 계산이 같음
def test_db_version_matches_file_version() -> None:
    chunks = [chunk(2), chunk(1)]
    pairs = [(c.chunk_id, c.content_hash) for c in reversed(chunks)]
    assert version_of(pairs) == index_version(chunks)


# 가짜 DB load

class FakeDB:

    def __init__(self) -> None:
        self.rows: dict[str, tuple[str, dict[str, Any], str]] = {}
        self.embedded = 0

    # --- 연결 ---
    def cursor(self) -> "FakeDB":
        return self

    def __enter__(self) -> "FakeDB":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: tuple = ()) -> None:
        if "to_regclass" in sql:
            self._out: list = [(True,)]
        elif "c.name = %s" in sql:
            self._out = [(i, m, d) for i, (c, m, d) in self.rows.items() if c == params[0]]
        elif "c.name <> %s" in sql:
            self._out = [
                (i, c) for i, (c, _, _) in self.rows.items() if c != params[0] and i in params[1]
            ]

    def fetchone(self) -> tuple:
        return self._out[0]

    def fetchall(self) -> list:
        return self._out

    def commit(self) -> None: ...

    def close(self) -> None: ...

    # --- 저장소 ---
    def store(self, collection: str) -> Any:
        db = self

        class Store:
            def add_embeddings(self, texts, embeddings, metadatas, ids):  # type: ignore[no-untyped-def]
                for t, m, i in zip(texts, metadatas, ids, strict=True):
                    db.rows[i] = (collection, m, t)

            def delete(self, ids, collection_only):  # type: ignore[no-untyped-def]
                for i in ids:
                    db.rows.pop(i)

        return Store()

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.embedded += len(texts)
        return [[0.0] for _ in texts]


# 가짜 DB 로 load() 실행
def run(db: FakeDB, chunks: list[Chunk], collection: str = "c1", **kw: Any) -> dict[str, Any]:
    return load(
        chunks,
        collection,
        connect=lambda: db,
        store=db.store(collection),
        embed=db.embed,
        embedding_model=MODEL,
        database="rag_db_test",
        **kw,
    )


# [검증] 두 번 적재해도 임베딩은 처음 한 번만 진행(=동일 버전) 
def test_load_twice_embeds_once_and_versions_match() -> None:
    db, chunks = FakeDB(), [chunk(1), chunk(2)]
    first = run(db, chunks)
    assert first["load"]["inserted"] == 2 and db.embedded == 2
    second = run(db, chunks)
    assert second["load"]["unchanged_skipped"] == 2 and db.embedded == 2
    assert first["index_version"] == second["index_version"] == index_version(chunks)


# [검증] 기존 행을 지우는 적재는 허용 없이는 멈추고 아무것도 바꾸지 않음
def test_changing_existing_rows_needs_allow_changes() -> None:
    db = FakeDB()
    run(db, [chunk(1), chunk(2)])
    with pytest.raises(ValueError, match="allow_changes"):
        run(db, [chunk(1)])  # p02 를 지우게 된다
    assert len(db.rows) == 2
    assert run(db, [chunk(1)], allow_changes=True)["load"]["deleted"] == 1


# [검증] 다른 컬렉션의 같은 ID 를 덮어쓰지 않음
def test_same_id_in_another_collection_stops_the_load() -> None:
    db = FakeDB()
    run(db, [chunk(1)], collection="other")
    with pytest.raises(ValueError, match="다른 컬렉션"):
        run(db, [chunk(1)], collection="c1")
    assert db.rows[chunk(1).chunk_id][0] == "other"  # 덮어쓰지 않았다


# [검증] 다른 임베딩 모델의 행과 섞지 않음
def test_other_embedding_model_rows_stop_the_load() -> None:
    db = FakeDB()
    run(db, [chunk(1)])
    with pytest.raises(ValueError, match="임베딩 모델"):
        load(
            [chunk(1)],
            "c1",
            connect=lambda: db,
            store=db.store("c1"),
            embed=db.embed,
            embedding_model="other-model",
            database="rag_db_test",
        )


# [검증] dry_run 은 계획만 보고 임베딩하지 않음
def test_dry_run_does_not_embed() -> None:
    db = FakeDB()
    report = run(db, [chunk(1)], dry_run=True)
    assert report["dry_run"] and report["load"]["inserted"] == 1 and db.embedded == 0
