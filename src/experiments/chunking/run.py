"""청킹 실험 실행기.

사용법 (저장소 루트에서, PYTHONPATH=src):
    python -m experiments.chunking.run stats R1            # 네트워크 없이 청크 수 · 정답 섹션 포함 여부
    python -m experiments.chunking.run index R1            # 컬렉션을 지우고 다시 임베딩
    python -m experiments.chunking.run run R1 --date 2026-11-25
    python -m experiments.chunking.run report RUN_ID ...   # 여러 실행을 표로 비교
"""

import argparse
import hashlib
import json
import statistics
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from experiments.chunking.chunker import (
    CHUNKER_VERSION,
    OVERLAP_MODE,
    VARIANTS,
    ExpChunk,
    build_variant,
    cut_report,
    handoff_paragraphs,
)
from experiments.chunking.data import (
    PROJECT_ROOT,
    TRICK_SET,
    Question,
    SectionRef,
    load_handoff,
    load_questions,
)
from experiments.chunking.schema import (
    is_extra_doc,
    load_schema,
    remap_questions,
    s2_chunks,
    schema_paragraphs,
    schema_section_texts,
)
from experiments.chunking.scoring import (
    build_probe_table,
    chunk_has_excerpt,
    cited_gold,
    classify_failure,
    excerpt_hit,
    excerpt_recall,
    excerpts_found,
    gold_in_index,
    gold_rank,
    section_hit,
    section_recall,
)

DEFAULT_MAX_CHARS = 800
DEFAULT_TOP_K = 5
DEFAULT_DEPTH = 20  # 검색은 상위 20개까지 받아 기록하고, 모델에는 상위 top_k 만 넣는다
RESULTS_DIR = PROJECT_ROOT / "results"


def size_tag(variant: str, max_chars: int, overlap: bool) -> str:
    """실행 · 컬렉션 이름에 들어가는 청커 버전 · 크기 · 겹치기 표시 (예: -c2-800-o1s).

    c1 시절 실행(R1-800, R2-800)은 버전 표시가 없었다. 이름이 겹쳐 옛 결과를 덮지 않도록 c2 부터 붙인다.
    """
    if variant == "R0":
        return ""
    version = "" if CHUNKER_VERSION == "c1" else f"-{CHUNKER_VERSION}"
    return f"{version}-{max_chars}" + (f"-{OVERLAP_MODE}" if overlap else "")


def collection_name(variant: str, max_chars: int, overlap: bool = False) -> str:
    if variant == "R0":
        return "exp_minji_r0"
    return f"exp_minji_{variant.lower()}{size_tag(variant, max_chars, overlap)}".replace("-", "_")


def index_version(
    variant: str, max_chars: int, chunks: list[ExpChunk], overlap: bool = False
) -> str:
    h = hashlib.sha256()
    for c in sorted(chunks, key=lambda c: c.chunk_id):
        h.update(f"{c.chunk_id}\x1f{c.embed_text()}\x1e".encode())
    if variant == "R0":
        return f"R0-handoff-{h.hexdigest()[:10]}"
    # size_tag 에 청커 버전이 들어 있다 (c1 시절 이름에는 없어서 그때만 붙인다)
    tag = size_tag(variant, max_chars, overlap)
    version = f"-{CHUNKER_VERSION}" if CHUNKER_VERSION == "c1" else ""
    return f"{variant}{version}{tag}-{h.hexdigest()[:10]}"


def overlap_stats(chunks: list[ExpChunk]) -> dict[str, Any]:
    """겹치기가 실제로 붙은 경계의 비율과 붙은 글자 수 (재료의 첫 조각은 경계가 아니라 뺀다)."""
    later = [c for c in chunks if not c.first_in_unit]
    with_ov = [c.overlap_chars for c in later if c.overlap_chars]
    return {
        "boundaries": len(later),
        "with_overlap": len(with_ov),
        "rate": round(len(with_ov) / len(later), 3) if later else None,
        "overlap_chars_median": sorted(with_ov)[len(with_ov) // 2] if with_ov else 0,
    }


RUN_VARIANTS = (*VARIANTS, "S2")


@dataclass
class Prepared:
    """실험 하나에 필요한 재료 (handoff 실험 R0 · R1 · R2 또는 새 데이터 실험 S2)."""

    chunks: list[ExpChunk]
    questions: list[Question]  # 근거 문서 ID 가 이 데이터의 ID 로 맞춰진 평가셋
    section_texts: list[str]  # 발췌 상투 문장 판정용 전체 원문
    aliases: list[dict[str, Any]]
    is_extra: Callable[[str], bool]  # 문서가 인물 · 세계관 쪽인가
    paragraphs: Callable[[], dict[SectionRef, set[str]]]  # 경계 검사용
    dataset: str


def prepare(variant: str, max_chars: int, overlap: bool, trick: bool = False) -> Prepared:
    # 함정 문항(민지 추가, 공식 평가셋 아님)은 본 문항 뒤에 붙이고 결과는 따로 집계한다
    questions = load_questions() + (load_questions(TRICK_SET) if trick else [])
    if variant == "S2":
        schema = load_schema()
        return Prepared(
            chunks=s2_chunks(schema, max_chars, overlap),
            questions=remap_questions(questions, schema),
            section_texts=schema_section_texts(schema),
            aliases=schema.aliases,
            is_extra=lambda d: is_extra_doc(schema, d),
            paragraphs=lambda: schema_paragraphs(schema),
            dataset="schema-2026-11-25",
        )
    handoff = load_handoff()
    return Prepared(
        chunks=build_variant(handoff, variant, max_chars, overlap),
        questions=questions,
        section_texts=[s["text"] for d in handoff.documents.values() for s in d["sections"]],
        aliases=handoff.aliases,
        is_extra=lambda d: handoff.documents[d]["doc_kind"] != "content",
        paragraphs=lambda: handoff_paragraphs(handoff),
        dataset="handoff-2026-11-24",
    )


def needs_extra_docs(q: Question, prep: Prepared) -> bool:
    """정답 근거에 줄거리 문서가 아닌 문서(인물 · 세계관)가 들어 있나"""
    return any(prep.is_extra(r.doc_id) for r in q.gold_refs)


def cmd_stats(args: argparse.Namespace) -> None:
    prep = prepare(args.variant, args.max_chars, args.overlap)
    questions, chunks = prep.questions, prep.chunks
    refs = {r for c in chunks for r in c.refs}
    lengths = sorted(len(c.text) for c in chunks)
    name = args.variant + size_tag(args.variant, args.max_chars, args.overlap)
    print(f"{name}: 청크 {len(chunks)}개, 문서 {len({c.doc_id for c in chunks})}개")
    if args.overlap:
        print(f"  겹치기: {overlap_stats(chunks)}")
    print(
        f"  글자 수 최소 {lengths[0]} / 중앙 {lengths[len(lengths) // 2]} / "
        f"90% {lengths[int(len(lengths) * 0.9)]} / 최대 {lengths[-1]} / 합계 {sum(lengths)}"
    )
    print(f"  문서 종류: {dict(Counter(c.doc_kind for c in chunks))}")
    answerable = [q for q in questions if q.answerable]
    covered = [q.question_id for q in answerable if gold_in_index(refs, q)]
    print(f"  정답 섹션이 색인에 있는 문항: {len(covered)}/{len(answerable)}")
    missing = [q.question_id for q in answerable if q.question_id not in covered]
    print(f"  색인에 정답이 아예 없는 문항: {missing}")
    cuts = cut_report(prep.paragraphs(), chunks)
    print(
        "  문단 안 경계: "
        + ", ".join(f"{k} {v}" for k, v in cuts.items() if k != "bad")
        + f", 문장 중간(약어 뒤) {len(cuts['bad'])}"
    )
    print(f"  index_version: {index_version(args.variant, args.max_chars, chunks, args.overlap)}")


def cmd_index(args: argparse.Namespace) -> None:
    from experiments.chunking.pipeline import build_index, indexed_ids

    chunks = prepare(args.variant, args.max_chars, args.overlap).chunks
    collection = collection_name(args.variant, args.max_chars, args.overlap)
    print(f"{collection}: 청크 {len(chunks)}개 임베딩 시작")
    build_index(chunks, collection)
    got = indexed_ids(collection)
    expected = {c.chunk_id for c in chunks}
    if got != expected:
        raise SystemExit(f"색인 불일치: 빠짐 {len(expected - got)}, 남는 것 {len(got - expected)}")
    print(f"{collection}: 완료, DB 대조 일치 ({len(got)}개)")


def _rate(values: list[bool]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def _mean(values: list[float]) -> float | None:
    return round(statistics.mean(values), 3) if values else None


def answer_correct(row: dict[str, Any]) -> bool | None:
    """정답 충족. 답변 가능 문항은 실제로 답했을(answered) 때만 센다.

    거절 문장 안에 사실 일부가 섞이면 LLM 채점자가 맞다고 볼 수 있어서(R2 Q049), 거절은 정답이 아니다.
    채점 실패(None)는 그대로 None 으로 둬서 분모에서 뺀다.
    """
    correct = row["judge"].get("correct")
    if correct is None:
        return None
    if row["answerable"] and row["status"] != "answered":
        return False
    return bool(correct)


def _correct_rate(rows: list[dict[str, Any]]) -> float | None:
    values = [c for c in (answer_correct(r) for r in rows) if c is not None]
    return _rate(values)


def summarize(all_rows: list[dict[str, Any]]) -> dict[str, Any]:
    trick = [r for r in all_rows if r.get("difficulty") == "함정"]
    rows = [r for r in all_rows if r.get("difficulty") != "함정"]
    ans = [r for r in rows if r["answerable"]]
    unans = [r for r in rows if not r["answerable"]]
    ok = [r for r in rows if r["status"] in ("answered", "no_evidence")]
    total_ms = [r["latency_ms"]["total"] for r in ok]
    gen_ms = [r["latency_ms"].get("generate", 0) for r in ok]
    groups: dict[str, list[dict[str, Any]]] = {
        "plot_only": [r for r in ans if not r["needs_extra_docs"]],
        "needs_character_universe": [r for r in ans if r["needs_extra_docs"]],
        **{
            f"difficulty_{d}": [r for r in ans if r.get("difficulty") == d]
            for d in ("초보", "중수", "고수")
        },
    }
    return {
        "n_questions": len(rows),
        "n_answerable": len(ans),
        "gold_in_index": sum(r["gold_in_index"] for r in ans),
        "hit_section_at_k": _rate([r["hit_section"] for r in ans]),
        "hit_excerpt_at_k": _rate([r["hit_excerpt"] for r in ans]),
        "section_recall_mean": _mean([r["section_recall"] for r in ans]),
        "excerpt_recall_mean": _mean([r["excerpt_recall"] for r in ans]),
        "context_chars_mean": round(statistics.mean(r["context_chars"] for r in rows)),
        "answer_correct": _correct_rate(ans),
        "judge_missing": sum(1 for r in rows if r["judge"].get("correct") is None),
        "refusal_correct": f"{sum(bool(answer_correct(r)) for r in unans)}/{len(unans)}",
        "cited_gold_rate": _rate([r["cited_gold"] for r in ans if r["status"] == "answered"]),
        "status": dict(Counter(r["status"] for r in rows)),
        "failure_types": dict(Counter(r["failure_type"] for r in rows if r.get("failure_type"))),
        "trick": {
            "n": len(trick),
            "answer_correct": _correct_rate(trick),
            "failure_types": dict(Counter(r.get("failure_type") for r in trick)),
            "rows": {r["question_id"]: answer_correct(r) for r in trick},
        },
        "latency_total_ms_mean": round(statistics.mean(total_ms)) if total_ms else None,
        "latency_total_ms_median": round(statistics.median(total_ms)) if total_ms else None,
        "latency_generate_ms_mean": round(statistics.mean(gen_ms)) if gen_ms else None,
        "tokens_input_mean": round(statistics.mean(r["tokens"].get("input", 0) for r in rows)),
        "tokens_output_mean": round(statistics.mean(r["tokens"].get("output", 0) for r in rows)),
        "by_group": {
            name: {
                "n": len(g),
                "hit_section_at_k": _rate([r["hit_section"] for r in g]),
                "hit_excerpt_at_k": _rate([r["hit_excerpt"] for r in g]),
                "section_recall_mean": _mean([r["section_recall"] for r in g]),
                "excerpt_recall_mean": _mean([r["excerpt_recall"] for r in g]),
                "answer_correct": _correct_rate(g),
            }
            for name, g in groups.items()
        },
    }


def cmd_run(args: argparse.Namespace) -> None:
    from evals.recorder import Recorder, measure
    from experiments.chunking.pipeline import (
        JUDGE_VERSION,
        PROMPT_VERSION,
        generate,
        indexed_ids,
        judge_answer,
        normalize_query,
        retrieve,
    )
    from shared.core.config import settings
    from shared.core.models import Candidate, EvidenceSource, RunRecord, Trace
    from shared.core.schemas import AnswerStatus, RetrievalMode
    from shared.core.vector_store import get_llm

    prep = prepare(args.variant, args.max_chars, args.overlap, trick=args.trick)
    questions = prep.questions
    if args.limit:
        questions = questions[: args.limit]
    chunks = prep.chunks
    by_id = {c.chunk_id: c for c in chunks}
    collection = collection_name(args.variant, args.max_chars, args.overlap)
    # 색인이 지금 청크 묶음과 같은지 먼저 대조한다 (다른 색인으로 평가하는 사고 방지)
    if indexed_ids(collection) != set(by_id):
        raise SystemExit(f"{collection} 색인이 청크 묶음과 다릅니다. 먼저 index 를 실행하세요.")
    version = index_version(args.variant, args.max_chars, chunks, args.overlap)
    index_refs = {r for c in chunks for r in c.refs}
    # 발췌 확인 조각은 색인 범위와 상관없이 '전체 원문' 기준으로 상투 문장을 뺀다 (모든 실행에 같은 표)
    probes = {q.question_id: build_probe_table(q, prep.section_texts) for q in questions}
    # 영문 검색어: 한 번 번역해 둔 파일을 모든 실행이 같이 쓴다 (실행마다 번역이 달라지지 않게)
    queries: dict[str, str] | None = None
    entries: dict[str, Any] = {}
    data: dict[str, Any] = {}
    translate_meta: dict[str, Any] = {}
    if args.queries:
        files = [json.loads(Path(f).read_text(encoding="utf-8")) for f in args.queries]
        tags = {str(f.get("tag", "en")) for f in files}
        if len(tags) != 1:
            raise SystemExit(f"검색어 파일의 번역 방식이 서로 다릅니다: {tags}")
        data = files[0]
        entries = {qid: v for f in files for qid, v in f["queries"].items()}
        queries = {qid: v["query_en"] for qid, v in entries.items()}
        translate_meta = {
            "files": [Path(f).name for f in args.queries],
            "version": sorted({f["version"] for f in files}),
        }
        missing = [q.question_id for q in questions if q.question_id not in queries]
        if missing:
            raise SystemExit(f"영문 검색어 파일에 없는 문항: {missing}")
    # 검색어 꼬리표: ko(한국어 그대로) · en(내 임시 번역 t1) · tr(팀 번역 도구 rewrite_movie_query)
    lang = str(data.get("tag", "en")) if queries else "ko"
    run_id = f"{args.date}-minji-{args.variant}{size_tag(args.variant, args.max_chars, args.overlap)}-{lang}"
    if args.trial:
        run_id += "-try"
    llm = get_llm()
    recorder = Recorder(run_id, RESULTS_DIR)
    params = {
        "variant": args.variant,
        "dataset": prep.dataset,
        "max_chars": None if args.variant == "R0" else args.max_chars,
        "overlap": OVERLAP_MODE if args.overlap else None,
        "overlap_stats": overlap_stats(chunks) if args.overlap else None,
        "top_k": args.top_k,
        "search_depth": max(args.top_k, args.depth),
        "with_trick": args.trick,
        "n_chunks": len(chunks),
        "chunker": CHUNKER_VERSION,
        "collection": collection,
        "search_query": "영문 번역(캐시)" if queries else "한국어 + 이름 사전",
        "translate": translate_meta or None,
        "agent": "Claude Code (Opus 5.5)",
    }
    rows: list[dict[str, Any]] = []
    for q in questions:
        entry = entries.get(q.question_id, {})
        normalized = entry.get("query_normalized") or normalize_query(q.question, prep.aliases)
        rewrite_failed = bool(entry.get("rewrite_failed", False))
        # 팀 도구는 번역에 실패하면 query_en 을 비운다. 그때는 한국어 + 영어 이름 질문으로 검색한다
        search_query = (queries[q.question_id] or normalized) if queries else normalized
        hits: list[tuple[str, float]] = []
        out = None
        error: str | None = None
        status = AnswerStatus.ANSWERED
        with measure() as m:
            try:
                # 상위 depth(기본 20)까지 받아 '순위 밀림'을 잴 수 있게 하고, 모델에는 상위 top_k 만 넣는다
                hits = retrieve(collection, search_query, max(args.top_k, args.depth))
                context = [by_id[cid] for cid, _ in hits[: args.top_k]]
            except Exception as exc:
                status, error = AnswerStatus.RETRIEVAL_ERROR, f"{type(exc).__name__}: {exc}"
                hits = []
            else:
                try:
                    out = generate(llm, normalized, context)
                except Exception as exc:
                    status, error = AnswerStatus.GENERATION_ERROR, f"{type(exc).__name__}: {exc}"
        cited_ids: list[str] = []
        if out is not None:
            in_model = hits[: args.top_k]  # 인용 번호 [n] 은 모델에 넣은 근거의 번호다
            for n in out.cited:
                if 1 <= n <= len(in_model) and in_model[n - 1][0] not in cited_ids:
                    cited_ids.append(in_model[n - 1][0])
            if not (out.has_evidence and cited_ids):
                status, cited_ids = AnswerStatus.NO_EVIDENCE, []
        record = RunRecord(
            run_id=run_id,
            question_id=q.question_id,
            status=status,
            candidates=[
                Candidate(
                    chunk_id=cid,
                    rank=i,
                    rank_before_rerank=None,
                    vector_score=round(score, 4),
                    bm25_score=None,
                    rrf_score=None,
                    rerank_score=None,
                )
                for i, (cid, score) in enumerate(hits, start=1)
            ],
            cited_chunk_ids=cited_ids,
            answer=out.answer if out else None,
            error=error,
            latency_ms=m.latency_ms,
            tokens=m.tokens,
            embedding_model=settings.EMBEDDING_MODEL,
            llm_model=settings.LLM_MODEL,
            prompt_version=PROMPT_VERSION,
            alias_version="handoff-aliases-112",
            params=params,
            trace=Trace(
                query_ko=q.question,
                query_normalized=normalized,
                query_en=queries[q.question_id] if queries else "",
                rewrite_failed=rewrite_failed,
                retrieval_mode=RetrievalMode.VECTOR,
                evidence_source=(
                    EvidenceSource.DOCUMENTS
                    if status == AnswerStatus.ANSWERED
                    else EvidenceSource.NONE
                ),
                web_called=False,  # 평가에서는 웹 검색을 끈다 (problem.md §9.2)
                web_cache_hit=False,
                web_failed=False,
                index_version=version,
            ),
        )
        recorder.write(record)
        top = [by_id[cid] for cid in record.top_chunk_ids(args.top_k)]
        cited = [by_id[cid] for cid in cited_ids]
        failed = status in (AnswerStatus.RETRIEVAL_ERROR, AnswerStatus.GENERATION_ERROR)
        judge: dict[str, Any]
        if failed:
            judge = {"correct": False, "note": "호출 오류 (올바른 거절로 세지 않음)"}
        else:
            try:
                judge = judge_answer(llm, q, record.answer or "")
            except Exception as exc:
                judge = {"correct": None, "judge_error": f"{type(exc).__name__}: {exc}"}
        row: dict[str, Any] = {
            "question_id": q.question_id,
            "answerable": q.answerable,
            "difficulty": q.difficulty,
            "needs_extra_docs": needs_extra_docs(q, prep),
            "status": str(status),
            "gold_in_index": gold_in_index(index_refs, q),
            "hit_section": section_hit(top, q.gold_refs),
            "hit_excerpt": excerpt_hit(top, probes[q.question_id]),
            "section_recall": round(section_recall(top, q.gold_refs), 3),
            "excerpt_recall": round(excerpt_recall(top, probes[q.question_id]), 3),
            "excerpts_found": excerpts_found(top, probes[q.question_id]),
            "context_chars": sum(len(c.header) + len(c.text) for c in top),
            "cited_gold": cited_gold(cited, q.gold_refs),
            "judge": judge,
            "latency_ms": m.latency_ms,
            "tokens": m.tokens,
            "search_query": search_query,
            "rewrite_failed": rewrite_failed,
        }
        ranked = [by_id[cid] for cid, _ in hits]
        first_gold = gold_rank(ranked, q.gold_refs)
        gold_set = set(q.gold_refs)
        flat_probes = [p for ps in probes[q.question_id].values() for p in ps]
        row["gold_rank"] = first_gold
        # 후보마다: 모델에 넣었나 · 인용했나 · 정답 섹션인가 (옛 팀 레포의 used_in_model_input · cited_in_answer)
        row["candidates"] = [
            {
                "rank": i,
                "chunk_id": cid,
                "doc_id": by_id[cid].doc_id,
                "sections": [f"{r.doc_id}#{r.section_index}" for r in by_id[cid].refs],
                "vector_score": round(score, 4),
                "used_in_model_input": i <= args.top_k,
                "cited_in_answer": cid in cited_ids,
                "gold_section": any(r in gold_set for r in by_id[cid].refs),
                "gold_excerpt": chunk_has_excerpt(by_id[cid].text, flat_probes),
            }
            for i, (cid, score) in enumerate(hits, start=1)
        ]
        row["failure_type"] = classify_failure(
            answerable=q.answerable,
            status=str(status),
            correct=answer_correct(row),
            in_index=row["gold_in_index"],
            first_gold_rank=first_gold,
            top_k=args.top_k,
            cited_gold_section=row["cited_gold"],
        )
        rows.append(row)
        print(
            f"{q.question_id} {status:13} 섹션Hit={int(row['hit_section'])} "
            f"발췌Hit={int(row['hit_excerpt'])} 정답={judge.get('correct')} {row['failure_type']} "
            f"{m.latency_ms.get('total')}ms",
            flush=True,
        )
    scores = {
        "run_id": run_id,
        "index_version": version,
        "params": params,
        "judge": {"model": settings.LLM_MODEL, "prompt_version": JUDGE_VERSION, "kind": "LLM 판정"},
        "summary": summarize(rows),
        "rows": rows,
    }
    path = RESULTS_DIR / f"{run_id}.scores.json"
    path.write_text(json.dumps(scores, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(scores["summary"], ensure_ascii=False, indent=2))
    print(f"기록: {recorder.path}\n채점: {path}")


def cmd_translate(args: argparse.Namespace) -> None:
    """20문항을 한 번만 영문 검색어로 바꿔 파일로 남긴다. 모든 실행이 이 파일을 같이 쓴다."""
    from evals.recorder import measure
    from experiments.chunking.pipeline import TRANSLATE_VERSION, normalize_query, translate_query
    from shared.core.config import settings
    from shared.core.vector_store import get_llm

    path = RESULTS_DIR / f"{args.date}-minji-query-en.json"
    if path.exists():
        raise SystemExit(
            f"{path.name} 이 이미 있습니다. 같은 검색어를 계속 쓰기 위해 덮어쓰지 않습니다."
        )
    handoff = load_handoff()
    llm = get_llm()
    out: dict[str, Any] = {"version": TRANSLATE_VERSION, "model": settings.LLM_MODEL, "queries": {}}
    for q in load_questions():
        normalized = normalize_query(q.question, handoff.aliases)
        with measure() as m:
            query_en = translate_query(llm, normalized)
        out["queries"][q.question_id] = {
            "question": q.question,
            "normalized": normalized,
            "query_en": query_en,
            "latency_ms": m.latency_ms,
            "tokens": m.tokens,
        }
        print(f"{q.question_id} {query_en}", flush=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장: {path}")


def cmd_translate_team(args: argparse.Namespace) -> None:
    """명석님 번역 도구(tools.translation.rewrite_movie_query)로 20문항의 영문 검색어를 한 번 만든다."""
    import asyncio
    import time

    from evals.recorder import measure
    from shared.core.config import settings
    from tools.translation import load_aliases, rewrite_movie_query

    suffix = "-trick" if args.trick else ""
    path = RESULTS_DIR / f"{args.date}-minji-query-team{suffix}.json"
    questions = load_questions(TRICK_SET) if args.trick else load_questions()
    if path.exists():
        raise SystemExit(
            f"{path.name} 이 이미 있습니다. 같은 검색어를 계속 쓰기 위해 덮어쓰지 않습니다."
        )
    aliases = load_aliases(PROJECT_ROOT / "data" / "schema" / "aliases.jsonl")
    out: dict[str, Any] = {
        "version": "team-rewrite_movie_query",
        "tag": "tr",
        "model": settings.LLM_MODEL,
        "queries": {},
    }

    async def run_all() -> None:
        # 한 이벤트 루프에서 모두 돌린다 (문항마다 루프를 새로 열면 닫힌 루프에서 연결 정리 경고가 난다)
        for q in questions:
            start = time.perf_counter()
            with measure() as m:
                res = await rewrite_movie_query(q.question, aliases)
            res["latency_ms"] = round((time.perf_counter() - start) * 1000)
            res["tokens"] = m.tokens
            out["queries"][q.question_id] = res
            flag = " (번역 실패)" if res["rewrite_failed"] else ""
            print(f"{q.question_id} {res['query_en'] or res['query_normalized']}{flag}", flush=True)

    asyncio.run(run_all())
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장: {path}")


def cmd_report(args: argparse.Namespace) -> None:
    runs = [
        json.loads((RESULTS_DIR / f"{rid}.scores.json").read_text(encoding="utf-8"))
        for rid in args.run_ids
    ]
    # 요약은 저장된 값 대신 행에서 다시 계산한다 (채점 규칙을 고쳐도 다시 실행할 필요가 없게)
    difficulty = {q.question_id: q.difficulty for q in load_questions() + load_questions(TRICK_SET)}
    for r in runs:
        for row in r["rows"]:  # 난이도 칸이 생기기 전 실행도 같은 기준으로 나눠 보게 채운다
            row.setdefault("difficulty", difficulty.get(row["question_id"]))
        r["summary"] = summarize(r["rows"])
    datasets = {r["params"].get("dataset", "handoff-2026-11-24") for r in runs}
    if len(datasets) > 1:
        print(
            f"> 주의: 데이터셋이 다른 실행이 섞여 있습니다 {sorted(datasets)}. "
            "옛 실행(R0 · R1 · R2)은 발췌 대조 규칙도 달라 수치를 바로 비교하지 마세요.\n"
        )
    ks = {r["params"]["top_k"] for r in runs}
    if len(ks) != 1:
        raise SystemExit(f"top_k 가 다른 실행끼리는 비교하지 않습니다: {ks}")
    k = ks.pop()
    keys = [
        ("청크 수", lambda s, r: r["params"]["n_chunks"]),
        ("검색어", lambda s, r: r["params"]["search_query"]),
        ("정답 섹션이 색인에 있음", lambda s, r: f"{s['gold_in_index']}/{s['n_answerable']}"),
        (f"섹션 Hit@{k}", lambda s, r: s["hit_section_at_k"]),
        (f"발췌 Hit@{k}", lambda s, r: s["hit_excerpt_at_k"]),
        (f"섹션 재현율@{k}", lambda s, r: s["section_recall_mean"]),
        (f"발췌 재현율@{k}", lambda s, r: s["excerpt_recall_mean"]),
        (f"상위 {k}개 맥락 글자 수 평균", lambda s, r: s["context_chars_mean"]),
        ("정답 충족 (LLM 판정)", lambda s, r: s["answer_correct"]),
        ("판정 실패(분모 제외)", lambda s, r: s["judge_missing"]),
        ("근거 없음 대응", lambda s, r: s["refusal_correct"]),
        ("인용이 정답 섹션", lambda s, r: s["cited_gold_rate"]),
        ("평균 응답 ms", lambda s, r: s["latency_total_ms_mean"]),
        ("중앙 응답 ms", lambda s, r: s["latency_total_ms_median"]),
        ("평균 입력 토큰", lambda s, r: s["tokens_input_mean"]),
        ("상태", lambda s, r: s["status"]),
        ("실패 단계", lambda s, r: s["failure_types"]),
        ("함정 문항 정답", lambda s, r: f"{s['trick']['rows']}" if s["trick"]["n"] else "-"),
    ]
    lines = ["| 지표 | " + " | ".join(r["run_id"] for r in runs) + " |"]
    lines.append("|---" * (len(runs) + 1) + "|")
    for name, f in keys:
        lines.append(f"| {name} | " + " | ".join(str(f(r["summary"], r)) for r in runs) + " |")
    lines.append("")
    for group in runs[0]["summary"]["by_group"]:
        ns = "/".join(str(r["summary"]["by_group"][group]["n"]) for r in runs)
        lines.append(f"**{group}** (n={ns})")
        lines.append("| 지표 | " + " | ".join(r["run_id"] for r in runs) + " |")
        lines.append("|---" * (len(runs) + 1) + "|")
        for metric in (
            "hit_section_at_k",
            "hit_excerpt_at_k",
            "section_recall_mean",
            "excerpt_recall_mean",
            "answer_correct",
        ):
            vals = [str(r["summary"]["by_group"][group][metric]) for r in runs]
            lines.append(f"| {metric} | " + " | ".join(vals) + " |")
        lines.append("")
    lines.append("| 문항 | " + " | ".join(r["run_id"] for r in runs) + " |")
    lines.append("|---" * (len(runs) + 1) + "|")
    for i, row in enumerate(runs[0]["rows"]):
        cells = []
        for r in runs:
            x = r["rows"][i] if i < len(r["rows"]) else {}
            if x.get("question_id") != row["question_id"]:
                raise SystemExit(f"{r['run_id']}: 문항 순서가 다릅니다 ({row['question_id']})")
            ok = answer_correct(x)
            cells.append(
                f"섹션{x['section_recall']:.2f} 발췌{x['excerpt_recall']:.2f} "
                f"정답{'?' if ok is None else int(ok)}"
                + (f" {x['failure_type']}" if x.get("failure_type") not in (None, "정답") else "")
            )
        lines.append(f"| {row['question_id']} | " + " | ".join(cells) + " |")
    print("\n".join(lines))


RUN_COLUMNS = (
    "run_id",
    "question_id",
    "difficulty",
    "answerable",
    "needs_extra_docs",
    "status",
    "answer_correct",
    "failure_type",
    "gold_in_index",
    "gold_rank",
    "hit_section",
    "hit_excerpt",
    "section_recall",
    "excerpt_recall",
    "cited_gold",
    "context_chars",
    "judge_facts",
    "judge_note",
    "latency_total_ms",
    "tokens_input",
    "tokens_output",
    "search_query",
    "rewrite_failed",
)
CANDIDATE_COLUMNS = (
    "run_id",
    "question_id",
    "rank",
    "chunk_id",
    "doc_id",
    "sections",
    "vector_score",
    "used_in_model_input",
    "cited_in_answer",
    "gold_section",
    "gold_excerpt",
)


def export_rows(scores: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """채점 파일 하나를 문항 표(runs) · 후보 표(candidates) 두 개로 펼친다 (옛 팀 레포의 두 표 방식)."""
    rid = scores["run_id"]
    runs, cands = [], []
    for r in scores["rows"]:
        runs.append(
            {
                **{k: r.get(k) for k in RUN_COLUMNS if k in r},
                "run_id": rid,
                "answer_correct": answer_correct(r),
                # 채점자가 핵심 사실마다 남긴 맞음(1)/틀림(0)과 이유 한 줄
                "judge_facts": ",".join(str(int(f)) for f in r["judge"].get("facts") or []),
                "judge_note": r["judge"].get("note") or r["judge"].get("judge_error", ""),
                "latency_total_ms": r["latency_ms"].get("total"),
                "tokens_input": r["tokens"].get("input"),
                "tokens_output": r["tokens"].get("output"),
            }
        )
        for c in r.get("candidates", []):
            cands.append(
                {
                    **c,
                    "run_id": rid,
                    "question_id": r["question_id"],
                    "sections": " ".join(c["sections"]),
                }
            )
    return runs, cands


def cmd_export(args: argparse.Namespace) -> None:
    import csv

    for rid in args.run_ids:
        scores = json.loads((RESULTS_DIR / f"{rid}.scores.json").read_text(encoding="utf-8"))
        runs, cands = export_rows(scores)
        if not cands:
            print(f"{rid}: 후보 기록이 없는 옛 실행이라 candidates.csv 는 비어 있습니다")
        for name, rows, cols in (
            ("runs", runs, RUN_COLUMNS),
            ("candidates", cands, CANDIDATE_COLUMNS),
        ):
            path = RESULTS_DIR / f"{rid}.{name}.csv"
            # utf-8-sig: 엑셀에서 한글이 깨지지 않게
            with path.open("w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
                w.writeheader()
                w.writerows(rows)
            print(f"저장: {path.name} ({len(rows)}행)")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="청킹 실험 (R0 · R1 · R2)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("stats", "index", "run"):
        p = sub.add_parser(name)
        p.add_argument("variant", choices=RUN_VARIANTS)
        p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
        p.add_argument(
            "--overlap", action="store_true", help="앞 조각의 마지막 문장 1개를 붙인다 (R1 · R2)"
        )
        if name == "run":
            p.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
            p.add_argument("--limit", type=int, default=0, help="앞에서부터 N 문항만 (시험용)")
            p.add_argument("--date", required=True, help="run_id 에 들어갈 날짜 (예: 2026-11-25)")
            p.add_argument("--trial", action="store_true", help="run_id 끝에 -try (git 에서 제외)")
            p.add_argument(
                "--queries",
                nargs="+",
                help="translate 가 만든 영문 검색어 파일 (여러 개면 합친다. 없으면 한국어로 검색)",
            )
            p.add_argument(
                "--depth", type=int, default=DEFAULT_DEPTH, help="검색 깊이 (순위 밀림 판정용)"
            )
            p.add_argument("--trick", action="store_true", help="함정 문항도 함께 (따로 집계)")
    tp = sub.add_parser("translate")
    tp.add_argument("--date", required=True)
    tt = sub.add_parser("translate-team")
    tt.add_argument("--date", required=True)
    tt.add_argument("--trick", action="store_true", help="함정 문항만 따로 번역 (-trick 파일)")
    rp = sub.add_parser("report")
    rp.add_argument("run_ids", nargs="+")
    ep = sub.add_parser("export", help="runs.csv · candidates.csv 두 표로 내보내기")
    ep.add_argument("run_ids", nargs="+")
    args = parser.parse_args(argv)
    commands = {
        "stats": cmd_stats,
        "index": cmd_index,
        "run": cmd_run,
        "translate": cmd_translate,
        "translate-team": cmd_translate_team,
        "report": cmd_report,
        "export": cmd_export,
    }
    commands[args.cmd](args)


if __name__ == "__main__":
    main()
