# 데이터 모양(src/shared/core/models.py) 테스트: 청크 ID 규칙과 값 검사가 지켜지는지 확인한다
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from shared.core.models import (
    Chunk,
    Document,
    EvalItem,
    EvidenceSource,
    RunRecord,
    SourceId,
    Trace,
    Work,
)
from shared.core.schemas import Section

QUESTION_SET = Path(__file__).resolve().parents[1] / "question_set.jsonl"


def make_chunk(**overrides: object) -> Chunk:
    """Wikipedia 줄거리 청크 하나 (아이언맨 1번 문단)"""
    fields: dict[str, object] = {
        "chunk_id": "tt0371746-wikipedia-plot-p01",
        "doc_id": "tt0371746-wikipedia",
        "work_id": "tt0371746",
        "source_id": SourceId.WIKIPEDIA_EN,
        "section": Section.PLOT,
        "position": 1,
        "total": 5,
        "text": "Tony Stark, who has inherited the defense contractor Stark Industries from his father...",
        "source_url": "https://en.wikipedia.org/w/index.php?oldid=1386653533",
        "revid": 1386653533,
    }
    fields.update(overrides)
    return Chunk(**fields)


def make_trace(**overrides: object) -> Trace:
    fields: dict[str, object] = {
        "query_ko": "토니 스타크는 엔드게임에서 죽어?",
        "query_normalized": "토니 스타크(Tony Stark)는 엔드게임(Avengers: Endgame)에서 죽어?",
        "retrieval_mode": "hybrid_rerank",
        "evidence_source": EvidenceSource.DOCUMENTS,
        "index_version": "test-index",
    }
    fields.update(overrides)
    return Trace(**fields)


# ---------- 청크 ID 규칙 (architecture.md §3) ----------


@pytest.mark.parametrize(
    ("chunk_id", "doc_id", "source_id", "section", "position", "total"),
    [
        ("tt0371746-plot-p01", "tt0371746", SourceId.FANDOM, Section.PLOT, 1, 10),
        (
            "tt0371746-wikipedia-plot-p05",
            "tt0371746-wikipedia",
            SourceId.WIKIPEDIA_EN,
            Section.PLOT,
            5,
            5,
        ),
        ("tt9140554-S1-plot-p03", "tt9140554-S1", SourceId.FANDOM, Section.PLOT, 3, 8),
        ("tt0371746-card", "tt0371746", SourceId.FANDOM, Section.CARD, 1, 1),
        ("tt0371746-connection", "tt0371746", SourceId.FANDOM, Section.CONNECTION, 1, 1),
        ("web-0123456789ab-web-p01", "web-0123456789ab", SourceId.BRAVE, Section.WEB, 1, 1),
    ],
)
def test_chunk_id_follows_rule(
    chunk_id: str, doc_id: str, source_id: SourceId, section: Section, position: int, total: int
) -> None:
    chunk = make_chunk(
        chunk_id=chunk_id,
        doc_id=doc_id,
        source_id=source_id,
        section=section,
        position=position,
        total=total,
    )
    assert chunk.chunk_id == chunk_id


@pytest.mark.parametrize(
    "overrides",
    [
        {"chunk_id": "tt0371746-wikipedia-plot-p02"},  # 번호가 position 과 다름
        # Wikipedia 청크인데 출처 꼬리가 빠짐 (팬덤 위키 ID 와 겹친다)
        {"chunk_id": "tt0371746-plot-p01"},
        {"chunk_id": "tt0371746-wikipedia-plot-p1"},  # 두 자리가 아님
        {"chunk_id": "tt0371746-wikipedia-intro-p01"},  # section 과 다름
    ],
)
def test_chunk_id_mismatch_is_rejected(overrides: dict[str, object]) -> None:
    # 정답 키가 조용히 틀어지지 않도록, 규칙과 다른 ID 는 만들 때 바로 막는다
    with pytest.raises(ValidationError):
        make_chunk(**overrides)


@pytest.mark.parametrize(("position", "total"), [(0, 5), (6, 5)])
def test_chunk_position_out_of_range_is_rejected(position: int, total: int) -> None:
    with pytest.raises(ValidationError):
        make_chunk(
            chunk_id=f"tt0371746-wikipedia-plot-p{position:02d}", position=position, total=total
        )


def test_fandom_and_wikipedia_ids_do_not_collide() -> None:
    # 같은 영화의 같은 번호 문단이라도 두 출처의 ID 는 달라야 한다 (§3)
    fandom = {f"tt0371746-plot-p{n:02d}" for n in range(1, 11)}
    wikipedia = {
        make_chunk(chunk_id=f"tt0371746-wikipedia-plot-p{n:02d}", position=n).chunk_id
        for n in range(1, 6)
    }
    assert fandom.isdisjoint(wikipedia)


# ---------- 닫힌 값 목록 ----------


def test_source_id_values_match_architecture() -> None:
    assert {s.value for s in SourceId} == {"fandom", "wikipedia_en", "brave"}


def test_unknown_source_id_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_chunk(source_id="wiki")


def test_unknown_split_is_rejected() -> None:
    with pytest.raises(ValidationError):
        EvalItem(question_id="Q999", question="질문", answerable=True, split="train")


@pytest.mark.parametrize("overrides", [{"retrieval_mode": "bm25"}, {"evidence_source": "wiki"}])
def test_trace_rejects_unknown_values(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        make_trace(**overrides)


def test_run_record_requires_trace() -> None:
    # 구성 · 색인 버전은 trace 에만 있으므로 trace 가 빠진 기록은 비교표에 쓸 수 없다
    with pytest.raises(ValidationError):
        RunRecord(
            run_id="r",
            question_id="Q001",
            status="answered",
            embedding_model="text-embedding-3-large",
            llm_model="gpt-6-luna",
        )


# ---------- JSONL 로 저장하고 다시 읽어도 같은가 ----------


def test_models_survive_json_round_trip() -> None:
    work = Work(
        work_id="tt9140554-S1",
        title_ko="로키",
        title_original="Loki",
        format="tvSeries",
        year=2021,
        season=1,
        parent_work_id="tt9140554",
    )
    document = Document(
        doc_id="tt0371746-wikipedia",
        work_id="tt0371746",
        source_id=SourceId.WIKIPEDIA_EN,
        source_url="https://en.wikipedia.org/w/index.php?oldid=1386653533",
        license="CC BY-SA 4.0 / GFDL",
        revid=1386653533,
        doc_type="film",
    )
    record = RunRecord(
        run_id="2026-11-25-test",
        question_id="Q001",
        status="answered",
        retrieved_chunk_ids=["tt0371746-wikipedia-plot-p01"],
        latency_ms={"retrieve": 410, "total": 4010},
        tokens={"input": 1520, "output": 210},
        embedding_model="text-embedding-3-large",
        llm_model="gpt-6-luna",
        params={"min_chars": 150},
        trace=make_trace(),
    )
    for model in (work, document, make_chunk(), record):
        assert type(model).model_validate_json(model.model_dump_json()) == model


# ---------- 저장소의 평가셋이 스키마로 읽히는가 ----------


@pytest.mark.skipif(not QUESTION_SET.exists(), reason="question_set.jsonl 이 없음")
def test_question_set_loads_as_eval_items() -> None:
    rows = [
        json.loads(line)
        for line in QUESTION_SET.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    items = [
        EvalItem(
            question_id=row["query_id"],
            question=row["question"],
            answerable=row["answerable"],
            expected_answer=row.get("expected_answer"),
            key_facts=[
                fact.strip() for fact in row.get("채점 핵심 사실", "").split("/") if fact.strip()
            ],
            difficulty=row.get("난이도"),
        )
        for row in rows
    ]
    assert len(items) == len(rows) > 0
    assert len({item.question_id for item in items}) == len(items)  # 문항 ID 중복 없음
