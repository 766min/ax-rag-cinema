"""API 호출 없이 레시피를 견준다 — 청크 수 · 길이 분포 · 색인 중복률 · 정답 발췌가 한 청크에 보존되는지 · 머리말.

    PYTHONPATH=src uv run --locked python scripts/chunk_stats.py --recipes s800,s800-nohdr,s800-o1,ck05,ck09

색인 중복률 = 겹침으로 두 번 들어간 글자 / 본문 청크 글자 합. 정답 발췌는 평가셋의 '원문 근거 발췌' 를
영문자 · 숫자만 남겨 비교한다 (발췌 자체가 '…' 로 잘린 판이라 '한 청크 보존' 은 잘린 발췌 기준이다).
"""

import argparse
import statistics

from build.cards import build_index_chunks
from build.chunks import RECIPES, SCOPES, Dataset, index_version
from evals.items import read_rows, to_items


def main() -> None:
    parser = argparse.ArgumentParser(description="레시피 비교 (API 없음)")
    parser.add_argument("--recipes", default="s800,s800-nohdr,s800-o1,ck05,ck09")
    parser.add_argument("--scope", default="works+characters+lore", choices=SCOPES)
    parser.add_argument("--questions", default="question_set.jsonl")
    parser.add_argument("--dataset", default=None)
    args = parser.parse_args()

    ds = Dataset.load(args.dataset)
    rows = read_rows(args.questions)
    print("| 레시피 | 행(본문+카드) | 본문 청크 | 길이 중앙 · p90 · 최대 | 최대 초과 | 색인 중복률 | "
          "정답 근거 → 청크 | 발췌가 한 청크에 | 정답 키 수 | 머리말 있는 본문 | index_version |")  # fmt: skip
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for name in args.recipes.split(","):
        recipe = RECIPES[name]
        chunks = build_index_chunks(ds, recipe, args.scope)
        body = [c for c in chunks if str(c.section) not in ("card", "connection")]
        lengths = sorted(len(c.text) for c in body)
        overlap = sum(int(c.source_ids.get("overlap_chars") or 0) for c in body)
        _, report = to_items(rows, chunks)
        ev = [e for r in report for e in r["evidence"]]
        gold = sum(len(r["gold"]) for r in report)
        print(
            f"| {name} | {len(chunks)} | {len(body)} | {int(statistics.median(lengths))} · "
            f"{lengths[int(len(lengths) * 0.9)]} · {lengths[-1]} | "
            f"{sum(n > recipe.max_chars for n in lengths)} | {overlap / sum(lengths):.1%} | "
            f"{sum(bool(e['chunk_ids']) for e in ev)}/{len(ev)} | "
            f"{sum(e['whole_excerpt_in_one_chunk'] for e in ev)}/{len(ev)} | {gold} | "
            f"{sum(bool(c.header) for c in body)}/{len(body)} | `{index_version(chunks)}` |"
        )


if __name__ == "__main__":
    main()
