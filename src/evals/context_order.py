"""V7 (EXP-10) 시도 계획: 어떤 문항을 어떤 배치로 몇 번 돌릴지 (설정 · DB 를 부르지 않는다)."""

import random
from typing import Any

from generation.reorder import STRATEGIES, order_for, place


def plan_trials(
    exp: str, contexts: list[dict[str, Any]], answerable: dict[str, bool], reps: int, seed: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(실행할 시도 목록, exp10b 에서 뺀 문항과 이유)."""
    trials: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for ctx in contexts:
        n = len(ctx["chunk_ids"])
        if exp == "exp10a":
            for strategy in STRATEGIES:
                for rep in range(1, reps + 1):
                    trials.append(
                        {
                            "question_id": ctx["question_id"],
                            "condition": strategy,
                            "rep": rep,
                            "ranks": order_for(strategy, n),
                            "short_context": n < 5,
                        }
                    )
            continue
        gold = [
            i + 1 for i, cid in enumerate(ctx["chunk_ids"]) if cid in set(ctx["gold_chunk_ids"])
        ]
        if not answerable[ctx["question_id"]] or not gold:
            reason = (
                "근거 없음 문항"
                if not answerable[ctx["question_id"]]
                else "상위 5 에 정답 청크 없음"
            )
            excluded.append({"question_id": ctx["question_id"], "reason": reason})
            continue
        for position in ("first", "middle", "last"):
            for rep in range(1, reps + 1):
                trials.append(
                    {
                        "question_id": ctx["question_id"],
                        "condition": f"gold_{position}",
                        "rep": rep,
                        "ranks": place(n, gold[0], position),
                        "gold_rank": gold[0],
                        "gold_in_top5": len(gold),
                        "isolation": "single_gold" if len(gold) == 1 else "multi_gold",
                        "short_context": n < 5,
                    }
                )
    random.Random(seed).shuffle(trials)
    anon = random.Random(seed + 1)
    for i, trial in enumerate(trials, start=1):
        trial["order"] = i
        trial["anon_id"] = f"T{anon.getrandbits(32):08x}"
    return trials, excluded
