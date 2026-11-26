# 색인을 만들어 DB 에 넣는 실행 명령_영민

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path
from build.cards import build_index_chunks
from build.chunks import RECIPES, SCOPES, Dataset, dataset_fingerprint, index_version, write_jsonl


def main() -> None:
    parser = argparse.ArgumentParser(description="색인 만들기 · 적재")
    parser.add_argument("--recipe", default="s800", choices=sorted(RECIPES))
    parser.add_argument("--scope", default="works+characters+lore", choices=SCOPES)
    parser.add_argument(
        "--dataset",
        default=None,
        help="데이터셋 폴더 (기본: CINEVERSE_DATASET_DIR 또는 data/cineverse-dataset-schema)",
    )
    parser.add_argument("--collection", default=None, help="기본: MOVIE_COLLECTION")
    parser.add_argument(
        "--out", default="data/indexes", help="청크 파일 · 적재 결과를 둘 폴더 (git 제외 경로)"
    )
    parser.add_argument("--no-db", action="store_true", help="청크만 만들고 DB 에 붙지 않는다")
    parser.add_argument("--dry-run", action="store_true", help="적재 계획만 (임베딩 없음)")
    parser.add_argument(
        "--allow-changes", action="store_true", help="기존 행을 고치거나 지우는 적재 허용"
    )
    args = parser.parse_args()  # fmt: skip

    # 1)·2) 레시피 고르기 → 데이터셋 읽기 → 청크 만들기
    recipe = RECIPES[args.recipe]
    ds = Dataset.load(args.dataset)
    chunks = build_index_chunks(ds, recipe, args.scope)
    body = [c for c in chunks if str(c.section) not in ("card", "connection")]
    lengths = [len(c.text) for c in body]
    # 요약: 레시피 · 범위 · 데이터 판 · 색인 버전 · 행 수 · 문서 수 · 길이
    report: dict = {
        "recipe": recipe.params(),
        "scope": args.scope,
        "dataset": dataset_fingerprint(args.dataset),
        "index_version": index_version(chunks),
        "rows": len(chunks),
        "rows_by_section": dict(Counter(str(c.section) for c in chunks)),
        "documents": len({c.doc_id for c in body}),
        "text_chars": {"median": int(statistics.median(lengths)), "max": max(lengths)},
    }
    # 3) 청크 파일 남기기
    out = Path(args.out) / args.recipe
    write_jsonl(out / "chunks.jsonl", chunks)
    # 4) 적재 (DB 설정은 이때만 읽는다 → --no-db 는 .env 없이도 돈다)
    if not args.no_db:
        from shared.core.config import settings
        from store.db import load

        collection = args.collection or settings.MOVIE_COLLECTION
        report["load"] = load(
            chunks, collection, allow_changes=args.allow_changes, dry_run=args.dry_run
        )
    # 5) 결과 남기기 (계획만 본 경우는 plan.json)
    name = "plan.json" if args.dry_run or args.no_db else "manifest.json"
    (out / name).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
