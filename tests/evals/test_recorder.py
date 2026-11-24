import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from evals.recorder import Recorder
from shared.core.timing import measure_time


def _fake_llm(*usages: tuple[int, int]) -> GenericFakeChatModel:
    """호출할 때마다 정해진 토큰 사용량을 돌려주는 가짜 LLM (실제 API 호출 없음)."""
    messages = [
        AIMessage(
            content="답",
            usage_metadata={"input_tokens": i, "output_tokens": o, "total_tokens": i + o},
        )
        for i, o in usages
    ]
    return GenericFakeChatModel(messages=iter(messages))


def _lines(rec: Recorder) -> list[dict[str, Any]]:
    return [json.loads(line) for line in rec.records_path.read_text(encoding="utf-8").splitlines()]


def test_manifest_생성(tmp_path: Path) -> None:
    rec = Recorder(
        "exp1",
        manifest={"variant_list": ["vector"], "dataset_version": "v1.1"},
        results_dir=tmp_path,
    )

    manifest = json.loads((rec.run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert rec.run_dir.parent == tmp_path / "exp1"
    assert manifest["experiment_id"] == "exp1"
    assert manifest["run_id"] == rec.run_id
    assert manifest["dataset_version"] == "v1.1"


def test_run_id는_매번_달라서_덮어쓰지_않는다(tmp_path: Path) -> None:
    a = Recorder("exp1", results_dir=tmp_path)
    b = Recorder("exp1", results_dir=tmp_path)
    assert a.run_dir != b.run_dir


def test_단계별_시간과_토큰을_한줄로_기록(tmp_path: Path) -> None:
    llm = _fake_llm((100, 10), (800, 150))

    @measure_time(label="query_rewrite")
    def rewrite(q: str) -> str:
        return str(llm.invoke(q).content)

    @measure_time(label="vector_search")
    def search(q: str) -> list[str]:
        return ["chunk-1"]

    @measure_time(label="generation")
    def generate(q: str) -> str:
        return str(llm.invoke(q).content)

    rec = Recorder("exp1", results_dir=tmp_path)
    with rec.track("q07", "hybrid_rerank_rewrite") as result:
        q = rewrite("질문")
        result["retrieved"] = search(q)
        result["answer"] = generate(q)

    [line] = _lines(rec)
    assert (line["query_id"], line["variant"], line["status"]) == (
        "q07",
        "hybrid_rerank_rewrite",
        "success",
    )
    assert [t["label"] for t in line["timings"]] == ["query_rewrite", "vector_search", "generation"]
    assert line["end_to_end_ms"] >= 0
    assert line["tokens"]["query_rewrite"]["input_tokens"] == 100
    assert line["tokens"]["generation"]["output_tokens"] == 150
    assert line["tokens"]["total"] == {
        "input_tokens": 900,
        "output_tokens": 160,
        "total_tokens": 1060,
    }
    assert "vector_search" not in line["tokens"]  # LLM 을 안 부른 단계는 토큰 없음
    assert line["result"] == {"retrieved": ["chunk-1"], "answer": "답"}


def test_LLM_호출이_없으면_tokens는_None(tmp_path: Path) -> None:
    rec = Recorder("exp1", results_dir=tmp_path)
    with rec.track("q01", "vector"):
        pass
    assert _lines(rec)[0]["tokens"] is None  # 0 이 아니라 '미측정'


def test_실패해도_기록하고_예외는_다시_던진다(tmp_path: Path) -> None:
    rec = Recorder("exp1", results_dir=tmp_path)
    with pytest.raises(TimeoutError), rec.track("q02", "vector"):
        raise TimeoutError

    [line] = _lines(rec)
    assert (line["status"], line["error_type"]) == ("error", "TimeoutError")


def test_비동기_동시_실행에서도_질문별로_섞이지_않는다(tmp_path: Path) -> None:
    rec = Recorder("exp1", results_dir=tmp_path)

    @measure_time(label="generation")
    async def generate(q: str) -> str:
        await asyncio.sleep(0.01)
        return str((await _fake_llm((len(q), 1)).ainvoke(q)).content)

    async def one(query_id: str, question: str) -> None:
        with rec.track(query_id, "vector"):
            await generate(question)

    async def main() -> None:
        await asyncio.gather(one("short", "ab"), one("long", "abcdefgh"))

    asyncio.run(main())
    by_id = {line["query_id"]: line for line in _lines(rec)}
    assert by_id["short"]["tokens"]["total"]["input_tokens"] == 2
    assert by_id["long"]["tokens"]["total"]["input_tokens"] == 8
    assert len(by_id["short"]["timings"]) == len(by_id["long"]["timings"]) == 1


def test_track_밖의_measure_time은_기록되지_않는다(tmp_path: Path) -> None:
    @measure_time(label="outside")
    def work() -> None:
        pass

    rec = Recorder("exp1", results_dir=tmp_path)
    work()
    with rec.track("q03", "vector"):
        pass
    assert _lines(rec)[0]["timings"] == []
