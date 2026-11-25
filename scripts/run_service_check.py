"""V5 서비스 경로 동작 확인 (공식 검색 품질 평가 아님). Cross-Encoder 가 필요해 겹쳐 설치해 실행한다.

uv run --with sentence-transformers==6.3.0 --with torch==2.15.0 --with transformers==5.24.1 \
    --with huggingface_hub==2.4.0 python scripts/run_service_check.py --results-version v5

- 내부 색인: CK05 원문 컬렉션 (C). V4 에서 D 가 C 보다 낮았고 D 는 LLM 가공 글이 섞여 원문 보존 · 비용 면에서 C 를 고름
- Brave: experiments/v5/scenarios.json 의 brave 값. real = 실제 호출 (내부 근거가 없을 때만, 전체 상한 --max-real),
  mock_* = 가짜 응답 (오류 · 타임아웃 · 관계없는 결과), off = 웹을 부르지 않음
- 키: settings.BRAVE_API_KEY (팀 이름), 없으면 BRAVE_SEARCH_API 를 읽는다. 값은 출력 · 저장하지 않는다
- 저장: 웹 결과 원문 · 스니펫 · URL · 웹 근거 답 글은 남기지 않는다 (ServiceRecord.redacted, WEB_STORE=false)
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx  # noqa: E402

from etl.chunking import RECIPES  # noqa: E402
from etl.dataset import (  # noqa: E402
    DATASET_DIR,
    PROJECT_ROOT,
    collection_for,
    dataset_fingerprint,
    index_version,
)
from etl.load import load_corpus, meta_values  # noqa: E402
from evals.provenance import results_dir, run_info  # noqa: E402
from evals.recorder import measure  # noqa: E402
from evals.search_eval import http_calls, summarize_calls  # noqa: E402
from generation.service_flow import (  # noqa: E402
    InternalSearch,
    WebOptions,
    run_korean_path,
    run_translate_path,
)
from retrieval.normalize import load_aliases  # noqa: E402
from retrieval.pipeline import Retriever, SearchConfig  # noqa: E402
from retrieval.refine import CrossEncoderReranker  # noqa: E402
from shared.core.config import settings  # noqa: E402
from shared.core.vector_store import get_llm, get_raw_db_connection, get_vector_store  # noqa: E402

SCENARIOS = PROJECT_ROOT / "experiments" / "v5" / "scenarios.json"


def _mock_client(kind: str) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if kind == "mock_500":
            return httpx.Response(500)
        if kind == "mock_timeout":
            raise httpx.ReadTimeout("mock timeout", request=request)
        return httpx.Response(  # mock_irrelevant
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": "Weather",
                            "url": "https://example.com/weather",
                            "description": "It will be sunny in Paris tomorrow.",
                        }
                    ]
                }
            },
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _fail(system: str, text: str) -> str:
    raise TimeoutError("시나리오: 리라이트 실패")


def _brave_key() -> tuple[str | None, str]:
    """(키, 읽은 환경변수 이름). 값은 밖으로 내보내지 않는다."""
    if settings.BRAVE_API_KEY.strip():
        return settings.BRAVE_API_KEY, "BRAVE_API_KEY"
    if os.getenv("BRAVE_SEARCH_API", "").strip():
        return os.environ["BRAVE_SEARCH_API"], "BRAVE_SEARCH_API (팀 이름 아님, 호환으로 읽음)"
    return None, "없음"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-version", required=True)
    parser.add_argument("--run-id", help="생략하면 {날짜-시각}")
    parser.add_argument("--only", help="쉼표 구분 시나리오 ID")
    parser.add_argument("--max-real", type=int, default=6, help="Brave 실제 호출 상한")
    args = parser.parse_args()
    if settings.WEB_STORE:
        raise SystemExit("WEB_STORE=true 입니다. 이 확인은 저장하지 않는 조건(false)에서만 돌린다")

    scenarios = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    if args.only:
        scenarios = [s for s in scenarios if s["id"] in args.only.split(",")]
    out = (
        results_dir(args.results_version)
        / "service_check"
        / (args.run_id or f"{datetime.now():%Y%m%d-%H%M%S}")
    )
    out.mkdir(parents=True, exist_ok=False)
    info = run_info(args.results_version)
    api_key, key_source = _brave_key()

    recipe = RECIPES["CK05"]
    fingerprint = dataset_fingerprint()
    collection = collection_for(settings.MOVIE_COLLECTION, recipe, fingerprint)
    conn = get_raw_db_connection()
    try:
        if meta_values(conn, collection, "index_version") != {index_version(recipe, fingerprint)}:
            raise SystemExit(f"{collection} 의 색인 버전이 다릅니다")
        corpus = load_corpus(conn, collection)
    finally:
        conn.close()
    texts = {cid: text for cid, (text, _) in corpus.items()}
    urls = {cid: str(meta.get("source_url")) for cid, (_, meta) in corpus.items()}
    reranker = CrossEncoderReranker()
    search = InternalSearch(
        Retriever(get_vector_store(collection), texts),
        reranker,
        SearchConfig(top_k=5, pool=20),
        urls,
    )
    aliases = load_aliases(DATASET_DIR / "aliases.jsonl")
    llm = get_llm()

    real_used = 0
    rows: list[dict[str, Any]] = []
    with (out / "records.jsonl").open("x", encoding="utf-8") as f:
        for sc in scenarios:
            for path in sc["paths"]:
                brave = sc["brave"]
                if brave == "real" and (api_key is None or real_used >= args.max_real):
                    brave = "off"  # 키가 없거나 상한에 닿으면 웹을 끈다 (기록에 남김)
                web = WebOptions(
                    enabled=brave != "off",
                    api_key=api_key if brave == "real" else "mock-key",
                    client=_mock_client(brave) if brave.startswith("mock") else None,
                )
                runner = run_korean_path if path == "korean_direct" else run_translate_path
                with http_calls() as calls, measure() as m:
                    record = await runner(
                        sc["question"],
                        aliases=aliases,
                        search=search,
                        llm=llm,
                        web=web,
                        translator=_fail if sc.get("translator") == "fail" else None,
                    )
                if brave == "real" and record.web_called:
                    real_used += 1
                    time.sleep(1.1)  # Brave 요청 간격 (무료 요금제 초당 1회)
                calls_summary = summarize_calls(calls)
                # 저장 전에 Brave 요청 주소(질의 포함)는 지운다
                calls_summary["non_2xx"] = [
                    {**c, "url": "brave" if "brave.com" in c["url"] else c["url"]}
                    for c in calls_summary["non_2xx"]
                ]
                row = {
                    "scenario": sc["id"],
                    "case": sc["case"],
                    "brave_mode": brave,
                    **record.redacted(),
                    "tokens": m.tokens,
                    "api_calls": calls_summary,
                }
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                rows.append(row)
                print(
                    f"{sc['id']} {path}: {row['status']} via {row['evidence_source']} "
                    f"web_called={row['web_called']} failed={row['web_failed']} "
                    f"results={row['web_results']} rewrite_failed={row['rewrite_failed']} "
                    f"{row['latency_ms'].get('total')}ms"
                )

    with (out / "summary.json").open("x", encoding="utf-8") as f:
        json.dump(
            {
                "purpose": "서비스 경로 동작 확인 (공식 검색 품질 지표 아님)",
                "internal_index": {"recipe": recipe.version, "collection": collection},
                "reranker": {"name": reranker.name, "device": reranker.device},
                "brave": {
                    "key_env": key_source,
                    "real_calls": real_used,
                    "max_real": args.max_real,
                    "web_store": settings.WEB_STORE,
                },
                "llm_model": settings.LLM_MODEL,
                "dataset": fingerprint,
                "run_info": info,
                "rows": [
                    {
                        k: r[k]
                        for k in (
                            "scenario",
                            "path",
                            "brave_mode",
                            "status",
                            "internal_status",
                            "evidence_source",
                            "web_called",
                            "web_failed",
                            "web_error",
                            "web_results",
                            "rewrite_failed",
                        )
                    }
                    for r in rows
                ],
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(
        f"→ {out.relative_to(PROJECT_ROOT)}  (Brave 키 출처: {key_source}, 실제 호출 {real_used}회)"
    )


if __name__ == "__main__":
    asyncio.run(main())
