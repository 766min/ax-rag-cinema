import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from eval_fakes import CHUNKS, NO_ROW, ROW

from build.chunks import index_version
from evals.items import to_items
from evals.recorder import Recorder
from evals.run import RunMeta, input_text, run_questions
from retrieve.pipeline import Retriever, SearchConfig
from service.answer import make_generate_en
from shared.core.models import Alias, RunRecord
from shared.core.schemas import AnswerStatus

ALIASES = [Alias(alias_ko="아이언맨", canonical_en="Iron Man", kind="work", target_id="tt0371746")]


class Store:
    def similarity_search_with_score(self, query: str, k: int) -> list:
        return [(SimpleNamespace(metadata={"chunk_id": c.chunk_id}), 0.3) for c in CHUNKS[1:]][:k]


class LLM:
    async def ainvoke(self, messages: list[Any]) -> Any:
        return SimpleNamespace(content=f"He builds armor [{CHUNKS[1].chunk_id}].\nCOVERAGE: full")


async def echo(_system: str, text: str) -> str:
    return text


def test_run_questions_writes_valid_run_records(tmp_path: Path) -> None:
    items, _ = to_items([ROW, NO_ROW], CHUNKS)
    recorder = Recorder("2026-11-26-gather-test-A-try1", results_dir=tmp_path)
    meta = RunMeta(index_version(CHUNKS), "e", "m", "p3-gather", "aliases-v", {"recipe": "s800"})
    records = asyncio.run(
        run_questions(items, mode_name="A", retriever=Retriever(Store(), CHUNKS),
                      generate_en=make_generate_en(LLM()), aliases=ALIASES,
                      config=SearchConfig(top_k=5, pool=20), recorder=recorder, meta=meta,
                      query_translator=echo, answer_translator=echo)
    )  # fmt: skip
    assert [r.status for r in records] == [AnswerStatus.ANSWERED, AnswerStatus.ANSWERED]
    path = tmp_path / "2026-11-26-gather-test-A-try1.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    first = RunRecord.model_validate_json(lines[0])
    assert first.trace.query_en == "" and first.trace.web_called is False
    assert first.params["mode"] == "A" and first.params["bm25_zero"] is None
    assert first.params["read"][0]["ids"] == [CHUNKS[1].chunk_id]
    assert "Q900" not in json.dumps(first.params)  # 정답표 값이 실행 기록 params 에 새지 않는다
    text = input_text(first.params["read"], {c.chunk_id: c for c in CHUNKS})
    assert CHUNKS[1].text in text
