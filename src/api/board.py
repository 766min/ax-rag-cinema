"""커뮤니티 게시판 (Cosmic Feed). 로그인 없이 닉네임 + 삭제용 비밀번호로 쓴다.

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
from pydantic import BaseModel, Field, StringConstraints

from api.limits import RateLimit, client_ip
from shared.core.exceptions import NotFoundException, SharedException
from shared.core.schemas import APIResponse

router = APIRouter(tags=["board"])

Category = Literal["lore", "post_credits", "watch_order", "theory", "free"]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
PAGE = 20

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
    secret = os.getenv("BOARD_SECRET", "dev-only-secret")
    return hmac.new(secret.encode(), client_ip(request).encode(), "sha256").hexdigest()[:32]


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


class CommentIn(BaseModel):
    body: Annotated[Text, StringConstraints(max_length=1000)]
    nickname: Annotated[Text, StringConstraints(max_length=20)]
    password: str = Field(min_length=4, max_length=64)


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


POST_COLS = "id, category, title, body, nickname, work_id, like_count, comment_count, created_at"


# ---------- 글 ----------


@router.get("/posts")
def list_posts(
    category: Category | None = None,
    sort: Literal["new", "popular"] = "new",
    q: str = Query("", max_length=100),
    page: int = Query(0, ge=0, le=500),
    limit: int = Query(PAGE, ge=1, le=50),
) -> APIResponse[PostPage]:
    order = "like_count DESC, comment_count DESC, id DESC" if sort == "popular" else "id DESC"
    where: list[str] = ["NOT hidden"]
    args: list[Any] = []
    if category:
        where.append("category = %s")
        args.append(category)
    if q.strip():
        where.append("(title ILIKE %s OR body ILIKE %s)")
        like = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        args += [like, like]
    with _db() as (cur, s):
        cur.execute(
            f"SELECT {POST_COLS} FROM {s}.posts WHERE {' AND '.join(where)} "
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
            f"INSERT INTO {s}.posts (category, title, body, nickname, password_hash, work_id) "
            f"VALUES (%s, %s, %s, %s, %s, %s) RETURNING {POST_COLS}",
            [
                body.category,
                body.title,
                body.body,
                body.nickname,
                hash_password(body.password),
                body.work_id,
            ],
        )
        row = cur.fetchone()
    assert row is not None
    return APIResponse.ok(PostOut(**row), message="글을 올렸어요.", code="CREATED")


def _visible_post(
    cur: psycopg.Cursor[dict[str, Any]], s: str, post_id: int, cols: str
) -> dict[str, Any]:
    cur.execute(f"SELECT {cols} FROM {s}.posts WHERE id = %s AND NOT hidden", [post_id])
    row = cur.fetchone()
    if row is None:
        raise NotFoundException("글을 찾을 수 없어요.")
    return row


@router.get("/posts/{post_id}")
def get_post(post_id: int) -> APIResponse[PostDetail]:
    with _db() as (cur, s):
        post = _visible_post(cur, s, post_id, POST_COLS)
        cur.execute(
            f"SELECT id, post_id, nickname, body, created_at FROM {s}.comments "
            "WHERE post_id = %s AND NOT hidden ORDER BY id",
            [post_id],
        )
        comments = [CommentOut(**r) for r in cur.fetchall()]
    return APIResponse.ok(PostDetail(**post, comments=comments))


@router.delete("/posts/{post_id}")
def delete_post(post_id: int, body: PasswordIn) -> APIResponse[None]:
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
def delete_comment(comment_id: int, body: PasswordIn) -> APIResponse[None]:
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
