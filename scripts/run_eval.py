"""골든셋으로 A(vector) · B(hybrid) 구성을 평가하고 results/ 에 기록한다 (실제 API 호출, 비용 발생).

uv run python scripts/run_eval.py --recipe CK03 --mode vector --limit 3   # 3문항 시험 (-try)
uv run python scripts/run_eval.py --recipe CK03 --mode vector --items Q001,Q003,Q019
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from answer import PROMPT_VERSION  # noqa: E402
from build.handoff import (  # noqa: E402
    HANDOFF_DIR,
    RECIPES,
    collection_for,
    dataset_fingerprint,
    index_version,
    load_handoff,
    read_jsonl,
    section_texts,
)
from evals.items import check_ready, load_eval_items  # noqa: E402
from evals.recorder import Recorder  # noqa: E402
from evals.run import RunSetup, run_eval  # noqa: E402
from evals.score import summarize  # noqa: E402
from retrieve.normalize import AliasNormalizer, load_aliases  # noqa: E402
from retrieve.pipeline import Retriever, SearchConfig  # noqa: E402
from shared.core.config import settings  # noqa: E402
from shared.core.schemas import RetrievalMode  # noqa: E402
from shared.core.vector_store import get_llm, get_raw_db_connection, get_vector_store  # noqa: E402
from store.indexer import load_corpus  # noqa: E402

MODES = {"vector": RetrievalMode.VECTOR, "hybrid": RetrievalMode.HYBRID}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recipe", choices=RECIPES, required=True)
    parser.add_argument("--mode", choices=MODES, required=True)
    parser.add_argument("--items", help="쉼표로 구분한 문항 ID (예: Q001,Q003). 생략하면 전체")
    parser.add_argument("--run-id", help="생략하면 {날짜-시각}-{mode}-try (git 에 안 올라감)")
    parser.add_argument("--limit", type=int, help="앞에서부터 N문항만 (비용 확인용)")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--pool", type=int, default=20)
    args = parser.parse_args()
    mode = MODES[args.mode]
    recipe = RECIPES[args.recipe]
    fingerprint = dataset_fingerprint()
    version = index_version(recipe, fingerprint)

    collection = collection_for(settings.MOVIE_COLLECTION, recipe)
    conn = get_raw_db_connection()
    try:
        corpus = load_corpus(conn, collection)
    finally:
        conn.close()
    versions = {meta.get("index_version") for _, meta in corpus.values()}
    models = {meta.get("embedding_model") for _, meta in corpus.values()}
    if models != {settings.EMBEDDING_MODEL}:
        raise SystemExit(
            f"컬렉션 {collection} 의 임베딩 모델 {models} 이 지금 설정({settings.EMBEDDING_MODEL})과 다릅니다."
        )
    if versions != {version}:
        raise SystemExit(
            f"컬렉션 {collection} 의 색인 버전 {versions} 이 코드({version})와 다릅니다. "
            f"scripts/index_handoff.py --recipe {recipe.recipe_id} 를 먼저 돌리세요."
        )

    _, _, chunks = load_handoff(recipe=recipe)
    sections = section_texts(read_jsonl(HANDOFF_DIR / "documents.jsonl"))
    items = load_eval_items(chunks, sections, version)
    if args.items:
        wanted = args.items.split(",")
        unknown = set(wanted) - {i.question_id for i in items}
        if unknown:
            raise SystemExit(f"평가셋에 없는 문항 ID: {sorted(unknown)}")
        items = [i for i in items if i.question_id in wanted]
    items = items[: args.limit]
    # Recorder 가 results/{run_id}.jsonl 을 만들기 전에 확인한다 (실패 시 빈 파일이 남지 않게)
    check_ready(items, version)
    run_id = args.run_id or (
        f"{datetime.now():%Y-%m-%d-%H%M}-{recipe.recipe_id.lower()}-{args.mode}-try"
    )
    config = SearchConfig(top_k=args.top_k, pool=args.pool)

    records = run_eval(
        items=items,
        mode=mode,
        recorder=Recorder(run_id),
        retriever=Retriever(
            get_vector_store(collection), {cid: text for cid, (text, _) in corpus.items()}
        ),
        normalizer=AliasNormalizer(load_aliases(HANDOFF_DIR / "aliases.jsonl")),
        llm=get_llm(),
        setup=RunSetup(
            index_version=version,
            embedding_model=settings.EMBEDDING_MODEL,
            llm_model=settings.LLM_MODEL,
            prompt_version=PROMPT_VERSION,
            config=config,
            extra_params={
                "recipe": recipe.recipe_id,
                "chunking_version": recipe.version,
                "collection": collection,
                "dataset": fingerprint,
                "eval_item_ids": [i.question_id for i in items],
            },
        ),
    )
    print(json.dumps(summarize(items, records, args.top_k), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
