import pytest

from evals.context_order import plan_trials
from generation.reorder import STRATEGIES, arrange, order_for, place, token_layout


def test_배치_패턴과_5개_미만_처리() -> None:
    assert order_for("CR03", 5) == [1, 3, 5, 4, 2]
    assert order_for("CR04", 5) == [4, 2, 1, 3, 5]
    assert order_for("CR04", 3) == [2, 1, 3]  # 3보다 큰 순위는 빼고 순서는 지킨다
    assert order_for("CR02", 1) == [1]
    with pytest.raises(ValueError):
        order_for("CR01", 6)
    assert set(STRATEGIES) == {"CR01", "CR02", "CR03", "CR04"}


def test_재배치는_항목을_바꾸지_않고_순서만_바꾼다() -> None:
    items = ["a", "b", "c", "d", "e"]
    assert arrange(items, [5, 4, 3, 2, 1]) == ["e", "d", "c", "b", "a"]
    assert sorted(arrange(items, order_for("CR04", 5))) == items
    with pytest.raises(ValueError):
        arrange(items, [1, 2, 3])


def test_정답_하나만_처음_가운데_끝으로_옮기고_나머지_순서는_지킨다() -> None:
    assert place(5, 3, "first") == [3, 1, 2, 4, 5]
    assert place(5, 3, "middle") == [1, 2, 3, 4, 5]
    assert place(5, 1, "middle") == [2, 3, 1, 4, 5]
    assert place(5, 1, "last") == [2, 3, 4, 5, 1]
    assert place(3, 2, "middle") == [1, 2, 3]


def test_토큰_구간은_이어지고_구분자만큼_띈다() -> None:
    layout = token_layout(["[1] alpha", "[2] beta gamma"], "\n\n---\n\n")
    (s1, e1), (s2, e2) = layout["spans"]
    assert s1 == 0 and e1 < s2 < e2 == layout["total"]


def test_시도_계획은_조건마다_같은_수이고_seed_로_순서가_고정된다() -> None:
    contexts = [
        {"question_id": "Q1", "chunk_ids": ["a", "b", "c", "d", "e"], "gold_chunk_ids": ["c"]},
        {"question_id": "Q2", "chunk_ids": ["a", "b", "c"], "gold_chunk_ids": ["x"]},
        {"question_id": "Q9", "chunk_ids": ["a", "b", "c", "d", "e"], "gold_chunk_ids": []},
    ]
    answerable = {"Q1": True, "Q2": True, "Q9": False}
    a1, _ = plan_trials("exp10a", contexts, answerable, 3, 7)
    a2, _ = plan_trials("exp10a", contexts, answerable, 3, 7)
    assert len(a1) == 3 * 4 * 3
    assert [t["anon_id"] for t in a1] == [t["anon_id"] for t in a2]
    assert any(t["short_context"] for t in a1)  # Q2 는 3개

    b, excluded = plan_trials("exp10b", contexts, answerable, 3, 7)
    assert len(b) == 1 * 3 * 3  # 정답이 상위에 있는 Q1 만
    assert {e["question_id"] for e in excluded} == {"Q2", "Q9"}
    firsts = [t for t in b if t["condition"] == "gold_first"]
    assert all(t["ranks"][0] == 3 for t in firsts)
