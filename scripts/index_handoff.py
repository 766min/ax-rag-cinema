"""handoff 데이터를 레시피대로 청킹해 레시피 전용 컬렉션에 적재한다 (실제 임베딩 API 호출, 비용 발생).

uv run python scripts/index_handoff.py --recipe CK03 --dry-run  # 개수만 확인 (API 호출 없음)
uv run python scripts/index_handoff.py --recipe CK03            # 증분 적재
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from build.handoff import (  # noqa: E402
    RECIPES,
    collection_for,
    dataset_fingerprint,
    index_version,
    load_handoff,
)
from shared.core.config import settings  # noqa: E402
from shared.core.logger import get_logger  # noqa: E402
from shared.core.vector_store import get_raw_db_connection, get_vector_store  # noqa: E402
from store.indexer import fetch_existing, index_chunks, plan  # noqa: E402

logger = get_logger("scripts.index_handoff")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recipe", choices=RECIPES, required=True)
    parser.add_argument("--dry-run", action="store_true", help="적재하지 않고 개수만 본다")
    args = parser.parse_args()
    recipe = RECIPES[args.recipe]

    fingerprint = dataset_fingerprint()
    version = index_version(recipe, fingerprint)
    collection = collection_for(settings.MOVIE_COLLECTION, recipe)
    _, documents, chunks = load_handoff(recipe=recipe)
    logger.info(
        "레시피 %s · 문서 %d · 청크 %d · 색인 %s · 컬렉션 %s",
        recipe.recipe_id,
        len(documents),
        len(chunks),
        version,
        collection,
    )

    conn = get_raw_db_connection()
    try:
        if args.dry_run:
            to_add, to_delete = plan(chunks, fetch_existing(conn, collection), version)
            chars = sum(len(c.header) + len(c.text) for c in to_add)
            logger.info(
                "추가 %d · 삭제 %d · 임베딩할 글자 수 %d", len(to_add), len(to_delete), chars
            )
            return
        print(index_chunks(chunks, get_vector_store(collection), conn, collection, version))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
