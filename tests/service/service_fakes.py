"""서비스 테스트가 같이 쓰는 가짜 함수와 데이터. 검색 · 생성 · 번역 · 웹 검색을 API 없이 흉내 낸다."""

import asyncio
from types import SimpleNamespace
from typing import Any

from retrieve.pipeline import MODES
from service.movie_qa import EnglishDraft, Evidence, answer_movie_question
from shared.core.models import Alias, Candidate, Chunk, SourceId
from shared.core.schemas import Section

ALIASES = [Alias(alias_ko="아이언맨", canonical_en="Iron Man", kind="work", target_id="tt0371746")]
Q = "아이언맨은 누구야?"


def chunk(n: int) -> Chunk:
    """출처 메타데이터(판 · 위치 · 수집 시점 · 라이선스)를 다 갖춘 청크."""
    return Chunk(
        chunk_id=f"tt0371746-wikipedia-plot-p{n:02d}",
        doc_id="tt0371746-wikipedia",
        work_id="tt0371746",
        source_id=SourceId.WIKIPEDIA_EN,
        section=Section.PLOT,
        position=n,
        total=9,
        text=f"Tony Stark fact number {n}.",
        header="[Iron Man · 2008 film]",
        title_ko="아이언맨",
        title_original="Iron Man",
        source_url="https://en.wikipedia.org/w/index.php?oldid=7",
        revid=7,
        source_ids={
            "parent_id": "tt0371746-wikipedia#s1",
            "char_start": str(n * 30),
            "char_end": str(n * 30 + 25),
            "team_section_path": "Plot",
            "fetched_at": "2026-11-23T00:00:00+00:00",
            "license": "CC BY-SA 4.0",
            "overlap_chars": "0",
        },
    )


def ev(n: int, rank: int) -> Evidence:
    c = chunk(n)
    cand = Candidate(chunk_id=c.chunk_id, rank=rank, rank_before_rerank=None, vector_score=0.5,
                     bm25_score=None, rrf_score=None, rerank_score=None)  # fmt: skip
    return Evidence(
        chunk_id=c.chunk_id, text=c.text, source_url=c.source_url, chunk=c, candidate=cand
    )


POOL = [ev(i, r) for r, i in enumerate(range(1, 8), start=1)]  # 후보 7개 (top_k=5 보다 많게)


class Calls:
    """가짜 함수들이 몇 번, 무엇으로 불렸는지 적어 두는 장부."""

    def __init__(self) -> None:
        self.rewrite = 0
        self.search: list[str] = []
        self.generated_with: list[tuple[str, list[str]]] = []
        self.translate = 0


def ports(calls: Calls, *, draft: EnglishDraft | None = None, pool: list[Evidence] | None = None,
          fail: str | None = None) -> dict[str, Any]:  # fmt: skip
    """가짜 리라이트 · 검색 · 생성 · 번역 함수 묶음. fail 에 단계 이름을 주면 그 단계를 실패시킨다."""

    async def query_translator(_s: str, text: str) -> str:
        calls.rewrite += 1
        if fail == "rewrite":
            raise RuntimeError("down")
        return text.replace("[[TERM_0]]은 누구야?", "Who is [[TERM_0]]?")

    async def retrieve(query: str) -> list[Evidence]:
        calls.search.append(query)
        if fail == "retrieve":
            raise TimeoutError("db timeout")
        return list(POOL if pool is None else pool)

    async def generate_en(question: str, evidence: Any) -> EnglishDraft:
        calls.generated_with.append((question, [e.chunk_id for e in evidence]))
        if fail == "generate":
            raise TimeoutError("llm timeout")
        return draft or EnglishDraft(
            answer_en=f"Stark. [{evidence[1].chunk_id}]", cited_chunk_ids=[evidence[1].chunk_id]
        )

    async def answer_translator(_s: str, text: str) -> str:
        calls.translate += 1
        if fail == "translate":
            raise RuntimeError("down")
        return text

    return {"retrieve": retrieve, "generate_en": generate_en, "aliases": ALIASES,
            "query_translator": query_translator, "answer_translator": answer_translator}  # fmt: skip


def ask(calls: Calls, mode: str = "A", **kw: Any) -> dict[str, Any]:
    """가짜 함수들로 질문을 한 번 돌린다. draft · pool · fail 은 ports 로, 나머지는 서비스로 간다."""
    port_kw = {k: kw.pop(k) for k in ("draft", "pool", "fail") if k in kw}
    return asyncio.run(
        answer_movie_question(Q, mode=MODES[mode], top_k=5, **ports(calls, **port_kw), **kw)
    )


class FakeLLM:
    """정해진 답을 돌려주고 받은 프롬프트를 기록하는 LLM."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[Any] = []

    async def ainvoke(self, messages: list[Any]) -> Any:
        self.prompts.append(messages)
        return SimpleNamespace(content=self.reply)


def web_outcome(results: list[Any], failed: bool = False) -> Any:
    """websearch.WebSearchOutcome 모양의 가짜 웹 검색 결과."""
    return SimpleNamespace(results=results, web_called=True, web_cache_hit=False, web_failed=failed)
