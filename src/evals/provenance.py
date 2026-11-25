"""실험 결과 파일에 함께 남기는 실행 환경 식별 정보와 버전별 결과 폴더.

결과는 results/{버전}/ 아래에만 쓴다 (V0 는 results/archive/v0_handoff/, 읽기 전용).
"""

import hashlib
import platform
import subprocess
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from etl.dataset import PROJECT_ROOT, SCOPE_VERSION

PACKAGES = (
    "langchain",
    "langchain-openai",
    "langchain-postgres",
    "langchain-text-splitters",
    "openai",
)


def results_dir(results_version: str) -> Path:
    """results/{버전} (예: results/v1). archive 같은 다른 이름은 받지 않는다."""
    if not (results_version.startswith("v") and results_version[1:].isdigit()):
        raise ValueError(f"결과 버전은 v1, v2 … 형식이어야 합니다: {results_version}")
    return PROJECT_ROOT / "results" / results_version


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, check=True
    ).stdout.rstrip()  # 앞 공백은 status 칸이라 지우지 않는다


def _package(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def code_fingerprint() -> str:
    """src/ · scripts/ 의 .py 파일 경로와 내용으로 만든 SHA-256."""
    digest = hashlib.sha256()
    for path in sorted((*PROJECT_ROOT.glob("src/**/*.py"), *PROJECT_ROOT.glob("scripts/*.py"))):
        digest.update(path.relative_to(PROJECT_ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def run_info(results_version: str) -> dict[str, Any]:
    """git 커밋 · 커밋 안 된 변경 · 패키지 · 공통 설정(비밀값 제외)."""
    from shared.core.config import settings

    # 아직 커밋 안 한 새 파일도 포함 (results · data 는 ignore 라 안 보인다)
    dirty = _git("status", "--porcelain")
    return {
        "results_version": results_version,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "git_commit": _git("rev-parse", "HEAD"),
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "git_dirty": bool(dirty),
        "git_dirty_files": dirty.splitlines(),
        # 커밋 전에 돌린 실행도 나중에 어느 커밋과 같은 코드였는지 대조할 수 있게
        "code_sha256": code_fingerprint(),
        "scope_version": SCOPE_VERSION,
        "python": platform.python_version(),
        "packages": {name: _package(name) for name in PACKAGES},
        "settings": {
            "LLM_PROVIDER": settings.LLM_PROVIDER,
            "LLM_MODEL": settings.LLM_MODEL,
            "EMBEDDING_MODEL": settings.EMBEDDING_MODEL,
            "REASONING_EFFORT": settings.REASONING_EFFORT,
            "LLM_TIMEOUT": settings.LLM_TIMEOUT,
            "MOVIE_COLLECTION": settings.MOVIE_COLLECTION,
        },
    }
