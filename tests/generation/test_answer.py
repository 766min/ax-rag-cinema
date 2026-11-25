from fakes import FakeLLM

from generation.answer import (
    NO_EVIDENCE_MESSAGE,
    CitedAnswer,
    format_context,
    generate_answer,
    resolve,
)
from shared.core.schemas import AnswerStatus

IDS = ["a", "b", "c"]


def test_문맥에_번호를_붙인다() -> None:
    assert format_context([("a", "first"), ("b", "second")]) == "[1] first\n\n---\n\n[2] second"


def test_인용_번호를_chunk_id로_바꾸고_범위_밖은_버린다() -> None:
    result = resolve(CitedAnswer(answer="답", cited_numbers=[2, 9, 2, 1]), IDS)
    assert (result.status, result.cited_chunk_ids) == (AnswerStatus.ANSWERED, ["b", "a"])


def test_남은_인용이_없으면_근거_없음() -> None:
    result = resolve(CitedAnswer(answer="지어낸 답", cited_numbers=[7]), IDS)
    assert (result.status, result.answer) == (AnswerStatus.NO_EVIDENCE, NO_EVIDENCE_MESSAGE)


def test_문맥이_없으면_모델을_부르지_않는다() -> None:
    llm = FakeLLM()
    result = generate_answer("q", [], llm)  # type: ignore[arg-type]
    assert result.status == AnswerStatus.NO_EVIDENCE
    assert llm.calls == 0


def test_모델_호출은_한_번_재시도한다() -> None:
    llm = FakeLLM(fail=True)
    try:
        generate_answer("q", [("a", "text")], llm)  # type: ignore[arg-type]
    except TimeoutError:
        pass
    assert llm.calls == 2  # with_retry(stop_after_attempt=2)
