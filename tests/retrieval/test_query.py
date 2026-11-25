import pytest

from retrieval.query import build_plan
from shared.core.models import Alias, AliasKind

ALIASES = [Alias(alias_ko="엔드게임", canonical_en="Avengers: Endgame", kind=AliasKind.WORK)]
QUESTION = "아이언맨은 엔드게임에서 어떻게 돼?"
REWRITES = {
    "Q1": {
        "query_en": "What happens to Iron Man in Avengers: Endgame?",
        "query_normalized": "정규화 질의",
        "rewrite_failed": False,
        "matched_aliases": [],
        "latency_ms": {"total": 10},
        "tokens": {"input": 5},
    },
    "Q2": {
        "query_en": "",
        "query_normalized": "정규화 질의",
        "rewrite_failed": True,
        "matched_aliases": [],
    },
}


def test_raw는_원문_그대로() -> None:
    plan = build_plan("raw", "Q1", QUESTION, ALIASES)
    assert plan.query_used == QUESTION and plan.matched_aliases == []


def test_alias는_V1_사전_치환과_같다() -> None:
    plan = build_plan("alias", "Q1", QUESTION, ALIASES)
    assert plan.query_used == "아이언맨은 엔드게임 (Avengers: Endgame)에서 어떻게 돼?"
    assert plan.matched_aliases == [{"alias_ko": "엔드게임", "canonical_en": "Avengers: Endgame"}]


def test_alias_plus는_명석님_정규화로_인물명까지_끝에_붙인다() -> None:
    plan = build_plan("alias_plus", "Q1", QUESTION, ALIASES)
    # 정규화 방식이 다르다: 이름 뒤 괄호가 아니라 질문 끝에 영어 이름을 덧붙인다
    assert plan.query_used == f"{QUESTION} Iron Man Avengers: Endgame"
    assert {m["canonical_en"] for m in plan.matched_aliases} == {"Iron Man", "Avengers: Endgame"}


def test_rewrite는_미리_만든_값을_쓰고_실패하면_정규화_질의로_검색한다() -> None:
    ok = build_plan("rewrite", "Q1", QUESTION, ALIASES, rewrites=REWRITES)
    assert ok.query_used == ok.query_en == "What happens to Iron Man in Avengers: Endgame?"
    assert ok.rewrite_meta == {"latency_ms": {"total": 10}, "tokens": {"input": 5}}

    failed = build_plan("rewrite", "Q2", QUESTION, ALIASES, rewrites=REWRITES)
    assert failed.rewrite_failed and failed.query_used == "정규화 질의"

    with pytest.raises(ValueError, match="리라이트가 없습니다"):
        build_plan("rewrite", "Q3", QUESTION, ALIASES, rewrites=REWRITES)


def test_manual_en과_알수없는_방식() -> None:
    plan = build_plan("manual_en", "Q1", QUESTION, ALIASES, manual_en={"Q1": "What?"})
    assert plan.query_used == plan.query_en == "What?"
    with pytest.raises(ValueError):
        build_plan("manual_en", "Q9", QUESTION, ALIASES, manual_en={"Q1": "What?"})
    with pytest.raises(ValueError, match="알 수 없는"):
        build_plan("nope", "Q1", QUESTION, ALIASES)
