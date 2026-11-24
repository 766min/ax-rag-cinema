"""평가 실행 기록: 단계별 시간·토큰을 재서 RunRecord(models.py) 형식으로 results/ 에 남긴다."""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.tracers.context import register_configure_hook

from shared.core.models import RunRecord
from shared.core.timing import collect_timings, current_label

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class _StageTokens(BaseCallbackHandler):
    """LLM 호출이 끝날 때마다 사용 토큰을 '지금 실행 중인 measure_time 단계' 이름으로 모은다."""

    def __init__(self) -> None:
        self.tokens: dict[str, int] = {}

    def _add(self, key: str, value: int) -> None:
        self.tokens[key] = self.tokens.get(key, 0) + value

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        stage = current_label() or "unlabeled"
        for generations in response.generations:
            for gen in generations:
                message = gen.message if isinstance(gen, ChatGeneration) else None
                usage = message.usage_metadata if isinstance(message, AIMessage) else None
                if usage:
                    self._add("input", usage["input_tokens"])
                    self._add("output", usage["output_tokens"])
                    self._add(f"{stage}_input", usage["input_tokens"])
                    self._add(f"{stage}_output", usage["output_tokens"])


_tokens_var: ContextVar[_StageTokens | None] = ContextVar("recorder_stage_tokens", default=None)
# 이 ContextVar 에 handler 가 있으면 LangChain 이 모든 LLM 호출에 자동으로 붙인다
register_configure_hook(_tokens_var, inheritable=True)


@dataclass
class Measurement:
    """measure() 블록이 끝나면 채워진다. RunRecord 의 latency_ms · tokens 칸에 그대로 넣는다."""

    latency_ms: dict[str, int] = field(
        default_factory=dict
    )  # {"retrieve": 410, ..., "total": 4010}
    tokens: dict[str, int] = field(default_factory=dict)  # {"input": 1520, "output": 210, ...}


@contextmanager
def measure() -> Iterator[Measurement]:
    """블록 안의 @measure_time 단계 시간과 LLM 토큰을 모은다.

    예외가 나도 블록을 나가는 순간까지의 값은 채운 뒤 예외를 그대로 다시 던진다.
    같은 단계가 여러 번 실행되면(재시도 등) 시간을 더한다.
    """
    result = Measurement()
    handler = _StageTokens()
    reset = _tokens_var.set(handler)
    start = time.perf_counter()
    try:
        with collect_timings() as events:
            yield result
    finally:
        _tokens_var.reset(reset)
        for event in events:
            label = event["label"]
            result.latency_ms[label] = result.latency_ms.get(label, 0) + round(event["duration_ms"])
        result.latency_ms["total"] = round((time.perf_counter() - start) * 1000)
        result.tokens.update(handler.tokens)


class Recorder:
    """실행 묶음 하나(run_id) = results/{run_id}.jsonl 파일 하나. 한 줄 = RunRecord 하나."""

    def __init__(self, run_id: str, results_dir: str | Path = "results") -> None:
        base = Path(results_dir)
        if not base.is_absolute():
            base = _PROJECT_ROOT / base
        base.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.path = base / f"{run_id}.jsonl"
        self.path.open(
            "x", encoding="utf-8"
        ).close()  # 같은 run_id 파일이 있으면 실패 (덮어쓰기 방지)

    def write(self, record: RunRecord) -> None:
        """한 줄을 덧붙인다. 실패하면 예외가 그대로 올라간다 (기록이 빠진 실행을 믿지 않기 위해)."""
        if record.run_id != self.run_id:
            raise ValueError(f"run_id 가 다릅니다: {record.run_id} (이 기록기는 {self.run_id})")
        with self.path.open("a", encoding="utf-8") as f:
            f.write(record.model_dump_json() + "\n")
