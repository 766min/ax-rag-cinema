"""색인 · 검색(A: 벡터) · 답변 생성 · LLM 채점. 공통 부품(vector_store, timing)을 그대로 쓴다."""

import functools
from typing import Any, cast

from langchain_core.documents import Document
from langchain_core.language_models import BaseChatModel
from langchain_postgres import PGVector
from pydantic import BaseModel, Field

from experiments.chunking.chunker import ExpChunk
from experiments.chunking.data import Question
from shared.core.timing import measure_time

# shared.core.vector_store 는 import 하는 순간 .env 를 읽는다 (config.Settings).
# CI 처럼 .env 가 없는 곳에서도 순수 함수(normalize_query, row_id) 테스트가 돌도록 쓰는 곳에서만 불러온다.

PROMPT_VERSION = "p1"
JUDGE_VERSION = "j1"
NO_ANSWER_TEXT = "제공된 문서에서 답을 찾을 수 없습니다."


# ---------- 이름 사전 치환 (problem.md §9.2 공통 단계, 규칙 기반) ----------


def normalize_query(question: str, aliases: list[dict[str, Any]]) -> str:
    """질문 속 한국어 작품 이름 뒤에 영어 정식 이름을 덧붙인다. 긴 이름부터, 겹치지 않게."""
    pairs = sorted(
        {(a["alias_ko"], a["canonical_en"]) for a in aliases if a.get("alias_ko")},
        key=lambda p: -len(p[0]),
    )
    taken: list[tuple[int, int]] = []
    inserts: list[tuple[int, str]] = []
    for ko, en in pairs:
        start = question.find(ko)
        while start != -1:
            end = start + len(ko)
            if not any(s < end and start < e for s, e in taken):
                taken.append((start, end))
                inserts.append((end, f" ({en})"))
            start = question.find(ko, end)
    out = question
    for pos, text in sorted(inserts, reverse=True):
        out = out[:pos] + text + out[pos:]
    return out


# ---------- 색인 ----------


@functools.cache
def _store(collection: str) -> PGVector:
    """컬렉션마다 한 번만 만든다. 매번 만들면 DB 연결 · 확장 확인이 검색 시간에 섞인다."""
    from shared.core.vector_store import get_vector_store

    return cast(PGVector, get_vector_store(collection))


def row_id(collection: str, chunk_id: str) -> str:
    """DB 행 ID. langchain_pg_embedding.id 는 컬렉션과 상관없이 전체에서 하나라서
    컬렉션 이름을 붙이지 않으면 R1 과 R2 처럼 같은 청크를 가진 컬렉션끼리 행을 뺏는다."""
    return f"{collection}:{chunk_id}"


def build_index(chunks: list[ExpChunk], collection: str, batch: int = 200) -> None:
    """컬렉션을 지우고 처음부터 다시 만든다 (실험마다 다른 컬렉션 이름을 쓴다)."""
    store = _store(collection)
    store.delete_collection()
    store.create_collection()
    for i in range(0, len(chunks), batch):
        part = chunks[i : i + batch]
        store.add_documents(
            [
                Document(page_content=c.embed_text(), metadata={"chunk_id": c.chunk_id})
                for c in part
            ],
            ids=[row_id(collection, c.chunk_id) for c in part],
        )


def indexed_ids(collection: str) -> set[str]:
    """DB 에 실제로 들어 있는 청크 ID (색인이 청크 묶음과 같은지 실행 전에 대조할 때 쓴다).

    행 ID 가 규칙(row_id)과 다른 행이 있으면 '__bad_row__' 로 돌려줘서 대조가 실패하게 한다.
    """
    from shared.core.vector_store import get_raw_db_connection

    conn = get_raw_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT e.id, e.cmetadata->>'chunk_id' FROM langchain_pg_embedding e "
            "JOIN langchain_pg_collection c ON e.collection_id = c.uuid WHERE c.name = %s",
            (collection,),
        )
        out = set()
        for rid, cid in cur.fetchall():
            out.add(cid if rid == row_id(collection, cid) else f"__bad_row__:{rid}")
        return out
    finally:
        conn.close()


# ---------- 검색어 영문화 (회의 11/24 "한글 질의 영문화 → 벡터 검색") ----------

TRANSLATE_VERSION = "t1"
TRANSLATE_PROMPT = """Rewrite the Korean question as one concise English search query for English
Wikipedia articles about the Marvel Cinematic Universe (MCU).
- Use official English names for characters, films, series and objects (e.g. 바키 -> Bucky Barnes).
- Keep every entity and constraint in the question. Do not answer the question.
- Output only the English query, nothing else."""


@measure_time("rewrite")
def translate_query(llm: BaseChatModel, question: str) -> str:
    out = llm.invoke([("system", TRANSLATE_PROMPT), ("human", question)])
    # .text 는 문자열 content 와 블록 리스트 content 를 모두 문자열로 바꿔 준다 (langchain-core 1.x)
    query = str(out.text).strip().strip('"').strip()
    if not query:
        raise ValueError("영문 검색어가 비었습니다")
    return query


# ---------- 검색 · 생성 ----------


@measure_time("retrieve")
def retrieve(collection: str, query: str, k: int) -> list[tuple[str, float]]:
    """(chunk_id, 코사인 유사도) 를 유사도 높은 순으로 돌려준다."""
    results = _store(collection).similarity_search_with_score(query, k=k)
    return [(doc.metadata["chunk_id"], 1.0 - float(distance)) for doc, distance in results]


class AnswerOut(BaseModel):
    answer: str = Field(description="한국어 답변")
    cited: list[int] = Field(description="답변의 근거가 된 [번호] 목록")
    has_evidence: bool = Field(description="제공된 근거만으로 답할 수 있으면 true")


SYSTEM_PROMPT = f"""너는 마블 시네마틱 유니버스(MCU) 질문에 답하는 도우미다.
규칙:
1. 아래 [번호] 근거에 적힌 내용만으로 한국어로 답한다. 근거에 없는 내용은 추측하거나 덧붙이지 않는다.
2. 답에 쓴 근거의 번호를 cited 에 모두 적는다.
3. 근거만으로 답할 수 없으면 has_evidence 를 false 로 하고, answer 는 "{NO_ANSWER_TEXT}" 로 시작해 이유를 한 줄 덧붙인다.
4. 근거는 영어 원문이다. 고유명사는 한국어로 쓰되 처음 한 번은 영어를 괄호로 함께 쓴다."""


def format_context(chunks: list[ExpChunk]) -> str:
    return "\n\n".join(f"[{i}] {c.header}\n{c.text}" for i, c in enumerate(chunks, start=1))


@measure_time("generate")
def generate(llm: BaseChatModel, question: str, context: list[ExpChunk]) -> AnswerOut:
    structured = llm.with_structured_output(AnswerOut)
    user = f"근거:\n{format_context(context)}\n\n질문: {question}"
    out = structured.invoke([("system", SYSTEM_PROMPT), ("human", user)])
    return cast(AnswerOut, out)


# ---------- LLM 채점 (사람 검토와 구분해 기록한다) ----------


class FactJudge(BaseModel):
    facts: list[bool] = Field(description="핵심 사실마다 답변이 충족하면 true (주어진 순서대로)")
    contradiction: bool = Field(description="답변에 기준 답안과 모순되는 내용이 있으면 true")
    note: str = Field(description="판단 이유 한 줄")


class RefusalJudge(BaseModel):
    declined: bool = Field(description="답변이 정보 부족을 안내했으면 true")
    unsupported_claim: bool = Field(description="근거 없이 사실을 단정했으면 true")
    note: str = Field(description="판단 이유 한 줄")


JUDGE_SYSTEM = """너는 RAG 답변 채점자다. 표현이 달라도 뜻이 같으면 충족으로 본다.
기준 답안에 없는 내용이 답변에 더 있어도, 기준 답안과 모순되지 않으면 감점하지 않는다."""


def judge_answer(llm: BaseChatModel, q: Question, answer: str) -> dict[str, Any]:
    """정답 충족(답변 가능 문항) 또는 근거 없음 대응(근거 없음 문항)을 판정한다."""
    if q.answerable:
        facts = "\n".join(f"{i}. {f}" for i, f in enumerate(q.key_facts, start=1))
        user = (
            f"질문: {q.question}\n기준 답안: {q.expected_answer}\n핵심 사실:\n{facts}\n\n"
            f"평가할 답변:\n{answer}\n\n핵심 사실 {len(q.key_facts)}개 각각을 충족했는지 순서대로 판정하라."
        )
        fj = cast(
            FactJudge,
            llm.with_structured_output(FactJudge).invoke(
                [("system", JUDGE_SYSTEM), ("human", user)]
            ),
        )
        if len(fj.facts) != len(q.key_facts):
            return {
                "correct": None,
                "judge_error": f"사실 수 불일치 {len(fj.facts)}",
                **fj.model_dump(),
            }
        correct = all(fj.facts) and not fj.contradiction
        return {"correct": correct, **fj.model_dump()}
    user = (
        f"질문: {q.question}\n이 질문은 문서에 근거가 없는 문항이다. 기준 처리: {q.expected_answer}\n\n"
        f"평가할 답변:\n{answer}\n\n정보 부족을 안내했는지, 근거 없이 단정했는지 판정하라."
    )
    rj = cast(
        RefusalJudge,
        llm.with_structured_output(RefusalJudge).invoke(
            [("system", JUDGE_SYSTEM), ("human", user)]
        ),
    )
    return {"correct": rj.declined and not rj.unsupported_claim, **rj.model_dump()}
