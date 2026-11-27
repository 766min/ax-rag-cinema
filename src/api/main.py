"""CINEVERSE 배포 서버: /api/* (챗봇 · 영화 정보 · 게시판) + Next.js 정적 화면(web/out).

실행 (저장소 루트):  uv run --locked uvicorn api.main:app --app-dir src --port 8000
화면 개발 중에는 web/ 에서 npm run dev (3000) 를 따로 띄우고 CORS_ORIGINS 로 잇는다.
"""

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

load_dotenv()  # 아래 모듈들이 FAKE_ASK · ASK_MODE 등을 import 시점에 읽는다

from api import ask, board, works  # noqa: E402
from shared.core.exception_handlers import register_exception_handlers  # noqa: E402
from shared.core.schemas import APIResponse  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = Path(os.getenv("WEB_DIR", ROOT / "web" / "out"))
CORS_ORIGINS = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
    if o.strip()
]


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # 서버가 켜지자마자 색인 · 재정렬 모델을 미리 읽어 둔다 (첫 질문이 수십 초 기다리지 않게)
    warm = None if ask.FAKE_ASK else asyncio.create_task(ask.warm_up())
    yield
    if warm:
        warm.cancel()


app = FastAPI(title="CINEVERSE API", lifespan=lifespan)
register_exception_handlers(app)
# 화면을 FastAPI 가 같이 보내는 배포에서는 같은 주소라 CORS 가 필요 없다 (CORS_ORIGINS 를 비워도 됨)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-Admin-Token"],
)
for module in (ask, works, board):
    app.include_router(module.router, prefix="/api")


@app.get("/api/health")
def health() -> APIResponse[dict[str, str]]:
    return APIResponse.ok({"status": "ok"})


class WebFiles(StaticFiles):
    """Next 정적 화면. 화면 이동 미리 받기는 explore/__next.explore.__PAGE__.txt 를 부르는데
    빌드 결과는 explore/__next.explore/__PAGE__.txt 라서 없으면 그 경로로 한 번 더 찾는다."""

    def lookup_path(self, path: str) -> tuple[str, os.stat_result | None]:
        full, stat = super().lookup_path(path)
        if stat is None and path.endswith(".__PAGE__.txt"):
            return super().lookup_path(path.removesuffix(".__PAGE__.txt") + "/__PAGE__.txt")
        return full, stat


# API 경로를 다 등록한 뒤에 마지막으로 붙여야 /api/* 가 화면 파일에 가려지지 않는다
if WEB_DIR.is_dir():
    app.mount("/", WebFiles(directory=WEB_DIR, html=True), name="web")
