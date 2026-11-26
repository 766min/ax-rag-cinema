"""Run the OpenAI embedding A/B/C + English Rewrite experiment.

This intentionally uses a separate collection and writes raw results. The
reranker here is a deterministic lexical baseline; it is not a cross-encoder.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from langchain_postgres import PGVector
from langchain_postgres.vectorstores import DistanceStrategy
from openai import OpenAI
from rank_bm25 import BM25Okapi

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "data" / "cineverse-dataset-schema"


@dataclass(frozen=True)
class Hit:
    chunk_id: str
    doc_id: str
    text: str
    score: float


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def rrf(*lists: list[Hit], limit: int = 40) -> list[Hit]:
    scores: dict[str, float] = {}
    by_id: dict[str, Hit] = {}
    for ranked in lists:
        for rank, hit in enumerate(ranked, 1):
            by_id[hit.chunk_id] = hit
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1 / (60 + rank)
    return sorted(by_id.values(), key=lambda hit: (-scores[hit.chunk_id], hit.chunk_id))[:limit]


def sections_of(reference: str) -> set[str]:
    lower = reference.lower()
    sections: set[str] = set()
    if "plot" in lower:
        sections.add("plot")
    if "synopsis" in lower:
        sections.add("synopsis")
    if "lead" in lower or "(lead)" in lower:
        sections.add("intro")
    if "biography" in lower or "early life" in lower or "fictional character" in lower:
        sections.add("biography")
    if "feature" in lower or "object" in lower or "lore" in lower:
        sections.add("lore")
    return sections


def rewrite_query(client: OpenAI, model: str, question: str) -> tuple[str, int]:
    started = time.perf_counter()
    response = client.responses.create(
        model=model,
        input=(
            "Rewrite the Korean Marvel movie question into one concise English search query. "
            "Keep film, character, object, and event names. Return only the query text.\n"
            f"Question: {question}"
        ),
    )
    query = response.output_text.strip().replace("\n", " ")
    return query or question, round((time.perf_counter() - started) * 1000)


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--collection", default="mcu_movies_openai_small_v1")
    parser.add_argument("--embedding-model", default="text-embedding-3-small")
    parser.add_argument("--llm-model", default=os.getenv("OPENAI_MODEL", "gpt-6-luna"))
    parser.add_argument("--database-url", default="postgresql+psycopg://postgres:postgres@localhost:5433/rag_db")
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "openai-full-experiment.json")
    args = parser.parse_args()
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY가 없습니다.")

    raw_dsn = args.database_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(raw_dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "select e.cmetadata->>'chunk_id', e.cmetadata->>'doc_id', e.document "
            "from langchain_pg_embedding e join langchain_pg_collection c "
            "on c.uuid=e.collection_id where c.name=%s",
            (args.collection,),
        )
        corpus = [(str(a), str(b or ""), str(c or "")) for a, b, c in cursor.fetchall()]
    corpus_hits = [Hit(chunk_id, doc_id, text, 0.0) for chunk_id, doc_id, text in corpus]
    by_chunk = {hit.chunk_id: hit for hit in corpus_hits}
    bm25 = BM25Okapi([words(text) for _, _, text in corpus])
    store = PGVector(
        embeddings=OpenAIEmbeddings(model=args.embedding_model, api_key=os.environ["OPENAI_API_KEY"]),
        collection_name=args.collection,
        connection=args.database_url,
        distance_strategy=DistanceStrategy.COSINE,
        use_jsonb=True,
    )
    client = OpenAI(timeout=45)
    aliases = read_jsonl(DATASET / "aliases.jsonl")
    alias_map = {row["alias_ko"]: row["canonical_en"] for row in aliases}
    works = read_jsonl(DATASET / "works.jsonl")
    title_map = {row.get("title_ko"): row.get("title_original") for row in works if row.get("title_ko") and row.get("title_original")}
    documents = {
        (row.get("source_ids") or {}).get("team_document_id"): row
        for row in read_jsonl(DATASET / "documents.jsonl")
        if (row.get("source_ids") or {}).get("team_document_id")
    }
    questions = read_jsonl(ROOT / "question_set.jsonl")
    records: list[dict] = []
    for number, item in enumerate(questions, 1):
        question = item["question"]
        expanded = question
        for ko, en in {**alias_map, **title_map}.items():
            if ko and ko in expanded and en not in expanded:
                expanded += f" {en}"
        rewritten, rewrite_ms = rewrite_query(client, args.llm_model, question)
        queries = {"raw": question, "expanded": expanded, "rewrite": rewritten}
        mode_rows: dict[str, dict] = {}
        for query_name, query in queries.items():
            vector = [
                Hit(str(doc.metadata["chunk_id"]), str(doc.metadata.get("doc_id", "")), doc.page_content, float(score))
                for doc, score in store.similarity_search_with_score(query, k=40)
            ]
            keyword_scores = bm25.get_scores(words(query))
            order = sorted(range(len(keyword_scores)), key=lambda index: (-float(keyword_scores[index]), corpus[index][0]))[:160]
            keyword = [Hit(corpus[i][0], corpus[i][1], corpus[i][2], float(keyword_scores[i])) for i in order if keyword_scores[i] > 0]
            hybrid = rrf(vector, keyword)
            candidate = rrf(vector, keyword, limit=40)
            query_words = set(words(query))
            rerank = sorted(candidate, key=lambda hit: (-len(query_words & set(words(hit.text))), hit.chunk_id))[:20]
            mode_rows[query_name] = {
                "query": query,
                "vector": [hit.chunk_id for hit in vector[:5]],
                "hybrid": [hit.chunk_id for hit in hybrid[:5]],
                "rerank": [hit.chunk_id for hit in rerank[:5]],
                "candidates": [hit.chunk_id for hit in rerank],
                "hybrid_candidates": [hit.chunk_id for hit in candidate],
                "hits": {hit.chunk_id: {"text": hit.text, "doc_id": hit.doc_id} for hit in rerank},
                "candidate_hits": {hit.chunk_id: {"text": hit.text, "doc_id": hit.doc_id} for hit in candidate},
            }
        gold: set[str] = set()
        reference = item.get("근거 문서·절·리비전", "")
        for old_id in re.findall(r"doc_[0-9a-f]+", reference):
            document = documents.get(old_id)
            if not document:
                continue
            for section in sections_of(reference):
                gold.update(
                    hit.chunk_id
                    for hit in corpus_hits
                    if hit.doc_id == document["doc_id"] and hit.chunk_id.startswith(f"{document['doc_id']}-{section}-p")
                )
        metrics: dict[str, dict] = {}
        for query_name, modes in mode_rows.items():
            for mode in ("vector", "hybrid", "rerank"):
                rank = next((i for i, chunk_id in enumerate(modes[mode], 1) if chunk_id in gold), None)
                metrics[f"{query_name}_{mode}"] = {"rank": rank, "hit_at_5": rank is not None}
        rewritten_hits = mode_rows["rewrite"]["rerank"]
        evidence = [by_chunk[chunk_id] for chunk_id in rewritten_hits[:5] if chunk_id in by_chunk]
        records.append(
            {
                "question_id": item["query_id"],
                "question": question,
                "expected_answer": item["expected_answer"],
                "key_facts": item.get("채점 핵심 사실", ""),
                "gold_count": len(gold),
                "gold_ids": sorted(gold),
                "rewrite_latency_ms": rewrite_ms,
                "queries": mode_rows,
                "metrics": metrics,
                "selected_mode": "rewrite_rerank",
                "evidence": [{"chunk_id": hit.chunk_id, "text": hit.text} for hit in evidence],
            }
        )
        print(f"experiment {number}/{len(questions)}", flush=True)
    scored = [row for row in records if row["gold_count"]]
    summary: dict[str, dict] = {}
    for key in ("raw_vector", "raw_hybrid", "raw_rerank", "expanded_rerank", "rewrite_rerank"):
        values = [row["metrics"][key] for row in scored]
        ranks = [value["rank"] for value in values if value["rank"]]
        summary[key] = {
            "hit_at_5": sum(value["hit_at_5"] for value in values),
            "total": len(values),
            "mrr": sum(1 / rank for rank in ranks) / len(values) if values else 0,
        }
    output = {
        "embedding_model": args.embedding_model,
        "llm_model": args.llm_model,
        "collection": args.collection,
        "chunks": len(corpus),
        "rows": len(records),
        "scored_rows": len(scored),
        "summary": summary,
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "summary": summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
