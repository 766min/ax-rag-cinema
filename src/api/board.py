"""커뮤니티 게시판 (멀티버스 아레나). 로그인 없이 닉네임 + 삭제용 비밀번호로 쓴다.

VS 토론 글(category=vs)은 두 선택지에 한 사람(IP)당 한 표씩 던질 수 있고, 표는 바꿀 수 있다.

표는 RAG 색인과 같은 Postgres 의 board 스키마에 둔다 (처음 요청 때 없으면 만든다).
글은 화면에서 텍스트로만 보여 준다 (HTML 을 해석하지 않음).
"""

import hashlib
import hmac
import os
import re
import secrets
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated, Any, Literal

import psycopg
from fastapi import APIRouter, Header, Query, Request, status
from psycopg.rows import dict_row
from pydantic import BaseModel, Field, StringConstraints, model_validator

from api.limits import RateLimit, client_ip
from shared.core.exceptions import NotFoundException, SharedException
from shared.core.schemas import APIResponse

router = APIRouter(tags=["board"])

Category = Literal["vs", "lore", "post_credits", "watch_order", "theory", "free"]
Side = Literal["a", "b"]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
PAGE = 20

# 지우기 비밀번호 맞히기 막기 (4자리 비밀번호를 하나씩 대 보는 공격)
password_tries = RateLimit(
    int(os.getenv("BOARD_DELETES_PER_MINUTE", "5")),
    60,
    "비밀번호를 너무 많이 시도했어요. 1분 뒤에 다시 해 주세요.",
)
# 좋아요 중복 막기용 비밀값. 없으면 서버마다 임의 값 (고정 기본값이면 해시에서 IP 를 되찾을 수 있다)
SECRET = os.getenv("BOARD_SECRET") or secrets.token_hex(32)
votes = RateLimit(
    int(os.getenv("BOARD_VOTES_PER_MINUTE", "30")),
    60,
    "투표를 너무 자주 했어요. 잠시 후 다시 해 주세요.",
)
writes = RateLimit(
    int(os.getenv("BOARD_WRITES_PER_MINUTE", "5")),
    60,
    "글을 너무 빨리 쓰고 있어요. 잠시 후 다시 해 주세요.",
)
_ready: set[str] = set()
_ready_lock = threading.Lock()


def _schema() -> str:
    name = os.getenv("BOARD_SCHEMA", "board")
    if not re.fullmatch(
        r"[a-z_][a-z0-9_]{0,40}", name
    ):  # SQL 에 그대로 들어가므로 이름 모양을 막는다
        raise RuntimeError(f"BOARD_SCHEMA 이름이 올바르지 않습니다: {name}")
    return name


def _dsn() -> str:
    url = os.getenv("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL 이 없습니다")
    return url.replace("postgresql+psycopg://", "postgresql://")


def _ddl(s: str) -> str:
    return f"""
    CREATE SCHEMA IF NOT EXISTS {s};
    CREATE TABLE IF NOT EXISTS {s}.posts (
        id bigserial PRIMARY KEY,
        category text NOT NULL,
        title text NOT NULL,
        body text NOT NULL,
        nickname text NOT NULL,
        password_hash text NOT NULL,
        work_id text,
        like_count integer NOT NULL DEFAULT 0,
        comment_count integer NOT NULL DEFAULT 0,
        hidden boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS posts_created_idx ON {s}.posts (created_at DESC);
    CREATE TABLE IF NOT EXISTS {s}.comments (
        id bigserial PRIMARY KEY,
        post_id bigint NOT NULL REFERENCES {s}.posts(id) ON DELETE CASCADE,
        nickname text NOT NULL,
        password_hash text NOT NULL,
        body text NOT NULL,
        hidden boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS comments_post_idx ON {s}.comments (post_id, id);
    CREATE TABLE IF NOT EXISTS {s}.post_likes (
        post_id bigint NOT NULL REFERENCES {s}.posts(id) ON DELETE CASCADE,
        voter text NOT NULL,
        PRIMARY KEY (post_id, voter)
    );
    ALTER TABLE {s}.posts ADD COLUMN IF NOT EXISTS choice_a text;
    ALTER TABLE {s}.posts ADD COLUMN IF NOT EXISTS choice_b text;
    ALTER TABLE {s}.posts ADD COLUMN IF NOT EXISTS votes_a integer NOT NULL DEFAULT 0;
    ALTER TABLE {s}.posts ADD COLUMN IF NOT EXISTS votes_b integer NOT NULL DEFAULT 0;
    CREATE TABLE IF NOT EXISTS {s}.post_votes (
        post_id bigint NOT NULL REFERENCES {s}.posts(id) ON DELETE CASCADE,
        voter text NOT NULL,
        side char(1) NOT NULL CHECK (side IN ('a', 'b')),
        voted_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (post_id, voter)
    );
    """


@contextmanager
def _db() -> Iterator[tuple[psycopg.Cursor[dict[str, Any]], str]]:
    s = _schema()
    try:
        conn = psycopg.connect(_dsn(), connect_timeout=5, row_factory=dict_row)
    except (psycopg.OperationalError, RuntimeError) as exc:
        raise SharedException(
            "게시판 DB 에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.",
            status_code=503,
            error_code="DB_UNAVAILABLE",
        ) from exc
    with conn:  # 블록이 끝나면 커밋, 예외면 롤백, 연결 닫기
        with _ready_lock:
            if s not in _ready:  # 표 만들기는 따로 커밋해서, 뒤 요청이 실패해도 표는 남게
                conn.execute(_ddl(s))
                conn.commit()
                _ready.add(s)
        with conn.cursor() as cur:
            yield cur, s


# ---------- 비밀번호 (표준 라이브러리 scrypt) ----------


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    _, salt, digest = stored.split("$")
    got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
    return hmac.compare_digest(got.hex(), digest)


def _voter(request: Request) -> str:
    # 좋아요 중복 막기용. IP 를 그대로 저장하지 않고 비밀값을 섞은 해시만 남긴다
    return hmac.new(SECRET.encode(), client_ip(request).encode(), "sha256").hexdigest()[:32]


def _admin(token: str | None) -> None:
    expected = os.getenv("BOARD_ADMIN_TOKEN", "")
    if not expected or not token or not hmac.compare_digest(token, expected):
        raise SharedException("관리자만 할 수 있어요.", status_code=403, error_code="FORBIDDEN")


# ---------- 요청 · 응답 모양 ----------


class PostIn(BaseModel):
    category: Category
    title: Annotated[Text, StringConstraints(max_length=100)]
    body: Annotated[Text, StringConstraints(max_length=5000)]
    nickname: Annotated[Text, StringConstraints(max_length=20)]
    password: str = Field(min_length=4, max_length=64)
    work_id: str | None = Field(None, max_length=40)
    choice_a: Annotated[Text, StringConstraints(max_length=40)] | None = None
    choice_b: Annotated[Text, StringConstraints(max_length=40)] | None = None

    @model_validator(mode="after")
    def _choices(self) -> "PostIn":
        """VS 토론 글은 두 선택지가 꼭 있어야 하고, 다른 글은 선택지를 버린다."""
        if self.category == "vs" and not (self.choice_a and self.choice_b):
            raise ValueError("VS 토론 글은 선택지 두 개가 필요해요")
        if self.category != "vs":
            self.choice_a = self.choice_b = None
        return self


class CommentIn(BaseModel):
    body: Annotated[Text, StringConstraints(max_length=1000)]
    nickname: Annotated[Text, StringConstraints(max_length=20)]
    password: str = Field(min_length=4, max_length=64)


class VoteIn(BaseModel):
    side: Side


class PasswordIn(BaseModel):
    password: str = Field(min_length=1, max_length=64)


class PostOut(BaseModel):
    id: int
    category: Category
    title: str
    body: str
    nickname: str
    work_id: str | None
    like_count: int
    comment_count: int
    created_at: datetime
    choice_a: str | None = None
    choice_b: str | None = None
    votes_a: int = 0
    votes_b: int = 0
    my_vote: Side | None = Field(None, description="이 사람(IP)이 던진 표")


class CommentOut(BaseModel):
    id: int
    post_id: int
    nickname: str
    body: str
    created_at: datetime


class PostDetail(PostOut):
    comments: list[CommentOut]


class PostPage(BaseModel):
    items: list[PostOut]
    page: int
    has_more: bool


class LikeOut(BaseModel):
    like_count: int
    liked: bool = Field(description="이번 요청으로 새로 눌렸나 (이미 눌렀으면 false)")


POST_COLS = (
    "id, category, title, body, nickname, work_id, like_count, comment_count, created_at, "
    "choice_a, choice_b, votes_a, votes_b"
)


class VoteOut(BaseModel):
    votes_a: int
    votes_b: int
    my_vote: Side


class BoardStats(BaseModel):
    today_posts: int = Field(description="오늘 올라온 글")
    open_debates: int = Field(description="최근 7일 VS 토론 글")
    week_participation: int = Field(description="최근 7일 글 · 댓글 · 투표 수")


def _with_my_vote(s: str) -> str:
    """POST_COLS + 이 사람의 표 (SQL 인자 맨 앞에 voter 를 넘긴다)."""
    return (
        f"{POST_COLS}, (SELECT v.side FROM {s}.post_votes v "
        f"WHERE v.post_id = {s}.posts.id AND v.voter = %s) AS my_vote"
    )


ORDERS = {
    "new": "id DESC",
    "popular": "like_count DESC, comment_count DESC, id DESC",
    "comments": "comment_count DESC, id DESC",
    "votes": "votes_a + votes_b DESC, id DESC",
}


# ---------- 글 ----------


@router.get("/posts")
def list_posts(
    request: Request,
    category: Category | None = None,
    sort: Literal["new", "popular", "comments", "votes"] = "new",
    q: str = Query("", max_length=100),
    days: int | None = Query(None, ge=1, le=30, description="최근 며칠 안의 글만"),
    page: int = Query(0, ge=0, le=500),
    limit: int = Query(PAGE, ge=1, le=50),
) -> APIResponse[PostPage]:
    order = ORDERS[sort]
    where: list[str] = ["NOT hidden"]
    args: list[Any] = [_voter(request)]
    if days:
        where.append("created_at > now() - make_interval(days => %s)")
        args.append(days)
    if category:
        where.append("category = %s")
        args.append(category)
    if q.strip():
        where.append("(title ILIKE %s OR body ILIKE %s)")
        like = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        args += [like, like]
    with _db() as (cur, s):
        cur.execute(
            f"SELECT {_with_my_vote(s)} FROM {s}.posts WHERE {' AND '.join(where)} "
            f"ORDER BY {order} LIMIT %s OFFSET %s",
            [*args, limit + 1, page * limit],
        )
        rows = cur.fetchall()
    return APIResponse.ok(
        PostPage(items=[PostOut(**r) for r in rows[:limit]], page=page, has_more=len(rows) > limit)
    )


@router.post("/posts", status_code=status.HTTP_201_CREATED)
def create_post(body: PostIn, request: Request) -> APIResponse[PostOut]:
    writes.check(client_ip(request))
    with _db() as (cur, s):
        cur.execute(
            f"INSERT INTO {s}.posts "
            "(category, title, body, nickname, password_hash, work_id, choice_a, choice_b) "
            f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING {POST_COLS}",
            [
                body.category,
                body.title,
                body.body,
                body.nickname,
                hash_password(body.password),
                body.work_id,
                body.choice_a,
                body.choice_b,
            ],
        )
        row = cur.fetchone()
    assert row is not None
    return APIResponse.ok(PostOut(**row), message="글을 올렸어요.", code="CREATED")


def _visible_post(
    cur: psycopg.Cursor[dict[str, Any]],
    s: str,
    post_id: int,
    cols: str,
    args: list[Any] | None = None,
) -> dict[str, Any]:
    cur.execute(
        f"SELECT {cols} FROM {s}.posts WHERE id = %s AND NOT hidden", [*(args or []), post_id]
    )
    row = cur.fetchone()
    if row is None:
        raise NotFoundException("글을 찾을 수 없어요.")
    return row


@router.get("/posts/{post_id}")
def get_post(post_id: int, request: Request) -> APIResponse[PostDetail]:
    with _db() as (cur, s):
        post = _visible_post(cur, s, post_id, _with_my_vote(s), [_voter(request)])
        cur.execute(
            f"SELECT id, post_id, nickname, body, created_at FROM {s}.comments "
            "WHERE post_id = %s AND NOT hidden ORDER BY id",
            [post_id],
        )
        comments = [CommentOut(**r) for r in cur.fetchall()]
    return APIResponse.ok(PostDetail(**post, comments=comments))


@router.delete("/posts/{post_id}")
def delete_post(post_id: int, body: PasswordIn, request: Request) -> APIResponse[None]:
    password_tries.check(client_ip(request))
    with _db() as (cur, s):
        row = _visible_post(cur, s, post_id, "password_hash")
        if not check_password(body.password, row["password_hash"]):
            raise SharedException(
                "비밀번호가 맞지 않아요.", status_code=403, error_code="WRONG_PASSWORD"
            )
        cur.execute(f"DELETE FROM {s}.posts WHERE id = %s", [post_id])
    return APIResponse.ok(None, message="글을 지웠어요.")


@router.post("/posts/{post_id}/like")
def like_post(post_id: int, request: Request) -> APIResponse[LikeOut]:
    with _db() as (cur, s):
        _visible_post(cur, s, post_id, "id")
        cur.execute(
            f"INSERT INTO {s}.post_likes (post_id, voter) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            [post_id, _voter(request)],
        )
        liked = cur.rowcount == 1
        if liked:
            cur.execute(
                f"UPDATE {s}.posts SET like_count = like_count + 1 WHERE id = %s", [post_id]
            )
        cur.execute(f"SELECT like_count FROM {s}.posts WHERE id = %s", [post_id])
        row = cur.fetchone()
    assert row is not None
    return APIResponse.ok(LikeOut(like_count=row["like_count"], liked=liked))


@router.post("/posts/{post_id}/vote")
def vote(post_id: int, body: VoteIn, request: Request) -> APIResponse[VoteOut]:
    """VS 토론에 한 표. 이미 던졌으면 다른 쪽으로 바꾼다 (같은 쪽이면 그대로)."""
    votes.check(client_ip(request))
    voter = _voter(request)
    with _db() as (cur, s):
        post = _visible_post(cur, s, post_id, "category")
        if post["category"] != "vs":
            raise SharedException("VS 토론 글이 아니에요.", status_code=400, error_code="NOT_VS")
        cur.execute(
            f"INSERT INTO {s}.post_votes (post_id, voter, side) VALUES (%s, %s, %s) "
            "ON CONFLICT (post_id, voter) DO UPDATE SET side = EXCLUDED.side, voted_at = now() "
            f"WHERE {s}.post_votes.side <> EXCLUDED.side",
            [post_id, voter, body.side],
        )
        # 표 수는 표 기록에서 다시 센다 (두 번 연달아 눌러도 한 표로만 남는다)
        cur.execute(
            f"UPDATE {s}.posts SET "
            f"votes_a = (SELECT count(*) FROM {s}.post_votes WHERE post_id = %s AND side = 'a'), "
            f"votes_b = (SELECT count(*) FROM {s}.post_votes WHERE post_id = %s AND side = 'b') "
            "WHERE id = %s",
            [post_id, post_id, post_id],
        )
        cur.execute(f"SELECT votes_a, votes_b FROM {s}.posts WHERE id = %s", [post_id])
        row = cur.fetchone()
    assert row is not None
    return APIResponse.ok(VoteOut(**row, my_vote=body.side))


@router.get("/board/stats")
def board_stats() -> APIResponse[BoardStats]:
    week = "now() - interval '7 days'"
    with _db() as (cur, s):
        cur.execute(
            f"""SELECT
              (SELECT count(*) FROM {s}.posts
                 WHERE NOT hidden AND created_at >= date_trunc('day', now())) AS today_posts,
              (SELECT count(*) FROM {s}.posts
                 WHERE NOT hidden AND category = 'vs' AND created_at > {week}) AS open_debates,
              (SELECT count(*) FROM {s}.posts WHERE created_at > {week})
              + (SELECT count(*) FROM {s}.comments WHERE created_at > {week})
              + (SELECT count(*) FROM {s}.post_votes WHERE voted_at > {week}) AS week_participation"""
        )
        row = cur.fetchone()
    assert row is not None
    return APIResponse.ok(BoardStats(**row))


# ---------- 댓글 ----------


@router.post("/posts/{post_id}/comments", status_code=status.HTTP_201_CREATED)
def create_comment(post_id: int, body: CommentIn, request: Request) -> APIResponse[CommentOut]:
    writes.check(client_ip(request))
    with _db() as (cur, s):
        _visible_post(cur, s, post_id, "id")
        cur.execute(
            f"INSERT INTO {s}.comments (post_id, nickname, password_hash, body) "
            "VALUES (%s, %s, %s, %s) RETURNING id, post_id, nickname, body, created_at",
            [post_id, body.nickname, hash_password(body.password), body.body],
        )
        row = cur.fetchone()
        cur.execute(
            f"UPDATE {s}.posts SET comment_count = comment_count + 1 WHERE id = %s", [post_id]
        )
    assert row is not None
    return APIResponse.ok(CommentOut(**row), message="댓글을 달았어요.", code="CREATED")


def _drop_comment(cur: psycopg.Cursor[dict[str, Any]], s: str, comment_id: int, how: str) -> None:
    cur.execute(f"{how} WHERE id = %s RETURNING post_id", [comment_id])
    row = cur.fetchone()
    if row:
        cur.execute(
            f"UPDATE {s}.posts SET comment_count = GREATEST(comment_count - 1, 0) WHERE id = %s",
            [row["post_id"]],
        )


def _visible_comment(
    cur: psycopg.Cursor[dict[str, Any]], s: str, comment_id: int
) -> dict[str, Any]:
    cur.execute(
        f"SELECT password_hash FROM {s}.comments WHERE id = %s AND NOT hidden", [comment_id]
    )
    row = cur.fetchone()
    if row is None:
        raise NotFoundException("댓글을 찾을 수 없어요.")
    return row


@router.delete("/comments/{comment_id}")
def delete_comment(comment_id: int, body: PasswordIn, request: Request) -> APIResponse[None]:
    password_tries.check(client_ip(request))
    with _db() as (cur, s):
        row = _visible_comment(cur, s, comment_id)
        if not check_password(body.password, row["password_hash"]):
            raise SharedException(
                "비밀번호가 맞지 않아요.", status_code=403, error_code="WRONG_PASSWORD"
            )
        _drop_comment(cur, s, comment_id, f"DELETE FROM {s}.comments")
    return APIResponse.ok(None, message="댓글을 지웠어요.")


# ---------- 관리자 숨김 (.env 의 BOARD_ADMIN_TOKEN 을 X-Admin-Token 머리글로) ----------


@router.post("/posts/{post_id}/hide")
def hide_post(post_id: int, x_admin_token: str | None = Header(None)) -> APIResponse[None]:
    _admin(x_admin_token)
    with _db() as (cur, s):
        _visible_post(cur, s, post_id, "id")
        cur.execute(f"UPDATE {s}.posts SET hidden = true WHERE id = %s", [post_id])
    return APIResponse.ok(None, message="글을 숨겼어요.")


@router.post("/comments/{comment_id}/hide")
def hide_comment(comment_id: int, x_admin_token: str | None = Header(None)) -> APIResponse[None]:
    _admin(x_admin_token)
    with _db() as (cur, s):
        _visible_comment(cur, s, comment_id)
        _drop_comment(cur, s, comment_id, f"UPDATE {s}.comments SET hidden = true")
    return APIResponse.ok(None, message="댓글을 숨겼어요.")
