import argparse
import asyncio
import json
from pathlib import Path

from evals.items import read_rows
from tools.translation import load_aliases, rewrite_movie_query


async def main() -> None:
    parser = argparse.ArgumentParser(description="영문 검색어 파일")
    parser.add_argument("--questions", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out)
    if out.exists():
        raise SystemExit(f"이미 있습니다 (덮어쓰지 않는다): {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    aliases = load_aliases()
    with out.open("x", encoding="utf-8") as f:
        for row in read_rows(args.questions):
            qid = row.get("query_id") or row["question_id"]
            result = await rewrite_movie_query(row["question"], aliases)
            f.write(json.dumps({"question_id": qid, **result}, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
