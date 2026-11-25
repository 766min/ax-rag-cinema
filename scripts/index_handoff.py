"""handoff 데이터를 레시피대로 청킹해 레시피 전용 컬렉션에 적재한다 (실제 임베딩 API 호출, 비용 발생).

uv run python scripts/index_handoff.py --recipe CK03 --dry-run  # 범위 · 토큰 추정만 (API 호출 없음)
uv run python scripts/index_handoff.py --recipe CK03            # 증분 적재 + results/index/ 기록
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from etl.handoff import (  # noqa: E402
    PROJECT_ROOT,
    RECIPES,
    collection_for,
    dataset_fingerprint,
    index_version,
    load_handoff,
)
from etl.load import (  # noqa: E402
    count_tokens,
    fetch_existing,
    index_chunks,
    index_text,
    meta_values,
    plan,
)
from shared.core.config import settings  # noqa: E402
from shared.core.logger import get_logger  # noqa: E402
from shared.core.vector_store import get_raw_db_connection, get_vector_store  # noqa: E402

logger = get_logger("scripts.index_handoff")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recipe", choices=RECIPES, required=True)
    parser.add_argument("--dry-run", action="store_true", help="적재하지 않고 범위 · 토큰만 본다")
    args = parser.parse_args()
    recipe = RECIPES[args.recipe]

    fingerprint = dataset_fingerprint()
    version = index_version(recipe, fingerprint)
    collection = collection_for(settings.MOVIE_COLLECTION, recipe)
    # 색인 설정: 청크마다 같이 저장하고, 다른 설정으로 만든 행이 섞여 있으면 적재를 멈춘다
    index_meta = {
        "embedding_model": settings.EMBEDDING_MODEL,
        "embedding_input": "header+text",
        "distance": "cosine",  # shared.core.vector_store.get_vector_store 의 설정
        "recipe": recipe.recipe_id,
    }
    _, documents, chunks = load_handoff(recipe=recipe)

    conn = get_raw_db_connection()
    try:
        existing = fetch_existing(conn, collection)
        models = meta_values(conn, collection, "embedding_model") - {None}
        if models and models != {settings.EMBEDDING_MODEL}:
            raise SystemExit(
                f"{collection} 에 다른 임베딩 모델({models})로 만든 행이 있습니다. "
                "지우지 않고 멈춥니다. 다른 컬렉션 이름을 쓰거나 팀과 정리 방법을 정하세요."
            )
        to_add, to_delete = plan(chunks, existing, version)
        encoding, tokens = count_tokens([index_text(c) for c in to_add], settings.EMBEDDING_MODEL)
        scope = {
            "recipe": recipe.recipe_id,
            "index_version": version,
            "collection": collection,
            "documents": len(documents),
            "chunks": len(chunks),
            "existing_rows": len(existing),
            "to_add": len(to_add),
            "to_delete": len(to_delete),
            "embedding_model": settings.EMBEDDING_MODEL,
            "embedding_tokens_estimate": sum(tokens),
            "tokenizer": encoding,
        }
        print(json.dumps(scope, ensure_ascii=False, indent=2))
        if args.dry_run:
            return

        started = time.perf_counter()
        stats = index_chunks(
            chunks,
            get_vector_store(collection),
            conn,
            collection,
            version,
            index_meta=index_meta,
        )
        seconds = round(time.perf_counter() - started, 2)
    finally:
        conn.close()

    out = PROJECT_ROOT / "results" / "index"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{datetime.now():%Y%m%d-%H%M%S}-{recipe.recipe_id}.json"
    record = {
        **scope,
        **stats,
        "index_seconds": seconds,
        "index_meta": index_meta,
        "dataset": fingerprint,
        "usage_note": "임베딩 토큰은 tiktoken 추정값 (API 보고값 아님)",
    }
    with open(path, "x", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    print(f"→ {path.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
