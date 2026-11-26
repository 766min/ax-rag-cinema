# ══════════════════════════════════════════════════════════════════════════════
# [학습 안내] tests/evals/test_judge.py — 채점기 테스트
#
# gather/tests/evals/test_gather_evals.py 에서 judge 관련 테스트를 분리한 파일이다.
# ══════════════════════════════════════════════════════════════════════════════
import json
from types import SimpleNamespace
from typing import Any

import pytest

from evals.judge import Judge, calibrate, parse_verdict, score
from shared.core.models import EvalItem


def test_verdict_must_cover_every_key_fact() -> None:
    good = 'ok {"facts": [{"n": 1, "verdict": "met"}, {"n": 2, "verdict": "Partial"}], "contradiction": false}'
    assert parse_verdict(good, 2)["verdicts"] == ["met", "partial"]

    for bad, n in (
        ('{"facts": [{"n": 1, "verdict": "met"}]}', 2),
        ('{"facts": [{"verdict": "yes"}]}', 1),
        ("no json", 2),
    ):
        with pytest.raises(ValueError):
            parse_verdict(bad, n)


def test_correct_means_every_fact_met_and_no_contradiction() -> None:
    assert score(["met", "met"], False) == {
        "passed": True,
        "fact_rate": 1.0,
    }
    assert score(["met", "partial"], False) == {
        "passed": False,
        "fact_rate": 0.75,
    }
    assert score(["met", "met"], True)["passed"] is False


class ScriptedLLM:
    """기준 답안이면 met, 아니면 missing 을 돌려주는 가짜 채점 모델"""

    def invoke(self, messages: list[Any]) -> Any:
        text = messages[1].content
        reference = text.split("Reference answer: ")[1].split("\n\n")[0]
        answer = text.split("Answer to grade:\n")[1]
        n = text.count("\n", text.index("Key facts:"), text.index("Answer to grade")) - 2
        verdict = "met" if answer.strip() == reference.strip() else "missing"
        return SimpleNamespace(
            content=json.dumps(
                {
                    "facts": [
                        {"n": i, "verdict": verdict}
                        for i in range(1, n + 1)
                    ],
                    "contradiction": False,
                }
            )
        )


def test_calibrate_with_fake_model() -> None:
    items = [
        EvalItem(
            question_id="Q001",
            question="아이언맨은 누구인가?",
            answerable=True,
            expected_answer="Tony Stark",
            key_facts=["Tony Stark"],
        ),
        EvalItem(
            question_id="Q002",
            question="헐크는 누구인가?",
            answerable=True,
            expected_answer="Bruce Banner",
            key_facts=["Bruce Banner"],
        ),
    ]

    judge = Judge(ScriptedLLM())
    result = calibrate(items, judge)

    assert result["reference_passed"] == "2/2"
    assert result["mismatched_passed"] == "0/2"
