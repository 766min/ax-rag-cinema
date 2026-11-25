"""답변 생성과 인용 검증 (architecture §7).

- 상위 청크에 번호를 붙여 문맥으로 넣고, 모델은 답과 '인용한 번호'를 구조화 출력으로 돌려준다
- 후보에 없는 번호는 버린다. 남은 인용이 없으면 근거 없음(no_evidence)이다
"""

from dataclasses import dataclass, field

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from shared.core.schemas import AnswerStatus

PROMPT_VERSION = "p1"
NO_EVIDENCE_MESSAGE = "제공된 문서에서 질문에 답할 근거를 찾지 못했습니다."

SYSTEM_PROMPT = """당신은 마블 시네마틱 유니버스(MCU) 영화 정보를 안내하는 어시스턴트입니다.
반드시 아래 [문맥]에 있는 사실만 근거로 한국어로 답하세요.

[규칙]
1. 문맥에 근거가 없으면 추측하지 말고 answer 에 "{no_evidence}" 라고 쓰고 cited_numbers 는 빈 목록으로 두세요.
2. 사실을 말한 문장 끝에 근거 문맥 번호를 [1], [2] 처럼 적으세요.
3. 실제로 근거로 쓴 문맥 번호만 cited_numbers 에 넣으세요.
4. 문맥은 영어입니다. 인물 · 작품 이름은 한국어로 옮기되 필요하면 영어를 괄호로 덧붙이세요.

[문맥]
{context}
"""


class CitedAnswer(BaseModel):
    """모델의 구조화 출력"""

    answer: str = Field(description="문맥만 근거로 쓴 한국어 답변 (문장 끝에 [1] 같은 인용 표기)")
    cited_numbers: list[int] = Field(default_factory=list, description="실제 인용한 문맥 번호")


@dataclass
class AnswerResult:
    status: AnswerStatus
    answer: str
    cited_chunk_ids: list[str] = field(default_factory=list)


def format_context(contexts: list[tuple[str, str]]) -> str:
    """(chunk_id, 본문) 목록 → '[1] ...' 번호 붙인 문맥."""
    return "\n\n---\n\n".join(f"[{i}] {text}" for i, (_, text) in enumerate(contexts, start=1))


def resolve(result: CitedAnswer, chunk_ids: list[str]) -> AnswerResult:
    """인용 번호 → chunk_id. 범위 밖 번호는 버리고, 남은 인용이 없으면 근거 없음으로 바꾼다."""
    cited: list[str] = []
    for number in result.cited_numbers:
        if 1 <= number <= len(chunk_ids) and chunk_ids[number - 1] not in cited:
            cited.append(chunk_ids[number - 1])
    if not cited:
        return AnswerResult(AnswerStatus.NO_EVIDENCE, NO_EVIDENCE_MESSAGE)
    return AnswerResult(AnswerStatus.ANSWERED, result.answer, cited)


def generate_answer(
    question: str, contexts: list[tuple[str, str]], llm: BaseChatModel
) -> AnswerResult:
    """문맥으로 답한다. 문맥이 없으면 모델을 부르지 않는다. 모델 호출 실패는 예외로 올린다."""
    if not contexts:
        return AnswerResult(AnswerStatus.NO_EVIDENCE, NO_EVIDENCE_MESSAGE)
    prompt = ChatPromptTemplate.from_messages([("system", SYSTEM_PROMPT), ("human", "{question}")])
    chain = (prompt | llm.with_structured_output(CitedAnswer)).with_retry(stop_after_attempt=2)
    output = chain.invoke(
        {
            "context": format_context(contexts),
            "question": question,
            "no_evidence": NO_EVIDENCE_MESSAGE,
        }
    )
    if not isinstance(output, CitedAnswer):
        raise TypeError(f"구조화 출력이 CitedAnswer 가 아닙니다: {type(output)}")
    return resolve(output, [chunk_id for chunk_id, _ in contexts])


# --- V5 서비스 확인: 명석님 번역 경로(service.movie_qa)에 넣을 영어 초안 생성기 ---------------

PROMPT_VERSION_EN = "p1-en"
SYSTEM_PROMPT_EN = """You answer questions about the Marvel Cinematic Universe (MCU).
Use only the facts in [Context]. Answer in English.

[Rules]
1. If the context has no evidence, write "{no_evidence}" as the answer and leave cited_numbers empty.
2. End each factual sentence with the context number, like [1] or [2].
3. Put only the numbers you actually used in cited_numbers.

[Context]
{context}
"""
NO_EVIDENCE_MESSAGE_EN = "The provided documents do not contain evidence to answer this question."


def generate_answer_en(
    question_en: str, contexts: list[tuple[str, str]], llm: BaseChatModel
) -> AnswerResult:
    """generate_answer 와 같은 규칙으로 영어 답을 만든다 (프롬프트만 영어). 인용 검증도 같다."""
    if not contexts:
        return AnswerResult(AnswerStatus.NO_EVIDENCE, NO_EVIDENCE_MESSAGE_EN)
    prompt = ChatPromptTemplate.from_messages(
        [("system", SYSTEM_PROMPT_EN), ("human", "{question}")]
    )
    chain = (prompt | llm.with_structured_output(CitedAnswer)).with_retry(stop_after_attempt=2)
    output = chain.invoke(
        {
            "context": format_context(contexts),
            "question": question_en,
            "no_evidence": NO_EVIDENCE_MESSAGE_EN,
        }
    )
    if not isinstance(output, CitedAnswer):
        raise TypeError(f"구조화 출력이 CitedAnswer 가 아닙니다: {type(output)}")
    result = resolve(output, [chunk_id for chunk_id, _ in contexts])
    if result.status == AnswerStatus.NO_EVIDENCE:
        return AnswerResult(AnswerStatus.NO_EVIDENCE, NO_EVIDENCE_MESSAGE_EN)
    return result
