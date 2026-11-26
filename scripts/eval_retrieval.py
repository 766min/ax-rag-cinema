"""검색 평가 (dry-run) — 질문 파일의 정답 청크 ID 와 Top-k 검색 결과를 RAGAS ID 기반 지표로 비교한다.

튜터 가이드 3·4단계: Top-k 결과 = 검색된 컨텍스트, gold = 정답 컨텍스트 → RAGAS → 임계치 미달 목록.

    $env:PYTHONPATH = "src"
    uv run python scripts/eval_retrieval.py --questions tmp/questions_labeled.jsonl
    uv run python scripts/eval_retrieval.py --questions tmp/questions_labeled.jsonl --lang ko --k 10

--lang
    en        question_en 그대로
    ko        question_ko 그대로 (번역 없음, 비교용)
    ko_alias  tools.translation.normalize_query — 한국어 + 별칭 사전의 영어 작품명 덧붙임 (LLM 없음)
    ko_tr     tools.translation.rewrite_movie_query — 서비스 경로와 같다.
              query_en 이 있으면 그것, 번역 실패면 query_normalized 로 검색 (service/movie_qa.py 와 동일)
              번역 결과는 tmp/translations_<질문파일>.json 에 캐시한다.

질문 파일 칸 (한 줄에 하나):
    qid, question_en, question_ko, category
    gold          : 그 청크 하나로 답할 수 있는 청크 ID 전부 (hit@k · MRR 기준)
    primary_gold  : (선택) 그중 가장 직접적인 1~3개. 있으면 Recall 은 이것으로 잰다
    gold 가 빈 질문은 '근거 없음' 문항으로 보고 검색 지표에서 뺀다

임베딩은 search_dryrun.py 가 만든 tmp/emb_dryrun.npy 를 재사용한다 (청크 수가 다르면 다시 계산).
"""

import argparse
import json
import re
import warnings
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

warnings.filterwarnings("ignore", category=DeprecationWarning)
from ragas import SingleTurnSample  # noqa: E402
from ragas.metrics import IDBasedContextPrecision, IDBasedContextRecall  # noqa: E402

from shared.core.vector_store import get_embeddings  # noqa: E402

CHUNKS = Path("tmp/chunks_dryrun.jsonl")
CACHE = Path("tmp/emb_dryrun.npy")
CONFIGS = ("A_vector", "BM25", "B_hybrid")


def tokenize(s: str) -> list[str]:
    return re.findall(r"[a-z0-9가-힣']+", s.lower())


def translate_all(qs: list[dict], cache_path: Path) -> dict[str, dict]:
    """서비스와 같은 rewrite_movie_query 로 question_ko 를 번역한다. 질문 문장이 같으면 캐시를 쓴다."""
    import asyncio

    from tools.translation import load_aliases, rewrite_movie_query

    cache: dict[str, dict] = {}
    if cache_path.exists():
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    aliases = load_aliases()
    todo = [q for q in qs if cache.get(q["qid"], {}).get("query_ko") != q["question_ko"]]

    async def run() -> None:
        sem = asyncio.Semaphore(8)

        async def one(q: dict) -> None:
            async with sem:
                cache[q["qid"]] = await rewrite_movie_query(q["question_ko"], aliases)

        await asyncio.gather(*(one(q) for q in todo))

    if todo:
        asyncio.run(run())
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    return cache


def query_texts(qs: list[dict], lang: str, questions_path: str) -> tuple[list[str], list[dict]]:
    if lang in ("en", "ko"):
        return [q[f"question_{lang}"] for q in qs], [{} for _ in qs]
    if lang == "ko_alias":
        from tools.translation import load_aliases, normalize_query

        aliases = load_aliases()
        out = [normalize_query(q["question_ko"], aliases) for q in qs]
        return [t for t, _ in out], [{"matched_aliases": m} for _, m in out]
    tr = translate_all(qs, Path(f"tmp/translations_{Path(questions_path).stem}.json"))
    infos = [tr[q["qid"]] for q in qs]
    return [i["query_en"] or i["query_normalized"] for i in infos], infos


def load_index():
    chunks = [json.loads(line) for line in CHUNKS.open(encoding="utf-8")]
    texts = [(c["header"] + " " + c["text"]).strip() for c in chunks]
    emb = get_embeddings()
    mat = np.load(CACHE) if CACHE.exists() else None
    if mat is None or mat.shape[0] != len(chunks):
        vecs: list[list[float]] = []
        for i in range(0, len(texts), 500):
            vecs.extend(emb.embed_documents(texts[i : i + 500]))
        mat = np.array(vecs, dtype=np.float32)
        np.save(CACHE, mat)
    mat = mat / np.linalg.norm(mat, axis=1, keepdims=True)
    bm25 = BM25Okapi([tokenize(t) for t in texts])
    return [c["chunk_id"] for c in chunks], mat, bm25, emb


def retrieve(q: str, qv: np.ndarray, mat, bm25, n: int = 100) -> tuple[dict, float]:
    sims = mat @ qv
    vec = list(np.argsort(-sims)[:n])
    bm = list(np.argsort(-bm25.get_scores(tokenize(q)))[:n])
    rrf: dict[int, float] = {}
    for order in (vec, bm):
        for r, i in enumerate(order, start=1):
            rrf[i] = rrf.get(i, 0.0) + 1 / (60 + r)
    hyb = sorted(rrf, key=lambda i: -rrf[i])
    return {"A_vector": vec, "BM25": bm, "B_hybrid": hyb}, float(sims[vec[0]])


def score(retrieved: list[str], gold: list[str], primary: list[str], k: int) -> dict:
    top = retrieved[:k]
    sample_p = SingleTurnSample(retrieved_context_ids=top, reference_context_ids=gold)
    sample_r = SingleTurnSample(retrieved_context_ids=top, reference_context_ids=primary)
    first = next((r for r, cid in enumerate(retrieved[:10], start=1) if cid in gold), None)
    return {
        "context_precision": IDBasedContextPrecision().single_turn_score(sample_p),
        "context_recall": IDBasedContextRecall().single_turn_score(sample_r),
        "hit": int(any(cid in gold for cid in top)),
        "rr": 1 / first if first else 0.0,
        "first_rank": first,
    }


def mean(xs: list[float]) -> float:
    return round(sum(xs) / len(xs), 3) if xs else float("nan")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", required=True)
    ap.add_argument("--lang", choices=("en", "ko", "ko_alias", "ko_tr"), default="en")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--min-recall", type=float, default=0.5, help="이 값 미만이면 실패 목록")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    qs = [json.loads(line) for line in open(args.questions, encoding="utf-8")]
    ids, mat, bm25, emb = load_index()
    qtexts, qinfos = query_texts(qs, args.lang, args.questions)
    qvecs = np.array(emb.embed_documents(qtexts), dtype=np.float32)
    qvecs /= np.linalg.norm(qvecs, axis=1, keepdims=True)

    rows = []
    for q, text, qv, info in zip(qs, qtexts, qvecs, qinfos, strict=True):
        orders, top1_sim = retrieve(text, qv, mat, bm25)
        gold = q.get("gold") or []
        primary = q.get("primary_gold") or gold
        row = {
            "qid": q["qid"],
            "category": q.get("category"),
            "question": text,
            "n_gold": len(gold),
            "top1_sim": round(top1_sim, 3),
            **({"rewrite_failed": info["rewrite_failed"]} if "rewrite_failed" in info else {}),
            **({"matched_aliases": info["matched_aliases"]} if "matched_aliases" in info else {}),
        }
        for cfg in CONFIGS:
            retrieved = [ids[i] for i in orders[cfg]]
            row[cfg] = {"top": retrieved[: args.k]}
            if gold:
                row[cfg].update(score(retrieved, gold, primary, args.k))
        rows.append(row)

    answerable = [r for r in rows if r["n_gold"]]
    no_gold = [r for r in rows if not r["n_gold"]]
    summary = {
        cfg: {
            f"context_precision@{args.k}": mean([r[cfg]["context_precision"] for r in answerable]),
            f"context_recall@{args.k}": mean([r[cfg]["context_recall"] for r in answerable]),
            f"hit@{args.k}": mean([r[cfg]["hit"] for r in answerable]),
            "mrr@10": mean([r[cfg]["rr"] for r in answerable]),
        }
        for cfg in CONFIGS
    }
    by_cat: dict[str, dict] = {}
    for cat in sorted({r["category"] for r in answerable}):
        sub = [r for r in answerable if r["category"] == cat]
        by_cat[cat] = {
            "n": len(sub),
            **{
                cfg: {
                    f"hit@{args.k}": mean([r[cfg]["hit"] for r in sub]),
                    "recall": mean([r[cfg]["context_recall"] for r in sub]),
                }
                for cfg in CONFIGS
            },
        }
    failures = [
        {
            "qid": r["qid"],
            "category": r["category"],
            "question": r["question"],
            "n_gold": r["n_gold"],
            **{
                cfg: {
                    "recall": round(r[cfg]["context_recall"], 2),
                    "first_rank": r[cfg]["first_rank"],
                }
                for cfg in CONFIGS
            },
            "B_top3": r["B_hybrid"]["top"][:3],
        }
        for r in answerable
        if r["B_hybrid"]["context_recall"] < args.min_recall
    ]
    sim_ans = [r["top1_sim"] for r in answerable]
    sim_none = [r["top1_sim"] for r in no_gold]
    result = {
        "questions": args.questions,
        "lang": args.lang,
        "k": args.k,
        "n_answerable": len(answerable),
        "n_no_gold": len(no_gold),
        "summary": summary,
        "by_category": by_cat,
        "failures_B": failures,
        "top1_similarity": {
            "answerable_mean": mean(sim_ans),
            "answerable_min": min(sim_ans) if sim_ans else None,
            "no_gold_mean": mean(sim_none),
            "no_gold_max": max(sim_none) if sim_none else None,
        },
        "rows": rows,
    }
    out = Path(args.out or f"tmp/eval_{Path(args.questions).stem}_{args.lang}_k{args.k}.json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {k: result[k] for k in ("summary", "by_category", "top1_similarity")},
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"\n임계치 미달 (B_hybrid recall < {args.min_recall}): {len(failures)}건")
    for f in failures:
        print(
            f"  {f['qid']} [{f['category']}] gold={f['n_gold']} B_rank={f['B_hybrid']['first_rank']}  {f['question'][:70]}"
        )
    print(f"\n→ {out}")


if __name__ == "__main__":
    main()
