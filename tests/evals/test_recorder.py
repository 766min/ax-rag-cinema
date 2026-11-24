import asyncio
from pathlib import Path

import pytest
from langchain_core.language_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from evals.recorder import Recorder, measure
from shared.core.models import EvidenceSource, RunRecord, Trace
from shared.core.schemas import AnswerStatus, RetrievalMode
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


def _record(run_id: str, **overrides: object) -> RunRecord:
    fields: dict[str, object] = {
        "run_id": run_id,
        "question_id": "Q001",
        "status": AnswerStatus.NO_EVIDENCE,
        "embedding_model": "emb",
        "llm_model": "llm",
        "prompt_version": "p1",
        "trace": Trace(
            query_ko="질문",
            query_normalized="질문",
            retrieval_mode=RetrievalMode.VECTOR,
            evidence_source=EvidenceSource.NONE,
            index_version="i1",
        ),
    }
    fields.update(overrides)
    return RunRecord.model_validate(fields)


# --- measure(): 시간과 토큰 -----------------------------------------------------


def test_단계별_시간과_토큰을_모은다() -> None:
    llm = _fake_llm((100, 10), (800, 150))

    @measure_time(label="rewrite")
    def rewrite(q: str) -> str:
        return str(llm.invoke(q).content)

    @measure_time(label="retrieve")
    def retrieve(q: str) -> list[str]:
        return ["chunk-1"]

    @measure_time(label="generate")
    def generate(q: str) -> str:
        return str(llm.invoke(q).content)

    with measure() as m:
        q = rewrite("질문")
        retrieve(q)
        generate(q)

    assert set(m.latency_ms) == {"rewrite", "retrieve", "generate", "total"}
    assert m.tokens == {
        "input": 900,
        "output": 160,
        "rewrite_input": 100,
        "rewrite_output": 10,
        "generate_input": 800,
        "generate_output": 150,
    }


def test_같은_단계를_여러번_실행하면_시간을_더한다() -> None:
    @measure_time(label="generate")
    def generate() -> None:
        pass

    with measure() as m:
        generate()
        generate()
    assert list(m.latency_ms) == ["generate", "total"]


def test_LLM_호출이_없으면_tokens는_비어있다() -> None:
    with measure() as m:
        pass
    assert m.tokens == {}
    assert "total" in m.latency_ms


def test_예외가_나도_그때까지의_값은_채운다() -> None:
    @measure_time(label="retrieve")
    def retrieve() -> None:
        raise TimeoutError

    with pytest.raises(TimeoutError), measure() as m:
        retrieve()
    assert "retrieve" in m.latency_ms
    assert "total" in m.latency_ms


def test_비동기_동시_실행에서도_질문별로_섞이지_않는다() -> None:
    @measure_time(label="generate")
    async def generate(q: str) -> str:
        await asyncio.sleep(0.01)
        return str((await _fake_llm((len(q), 1)).ainvoke(q)).content)

    async def one(q: str) -> dict[str, int]:
        with measure() as m:
            await generate(q)
        return m.tokens

    async def main() -> list[dict[str, int]]:
        return await asyncio.gather(one("ab"), one("abcdefgh"))

    short, long = asyncio.run(main())
    assert short["input"] == 2
    assert long["input"] == 8


# --- Recorder: RunRecord 저장 --------------------------------------------------


def test_RunRecord를_한줄씩_저장한다(tmp_path: Path) -> None:
    rec = Recorder("2026-11-25-test-A", results_dir=tmp_path)
    with measure() as m:
        pass
    rec.write(_record(rec.run_id, latency_ms=m.latency_ms, tokens=m.tokens))
    rec.write(_record(rec.run_id, question_id="Q002"))

    lines = rec.path.read_text(encoding="utf-8").splitlines()
    assert rec.path == tmp_path / "2026-11-25-test-A.jsonl"
    assert [RunRecord.model_validate_json(line).question_id for line in lines] == ["Q001", "Q002"]


def test_같은_run_id는_덮어쓰지_않는다(tmp_path: Path) -> None:
    Recorder("dup", results_dir=tmp_path)
    with pytest.raises(FileExistsError):
        Recorder("dup", results_dir=tmp_path)


def test_다른_run_id_기록은_거부한다(tmp_path: Path) -> None:
    rec = Recorder("mine", results_dir=tmp_path)
    with pytest.raises(ValueError, match="run_id"):
        rec.write(_record("other"))
