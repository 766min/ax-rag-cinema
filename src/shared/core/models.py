# 데이터 모양 (architecture.md §3 · §4.2 · §8 · §9). API 응답 형식은 schemas.py 에 둔다
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from shared.core.schemas import AnswerStatus, RetrievalMode, Section


class SourceId(StrEnum):
    """문서 출처 (§3 출처 칸 source_id)"""

    FANDOM = "fandom"  # MCU 팬덤 위키
    WIKIPEDIA_EN = "wikipedia_en"  # 영어 Wikipedia
    BRAVE = "brave"  # Brave 웹 검색 결과


class AliasKind(StrEnum):
    """별칭이 가리키는 대상 종류"""

    WORK = "work"  # 작품
    CHARACTER = "character"  # 인물
    ITEM = "item"  # 아이템 (묠니르 등)


class EvalSplit(StrEnum):
    """평가셋 구분 (발제: dev 10 · test 20)"""

    DEV = "dev"
    TEST = "test"


class EvidenceSource(StrEnum):
    """답의 근거가 어디서 왔나 (§9 trace.evidence_source)"""

    DOCUMENTS = "documents"  # 고정 문서 컬렉션
    WEB = "web"  # 웹 검색 결과
    NONE = "none"  # 근거 없음


class Work(BaseModel):
    """작품 또는 시즌 한 개"""

    work_id: str = Field(description="IMDb tconst. 시즌별 시리즈는 tt9140554-S1")
    title_ko: str = Field(description="한국어 제목")
    title_original: str = Field(description="원제")
    format: str = Field(description="형식 (movie, tvSeries, tvMiniSeries 등)")
    year: int | None = Field(None, description="공개 연도")
    season: int | None = Field(None, description="시즌 번호 (시즌별 작품일 때)")
    parent_work_id: str | None = Field(
        None, description="시즌이면 시리즈 전체의 work_id (예: tt9140554)"
    )
    phase: str | None = Field(None, description="MCU Phase (값이 생기면 채움)")
    timeline_order: int | None = Field(None, description="시간순 순서 (값이 생기면 채움)")
    is_canon: bool | None = Field(None, description="MCU 정식 여부 (값이 생기면 채움)")


class Document(BaseModel):
    """수집한 문서 한 개 (팬덤 위키 · Wikipedia · 웹 검색 결과)"""

    doc_id: str = Field(
        description="팬덤 위키 {work_id} · Wikipedia {work_id}-wikipedia · 웹 web-{URL 해시 12자}"
    )
    work_id: str | None = Field(None, description="연결된 작품 (웹 결과는 없을 수 있음)")
    source_id: SourceId = Field(description="출처")
    source_url: str = Field(description="원문 주소")
    license: str | None = Field(
        None, description="출처가 적은 라이선스 그대로 (예: CC BY-SA 4.0 / GFDL)"
    )
    revid: int | None = Field(None, description="원문 판 번호 (다시 받아도 같은 글이 되게 고정)")
    fetched_at: datetime | None = Field(None, description="수집 시각")
    doc_type: str = Field(
        description="문서 종류만 (film, season, character, web 등). 출처는 source_id 가 맡는다"
    )


class Chunk(BaseModel):
    """검색 단위 한 개. chunk_id 가 답변의 인용이자 평가 정답 키다"""

    chunk_id: str = Field(
        description="{doc_id}-{section}-p{두 자리}. 카드는 {work_id}-card · {work_id}-connection"
    )
    doc_id: str = Field(description="속한 문서 (카드는 work_id)")
    work_id: str | None = Field(None, description="속한 작품")
    source_id: SourceId = Field(description="출처 (한 출처만 골라 검색할 때 쓴다)")
    section: Section = Field(description="청크 종류")
    position: int = Field(description="문서 안에서 몇 번째 청크인지 (1부터)")
    total: int = Field(description="문서의 전체 청크 수")
    text: str = Field(description="원문 그대로 (머리말 없음)")
    header: str = Field(
        "", description="검색용 머리말 (영어). 임베딩 · BM25 는 header + text 를 본다"
    )
    title_ko: str | None = Field(None, description="한국어 제목 (출처 표시용)")
    title_original: str | None = Field(None, description="원제 (출처 표시용)")
    source_url: str = Field(description="원문 주소")
    revid: int | None = Field(None, description="원문 판 번호")
    entities: list[str] = Field(
        default_factory=list, description="청크에 나오는 인물 · 조직 · 장소"
    )
    flags: list[str] = Field(
        default_factory=list, description="표시 (post_credit, alt_cut, unverified 등)"
    )
    content_hash: str = Field(
        "",
        description="chunk_id · header · text 를 이은 SHA-256. 같으면 다시 임베딩하지 않는다 (§4.2)",
    )

    @model_validator(mode="after")
    def _check_ids(self) -> "Chunk":
        """chunk_id 가 규칙(§3)과 어긋나면 만들 때 바로 막는다. 평가 정답 키가 조용히 틀어지는 것을 막기 위해서다"""
        if self.section in (Section.CARD, Section.CONNECTION):
            expected = f"{self.doc_id}-{self.section}"
        else:
            expected = f"{self.doc_id}-{self.section}-p{self.position:02d}"
        if self.chunk_id != expected:
            raise ValueError(f"chunk_id 가 규칙과 다릅니다: {self.chunk_id} (기대값 {expected})")
        if not 1 <= self.position <= self.total:
            raise ValueError(
                f"position 은 1 이상 total({self.total}) 이하여야 합니다: {self.position}"
            )
        return self


class Alias(BaseModel):
    """한국어 이름 한 개. 질문의 한국어 이름 뒤에 영어 정식 이름을 덧붙일 때 쓴다"""

    alias_ko: str = Field(description="사용자가 쓰는 한국어 이름 (예: 아이언맨, 엔드게임)")
    canonical_en: str = Field(description="영어 정식 이름 (예: Iron Man, Avengers: Endgame)")
    kind: AliasKind = Field(description="가리키는 대상 종류")
    target_id: str | None = Field(None, description="가리키는 대상 ID (작품이면 work_id)")


class EvalItem(BaseModel):
    """평가 문항 한 개"""

    question_id: str = Field(description="문항 ID (예: Q001)")
    question: str = Field(description="질문 (한국어)")
    answerable: bool = Field(description="문서에 근거가 있는 문항인가 (False 면 근거 없음 문항)")
    expected_answer: str | None = Field(None, description="기준 답안")
    key_facts: list[str] = Field(default_factory=list, description="채점 핵심 사실")
    difficulty: str | None = Field(None, description="난이도 (초보 · 중수 · 고수)")
    split: EvalSplit | None = Field(None, description="dev 또는 test (정해지기 전에는 비움)")
    gold_chunk_ids: list[str] = Field(
        default_factory=list,
        description="허용 근거 청크 ID. 두 출처에 같은 사실이 있으면 둘 다 적는다 (§8)",
    )


class Trace(BaseModel):
    """요청 하나의 호출 기록 (§9). 화면과 실행 기록이 같은 값을 쓴다"""

    query_ko: str = Field(description="받은 질문")
    query_normalized: str = Field(description="이름 사전 치환 뒤의 질문")
    query_en: str = Field("", description="변환된 영어 질의 (리라이트를 안 쓰는 구성에서는 빈 값)")
    rewrite_failed: bool = Field(False, description="영어 변환이 실패해 원질문으로 검색했는가")
    retrieval_mode: RetrievalMode = Field(description="검색 구성")
    evidence_source: EvidenceSource = Field(description="답의 근거가 어디서 왔나")
    web_called: bool = Field(False, description="이번 요청에서 Brave 를 불렀는가")
    web_cache_hit: bool = Field(False, description="전에 넣어 둔 웹 보강분으로 답했는가")
    web_failed: bool = Field(False, description="Brave 호출이 실패했는가")
    index_version: str = Field(description="답할 때의 색인 버전 (§4.2)")


class RunRecord(BaseModel):
    """평가 문항 하나를 돌린 기록 (results/ 의 JSONL 한 줄). 구성 · 색인 버전은 trace 에 있다"""

    run_id: str = Field(description="실행 묶음 ID (예: 2026-11-25-minji-C)")
    question_id: str = Field(description="평가 문항 ID")
    status: AnswerStatus = Field(description="처리 상태")
    retrieved_chunk_ids: list[str] = Field(
        default_factory=list, description="답변에 쓰인 청크 ID (순위 순)"
    )
    answer: str | None = Field(None, description="생성된 답변")
    latency_ms: dict[str, int] = Field(
        default_factory=dict, description="단계별 시간 (rewrite, retrieve, rerank, generate, total)"
    )
    tokens: dict[str, int] = Field(default_factory=dict, description="토큰 수 (input, output)")
    embedding_model: str = Field(description="임베딩 모델 이름")
    llm_model: str = Field(description="답변 생성 모델 이름")
    params: dict[str, Any] = Field(
        default_factory=dict, description="각자 바꿔 본 설정 (예: {'min_chars': 300, 'top_k': 10})"
    )
    trace: Trace = Field(description="호출 기록 (§9)")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="기록 시각 (UTC)",
    )
