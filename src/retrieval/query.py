"""검색 질의 만들기 (V2 EXP-02 · EXP-03 진단용).

| 방식 | 검색에 쓰는 글 |
|---|---|
| raw | 한국어 원문 그대로 |
| alias | V1 이름 사전 치환 (retrieval.normalize: 이름 바로 뒤에 "(English)") |
| alias_plus | 명석님 tools.translation.normalize_query (작품명 + 인물 5명, 끝에 영어 이름을 덧붙임) |
| rewrite | 명석님 tools.translation.rewrite_movie_query 의 영어 검색어. 실패하면 alias_plus 질의 |
| manual_en | 사람이 옮긴 영어 질의 (experiments/v2/manual_en.jsonl, 정답 · 단서 없이 뜻만 옮김) |

공식 C+리라이트(RetrievalMode.HYBRID_RERANK_REWRITE)와는 다르다. 여기서는 검색 모드를 바꾸지 않고
질의만 바꿔 원인을 본다. 리라이트는 문항마다 한 번 만들어 파일에 두고 모든 레시피에서 같은 값을 쓴다.
"""

import asyncio
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from retrieval.normalize import AliasNormalizer
from shared.core.models import Alias

VARIANTS = ("raw", "alias", "alias_plus", "rewrite", "manual_en")


@dataclass
class QueryPlan:
    """한 문항의 검색 질의 (실제 검색에 쓴 글과 만든 과정)."""

    question_id: str
    variant: str
    query_ko: str
    query_used: str  # 실제 임베딩한 글
    query_en: str = ""  # rewrite · manual_en 의 영어 질의
    rewrite_failed: bool = False
    matched_aliases: list[dict[str, str]] = field(default_factory=list)
    rewrite_meta: dict[str, Any] = field(default_factory=dict)  # 리라이트 시간 · 토큰 · 오류

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_plan(
    variant: str,
    question_id: str,
    question: str,
    aliases: list[Alias],
    manual_en: dict[str, str] | None = None,
    rewrites: dict[str, dict[str, Any]] | None = None,
) -> QueryPlan:
    """rewrite 는 미리 만든 값(rewrites)만 쓴다. 여기서 LLM 을 부르지 않는다."""
    from tools.translation import normalize_query

    if variant == "raw":
        return QueryPlan(question_id, variant, question, question)
    if variant == "alias":
        normalizer = AliasNormalizer(aliases)
        return QueryPlan(
            question_id,
            variant,
            question,
            normalizer.normalize(question),
            matched_aliases=[
                {"alias_ko": ko, "canonical_en": en} for _, ko, en in normalizer.matches(question)
            ],
        )
    if variant == "alias_plus":
        used, matched = normalize_query(question, aliases)
        return QueryPlan(question_id, variant, question, used, matched_aliases=matched)
    if variant == "manual_en":
        if not manual_en or question_id not in manual_en:
            raise ValueError(f"{question_id} 의 manual_en 질의가 없습니다")
        text = manual_en[question_id]
        return QueryPlan(question_id, variant, question, text, query_en=text)
    if variant == "rewrite":
        if not rewrites or question_id not in rewrites:
            raise ValueError(f"{question_id} 의 리라이트가 없습니다. 먼저 만들어 두세요")
        r = rewrites[question_id]
        used = r["query_normalized"] if r["rewrite_failed"] else r["query_en"]
        return QueryPlan(
            question_id,
            variant,
            question,
            used,
            query_en=r["query_en"],
            rewrite_failed=r["rewrite_failed"],
            matched_aliases=r["matched_aliases"],
            rewrite_meta={k: r[k] for k in ("latency_ms", "tokens", "error") if k in r},
        )
    raise ValueError(f"알 수 없는 질의 방식: {variant} (가능: {VARIANTS})")


def make_rewrite(question: str, aliases: list[Alias]) -> dict[str, Any]:
    """명석님 리라이트를 한 번 부른다 (실제 LLM 호출). 실패는 rewrite_failed 로 돌아온다."""
    from evals.recorder import measure
    from tools.translation import rewrite_movie_query

    with measure() as m:
        result = asyncio.run(rewrite_movie_query(question, aliases))
    return {**result, "latency_ms": m.latency_ms, "tokens": m.tokens}


def read_jsonl_map(path: Path, key: str) -> dict[str, dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    return {row[key]: row for row in rows}
