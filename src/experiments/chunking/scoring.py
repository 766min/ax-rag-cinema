"""검색 채점. 청킹 방식이 달라도 같은 기준으로 재도록 '원문 섹션'과 '원문 발췌'로 본다."""

import re
from collections.abc import Iterable, Sequence

from experiments.chunking.chunker import ExpChunk
from experiments.chunking.data import Question, SectionRef

_WS = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^0-9a-z]")
PROBE_LEN = 40  # 발췌에서 떼어 낼 확인용 조각 길이 (영문자 · 숫자만 센 글자 수)
# 이보다 많은 섹션에 나오는 조각은 상투 문장으로 보고 뺀다.
# (Q016 의 'niverse (MCU) media franchise, based on the Marvel' 은 인물 문서 20곳의 첫 문장에 나온다.
#  2~4곳에 나오는 조각은 영화 줄거리 문장이 인물 문서에도 그대로 실린 경우라 정당한 근거로 둔다)
MAX_PROBE_SECTIONS = 5

ProbeTable = dict[int, list[str]]  # 근거 번호(1부터) -> 확인용 조각


def normalize(text: str) -> str:
    """공백만 하나로 맞춘다 (글 비교용)."""
    return _WS.sub(" ", text).strip()


def letters(text: str) -> str:
    """영문자 · 숫자만 남긴 소문자. 발췌 대조용이다.

    새 데이터는 되살린 글자(Darkhold's)가 있고 평가셋 발췌는 되살리기 전 글자(Darkholds)라,
    아포스트로피 · 줄표 · 공백을 빼고 비교해야 같은 문장을 같다고 본다.
    """
    return _NON_ALNUM.sub("", text.lower())


def excerpt_probes(excerpt: str, probe_len: int = PROBE_LEN) -> list[str]:
    """발췌문에서 확인용 조각을 뽑는다 (영문자 · 숫자만 남긴 꼴).

    발췌는 원문을 앞뒤로 잘라 둔 창이라 첫 단어가 잘려 있고 끝에 '…' 가 붙는다.
    그래서 첫 공백 이후부터 '…' 앞까지를 쓰고, 앞 · 가운데 · 끝 세 곳에서 조각을 뗀다.
    어느 한 조각이라도 청크에 있으면 그 청크가 발췌 문장을 담고 있다고 본다 (청크 경계가 발췌를 자를 수 있어서).
    """
    body = normalize(excerpt.split("…")[0].split("...")[0])
    first_space = body.find(" ")
    if first_space != -1:
        body = body[first_space + 1 :]
    body = letters(body)
    if len(body) <= probe_len:
        return [body] if len(body) >= 16 else []
    mid = (len(body) - probe_len) // 2
    starts = sorted({0, mid, len(body) - probe_len})
    return [body[s : s + probe_len] for s in starts]


def build_probe_table(
    question: Question, section_texts: Sequence[str], max_sections: int = MAX_PROBE_SECTIONS
) -> ProbeTable:
    """문항의 발췌마다 확인용 조각을 만들고, 원문 전체에서 상투 문장인 조각은 뺀다.

    section_texts 는 전체 문서 섹션 본문이다 (색인 범위와 상관없이 같은 표를 쓴다).
    """
    flat = [letters(s) for s in section_texts]
    table: ProbeTable = {}
    for i, excerpt in question.excerpts.items():
        kept = []
        for p in excerpt_probes(excerpt):
            if sum(1 for s in flat if p in s) <= max_sections:
                kept.append(p)
        table[i] = kept
    return table


def chunk_has_excerpt(chunk_text: str, probes: Sequence[str]) -> bool:
    text = letters(chunk_text)
    return any(p in text for p in probes)


def section_hit(top: Sequence[ExpChunk], gold: Iterable[SectionRef]) -> bool:
    gold_set = set(gold)
    return any(ref in gold_set for c in top for ref in c.refs)


def section_recall(top: Sequence[ExpChunk], gold: Iterable[SectionRef]) -> float:
    """정답 섹션 중 상위 청크가 덮은 비율 (근거가 여럿인 문항에서 '얼마나 모았나')"""
    gold_set = set(gold)
    if not gold_set:
        return 0.0
    found = {ref for c in top for ref in c.refs} & gold_set
    return len(found) / len(gold_set)


def excerpts_found(top: Sequence[ExpChunk], probes: ProbeTable) -> list[int]:
    """상위 청크 안에 그대로 들어 있는 발췌의 번호"""
    return [i for i, ps in probes.items() if ps and any(chunk_has_excerpt(c.text, ps) for c in top)]


def excerpt_hit(top: Sequence[ExpChunk], probes: ProbeTable) -> bool:
    """상위 청크 안에 정답 발췌 문장이 하나라도 그대로 들어 있나"""
    return bool(excerpts_found(top, probes))


def excerpt_recall(top: Sequence[ExpChunk], probes: ProbeTable) -> float:
    """정답 발췌 중 상위 청크에 그대로 들어 있는 비율 (확인 조각이 남은 발췌만 분모)"""
    usable = [i for i, ps in probes.items() if ps]
    if not usable:
        return 0.0
    return len(excerpts_found(top, probes)) / len(usable)


def gold_in_index(index_refs: set[SectionRef], question: Question) -> bool:
    """정답 섹션이 색인에 하나라도 들어 있나 (없으면 검색이 아무리 좋아도 0점)"""
    return any(r in index_refs for r in question.gold_refs)


def cited_gold(cited: Sequence[ExpChunk], gold: Iterable[SectionRef]) -> bool:
    """답변이 인용한 청크 중 하나라도 정답 섹션에서 나왔나 (근거 적합의 기계적 대리 지표)"""
    return section_hit(cited, gold)


# ---------- 실패 단계 분류 (옛 팀 레포 docs/ERRORS.md 의 분류를 우리 지표에 맞춤) ----------

FAILURE_TYPES = (
    "정답",
    "잘못된 인용",  # 답은 맞았는데 인용한 청크가 정답 섹션이 아님 (모델 사전지식 의심)
    "색인에 근거 없음",  # 정답 섹션이 아예 색인에 없음 (청킹 범위 문제)
    "검색 누락",  # 정답 섹션이 검색 깊이(상위 20) 안에 없음
    "순위 밀림",  # 정답 섹션이 상위 20 안에는 있지만 모델에 넣은 상위 k 밖
    "근거 받고 거절",  # 정답 섹션을 모델에 넣었는데 '찾을 수 없음'
    "생성 오류",  # 정답 섹션을 모델에 넣었고 답했는데 틀림
    "근거 없음 대응 실패",  # 근거 없음 문항인데 단정함
    "호출 오류",
)


def gold_rank(ranked: Sequence[ExpChunk], gold: Iterable[SectionRef]) -> int | None:
    """정답 섹션에서 나온 청크가 처음 나오는 순위 (1부터). 없으면 None"""
    gold_set = set(gold)
    for i, c in enumerate(ranked, start=1):
        if any(r in gold_set for r in c.refs):
            return i
    return None


def classify_failure(
    *,
    answerable: bool,
    status: str,
    correct: bool | None,
    in_index: bool,
    first_gold_rank: int | None,
    top_k: int,
    cited_gold_section: bool,
) -> str:
    if status in ("retrieval_error", "generation_error"):
        return "호출 오류"
    if not answerable:
        return "정답" if correct else "근거 없음 대응 실패"
    if correct:
        return "정답" if cited_gold_section else "잘못된 인용"
    if not in_index:
        return "색인에 근거 없음"
    if first_gold_rank is None:
        return "검색 누락"
    if first_gold_rank > top_k:
        return "순위 밀림"
    if status == "no_evidence":
        return "근거 받고 거절"
    return "생성 오류"
