# 데이터 모양(src/shared/core/models.py) 테스트: 청크 ID 규칙과 값 검사가 지켜지는지 확인한다
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from shared.core.models import (
    Candidate,
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

# 레포 루트(pyproject.toml 이 있는 폴더)에서 찾는다. 테스트 파일을 옮겨도 경로가 틀어져 조용히 건너뛰지 않게
REPO_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "pyproject.toml").exists())
QUESTION_SET = REPO_ROOT / "question_set.jsonl"


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


def make_candidates() -> list[Candidate]:
    """C 구성의 후보 3개 (재정렬로 2위가 1위로 올라옴)"""
    return [
        Candidate(
            chunk_id="tt4154796-wikipedia-plot-p07",
            rank=1,
            rank_before_rerank=2,
            vector_score=0.81,
            bm25_score=12.4,
            rrf_score=0.032,
            rerank_score=0.97,
        ),
        Candidate(
            chunk_id="tt4154796-wikipedia-plot-p06",
            rank=2,
            rank_before_rerank=1,
            vector_score=0.84,
            bm25_score=13.1,
            rrf_score=0.033,
            rerank_score=0.42,
        ),
        Candidate(
            chunk_id="tt0371746-wikipedia-plot-p01",
            rank=3,
            rank_before_rerank=3,
            vector_score=0.62,
            bm25_score=4.0,
            rrf_score=0.016,
            rerank_score=0.05,
        ),
    ]


def make_record(**overrides: object) -> RunRecord:
    """정상 답변 기록 하나 (1위 청크를 인용)"""
    fields: dict[str, object] = {
        "run_id": "2026-11-25-test-C",
        "question_id": "Q001",
        "status": "answered",
        "candidates": make_candidates(),
        "cited_chunk_ids": ["tt4154796-wikipedia-plot-p07"],
        "answer": "네, 토니 스타크는 엔드게임에서 스냅 뒤 숨집니다 [tt4154796-wikipedia-plot-p07].",
        "latency_ms": {"retrieve": 410, "rerank": 820, "generate": 2780, "total": 4010},
        "tokens": {"input": 1520, "output": 210},
        "embedding_model": "text-embedding-3-large",
        "llm_model": "gpt-6-luna",
        "prompt_version": "p1",
        "params": {"min_chars": 150, "top_k": 20},
        "trace": make_trace(),
    }
    fields.update(overrides)
    return RunRecord(**fields)


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
    # 웹 결과는 작품과 이어지지 않을 수 있다. 나머지는 doc_id 에서 출처 꼬리를 뗀 것이 work_id 다
    work_id = None if source_id == SourceId.BRAVE else doc_id.removesuffix("-wikipedia")
    chunk = make_chunk(
        chunk_id=chunk_id,
        doc_id=doc_id,
        work_id=work_id,
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


# ---------- 출처별 doc_id 규칙 (§3, PR #11 리뷰) ----------


@pytest.mark.parametrize(
    "overrides",
    [
        # Wikipedia 출처인데 팬덤 위키 모양 ID (팬덤 위키 청크와 ID 가 겹친다)
        {"chunk_id": "tt0371746-plot-p01", "doc_id": "tt0371746"},
        # 팬덤 위키 출처인데 Wikipedia 모양 ID
        {"source_id": SourceId.FANDOM},
        # Brave 출처인데 작품 ID
        {
            "chunk_id": "tt0371746-web-p01",
            "doc_id": "tt0371746",
            "source_id": SourceId.BRAVE,
            "section": Section.WEB,
        },
        # Brave 해시가 12자 16진수가 아님
        {
            "chunk_id": "web-0123ABC-web-p01",
            "doc_id": "web-0123ABC",
            "source_id": SourceId.BRAVE,
            "section": Section.WEB,
        },
        # doc_id 와 work_id 가 다른 작품
        {"work_id": "tt9999999"},
        # web 청크인데 Brave 출처가 아님
        {"chunk_id": "tt0371746-wikipedia-web-p01", "section": Section.WEB},
        # Brave 출처인데 web 청크가 아님
        {
            "chunk_id": "web-0123456789ab-plot-p01",
            "doc_id": "web-0123456789ab",
            "source_id": SourceId.BRAVE,
        },
        # 카드인데 doc_id 가 작품 ID 가 아님
        {"chunk_id": "tt0371746-wikipedia-card", "section": Section.CARD, "total": 1},
        # 카드인데 doc_id 가 다른 작품
        {
            "chunk_id": "tt9999999-card",
            "doc_id": "tt9999999",
            "section": Section.CARD,
            "total": 1,
        },
    ],
)
def test_chunk_doc_id_must_match_source(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        make_chunk(**overrides)


@pytest.mark.parametrize(
    ("doc_id", "source_id", "work_id"),
    [
        ("tt0371746", SourceId.FANDOM, "tt0371746"),
        ("tt9140554-S1", SourceId.FANDOM, "tt9140554-S1"),
        ("tt0371746-wikipedia", SourceId.WIKIPEDIA_EN, "tt0371746"),
        ("web-0123456789ab", SourceId.BRAVE, None),
        ("web-0123456789ab", SourceId.BRAVE, "tt0371746"),  # 작품과 이어진 웹 결과
        ("wp-52234178", SourceId.WIKIPEDIA_EN, None),  # 작품이 없는 문서 (인물 · 세계관)
    ],
)
def test_document_doc_id_follows_source(
    doc_id: str, source_id: SourceId, work_id: str | None
) -> None:
    document = Document(
        doc_id=doc_id, work_id=work_id, source_id=source_id, source_url="u", doc_type="film"
    )
    assert document.doc_id == doc_id


@pytest.mark.parametrize(
    ("doc_id", "source_id", "work_id"),
    [
        ("tt0371746", SourceId.WIKIPEDIA_EN, "tt0371746"),  # 꼬리 빠짐
        ("tt0371746-wikipedia", SourceId.FANDOM, "tt0371746"),  # 출처 바뀜
        ("tt0371746", SourceId.BRAVE, None),  # 웹인데 작품 ID
        ("tt0371746-wikipedia", SourceId.WIKIPEDIA_EN, "tt9999999"),  # 다른 작품
        ("Q192724", SourceId.WIKIPEDIA_EN, None),  # IMDb tconst 가 아님
        # 작품 문서인데 wp- 를 씀 (작품 문서는 {work_id}-wikipedia 여야 한 ID 로 모인다)
        ("wp-52234178", SourceId.WIKIPEDIA_EN, "tt0371746"),
        ("wp-52234178", SourceId.FANDOM, None),  # wp- 는 Wikipedia 전용
        ("wp-Tony_Stark", SourceId.WIKIPEDIA_EN, None),  # pageid 는 숫자
        ("wp-", SourceId.WIKIPEDIA_EN, None),  # pageid 빠짐
    ],
)
def test_document_doc_id_mismatch_is_rejected(
    doc_id: str, source_id: SourceId, work_id: str | None
) -> None:
    with pytest.raises(ValidationError):
        Document(
            doc_id=doc_id, work_id=work_id, source_id=source_id, source_url="u", doc_type="film"
        )


def test_chunk_from_page_without_work() -> None:
    # 인물 문서(작품 없음)의 소개 문단도 청크가 될 수 있어야 한다 (평가셋 Q007 등의 근거)
    chunk = make_chunk(
        chunk_id="wp-28466133-intro-p01",
        doc_id="wp-28466133",
        work_id=None,
        section=Section.INTRO,
        total=3,
    )
    assert chunk.chunk_id == "wp-28466133-intro-p01"


def test_chunk_from_page_without_work_rejects_work_id() -> None:
    # wp- 문서에 work_id 를 붙이면 작품 문서({work_id}-wikipedia)와 같은 글이 두 ID 로 갈린다
    with pytest.raises(ValidationError):
        make_chunk(
            chunk_id="wp-28466133-intro-p01",
            doc_id="wp-28466133",
            work_id="tt0371746",
            section=Section.INTRO,
        )


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


# ---------- 평가 기록 (architecture.md §7 · §8) ----------


@pytest.mark.parametrize("missing", ["trace", "prompt_version", "embedding_model", "llm_model"])
def test_run_record_requires_versions(missing: str) -> None:
    # 구성 · 버전이 빠진 기록은 내일 비교표에서 어느 실행인지 가릴 수 없다
    fields = make_record().model_dump()
    del fields[missing]
    with pytest.raises(ValidationError):
        RunRecord(**fields)


@pytest.mark.parametrize(
    ("status", "cited", "error"),
    [
        ("answered", ["tt4154796-wikipedia-plot-p07"], None),
        ("no_evidence", [], None),
        ("retrieval_error", [], "DB 연결 시간 초과"),
        ("generation_error", [], "LLM 429"),
    ],
)
def test_run_record_accepts_each_status(status: str, cited: list[str], error: str | None) -> None:
    record = make_record(status=status, cited_chunk_ids=cited, error=error)
    assert record.status == status


@pytest.mark.parametrize(
    "overrides",
    [
        {"cited_chunk_ids": ["tt9999999-wikipedia-plot-p01"]},  # 후보에 없는 청크 인용 (§7)
        {"cited_chunk_ids": []},  # answered 인데 인용 없음 → no_evidence 여야 함
        {"status": "no_evidence"},  # no_evidence 인데 인용 있음
        {"status": "generation_error", "cited_chunk_ids": []},  # 실패인데 이유 없음
        {"error": "이유"},  # 성공인데 실패 이유가 있음
    ],
)
def test_run_record_rejects_status_mismatch(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        make_record(**overrides)


@pytest.mark.parametrize("ranks", [[1, 3], [1, 1], [0, 1], [2, 3]])
def test_run_record_rejects_broken_ranks(ranks: list[int]) -> None:
    candidates = [
        {"chunk_id": f"tt0371746-wikipedia-plot-p{n:02d}", "rank": r}
        for n, r in enumerate(ranks, start=1)
    ]
    with pytest.raises(ValidationError):
        make_record(candidates=candidates, cited_chunk_ids=[candidates[0]["chunk_id"]])


def test_run_record_rejects_duplicate_candidates() -> None:
    same = "tt0371746-wikipedia-plot-p01"
    candidates = [{"chunk_id": same, "rank": 1}, {"chunk_id": same, "rank": 2}]
    with pytest.raises(ValidationError):
        make_record(candidates=candidates, cited_chunk_ids=[same])


def test_top_chunk_ids_follow_final_rank() -> None:
    # 후보가 어떤 순서로 저장돼도 Hit@k 는 최종 순위로 잰다
    record = make_record(candidates=list(reversed(make_candidates())))
    assert record.top_chunk_ids(2) == [
        "tt4154796-wikipedia-plot-p07",
        "tt4154796-wikipedia-plot-p06",
    ]
    assert len(record.top_chunk_ids()) == 3  # 후보가 5개보다 적으면 있는 만큼


# ---------- 정답 키와 색인 버전 (#11 리뷰: 청킹이 바뀌면 문단 번호가 밀린다) ----------


def make_item(**overrides: object) -> EvalItem:
    """make_record() 의 색인(test-index) 기준으로 정답 키를 적은 문항"""
    fields: dict[str, object] = {
        "question_id": "Q001",
        "question": "토니 스타크는 엔드게임에서 죽어?",
        "answerable": True,
        "gold_chunk_ids": ["tt4154796-wikipedia-plot-p06"],
        "gold_index_version": "test-index",
    }
    fields.update(overrides)
    return EvalItem.model_validate(fields)


@pytest.mark.parametrize(
    ("gold", "k", "hit"),
    [
        (["tt4154796-wikipedia-plot-p06"], 5, True),  # 2위
        (["tt4154796-wikipedia-plot-p06"], 1, False),  # 1위 안에는 없음
        (["tt9999999-wikipedia-plot-p01"], 5, False),  # 후보에 없음
        # 두 출처 중 하나만 찾아와도 맞음 (§8)
        (["tt4154796-plot-p03", "tt4154796-wikipedia-plot-p07"], 1, True),
    ],
)
def test_hit_at_k(gold: list[str], k: int, hit: bool) -> None:
    assert make_item(gold_chunk_ids=gold).hit_at(make_record(), k) is hit


def test_hit_at_rejects_other_index_version() -> None:
    # 다른 색인(예: 청크 크기를 바꿔 다시 만든 색인)에서 잰 기록은 정답 키와 번호가 안 맞을 수 있다
    record = make_record(trace=make_trace(index_version="other-index"))
    with pytest.raises(ValueError, match="색인"):
        make_item().hit_at(record)


def test_hit_at_without_gold_index_version_skips_version_check() -> None:
    # 기준 버전을 아직 안 적은 문항은 버전 검사 없이 잰다
    record = make_record(trace=make_trace(index_version="other-index"))
    assert make_item(gold_index_version=None).hit_at(record) is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"question_id": "Q002"},  # 다른 문항의 기록
        {"gold_chunk_ids": []},  # 정답 키를 아직 안 적음
    ],
)
def test_hit_at_rejects_unusable_item(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        make_item(**overrides).hit_at(make_record())


def test_no_evidence_item_cannot_have_gold() -> None:
    # 근거 없음 문항에 정답 키가 있으면 문항이 잘못 적힌 것이다
    with pytest.raises(ValidationError):
        make_item(answerable=False)


def test_hit_at_rejects_no_evidence_item() -> None:
    # 근거 없음 문항은 Hit@k 대상이 아니다 (올바른 거절로 채점한다)
    item = make_item(answerable=False, gold_chunk_ids=[], gold_index_version=None)
    # "정답 키가 비었다" 가 아니라 "근거 없음 문항이라 재지 않는다" 로 알려야 고칠 곳을 안다
    with pytest.raises(ValueError, match="근거 없음"):
        item.hit_at(make_record())


# ---------- 원본 ID (handoff 의 cnt_ · doc_ 번호를 남겨 평가셋 근거와 잇는다) ----------


def make_work(**overrides: object) -> Work:
    fields: dict[str, object] = {
        "work_id": "tt0371746",
        "title_ko": "아이언맨",
        "title_original": "Iron Man",
        "format": "movie",
    }
    fields.update(overrides)
    return Work.model_validate(fields)


def make_document(**overrides: object) -> Document:
    fields: dict[str, object] = {
        "doc_id": "wp-66902704",
        "source_id": SourceId.WIKIPEDIA_EN,
        "source_url": "https://en.wikipedia.org/w/index.php?oldid=1386856486",
        "doc_type": "universe",
    }
    fields.update(overrides)
    return Document.model_validate(fields)


def test_source_ids_default_empty() -> None:
    # 원본 ID 가 없는 데이터(팬덤 위키 · 웹)도 그대로 만들 수 있어야 한다
    assert make_work().source_ids == {}
    assert make_document().source_ids == {}
    assert make_chunk().source_ids == {}


def test_source_ids_kept_on_each_model() -> None:
    # handoff works.jsonl 의 source_ids 를 그대로 받는다 (tt 가 없는 작품은 imdb_tconst 가 None)
    work_ids = {
        "imdb_tconst": "tt0371746",
        "team_content_id": "cnt_8b4a3e3c1b2d4f5a",
        "wikipedia_document_id": None,
    }
    doc_ids = {"team_document_id": "doc_2bf22c03fce35efd", "wikidata_qid": "Q131448287"}
    chunk_ids = {"team_chunk_id": "doc_0119b33dcbf45f7b-plot-p01"}
    assert make_work(source_ids=work_ids).source_ids == work_ids
    assert make_document(source_ids=doc_ids).source_ids == doc_ids
    assert make_chunk(source_ids=chunk_ids).source_ids == chunk_ids


@pytest.mark.parametrize("value", [78918930, ["doc_1"], {"x": "y"}])
def test_source_ids_values_must_be_text(value: object) -> None:
    # pageid 같은 숫자도 문자열로 바꿔 넣는다. 섞이면 원본 ID 로 다시 찾을 때 "1" 과 1 이 엇갈린다
    with pytest.raises(ValidationError):
        make_document(source_ids={"pageid": value})


def test_source_ids_do_not_affect_id_rules() -> None:
    # 원본 ID 를 남겨도 우리 ID 규칙 검사는 그대로 돈다 (원본 doc_ 번호를 doc_id 로 쓰면 막힘)
    with pytest.raises(ValidationError):
        make_document(doc_id="doc_2bf22c03fce35efd", source_ids={"team_document_id": "x"})


def test_find_document_by_original_id() -> None:
    # 평가셋 근거는 doc_… 로 적혀 있다. 원본 ID 로 새 doc_id 를 찾을 수 있어야 정답 키를 만든다
    documents = [
        make_document(
            doc_id="wp-66902704", source_ids={"team_document_id": "doc_2bf22c03fce35efd"}
        ),
        make_document(
            doc_id="tt0371746-wikipedia",
            work_id="tt0371746",
            doc_type="film",
            source_ids={"team_document_id": "doc_aaaaaaaaaaaaaaaa"},
        ),
    ]
    by_original = {d.source_ids["team_document_id"]: d.doc_id for d in documents}
    assert by_original["doc_2bf22c03fce35efd"] == "wp-66902704"


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
        source_ids={"team_content_id": "cnt_0119b33dcbf45f7b", "imdb_tconst": None},
    )
    document = Document(
        doc_id="tt0371746-wikipedia",
        work_id="tt0371746",
        source_id=SourceId.WIKIPEDIA_EN,
        source_url="https://en.wikipedia.org/w/index.php?oldid=1386653533",
        license="CC BY-SA 4.0 / GFDL",
        revid=1386653533,
        doc_type="film",
        source_ids={"team_document_id": "doc_aaaaaaaaaaaaaaaa"},
    )
    chunk = make_chunk(source_ids={"team_chunk_id": "doc_aaaaaaaaaaaaaaaa-plot-p01"})
    for model in (work, document, chunk, make_record()):
        assert type(model).model_validate_json(model.model_dump_json()) == model
    # 원본 ID 는 저장했다 읽어도 값 그대로 남아야 한다 (None 도 None 으로)
    assert Work.model_validate_json(work.model_dump_json()).source_ids == {
        "team_content_id": "cnt_0119b33dcbf45f7b",
        "imdb_tconst": None,
    }
    assert Document.model_validate_json(document.model_dump_json()).source_ids == {
        "team_document_id": "doc_aaaaaaaaaaaaaaaa"
    }
    assert Chunk.model_validate_json(chunk.model_dump_json()).source_ids == {
        "team_chunk_id": "doc_aaaaaaaaaaaaaaaa-plot-p01"
    }


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
