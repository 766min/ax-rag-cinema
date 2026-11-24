"""평가 실행 기록기: 질문 x 구성마다 시간·토큰·결과를 results/ 에 JSONL 한 줄로 남긴다."""

import json
import platform
import subprocess
import time
import uuid
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.tracers.context import register_configure_hook

from shared.core.timing import collect_timings, current_label, run_status

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_TOKEN_KEYS = ("input_tokens", "output_tokens", "total_tokens")


class _StageTokens(BaseCallbackHandler):
    """LLM 호출이 끝날 때마다 사용 토큰을 '지금 실행 중인 measure_time 단계' 이름으로 모은다."""

    def __init__(self) -> None:
        self.by_stage: dict[str, dict[str, int]] = defaultdict(
            lambda: dict.fromkeys(_TOKEN_KEYS, 0)
        )

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        stage = current_label() or "unlabeled"
        for generations in response.generations:
            for gen in generations:
                message = gen.message if isinstance(gen, ChatGeneration) else None
                usage = message.usage_metadata if isinstance(message, AIMessage) else None
                if usage:
                    counts = self.by_stage[stage]
                    counts["input_tokens"] += usage["input_tokens"]
                    counts["output_tokens"] += usage["output_tokens"]
                    counts["total_tokens"] += usage["total_tokens"]


_tokens_var: ContextVar[_StageTokens | None] = ContextVar("recorder_stage_tokens", default=None)
# 이 ContextVar 에 handler 가 있으면 LangChain 이 모든 LLM 호출에 자동으로 붙인다
register_configure_hook(_tokens_var, inheritable=True)


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=_PROJECT_ROOT,
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Recorder:
    """실험 1회(run) = results/{experiment_id}/{run_id}/ 폴더 1개.

    - manifest.json : 실행 조건 (시작할 때 1번, 덮어쓰기 금지)
    - records.jsonl : track() 블록 1번 = 1줄
    """

    def __init__(
        self,
        experiment_id: str,
        manifest: dict[str, Any] | None = None,
        results_dir: str | Path = "results",
    ) -> None:
        base = Path(results_dir)
        if not base.is_absolute():
            base = _PROJECT_ROOT / base
        self.experiment_id = experiment_id
        self.run_id = f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
        self.run_dir = base / experiment_id / self.run_id
        self.run_dir.mkdir(
            parents=True, exist_ok=False
        )  # 같은 run 폴더가 있으면 실패 (덮어쓰기 방지)
        self.records_path = self.run_dir / "records.jsonl"

        header = {
            "experiment_id": experiment_id,
            "run_id": self.run_id,
            "started_at": _now(),
            "git_commit": _git_commit(),
            "python": platform.python_version(),
        }
        with open(self.run_dir / "manifest.json", "x", encoding="utf-8") as f:
            json.dump({**header, **(manifest or {})}, f, ensure_ascii=False, indent=2)

    @contextmanager
    def track(self, query_id: str, variant: str, **meta: Any) -> Iterator[dict[str, Any]]:
        """블록 안의 시간·토큰을 모아 records.jsonl 에 1줄 쓴다. 예외는 기록한 뒤 다시 던진다.

        yield 되는 dict 에 답변·검색 결과 등을 넣으면 그대로 함께 저장된다.
        """
        result: dict[str, Any] = {}
        tokens = _StageTokens()
        token_reset = _tokens_var.set(tokens)
        error: BaseException | None = None
        start = time.perf_counter()
        try:
            with collect_timings() as events:
                yield result
        except BaseException as exc:
            error = exc
            raise
        finally:
            end_to_end_ms = round((time.perf_counter() - start) * 1000, 1)
            _tokens_var.reset(token_reset)
            self._write(
                {
                    "query_id": query_id,
                    "variant": variant,
                    **meta,
                    "status": run_status(error),
                    "error_type": type(error).__name__ if error else None,
                    "end_to_end_ms": end_to_end_ms,
                    "timings": events,
                    "tokens": _with_total(tokens.by_stage),
                    "result": result,
                    "recorded_at": _now(),
                }
            )

    def _write(self, record: dict[str, Any]) -> None:
        # 기록 실패는 삼키지 않는다: 이 실행 결과를 믿을 수 없게 되므로 예외가 그대로 올라간다
        with open(self.records_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _with_total(by_stage: dict[str, dict[str, int]]) -> dict[str, dict[str, int]] | None:
    """단계별 토큰에 total 을 더한다. LLM 호출이 없었으면 0 이 아니라 None (미측정)."""
    if not by_stage:
        return None
    total = {key: sum(stage[key] for stage in by_stage.values()) for key in _TOKEN_KEYS}
    return {**by_stage, "total": total}
