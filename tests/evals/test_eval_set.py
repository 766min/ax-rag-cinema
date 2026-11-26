"""공식 평가셋(eval/dev.json · eval/test.json)이 정한 모양을 지키는지 본다.

test.json 은 잠근 뒤 고치지 않는다(architecture.md §8). 실수로 문항이 바뀌거나 빠지면 여기서 잡힌다.
원문 발췌 대조는 공통 데이터셋(data/, git 밖)이 있을 때만 한다.
"""

import json
import re
from pathlib import Path

import pytest

from shared.core.models import EvalItem

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = REPO_ROOT / "eval"
SECTIONS = REPO_ROOT / "data" / "cineverse-dataset-schema" / "sections.jsonl"

TEAM_COLUMNS = [
    "query_id",
    "question",
    "expected_answer",
    "answerable",
    "난이도",
    "채점 핵심 사실",
    "근거 문서·절·리비전",
    "검토 메모",
    "원문 근거 발췌",
]


def load(split: str) -> list[dict]:
    return json.loads((EVAL_DIR / f"{split}.json").read_text(encoding="utf-8"))


def to_item(row: dict) -> EvalItem:
    """tests/shared/test_models.py 의 question_set.jsonl 읽기와 같은 방식"""
    return EvalItem(
        question_id=row["query_id"],
        question=row["question"],
        answerable=row["answerable"],
        expected_answer=row.get("expected_answer"),
        key_facts=[f.strip() for f in (row.get("채점 핵심 사실") or "").split("/") if f.strip()],
        difficulty=row.get("난이도"),
        split=row["split"],
    )


def test_split_sizes() -> None:
    dev, test = load("dev"), load("test")
    assert len(dev) == 11
    assert len(test) == 20
    # 발제 기준: test 는 답변 가능 17 · 근거 없음 3
    assert sum(not r["answerable"] for r in test) == 3
    # 근거 부족 판정은 dev 로만 고치므로(architecture.md §5) dev 에도 근거 없음 문항이 있어야 한다
    assert sum(not r["answerable"] for r in dev) == 1


@pytest.mark.parametrize("split", ["dev", "test"])
def test_rows_load_as_eval_items(split: str) -> None:
    rows = load(split)
    items = [to_item(r) for r in rows]
    assert all(item.split == split for item in items)
    assert list(rows[0])[: len(TEAM_COLUMNS)] == TEAM_COLUMNS  # 팀 question_set.jsonl 칸 순서


def test_ids_unique_across_splits() -> None:
    ids = [r["query_id"] for split in ("dev", "test") for r in load(split)]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("split", ["dev", "test"])
def test_every_row_has_question_facts_and_evidence(split: str) -> None:
    for r in load(split):
        assert re.search(r"[가-힣]", r["question"]), r["query_id"]
        assert r["채점 핵심 사실"].strip(), r["query_id"]
        if r["answerable"]:
            assert r["근거"], r["query_id"]
            for e in r["근거"]:
                assert e["doc_id"] and e["section_index"] is not None and e["발췌"], r["query_id"]
        if r["난이도"] == "함정" and r["answerable"]:
            assert r["전제 오류"], r["query_id"]


@pytest.mark.parametrize("split", ["dev", "test"])
def test_key_facts_at_most_four(split: str) -> None:
    """정답 충족은 핵심 사실을 모두 맞혀야 1 이다. 사실이 많으면 맞는 답도 거의 오답이 된다"""
    for r in load(split):
        facts = [f for f in r["채점 핵심 사실"].split("/") if f.strip()]
        assert len(facts) <= 4, (r["query_id"], len(facts))


# 팀 평가 코드(src/evals/items.py)가 평가셋 글 칸을 읽는 규칙. 이 규칙으로 근거가 다 읽혀야
# 정답 청크(gold_chunk_ids)가 붙는다. 팀 문서 ID(doc_…)만 읽으므로 데이터셋 ID 를 쓰면 근거 0개가 된다
TEAM_EVIDENCE = re.compile(r"\[(\d+)\] .+?\n(doc_[0-9a-f]+) / 섹션 (\d+): .+?\nrevision (\d+)")
TEAM_EXCERPT = re.compile(r"\[(\d+)\] (.+?)(?=\n\n\[\d+\] |\Z)", re.DOTALL)


@pytest.mark.parametrize("split", ["dev", "test"])
def test_evidence_text_readable_by_team_eval_code(split: str) -> None:
    for r in load(split):
        if not r["answerable"]:
            continue
        found = TEAM_EVIDENCE.findall(r["근거 문서·절·리비전"])
        excerpts = dict(TEAM_EXCERPT.findall(r["원문 근거 발췌"]))
        assert len(found) == len(r["근거"]), r["query_id"]
        for (n, doc, section, _), e in zip(found, r["근거"], strict=True):
            assert (doc, int(section)) == (e["team_doc_id"], e["section_index"]), r["query_id"]
            assert excerpts.get(n), r["query_id"]


def section_text(s: dict) -> str:
    """절 본문 + 회차 요약 ('Episodes' 절은 text 가 비고 episode_entries[].summary 에 요약이 있다)"""
    parts = [s.get("text") or ""] + [e.get("summary") or "" for e in s.get("episode_entries") or []]
    return "\n".join(p for p in parts if p)


@pytest.mark.skipif(not SECTIONS.exists(), reason="공통 데이터셋(data/)이 없음")
def test_excerpts_exist_in_sections() -> None:
    def ws(s: str) -> str:
        return re.sub(r"\s+", " ", s).strip()

    texts = {}
    with SECTIONS.open(encoding="utf-8") as f:
        for line in f:
            s = json.loads(line)
            texts[(s["doc_id"], s["section_index"])] = ws(section_text(s))
    for split in ("dev", "test"):
        for r in load(split):
            for e in r["근거"]:
                excerpt = ws(e["발췌"].rstrip("…"))[:60]  # 팀 원본 발췌는 … 로 잘려 있다
                assert excerpt in texts[(e["doc_id"], e["section_index"])], (r["query_id"], e)
