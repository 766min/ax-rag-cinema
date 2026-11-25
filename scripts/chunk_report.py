"""청킹 결과 검수 (임베딩 없음, API · DB 호출 없음).

레시피마다 청크 통계 · 버린 조각 · 절 경계 · 원문 커버리지 · ID · 정답 근거 매핑 · 임베딩 토큰 추정을
results/{버전}/chunking/{실행시각}/ 아래 새 파일로 남긴다. 해석이나 판정은 넣지 않는다.

    uv run python scripts/chunk_report.py --results-version v1                 # CK01~CK04 전부
    uv run python scripts/chunk_report.py --results-version v1 --recipe CK02   # 하나만
"""

import argparse
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from etl.chunking import RECIPES, ChunkRecipe, chunk_spans, normalize_ws  # noqa: E402
from etl.dataset import (  # noqa: E402
    PROJECT_ROOT,
    Dataset,
    build_chunks,
    dataset_fingerprint,
    index_version,
    load_dataset,
    scope_documents,
)
from etl.load import count_tokens, index_text  # noqa: E402
from evals.items import map_citations, read_question_set  # noqa: E402
from evals.provenance import results_dir, run_info  # noqa: E402
from shared.core.models import Chunk  # noqa: E402

BUCKETS = (50, 100, 250, 500, 750, 1000, 1500, 2000, 4000)
EMBEDDING_INPUT_LIMIT = 8191  # OpenAI text-embedding-3 계열 입력 한도 (다른 모델이면 바꿔야 함)
_SENTENCE_END = re.compile(r"[.!?\"'”’)\]]$")


def _counts(citations: Any) -> dict[str, int]:
    statuses = [c["status"] for c in citations]
    return {s: statuses.count(s) for s in ("contained", "split", "partial", "missing")}


def _histogram(lengths: list[int]) -> dict[str, int]:
    edges = (0, *BUCKETS)
    hist = {f"{lo}-{hi - 1}": 0 for lo, hi in zip(edges, BUCKETS, strict=False)}
    hist[f"{BUCKETS[-1]}+"] = 0
    for n in lengths:
        for lo, hi in zip(edges, BUCKETS, strict=False):
            if lo <= n < hi:
                hist[f"{lo}-{hi - 1}"] += 1
                break
        else:
            hist[f"{BUCKETS[-1]}+"] += 1
    return hist


def _coverage(section_text: str, chunks: list[Chunk]) -> dict[str, Any]:
    """절 원문(공백 정규화)에서 청크들이 덮는 범위 (etl.chunking.chunk_spans 와 같은 기준).

    절 안에서 찾지 못한 청크는 not_in_section 으로 센다 (절 경계를 넘었거나 글이 바뀐 경우).
    """
    source = normalize_ws(section_text)
    spans = chunk_spans(section_text, [c.text for c in chunks])
    covered = [False] * len(source)
    for span in spans:
        if span:
            for i in range(*span):
                covered[i] = True
    gaps: list[str] = []
    i = 0
    while i < len(source):
        if covered[i]:
            i += 1
            continue
        j = i
        while j < len(source) and not covered[j]:
            j += 1
        if source[i:j].strip():
            gaps.append(source[i:j])
        i = j
    return {
        "source_chars": len(source),
        "covered_chars": sum(covered),
        # 청크 경계의 공백은 splitter 가 떼어 내므로 실제로 빠진 글자는 공백을 뺀 수로 센다
        "uncovered_nonspace": sum(
            1 for ch, hit in zip(source, covered, strict=True) if not hit and ch != " "
        ),
        "chunk_chars": sum(len(normalize_ws(c.text)) for c in chunks),
        "not_in_section": [c.chunk_id for c, s in zip(chunks, spans, strict=True) if s is None],
        "uncovered": gaps,
    }


def report(recipe: ChunkRecipe, data: Dataset) -> dict[str, Any]:
    started = time.perf_counter()
    chunks = build_chunks(data, recipe)
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    documents = scope_documents(data)

    by_section: dict[tuple[str, str], list[Chunk]] = {}
    for c in chunks:
        key = (c.source_ids["team_document_id"] or "", c.source_ids["section_index"] or "")
        by_section.setdefault(key, []).append(c)

    dropped: list[dict[str, Any]] = []
    source_chars = covered_chars = chunk_chars = uncovered_nonspace = 0
    not_in_section: list[str] = []
    uncovered: list[dict[str, Any]] = []
    for doc, section, _ in data.scope:
        team_id = doc.source_ids.get("team_document_id") or ""
        _, gone = recipe.split(section["text"])
        for piece in gone:
            dropped.append(
                {
                    "doc_id": doc.doc_id,
                    "team_document_id": team_id,
                    "title": data.titles[doc.doc_id],
                    "section_index": section["section_index"],
                    "text": piece,
                    "reason": f"min_chars({recipe.min_chars}) 미만",
                }
            )
        key = (team_id, str(section["section_index"]))
        cov = _coverage(section["text"], sorted(by_section.get(key, []), key=lambda c: c.position))
        source_chars += cov["source_chars"]
        covered_chars += cov["covered_chars"]
        chunk_chars += cov["chunk_chars"]
        uncovered_nonspace += cov["uncovered_nonspace"]
        not_in_section += cov["not_in_section"]
        for gap in cov["uncovered"]:
            uncovered.append({"doc_id": doc.doc_id, "section_index": key[1], "text": gap})

    lengths = sorted(len(c.text) for c in chunks)
    ids = [c.chunk_id for c in chunks]
    doc_ids = {d.doc_id for d in documents}
    encoding, tokens = count_tokens([index_text(c) for c in chunks], _embedding_model())
    cut_end = [c for c in chunks if not _SENTENCE_END.search(c.text.rstrip())]
    cut_start = [c for c in chunks if c.text[:1].islower()]

    return {
        "recipe": recipe.recipe_id,
        "chunking_version": recipe.version,
        "settings": recipe.settings(),
        "documents": len(documents),
        "source_sections": len(data.scope),
        "chunks": len(chunks),
        "chunks_by_section": dict(Counter(str(c.section) for c in chunks)),
        "chunk_ms": elapsed_ms,
        "length": {
            "min": lengths[0],
            "max": lengths[-1],
            "mean": round(mean(lengths), 1),
            "median": median(lengths),
            "histogram": _histogram(lengths),
            "under_50": sum(n < 50 for n in lengths),
            "under_100": sum(n < 100 for n in lengths),
        },
        "dropped": {"count": len(dropped), "items": dropped},
        "coverage": {
            "source_chars_normalized": source_chars,
            "covered_chars": covered_chars,
            "uncovered_chars_incl_spaces": source_chars - covered_chars,
            "uncovered_nonspace_chars": uncovered_nonspace,
            "chunk_chars_total": chunk_chars,
            "chunk_to_source_ratio": round(chunk_chars / source_chars, 4) if source_chars else None,
            "note": "비율은 overlap · 공백 정규화 · 버린 조각의 영향을 함께 받는다 (순수 overlap 증가량 아님)",
            "uncovered_items": uncovered,
        },
        "integrity": {
            "duplicate_chunk_ids": len(ids) - len(set(ids)),
            "chunks_outside_their_section": not_in_section,
            "chunks_without_document": sum(c.doc_id not in doc_ids for c in chunks),
            "chunks_without_source_position": sum(
                not (c.source_ids.get("team_document_id") and c.source_ids.get("section_index"))
                for c in chunks
            ),
        },
        "boundaries": {
            "ends_without_sentence_end": len(cut_end),
            "starts_lowercase": len(cut_start),
        },
        "embedding_tokens_estimate": {
            "tokenizer": encoding,
            "model": _embedding_model(),
            "input": "header + '\\n' + text",
            "total": sum(tokens),
            "max": max(tokens),
            "over_input_limit": sum(t > EMBEDDING_INPUT_LIMIT for t in tokens),
            "input_limit_assumed": EMBEDDING_INPUT_LIMIT,
        },
        "_samples": {"cut_end": cut_end[:8], "cut_start": cut_start[:5], "chunks": chunks},
    }


def _embedding_model() -> str:
    from shared.core.config import settings

    return settings.EMBEDDING_MODEL


def _sample_text(rep: dict[str, Any]) -> str:
    """사람이 읽을 샘플: 가장 짧은 · 긴 청크, 문장 중간에서 끝나는 청크, 버린 조각."""
    chunks: list[Chunk] = rep["_samples"]["chunks"]
    by_len = sorted(chunks, key=lambda c: len(c.text))
    blocks = [f"# {rep['recipe']} {rep['chunking_version']}\n"]

    def show(title: str, items: list[Chunk]) -> None:
        blocks.append(f"\n## {title}\n")
        for c in items:
            blocks.append(f"\n--- {c.chunk_id} ({len(c.text)}자) | {c.header}\n{c.text}\n")

    show("가장 짧은 청크 5", by_len[:5])
    show("가장 긴 청크 3", by_len[-3:])
    show("문장 끝 기호 없이 끝나는 청크 (최대 8)", rep["_samples"]["cut_end"])
    show("소문자로 시작하는 청크 (최대 5)", rep["_samples"]["cut_start"])
    blocks.append("\n## 버린 조각 전체\n")
    for d in rep["dropped"]["items"]:
        blocks.append(f"- [{d['title']} §{d['section_index']}] {d['text']!r} ({d['reason']})\n")
    return "".join(blocks)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recipe", choices=RECIPES, action="append")
    parser.add_argument("--results-version", required=True, help="결과 버전 (예: v1)")
    args = parser.parse_args()
    recipes = [RECIPES[r] for r in (args.recipe or RECIPES)]

    fingerprint = dataset_fingerprint()
    data = load_dataset()
    questions = read_question_set()
    sections = data.sections
    info = run_info(args.results_version)

    out_dir = results_dir(args.results_version) / "chunking" / f"{datetime.now():%Y%m%d-%H%M%S}"
    out_dir.mkdir(parents=True, exist_ok=False)  # 같은 시각 폴더가 있으면 실패 (덮어쓰기 방지)

    for recipe in recipes:
        rep = report(recipe, data)
        chunks = rep["_samples"]["chunks"]
        gold = [c for raw in questions for c in map_citations(raw, chunks, sections)]
        for g in gold:
            g["answerable"] = next(
                q["answerable"] for q in questions if q["query_id"] == g["question_id"]
            )
        rep["index_version"] = index_version(recipe, fingerprint)
        rep["dataset"] = fingerprint
        rep["run_info"] = info
        rep["gold_mapping"] = {
            "status_counts_answerable": _counts(g for g in gold if g["answerable"]),
            "status_counts_unanswerable": _counts(g for g in gold if not g["answerable"]),
            "citations": gold,
        }
        sample = _sample_text(rep)
        del rep["_samples"]
        with open(out_dir / f"{recipe.recipe_id}.json", "x", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
        with open(out_dir / f"{recipe.recipe_id}-samples.md", "x", encoding="utf-8") as f:
            f.write(sample)
        print(
            f"{recipe.recipe_id}: chunks {rep['chunks']} · dropped {rep['dropped']['count']} · "
            f"gold(답변 가능) {rep['gold_mapping']['status_counts_answerable']} · "
            f"gold(근거 없음 문항) {rep['gold_mapping']['status_counts_unanswerable']}"
        )
    print(f"→ {out_dir.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
