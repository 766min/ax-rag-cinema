"""배포 API (src/api) 검사. OpenAI · 색인 없이 돈다.

게시판은 진짜 Postgres 가 있어야 해서 BOARD_TEST_DATABASE_URL(없으면 로컬 Docker DB)에
임시 스키마를 만들어 쓰고 지운다. 연결이 안 되면 게시판 테스트만 건너뛴다.
"""

import os
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from api import ask, board, works
from api.limits import RateLimit
from api.main import app
from build.chunks import Dataset
from shared.core.models import Document, SourceId, Work
from shared.core.schemas import AnswerStatus

client = TestClient(app)


# ---------- 영화 정보 ----------


def _work(
    work_id: str, title_ko: str, title: str, year: int, canon: bool, order: int | None
) -> Work:
    return Work(work_id=work_id, title_ko=title_ko, title_original=title, format="movie",
                year=year, phase="1", timeline_order=order, is_canon=canon)  # fmt: skip


FAKE_DATA = Dataset(
    works=[
        _work("tt0000002", "토르", "Thor", 2011, True, 5),
        _work("tt0000001", "아이언맨", "Iron Man", 2008, True, 4),
        _work("tt0000003", "옛날 만화", "Old Cartoon", 1996, False, None),
    ],
    documents={
        "tt0000001-wikipedia": Document(
            doc_id="tt0000001-wikipedia",
            work_id="tt0000001",
            source_id=SourceId.WIKIPEDIA_EN,
            source_url="https://en.wikipedia.org/wiki/Iron_Man_(2008_film)",
            license="CC BY-SA 4.0",
            doc_type="film",
        )  # fmt: skip
    },
    works_meta={},
    docs_meta={},
    sections={
        "tt0000001-wikipedia": [
            {"section_path": ["Plot"], "text": "plot text"},
            {"section_path": ["(lead)"], "text": "Iron Man is a 2008 film."},
        ]
    },
)


@pytest.fixture(autouse=True)
def fake_dataset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(works, "_dataset", lambda: FAKE_DATA)


def test_health() -> None:
    assert client.get("/api/health").json()["data"] == {"status": "ok"}


def test_works_default_is_canon_by_year() -> None:
    data = client.get("/api/works").json()["data"]
    assert [w["title_ko"] for w in data] == ["아이언맨", "토르"]
    assert "source_ids" not in data[0]  # IMDb · 원본 ID 는 보내지 않는다


def test_works_search_timeline_and_all() -> None:
    assert [w["title_original"] for w in client.get("/api/works?q=THOR").json()["data"]] == ["Thor"]
    timeline = client.get("/api/works?sort=timeline").json()["data"]
    assert [w["timeline_order"] for w in timeline] == [4, 5]
    assert len(client.get("/api/works?canon=false").json()["data"]) == 1


def test_work_detail_has_lead_and_license() -> None:
    data = client.get("/api/works/tt0000001").json()["data"]
    assert data["summary"] == "Iron Man is a 2008 film."
    assert data["license"] == "CC BY-SA 4.0" and data["source_url"].startswith("https://")
    no_doc = client.get("/api/works/tt0000002").json()["data"]
    assert no_doc["summary"] == "" and no_doc["source_url"] is None


def test_unknown_work_is_404() -> None:
    r = client.get("/api/works/tt9999999")
    assert r.status_code == 404 and r.json()["success"] is False


# ---------- 챗봇 ----------

CITE = {
    "chunk_id": "tt0000001-wikipedia-plot-p03",
    "kind": "document",
    "title": "Iron Man",
    "title_ko": "아이언맨",
    "section_path": "Plot",
    "excerpt": "Stark builds a suit…",
    "source_url": "https://en.wikipedia.org/w/index.php?oldid=7",
    "license": "CC BY-SA 4.0",
    "rank": 1,
}


@pytest.fixture
def fake_answer(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """서비스 경로 대신 정해 둔 결과를 돌려준다. 테스트가 result 를 바꿔 상태별 화면을 본다."""
    monkeypatch.setattr(ask, "per_ip", RateLimit(100, 60, "too many"))
    monkeypatch.setattr(ask, "per_ip_day", RateLimit(100, 86400, "too many"))
    seen: dict[str, Any] = {
        "result": {
            "status": AnswerStatus.ANSWERED,
            "answer_ko": "토니는 동굴에서 아머를 만들어요 [tt0000001-wikipedia-plot-p03, tt9-bad-p01].",
            "search_query": "What did Tony build?",
            "partial": False,
            "citations": [CITE],
        }
    }

    async def answer(question: str) -> dict[str, Any]:
        seen["question"] = question
        return seen["result"]

    monkeypatch.setattr(ask, "answer", answer)
    return seen


def test_ask_answered_numbers_citations(fake_answer: dict[str, Any]) -> None:
    r = client.post("/api/ask", json={"question": " 토니는 동굴에서 뭘 만들어? "})
    data = r.json()["data"]
    assert r.status_code == 200 and data["status"] == "answered"
    assert data["answer"] == "토니는 동굴에서 아머를 만들어요 [1]."  # 출처 밖 ID 는 지움
    assert data["sources"][0]["n"] == 1 and data["sources"][0]["section_path"] == "Plot"
    assert data["search_query"] == "What did Tony build?" and data["request_id"].startswith("req_")
    assert fake_answer["question"] == "토니는 동굴에서 뭘 만들어?"


def test_ask_with_work_id_prefixes_title(fake_answer: dict[str, Any]) -> None:
    client.post("/api/ask", json={"question": "결말이 어때?", "work_id": "tt0000001"})
    assert fake_answer["question"] == "(아이언맨) 결말이 어때?"
    assert client.post("/api/ask", json={"question": "q", "work_id": "tt9"}).status_code == 404


def test_ask_no_evidence_and_web_are_200(fake_answer: dict[str, Any]) -> None:
    fake_answer["result"] = {
        "status": AnswerStatus.NO_EVIDENCE,
        "answer_ko": "확인 가능한 근거를 찾지 못했습니다.",
        "citations": [],
    }
    data = client.post("/api/ask", json={"question": "q"}).json()["data"]
    assert data["status"] == "no_evidence" and data["sources"] == []
    web = {**CITE, "chunk_id": "web-0123456789ab-snippet-p01", "kind": "web_snippet"}
    fake_answer["result"] = {
        "status": AnswerStatus.WEB_UNVERIFIED,
        "answer_ko": "웹 [web-0123456789ab-snippet-p01]",
        "citations": [web],
    }
    data = client.post("/api/ask", json={"question": "q"}).json()["data"]
    assert data["status"] == "web_unverified" and data["answer"] == "웹 [1]"
    assert data["sources"][0]["kind"] == "web_snippet"


@pytest.mark.parametrize("status", [AnswerStatus.RETRIEVAL_ERROR, AnswerStatus.GENERATION_ERROR])
def test_ask_failures_are_503(fake_answer: dict[str, Any], status: AnswerStatus) -> None:
    fake_answer["result"] = {"status": status, "answer_ko": "", "error": "secret detail"}
    r = client.post("/api/ask", json={"question": "q"})
    assert r.status_code == 503 and r.json()["code"] == status.upper()
    assert "secret detail" not in r.text  # 내부 오류 내용은 밖으로 보내지 않는다


def test_ask_input_limits(fake_answer: dict[str, Any]) -> None:
    assert client.post("/api/ask", json={"question": ""}).status_code == 422
    assert client.post("/api/ask", json={"question": "가" * 501}).status_code == 422


def test_ask_rate_limit(fake_answer: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ask, "per_ip", RateLimit(2, 60, "too many"))
    codes = [client.post("/api/ask", json={"question": "q"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_fake_mode_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ask, "FAKE_ASK", True)
    monkeypatch.setattr(ask, "per_ip", RateLimit(100, 60, "too many"))
    data = client.post("/api/ask", json={"question": "타임 스톤이 뭐야?"}).json()["data"]
    assert data["status"] == "answered" and data["sources"][0]["n"] == 1


def test_number_citations_keeps_plain_brackets() -> None:
    ids = ["tt1-wikipedia-plot-p01", "wp-5-lore-p02"]
    text = "A [tt1-wikipedia-plot-p01]. B [wp-5-lore-p02, tt1-wikipedia-plot-p01]. C [메모]. D [wp-9-x-p01]."
    assert ask.number_citations(text, ids) == "A [1]. B [2, 1]. C [메모]. D ."
    # 모델이 ; 로 나눈 경우 (실제 C+ 답에서 나옴)
    assert (
        ask.number_citations("E [tt1-wikipedia-plot-p01, wp-9-x-p01; wp-5-lore-p02].", ids)
        == "E [1, 2]."
    )


# ---------- 게시판 (진짜 Postgres) ----------


def _test_dsn() -> str:
    return os.getenv(
        "BOARD_TEST_DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5432/rag_db"
    )


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    dsn = _test_dsn()
    try:
        psycopg.connect(dsn, connect_timeout=2).close()
    except psycopg.OperationalError:
        pytest.skip("게시판 테스트용 Postgres 가 없음 (BOARD_TEST_DATABASE_URL)")
    schema = f"board_test_{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("DATABASE_URL", dsn)
    monkeypatch.setenv("BOARD_SCHEMA", schema)
    monkeypatch.setenv("BOARD_ADMIN_TOKEN", "admin-token")
    monkeypatch.setattr(board, "writes", RateLimit(100, 60, "too many"))
    monkeypatch.setattr(board, "password_tries", RateLimit(100, 60, "too many"))
    yield schema
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    board._ready.discard(schema)


POST = {
    "category": "lore",
    "title": "타노스의 계획",
    "body": "<b>본문</b>",
    "nickname": "로어탐험가",
    "password": "1234",
}


def _post(**over: Any) -> dict[str, Any]:
    r = client.post("/api/posts", json={**POST, **over})
    assert r.status_code == 201, r.text
    return r.json()["data"]


def test_board_post_flow(db: str) -> None:
    post = _post()
    assert post["body"] == "<b>본문</b>" and "password" not in str(
        post
    )  # 글은 그대로 저장, 비밀번호는 안 나감
    _post(category="free", title="두 번째")
    items = client.get("/api/posts").json()["data"]["items"]
    assert [p["title"] for p in items] == ["두 번째", "타노스의 계획"]
    assert [p["title"] for p in client.get("/api/posts?category=lore").json()["data"]["items"]] == [
        "타노스의 계획"
    ]

    pid = post["id"]
    c = client.post(
        f"/api/posts/{pid}/comments", json={"body": "좋은 글", "nickname": "팬", "password": "abcd"}
    )
    assert c.status_code == 201
    detail = client.get(f"/api/posts/{pid}").json()["data"]
    assert detail["comment_count"] == 1 and detail["comments"][0]["body"] == "좋은 글"

    first = client.post(f"/api/posts/{pid}/like").json()["data"]
    again = client.post(f"/api/posts/{pid}/like").json()["data"]
    assert first == {"like_count": 1, "liked": True} and again == {"like_count": 1, "liked": False}
    assert client.get("/api/posts?sort=popular").json()["data"]["items"][0]["id"] == pid

    cid = c.json()["data"]["id"]
    assert (
        client.request("DELETE", f"/api/comments/{cid}", json={"password": "nope"}).status_code
        == 403
    )
    assert (
        client.request("DELETE", f"/api/comments/{cid}", json={"password": "abcd"}).status_code
        == 200
    )
    assert client.get(f"/api/posts/{pid}").json()["data"]["comment_count"] == 0

    assert (
        client.request("DELETE", f"/api/posts/{pid}", json={"password": "0000"}).status_code == 403
    )
    assert (
        client.request("DELETE", f"/api/posts/{pid}", json={"password": "1234"}).status_code == 200
    )
    assert client.get(f"/api/posts/{pid}").status_code == 404


def test_board_validation(db: str) -> None:
    assert client.post("/api/posts", json={**POST, "title": "   "}).status_code == 422
    assert client.post("/api/posts", json={**POST, "category": "spam"}).status_code == 422
    assert client.post("/api/posts", json={**POST, "body": "가" * 5001}).status_code == 422
    assert client.post("/api/posts", json={**POST, "password": "12"}).status_code == 422
    assert (
        client.post(
            "/api/posts/999999/comments", json={"body": "x", "nickname": "n", "password": "abcd"}
        ).status_code
        == 404
    )


def test_board_search_and_paging(db: str) -> None:
    _post(title="100% 확실한 이론")
    _post(title="보통 글")
    assert [
        p["title"] for p in client.get("/api/posts", params={"q": "100%"}).json()["data"]["items"]
    ] == ["100% 확실한 이론"]
    assert (
        client.get("/api/posts", params={"q": "%"}).json()["data"]["items"][0]["title"]
        == "100% 확실한 이론"
    )
    page = client.get("/api/posts?limit=1").json()["data"]
    assert len(page["items"]) == 1 and page["has_more"] is True


def test_board_admin_hide(db: str) -> None:
    pid = _post()["id"]
    assert client.post(f"/api/posts/{pid}/hide").status_code == 403
    assert (
        client.post(f"/api/posts/{pid}/hide", headers={"X-Admin-Token": "wrong"}).status_code == 403
    )
    assert (
        client.post(f"/api/posts/{pid}/hide", headers={"X-Admin-Token": "admin-token"}).status_code
        == 200
    )
    assert client.get("/api/posts").json()["data"]["items"] == []
    assert client.post(f"/api/posts/{pid}/like").status_code == 404


def test_board_write_rate_limit(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(board, "writes", RateLimit(1, 60, "too many"))
    assert client.post("/api/posts", json=POST).status_code == 201
    assert client.post("/api/posts", json=POST).status_code == 429


def test_board_db_down_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://x:y@127.0.0.1:1/none")
    monkeypatch.setenv("BOARD_SCHEMA", "board_down")
    r = client.get("/api/posts")
    assert r.status_code == 503 and r.json()["code"] == "DB_UNAVAILABLE"


def test_password_hash_roundtrip() -> None:
    stored = board.hash_password("1234")
    assert stored.startswith("scrypt$") and "1234" not in stored
    assert board.check_password("1234", stored) and not board.check_password("1235", stored)


def test_summary_cut_at_sentence_end(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(works, "SUMMARY_CHARS", 30)
    assert works._cut("First sentence here. Second one is long.") == "First sentence here."
    assert works._cut("short") == "short"


def test_web_files_serves_next_prefetch(tmp_path: Any) -> None:
    from fastapi import FastAPI

    from api.main import WebFiles

    (tmp_path / "explore" / "__next.explore").mkdir(parents=True)
    (tmp_path / "explore" / "__next.explore" / "__PAGE__.txt").write_text("rsc")
    (tmp_path / "explore" / "index.html").write_text("<h1>explore</h1>")
    web = FastAPI()
    web.mount("/", WebFiles(directory=tmp_path, html=True))
    c = TestClient(web)
    assert c.get("/explore/__next.explore.__PAGE__.txt").text == "rsc"
    assert "explore" in c.get("/explore/").text
    assert c.get("/explore/nope.txt").status_code == 404


def test_board_password_guessing_is_limited(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    pid = _post()["id"]
    monkeypatch.setattr(board, "password_tries", RateLimit(3, 60, "too many"))
    codes = [
        client.request("DELETE", f"/api/posts/{pid}", json={"password": f"{n:04d}"}).status_code
        for n in range(4)
    ]
    assert codes == [403, 403, 403, 429]  # 4자리 비밀번호를 하나씩 대 보는 공격을 막는다


def test_ask_per_ip_daily_limit(
    fake_answer: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ask, "per_ip_day", RateLimit(1, 86400, "today done"))
    assert client.post("/api/ask", json={"question": "q"}).status_code == 200
    r = client.post("/api/ask", json={"question": "q"})
    assert r.status_code == 429 and r.json()["message"] == "today done"


def test_ask_while_warming_up_is_503_not_hang(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    monkeypatch.setattr(ask, "FAKE_ASK", False)
    monkeypatch.setattr(ask, "_state", {})
    lock = asyncio.Lock()
    monkeypatch.setattr(ask, "_lock", lock)

    async def run() -> int:
        await lock.acquire()  # 서버 시작 때 색인 · 모델을 읽는 중인 상태
        try:
            await ask.answer("q")
        except ask.SharedException as exc:
            return exc.status_code
        return 200

    assert asyncio.run(run()) == 503


def test_ipv6_counted_by_64_block() -> None:
    from types import SimpleNamespace

    from api.limits import client_ip

    a = client_ip(SimpleNamespace(client=SimpleNamespace(host="2001:db8:1:2::1")))  # type: ignore[arg-type]
    b = client_ip(SimpleNamespace(client=SimpleNamespace(host="2001:db8:1:2:ffff::9")))  # type: ignore[arg-type]
    assert a == b == "2001:db8:1:2::/64"
    assert client_ip(SimpleNamespace(client=SimpleNamespace(host="1.2.3.4"))) == "1.2.3.4"  # type: ignore[arg-type]


def test_answer_cache_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    from service.answer_cache import AnswerCache

    monkeypatch.setattr(ask, "CACHE_MAX", 2)
    cache = ask._bounded(AnswerCache({}, {}))
    for k in "abc":
        cache.put(k, {"v": k})
    assert list(cache._store) == ["b", "c"]  # 가장 오래된 답부터 버린다


VS = {
    **POST,
    "category": "vs",
    "title": "아이언맨의 후계자는?",
    "choice_a": "스파이더맨",
    "choice_b": "샘 윌슨",
}


def test_board_vs_vote_and_change(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(board, "votes", RateLimit(100, 60, "too many"))
    post = _post(**{k: v for k, v in VS.items() if k not in POST or VS[k] != POST[k]})
    assert post["choice_a"] == "스파이더맨" and post["votes_a"] == 0
    pid = post["id"]
    first = client.post(f"/api/posts/{pid}/vote", json={"side": "a"}).json()["data"]
    again = client.post(f"/api/posts/{pid}/vote", json={"side": "a"}).json()["data"]
    assert first == again == {"votes_a": 1, "votes_b": 0, "my_vote": "a"}  # 같은 쪽 두 번은 한 표
    changed = client.post(f"/api/posts/{pid}/vote", json={"side": "b"}).json()["data"]
    assert changed == {"votes_a": 0, "votes_b": 1, "my_vote": "b"}  # 바꾸면 옮겨 간다
    listed = client.get("/api/posts?category=vs").json()["data"]["items"][0]
    assert listed["my_vote"] == "b" and listed["votes_b"] == 1
    assert client.get(f"/api/posts/{pid}").json()["data"]["my_vote"] == "b"
    assert client.post(f"/api/posts/{pid}/vote", json={"side": "c"}).status_code == 422


def test_board_vs_rules(db: str) -> None:
    assert (
        client.post("/api/posts", json={**POST, "category": "vs"}).status_code == 422
    )  # 선택지 없음
    plain = _post(choice_a="버려짐", choice_b="버려짐")
    assert plain["choice_a"] is None  # VS 가 아닌 글은 선택지를 버린다
    r = client.post(f"/api/posts/{plain['id']}/vote", json={"side": "a"})
    assert r.status_code == 400 and r.json()["code"] == "NOT_VS"


def test_board_sorts_days_and_stats(db: str) -> None:
    a = _post(title="댓글 많은 글")
    _post(title="새 글")
    client.post(
        f"/api/posts/{a['id']}/comments", json={"body": "c", "nickname": "n", "password": "abcd"}
    )
    by_comments = client.get("/api/posts?sort=comments").json()["data"]["items"]
    assert by_comments[0]["title"] == "댓글 많은 글"
    assert len(client.get("/api/posts?days=7").json()["data"]["items"]) == 2
    stats = client.get("/api/board/stats").json()["data"]
    assert stats == {"today_posts": 2, "open_debates": 0, "week_participation": 3}


@pytest.mark.parametrize(
    ("raw", "text", "partial"),
    [
        ("타노스는 균형을 원한다. [1] 범위: 전체", "타노스는 균형을 원한다. [1]", False),
        ("일부만 확인돼요. [1]\nCOVERAGE: partial", "일부만 확인돼요. [1]", True),
        ("범위가 넓은 이야기예요.", "범위가 넓은 이야기예요.", False),  # 본문 속 '범위' 는 그대로
    ],
)
def test_strip_coverage_tail(raw: str, text: str, partial: bool) -> None:
    assert ask.strip_coverage(raw) == (text, partial)


def test_ask_hides_coverage_tail(fake_answer: dict[str, Any]) -> None:
    fake_answer["result"] = {
        **fake_answer["result"],
        "answer_ko": "답이에요 [tt0000001-wikipedia-plot-p03]. 범위: 부분",
    }
    data = client.post("/api/ask", json={"question": "q"}).json()["data"]
    assert data["answer"] == "답이에요 [1]." and data["partial"] is True
