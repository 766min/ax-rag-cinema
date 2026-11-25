from pathlib import Path

import pytest
from fakes import FakeLLM, FakeStore

from evals.recorder import Recorder
from evals.run import RunSetup, run_eval
from evals.score import failure_type, summarize
from generation.answer import CitedAnswer
from retrieval.normalize import AliasNormalizer
from retrieval.pipeline import Retriever, SearchConfig
from shared.core.models import EvalItem, RunRecord
from shared.core.schemas import AnswerStatus, RetrievalMode

CORPUS = {"a": "Thanos Infinity Stones", "b": "Wakanda nation", "c": "Iron Man suit"}
SETUP = RunSetup("v2", "emb-model", "llm-model", "p1", SearchConfig(top_k=2, pool=3))


def _items() -> list[EvalItem]:
    return [
        EvalItem(
            question_id="Q1",
            question="타노스?",
            answerable=True,
            gold_chunk_ids=["a"],
            gold_index_version="v2",
        ),
        EvalItem(question_id="Q2", question="없는 이야기?", answerable=False),
    ]


def _run(tmp_path: Path, llm: FakeLLM, ranked: list[tuple[str, float]]) -> list[RunRecord]:
    retriever = Retriever(FakeStore(ranked), CORPUS)  # type: ignore[arg-type]
    return run_eval(
        _items(),
        RetrievalMode.VECTOR,
        Recorder("t-try", results_dir=tmp_path),
        retriever,
        AliasNormalizer([]),
        llm,
        SETUP,
    )  # type: ignore[arg-type]


def test_문항마다_RunRecord_한_줄을_남긴다(tmp_path: Path) -> None:
    records = _run(tmp_path, FakeLLM(), [("a", 0.9), ("b", 0.5)])

    lines = (tmp_path / "t-try.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = RunRecord.model_validate_json(lines[0])
    assert first.trace.index_version == "v2"
    assert first.cited_chunk_ids == ["a"]
    assert {"retrieve", "generate", "total"} <= set(first.latency_ms)
    assert records[0].params == {"top_k": 2, "pool": 3, "reranker": None}


def test_요약과_실패_유형(tmp_path: Path) -> None:
    items = _items()
    records = _run(tmp_path, FakeLLM(), [("b", 0.9), ("c", 0.8), ("a", 0.1)])  # 정답 a 가 3위

    assert failure_type(items[0], records[0], k=2) == "순위 실패"
    assert failure_type(items[1], records[1]) == "근거 없음 대응 실패"  # 근거 없음 문항에 답해 버림
    summary = summarize(items, records, k=2)
    assert (summary["n"], summary["hit@2"], summary["hit@2_n"]) == (2, 0.0, 1)
    assert summary["failures"] == {"순위 실패": 1, "근거 없음 대응 실패": 1}


def test_호출_오류도_Hit_분모에_들어가고_검색과_생성을_나눈다(tmp_path: Path) -> None:
    items = _items()
    failing = Retriever(FakeStore([], fail=True), CORPUS)  # type: ignore[arg-type]
    records = run_eval(
        items,
        RetrievalMode.VECTOR,
        Recorder("e-try", results_dir=tmp_path),
        failing,
        AliasNormalizer([]),
        FakeLLM(),
        SETUP,  # type: ignore[arg-type]
    )
    summary = summarize(items, records, k=2)

    assert (summary["hit@2"], summary["hit@2_n"]) == (0.0, 1)  # 검색 실패도 분모 1, 점수 0
    assert failure_type(items[0], records[0]) == "검색 호출 오류"

    gen_fail = _run(tmp_path, FakeLLM(fail=True), [("a", 0.9)])
    assert failure_type(items[0], gen_fail[0]) == "생성 호출 오류"
    assert summarize(items, gen_fail, k=2)["hit@2"] == 1.0  # 생성만 실패: 검색 결과로 잰다


def test_근거_없음으로_답하면_정상(tmp_path: Path) -> None:
    items = _items()
    records = _run(tmp_path, FakeLLM(CitedAnswer(answer="모름", cited_numbers=[])), [("a", 0.9)])

    assert records[1].status == AnswerStatus.NO_EVIDENCE
    assert failure_type(items[1], records[1]) is None
    assert failure_type(items[0], records[0]) == "생성 오류"  # 정답이 1위인데 근거 없음


def test_정답_키_버전이_다르면_시작하지_않는다(tmp_path: Path) -> None:
    bad = RunSetup("v3", "e", "l", "p1", SearchConfig())
    with pytest.raises(ValueError, match="평가를 시작할 수 없습니다"):
        run_eval(
            _items(),
            RetrievalMode.VECTOR,
            Recorder("x-try", results_dir=tmp_path),
            Retriever(FakeStore([]), CORPUS),
            AliasNormalizer([]),
            FakeLLM(),
            bad,
        )  # type: ignore[arg-type]


def test_질의_계획을_주면_그_글로_검색하고_기록에_남긴다(tmp_path: Path) -> None:
    from retrieval.query import QueryPlan

    store = FakeStore([("a", 0.9), ("b", 0.5)])
    plans = {
        "Q1": QueryPlan("Q1", "rewrite", "타노스?", "Thanos?", query_en="Thanos?"),
        "Q2": QueryPlan("Q2", "rewrite", "없는 이야기?", "fallback", rewrite_failed=True),
    }
    records = run_eval(
        _items(),
        RetrievalMode.VECTOR,
        Recorder("p-try", results_dir=tmp_path),
        Retriever(store, CORPUS),  # type: ignore[arg-type]
        AliasNormalizer([]),
        FakeLLM(),  # type: ignore[arg-type]
        SETUP,
        plans=plans,
    )
    assert store.queries == ["Thanos?", "fallback"]
    assert records[0].trace.query_en == "Thanos?"
    assert records[1].trace.rewrite_failed is True
    assert records[0].params["query_plan"]["variant"] == "rewrite"
