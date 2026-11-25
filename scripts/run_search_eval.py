"""검색만 하는 진단 실험 (V2 EXP-02 · EXP-03, V3 EXP-04~06). 답변은 만들지 않는다.

--modes: vector(A) · bm25(진단) · hybrid(B) · hybrid_rerank(C). 기본 vector (V2 와 같음).
hybrid_rerank 는 Cross-Encoder 가 필요해 sentence-transformers 를 겹쳐 설치해 실행한다 (retrieval.refine).

uv run python scripts/run_search_eval.py --results-version v2 --exp exp02 --recipes CK04 \
    --variants raw,alias,alias_plus,rewrite,manual_en
uv run python scripts/run_search_eval.py --results-version v2 --exp exp03 \
    --recipes CK01,CK02,CK03,CK04,CK05,CK06 --variants alias_plus,rewrite

리라이트는 results/{버전}/rewrites.jsonl 에 문항마다 한 번 만들어 두고(실제 LLM 호출), 있으면 다시 쓴다.
결과: results/{버전}/{exp}/{run_id}/ — search.jsonl(문항별 원본) · summary.json · compare.csv · per_question.csv
"""

import argparse
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from etl.chunking import RECIPES  # noqa: E402
from etl.dataset import (  # noqa: E402
    DATASET_DIR,
    PROJECT_ROOT,
    SCOPE_VERSION,
    build_chunks,
    collection_for,
    dataset_fingerprint,
    index_version,
    load_dataset,
)
from etl.load import load_corpus, meta_values  # noqa: E402
from evals.items import load_eval_items, map_citations, read_question_set  # noqa: E402
from evals.provenance import results_dir, run_info  # noqa: E402
from evals.search_eval import (  # noqa: E402
    STAGE_MODES,
    aggregate,
    aggregate_v6,
    chunk_positions,
    context_redundancy,
    gold_movement,
    gold_scores,
    http_calls,
    run_stages,
    score_question,
    search_vector,
    summarize_calls,
)
from retrieval.normalize import load_aliases  # noqa: E402
from retrieval.query import VARIANTS, build_plan, make_rewrite, read_jsonl_map  # noqa: E402
from retrieval.search import BM25Index  # noqa: E402
from shared.core.config import settings  # noqa: E402
from shared.core.vector_store import (  # noqa: E402
    get_embeddings,
    get_raw_db_connection,
    get_vector_store,
)

DEV_ITEMS = "Q015,Q001,Q007,Q003,Q052,Q044,Q047,Q049,Q019"
MANUAL_EN = PROJECT_ROOT / "experiments" / "v2" / "manual_en.jsonl"
POOL, TOP_K = 20, 5


def ensure_rewrites(path: Path, questions: dict[str, str], aliases: list[Any]) -> None:
    """없는 문항만 리라이트를 만들어 파일에 덧붙인다 (있는 줄은 고치지 않는다)."""
    done = read_jsonl_map(path, "question_id") if path.exists() else {}
    todo = [qid for qid in questions if qid not in done]
    if not todo:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for qid in todo:
            with http_calls() as calls:
                row = make_rewrite(questions[qid], aliases)
            row.update(
                {
                    "question_id": qid,
                    "api_calls": summarize_calls(calls),
                    "llm_model": settings.LLM_MODEL,
                    "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                }
            )
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"rewrite {qid}: failed={row['rewrite_failed']} → {row['query_en']!r}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-version", required=True)
    parser.add_argument("--exp", required=True, help="실험 폴더 이름 (예: exp02)")
    parser.add_argument("--recipes", required=True, help="쉼표 구분 (예: CK04)")
    parser.add_argument("--variants", required=True, help=f"쉼표 구분: {','.join(VARIANTS)}")
    parser.add_argument("--items", default=DEV_ITEMS)
    parser.add_argument("--run-id", help="생략하면 {날짜-시각}")
    parser.add_argument("--modes", default="vector", help=f"쉼표 구분: {','.join(STAGE_MODES)}")
    parser.add_argument("--enriched", action="store_true", help="D 가공 색인 컬렉션을 검색한다")
    args = parser.parse_args()
    modes = args.modes.split(",")
    if set(modes) - set(STAGE_MODES):
        raise SystemExit(f"알 수 없는 검색 방식: {sorted(set(modes) - set(STAGE_MODES))}")

    recipes = [RECIPES[r] for r in args.recipes.split(",")]
    variants = args.variants.split(",")
    unknown = set(variants) - set(VARIANTS)
    if unknown:
        raise SystemExit(f"알 수 없는 질의 방식: {sorted(unknown)}")
    item_ids = args.items.split(",")
    root = results_dir(args.results_version)
    run_id = args.run_id or f"{datetime.now():%Y%m%d-%H%M%S}"
    out = root / args.exp / run_id
    out.mkdir(parents=True, exist_ok=False)  # 같은 run_id 가 있으면 실패 (덮어쓰기 방지)

    info = run_info(args.results_version)
    fingerprint = dataset_fingerprint()
    data = load_dataset()
    aliases = load_aliases(DATASET_DIR / "aliases.jsonl")
    raw_by_id = {raw["query_id"]: raw for raw in read_question_set()}
    questions = {qid: raw_by_id[qid]["question"] for qid in item_ids}
    manual = {qid: r["query_en"] for qid, r in read_jsonl_map(MANUAL_EN, "question_id").items()}
    rewrites_path = root / "rewrites.jsonl"
    if "rewrite" in variants:
        ensure_rewrites(rewrites_path, questions, aliases)
    rewrites = read_jsonl_map(rewrites_path, "question_id") if rewrites_path.exists() else None

    plans = {
        (v, qid): build_plan(v, qid, questions[qid], aliases, manual, rewrites)
        for v in variants
        for qid in item_ids
    }
    embeddings = get_embeddings()
    vectors: dict[str, list[float]] = {}
    embed_meta: dict[str, dict[str, Any]] = {}
    for plan in plans.values():
        if plan.query_used in vectors:
            continue
        with http_calls() as calls:
            started = time.perf_counter()
            vectors[plan.query_used] = embeddings.embed_query(plan.query_used)
            ms = round((time.perf_counter() - started) * 1000)
        embed_meta[plan.query_used] = {"embed_ms": ms, "api_calls": summarize_calls(calls)}

    reranker: Any = None
    reranker_meta: dict[str, Any] = {}
    if "hybrid_rerank" in modes:
        from retrieval.refine import CrossEncoderReranker

        started = time.perf_counter()
        reranker = CrossEncoderReranker()
        reranker_meta = {
            "name": reranker.name,
            "device": reranker.device,
            "dtype": reranker.dtype,
            "max_length": reranker.max_length,
            "load_s": round(time.perf_counter() - started, 2),
        }
    needs_stages = any(m != "vector" for m in modes)

    conn = get_raw_db_connection()
    rows: list[dict[str, Any]] = []
    compare: list[dict[str, Any]] = []
    try:
        with (out / "search.jsonl").open("x", encoding="utf-8") as raw_out:
            for recipe in recipes:
                version = index_version(recipe, fingerprint)
                collection = collection_for(settings.MOVIE_COLLECTION, recipe, fingerprint)
                chunks = build_chunks(data, recipe)
                if args.enriched:
                    from etl.enrich import enriched_index_version

                    version = enriched_index_version(version)
                    collection = collection_for(settings.ENRICHED_COLLECTION, recipe, fingerprint)
                stored = meta_values(conn, collection, "index_version")
                if stored != {version}:
                    raise SystemExit(f"{collection} 의 색인 버전 {stored} ≠ {version}")
                items = {
                    i.question_id: i
                    for i in load_eval_items(chunks, data.sections, version)
                    if i.question_id in questions
                }
                store = get_vector_store(collection)
                positions = chunk_positions(chunks, data.sections)
                texts: dict[str, str] = {}
                bm25: Any = None
                if needs_stages:
                    texts = {cid: text for cid, (text, _) in load_corpus(conn, collection).items()}
                    bm25 = BM25Index(texts.items())
                for variant in variants:
                    per_mode: dict[str, list[dict[str, Any]]] = {m: [] for m in modes}
                    for qid in item_ids:
                        plan = plans[(variant, qid)]
                        item = items[qid]
                        vector = vectors[plan.query_used]
                        started = time.perf_counter()
                        hits = search_vector(store, vector, POOL)
                        search_ms = round((time.perf_counter() - started) * 1000)
                        gold = gold_scores(conn, collection, vector, item.gold_chunk_ids)
                        citations = map_citations(raw_by_id[qid], chunks, data.sections)
                        stages = (
                            run_stages(plan.query_used, hits, bm25, texts, POOL, reranker)
                            if needs_stages
                            else {}
                        )
                        similarity = dict(hits)
                        for mode in modes:
                            if mode == "vector":
                                ranked = [cid for cid, _ in hits]
                            else:
                                ranked = [c["chunk_id"] for c in stages[mode]]
                            missing = [cid for cid in ranked if cid not in similarity]
                            for cid, found in gold_scores(
                                conn, collection, vector, missing
                            ).items():
                                similarity[cid] = float(found["similarity"])
                            final = [(cid, similarity[cid]) for cid in ranked]
                            scored = score_question(item, final, gold, citations, TOP_K)
                            scored["top_redundancy"] = context_redundancy(ranked[:TOP_K], positions)
                            row = {
                                "exp": args.exp,
                                "run_id": run_id,
                                "recipe": recipe.recipe_id,
                                "index_version": version,
                                "collection": collection,
                                "variant": variant,
                                "mode": mode,
                                "question_id": qid,
                                "query": plan.as_dict(),
                                "embed": embed_meta[plan.query_used],
                                "search_ms": search_ms,
                                "gold_chunk_ids": item.gold_chunk_ids,
                                # 최종 순위 · 그 청크의 벡터 코사인 유사도 (방식별 점수는 stages 에)
                                "candidates": [
                                    {"rank": i, "chunk_id": cid, "similarity": round(score, 6)}
                                    for i, (cid, score) in enumerate(final, start=1)
                                ],
                                **scored,
                            }
                            if needs_stages:
                                row["stages"] = stages
                                row["gold_movement"] = gold_movement(stages, item.gold_chunk_ids)
                            raw_out.write(json.dumps(row, ensure_ascii=False) + "\n")
                            rows.append(row)
                            per_mode[mode].append(scored)
                    for mode in modes:
                        summary = aggregate(per_mode[mode], TOP_K) | aggregate_v6(per_mode[mode])
                        compare.append(
                            {
                                "recipe": recipe.recipe_id,
                                "variant": variant,
                                "mode": mode,
                                "chunks": len(chunks),
                            }
                            | {k: v for k, v in summary.items() if not isinstance(v, (dict, list))}
                            | {"outcomes": json.dumps(summary["outcomes"], ensure_ascii=False)}
                            | {
                                "evidence_recall_by_section": json.dumps(
                                    summary["evidence_recall_by_section"], ensure_ascii=False
                                )
                            }
                        )
                        print(f"{recipe.recipe_id} {variant} {mode}: {compare[-1]}")
    finally:
        conn.close()

    with (out / "summary.json").open("x", encoding="utf-8") as f:
        json.dump(
            {
                "exp": args.exp,
                "run_id": run_id,
                "modes": modes,
                "index": "D (LLM 가공 색인)" if args.enriched else "원문",
                "rrf_k": 60,
                "reranker": reranker_meta or None,
                "pool": POOL,
                "top_k": TOP_K,
                "score": "cosine similarity = 1 - cosine distance (PGVector <=>)",
                "items": item_ids,
                "recipes": {r.recipe_id: r.version for r in recipes},
                "variants": variants,
                "scope_version": SCOPE_VERSION,
                "dataset": fingerprint,
                "run_info": info,
                "embedding_requests": sum(m["api_calls"]["requests"] for m in embed_meta.values()),
                "compare": compare,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    _write_csv(out / "compare.csv", compare)
    _write_csv(
        out / "per_question.csv",
        [
            {
                "recipe": r["recipe"],
                "variant": r["variant"],
                "mode": r["mode"],
                "question_id": r["question_id"],
                "answerable": r["answerable"],
                "query_used": r["query"]["query_used"],
                "outcome": r.get("outcome", "근거 없음 문항"),
                "first_gold_rank_in_pool": r.get("first_gold_rank_in_pool"),
                "first_gold_rank_in_collection": r.get("first_gold_rank_in_collection"),
                "best_gold_similarity": r.get("best_gold_similarity"),
                "top1_similarity": r["top1_similarity"],
                "evidence_recall": (r.get("evidence") or {}).get("mean_recall"),
                "top5": " ".join(c["chunk_id"] for c in r["candidates"][:TOP_K]),
            }
            for r in rows
        ],
    )
    print(f"→ {out.relative_to(PROJECT_ROOT)}")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("x", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
