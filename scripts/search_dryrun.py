"""시험용 검색 (dry-run) — tmp/chunks_dryrun.jsonl 전체를 메모리에 올려 A/B 구성을 비교해 본다.

DB(pgvector) 없이 돈다. 임베딩은 한 번만 계산해 tmp/emb_dryrun.npy 에 캐시한다.
질문·정답 청크는 이 시험을 위해 손으로 만든 것이다 (eval/dev.json 은 아직 비어 있음).

    $env:PYTHONPATH = "src"; uv run python scripts/search_dryrun.py
"""

import json
import re
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from shared.core.vector_store import get_embeddings

CHUNKS = Path("tmp/chunks_dryrun.jsonl")
CACHE = Path("tmp/emb_dryrun.npy")
OUT = Path("tmp/search_dryrun.json")
K = 5

QUESTIONS = [
    (
        "How long did Scott Lang feel he was stuck in the quantum realm?",
        "tt4154796-wikipedia-plot-p02",
    ),
    ("Who did Steve Rogers see when he travelled back to 1970?", "tt4154796-wikipedia-plot-p03"),
    (
        "What did Thor do when the Avengers found Thanos on the empty planet?",
        "tt4154796-wikipedia-plot-p01",
    ),
    (
        "Which Avenger used the new gauntlet to bring back everyone who vanished?",
        "tt4154796-wikipedia-plot-p05",
    ),
    ("Who challenged T'Challa for the throne at his coronation?", "tt1825683-wikipedia-plot-p02"),
    ("Where were Wanda and Vision ambushed by Thanos's followers?", "tt4154756-wikipedia-plot-p04"),
    ("Which actress plays Agatha Harkness?", "wp-78918930-intro-p01"),
    ("What is She-Hulk's job?", "tt10857160-wikipedia-plot-p01"),
    ("Why did He Who Remains create the TVA?", "wp-68200177-plot-p02"),
    ("Who took Peter Quill away from Earth when he was a boy?", "wp-65358849-intro-p01"),
    ("Who created the Netflix series about Luke Cage?", "tt3322314-wikipedia-synopsis-p01"),
    (
        "Who became Sorcerer Supreme while Strange was gone during the Blip?",
        "wp-68044232-intro-p02",
    ),
    ("Why did Mack resign from S.H.I.E.L.D.?", "wp-46434294-plot-p02"),
    # 한국어 원문 그대로 (서비스에서는 번역 후 검색하므로 참고용)
    (
        "엔드게임에서 스콧 랭은 양자 영역에 얼마나 갇혀 있었다고 느꼈나?",
        "tt4154796-wikipedia-plot-p02",
    ),
]


def tokenize(s: str) -> list[str]:
    return re.findall(r"[a-z0-9가-힣']+", s.lower())


def index_text(c: dict) -> str:
    return (c["header"] + " " + c["text"]).strip()  # 머리말 + 본문 (§3)


def rank_of(order: list[int], ids: list[str], gold: str) -> int | None:
    for r, i in enumerate(order, start=1):
        if ids[i] == gold:
            return r
    return None


def main() -> None:
    chunks = [json.loads(line) for line in CHUNKS.open(encoding="utf-8")]
    ids = [c["chunk_id"] for c in chunks]
    texts = [index_text(c) for c in chunks]
    emb_model = get_embeddings()

    if CACHE.exists() and np.load(CACHE).shape[0] == len(chunks):
        mat = np.load(CACHE)
    else:
        vecs: list[list[float]] = []
        for i in range(0, len(texts), 500):
            vecs.extend(emb_model.embed_documents(texts[i : i + 500]))
        mat = np.array(vecs, dtype=np.float32)
        np.save(CACHE, mat)
    mat /= np.linalg.norm(mat, axis=1, keepdims=True)

    bm25 = BM25Okapi([tokenize(t) for t in texts])
    qvecs = np.array(emb_model.embed_documents([q for q, _ in QUESTIONS]), dtype=np.float32)
    qvecs /= np.linalg.norm(qvecs, axis=1, keepdims=True)

    rows = []
    for (q, gold), qv in zip(QUESTIONS, qvecs, strict=True):
        vec_order = list(np.argsort(-(mat @ qv)))
        bm_order = list(np.argsort(-bm25.get_scores(tokenize(q))))
        # B: RRF(k=60) 로 두 순위를 합친다
        rrf: dict[int, float] = {}
        for order in (vec_order[:100], bm_order[:100]):
            for r, i in enumerate(order, start=1):
                rrf[i] = rrf.get(i, 0.0) + 1 / (60 + r)
        hyb_order = sorted(rrf, key=lambda i: -rrf[i])
        rows.append(
            {
                "q": q,
                "gold": gold,
                "A_vector": rank_of(vec_order, ids, gold),
                "BM25": rank_of(bm_order, ids, gold),
                "B_hybrid": rank_of(hyb_order, ids, gold),
                "A_top3": [ids[i] for i in vec_order[:3]],
                "B_top3": [ids[i] for i in hyb_order[:3]],
            }
        )

    def summary(key: str, subset: list[dict]) -> dict:
        ranks = [r[key] for r in subset]
        return {
            "hit@1": sum(1 for r in ranks if r == 1),
            f"hit@{K}": sum(1 for r in ranks if r and r <= K),
            "mrr@10": round(sum(1 / r for r in ranks if r and r <= 10) / len(subset), 3),
        }

    en = rows[:-1]
    result = {
        "n_chunks": len(chunks),
        "n_questions_en": len(en),
        "summary_en": {k: summary(k, en) for k in ("A_vector", "BM25", "B_hybrid")},
        "rows": rows,
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["summary_en"], indent=2))
    for r in rows:
        print(f"A={r['A_vector']!s:>4} BM25={r['BM25']!s:>4} B={r['B_hybrid']!s:>4}  {r['q'][:60]}")


if __name__ == "__main__":
    main()
