"""청킹 레시피 (V1: CK01~CK04, V2: CK05 문장 단위 · CK06 절 종류별)와 청크 · 원문 위치 대조.

CK01~CK04 의 규칙과 version 은 V1 그대로다 (V1 컬렉션 · 정답 키와 맞아야 한다).

어떤 레시피든 절(section) 하나씩 따로 자르므로 절 · 문서 경계를 넘지 않는다.
V0(etl/handoff.py, b0b76e1)에서 옮겼다. 자르는 규칙은 그대로이고 version 의 앞머리만 뺐다
(데이터셋은 index_version 의 @체크섬 이 구분한다).
"""

import re
from dataclasses import dataclass
from functools import cache
from typing import Literal

from langchain_text_splitters import RecursiveCharacterTextSplitter


@dataclass(frozen=True)
class ChunkRecipe:
    """청킹 방식 하나. 바꾸면 청크 번호가 밀리므로 version 도 달라진다 (정답 키 gold_index_version 과 맞춤)."""

    recipe_id: str
    method: Literal["paragraph", "rcts", "sentence", "whole", "section"]
    size: int = 0  # rcts · sentence: 최대 글자 수 (length_function=len)
    overlap: int = 0  # rcts: chunk_overlap · sentence: 너무 긴 문장을 RCTS 로 자를 때의 overlap
    merge_min: int = 150  # paragraph: 이보다 짧은 문단은 다음 문단과 합친다
    min_chars: int = 10  # 공통: 이보다 짧은 최종 조각은 버린다 (표 잔해)
    separators: tuple[str, ...] = ("\n", ". ", " ", "")  # rcts: 문단 → 문장 → 단어 → 글자
    keep_separator: Literal["start", "end"] = "end"
    overlap_sentences: int = 0  # sentence: 앞 청크의 마지막 N문장을 다음 청크 앞에 다시 넣는다
    parts: tuple[tuple[str, "ChunkRecipe"], ...] = ()  # section: 절 종류(Section 값) → 레시피

    @property
    def version(self) -> str:
        base = self.recipe_id
        if self.method == "paragraph":
            return f"{base}-para-m{self.merge_min}-min{self.min_chars}"
        if self.method == "sentence":
            return (
                f"{base}-sent{self.size}-os{self.overlap_sentences}-fb{self.overlap}"
                f"-min{self.min_chars}"
            )
        if self.method == "whole":
            return f"{base}-whole-min{self.min_chars}"
        if self.method == "section":
            rule = ",".join(f"{name}={r.recipe_id}" for name, r in self.parts)
            return f"{base}-section[{rule}]-min{self.min_chars}"
        return f"{base}-rcts{self.size}-o{self.overlap}-min{self.min_chars}"

    def split(self, text: str, section: str | None = None) -> tuple[list[str], list[str]]:
        """(남긴 조각, min_chars 미만이라 버린 조각). section 방식만 절 종류(section)를 쓴다."""
        if self.method == "section":
            recipe = dict(self.parts).get(section or "")
            if recipe is None:
                raise ValueError(f"{self.recipe_id} 에 절 종류 {section!r} 의 규칙이 없습니다")
            return recipe.split(text, section)
        if self.method == "paragraph":
            raw = _paragraphs(text, self.merge_min)
        elif self.method == "sentence":
            raw = _sentence_chunks(text, self)
        elif self.method == "whole":
            raw = [text.strip()]
        else:
            raw = [piece.strip() for piece in _rcts(self).split_text(text)]
        kept = [p for p in raw if len(p) >= self.min_chars]
        dropped = [p for p in raw if p and len(p) < self.min_chars]
        return kept, dropped

    def settings(self) -> dict[str, object]:
        """결과 파일에 남길 설정값 (방식과 상관없는 칸은 None)."""
        rcts = self.method == "rcts"
        sentence = self.method == "sentence"
        return {
            "method": self.method,
            "chunk_size": self.size if rcts or sentence else None,
            "chunk_overlap": self.overlap if rcts else None,
            "merge_min": self.merge_min if self.method == "paragraph" else None,
            "min_chars": self.min_chars,
            "separators": list(self.separators) if rcts else None,
            "keep_separator": self.keep_separator if rcts else None,
            "overlap_sentences": self.overlap_sentences if sentence else None,
            "long_sentence_fallback": (f"RCTS {self.size}/{self.overlap}" if sentence else None),
            "parts": {name: r.version for name, r in self.parts} or None,
            "length_function": "len (문자 수)",
        }


_CK01 = ChunkRecipe("CK01", "paragraph")
_CK04 = ChunkRecipe("CK04", "rcts", size=1500, overlap=100)
_WHOLE = ChunkRecipe("WHOLE", "whole")

RECIPES: dict[str, ChunkRecipe] = {
    r.recipe_id: r
    for r in (
        _CK01,
        ChunkRecipe("CK02", "rcts", size=500, overlap=100),
        ChunkRecipe("CK03", "rcts", size=1000, overlap=100),
        _CK04,
        # V2: 문장 경계에서만 끊고 1문장 겹침. 1000자를 넘는 한 문장은 RCTS 1000/100 으로 자른다
        ChunkRecipe("CK05", "sentence", size=1000, overlap=100, overlap_sentences=1),
        # V2: 절 종류별로 다른 방식 (synopsis · intro 절 전체, plot 문단, 인물 서사 · 세계관 CK04)
        ChunkRecipe(
            "CK06",
            "section",
            parts=(
                ("synopsis", _WHOLE),
                ("intro", _WHOLE),
                ("plot", _CK01),
                ("biography", _CK04),
                ("lore", _CK04),
            ),
        ),
    )
}
DEFAULT_RECIPE = RECIPES["CK03"]

# 문장 끝 뒤에서 끊지 않는 말: 호칭 · 약어 (소문자로 비교). 한 글자 이니셜(J. · S.H.I.E.L.D.)은 따로 본다
_ABBREVIATIONS = frozenset(
    "mr mrs ms dr st jr sr prof gen col lt sgt capt cmdr adm gov sen rep pres vs etc "
    "inc ltd co corp vol fig approx dept mt ft ave "
    "jan feb mar apr jun jul aug sep sept oct nov dec".split()
)
_BOUNDARY = re.compile(r"[.!?…][\"'”’)\]]*\s+")
_INITIALS = re.compile(r"(?:\b[A-Za-z]\.)+$")  # 마침표 바로 앞이 한 글자 이니셜 묶음 (J. R. · U.S.)


def split_sentences(paragraph: str) -> list[str]:
    """영어 문단 → 문장. 다음 글자가 대문자 · 숫자 · 따옴표 · 괄호로 시작할 때만 끊는다.

    약어(Dr. · vs.)와 한 글자 이니셜(J. · U.S. · S.H.I.E.L.D.) 뒤에서는 끊지 않는다. 소수점(3.5)은
    마침표 뒤에 공백이 없어서 끊기지 않는다. 애매하면 끊지 않는 쪽을 택한다 (문장이 길어질 뿐 잘리지 않음).
    """
    sentences: list[str] = []
    start = 0
    for match in _BOUNDARY.finditer(paragraph):
        end = match.end()
        following = paragraph[end : end + 1]
        if not following or not (
            following.isupper() or following.isdigit() or following in "\"'“‘("
        ):
            continue
        before = paragraph[start : match.start() + 1]
        word = before.split()[-1].rstrip(".").lower() if before.split() else ""
        if word in _ABBREVIATIONS or _INITIALS.search(before):
            continue
        sentences.append(paragraph[start:end].strip())
        start = end
    tail = paragraph[start:].strip()
    if tail:
        sentences.append(tail)
    return [s for s in sentences if s]


def _sentence_chunks(text: str, recipe: ChunkRecipe) -> list[str]:
    """문장을 최대 크기까지 채운다. 새 청크는 앞 청크의 마지막 N문장으로 시작한다 (넘치면 겹침 생략).

    문단(줄바꿈)은 문장 경계로 보고 청크 안에서는 줄바꿈으로 잇는다. 최대 크기를 넘는 한 문장은 RCTS 로
    잘라 따로 내보낸다 (그 앞뒤로는 겹침이 없다).
    """
    units: list[tuple[str, str]] = []  # (문장, 앞 문장과 잇는 글자)
    for line in text.split("\n"):
        for i, sentence in enumerate(split_sentences(line.strip())):
            units.append((sentence, " " if i else "\n"))

    fallback = ChunkRecipe(f"{recipe.recipe_id}-fallback", "rcts", recipe.size, recipe.overlap)
    chunks: list[str] = []
    current: list[tuple[str, str]] = []
    fresh = False  # current 에 겹침이 아닌 새 문장이 있는가

    def text_of(items: list[tuple[str, str]]) -> str:
        return "".join((sep if i else "") + s for i, (s, sep) in enumerate(items))

    def emit() -> None:
        nonlocal current, fresh
        if fresh:
            chunks.append(text_of(current))
        keep = current[-recipe.overlap_sentences :] if recipe.overlap_sentences and fresh else []
        current, fresh = keep, False

    for sentence, sep in units:
        if len(sentence) > recipe.size:
            emit()
            current = []
            chunks.extend(p.strip() for p in _rcts(fallback).split_text(sentence))
            continue
        if current and len(text_of([*current, (sentence, sep)])) > recipe.size:
            emit()
            if current and len(text_of([*current, (sentence, sep)])) > recipe.size:
                current = []  # 겹침 문장을 넣으면 넘치므로 겹침 없이 시작
        current.append((sentence, sep))
        fresh = True
    emit()
    return chunks


@cache
def _rcts(recipe: ChunkRecipe) -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=recipe.size,
        chunk_overlap=recipe.overlap,
        separators=list(recipe.separators),
        keep_separator=recipe.keep_separator,
    )


def _paragraphs(text: str, merge_min: int) -> list[str]:
    """줄 단위 문단으로 나누고, merge_min 보다 짧은 문단은 다음 문단과 합친다 (길이 상한 없음).

    마지막 문단이 짧으면 앞 문단에 붙인다. 빈 줄은 버린다.
    """
    pieces: list[str] = []
    buffer = ""
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        buffer = f"{buffer}\n{line}" if buffer else line
        if len(buffer) >= merge_min:
            pieces.append(buffer)
            buffer = ""
    if buffer:
        if pieces:
            pieces[-1] = f"{pieces[-1]}\n{buffer}"
        else:
            pieces.append(buffer)
    return pieces


def split_text(
    text: str, recipe: ChunkRecipe = DEFAULT_RECIPE, section: str | None = None
) -> list[str]:
    """절 본문을 레시피대로 나눈다 (짧은 조각은 버린 뒤). section: 청크 종류 (CK06 만 쓴다)."""
    return recipe.split(text, section)[0]


def normalize_ws(text: str) -> str:
    """공백 · 줄바꿈을 한 칸으로 (원문과 청크를 위치로 대조할 때 쓰는 기준 글)."""
    return re.sub(r"\s+", " ", text).strip()


def chunk_spans(section_text: str, texts: list[str]) -> list[tuple[int, int] | None]:
    """각 청크가 덮는 원문 절의 구간 [시작, 끝) (normalize_ws 기준). 절 안에서 못 찾으면 None.

    청크는 절 안에서 순서대로 나오므로 앞 청크 위치 뒤부터 찾는다 (같은 문장이 반복돼도 엉뚱한 곳에
    붙지 않게). overlap 때문에 앞쪽에서 시작할 수 있어 못 찾으면 처음부터 다시 찾는다.
    """
    source = normalize_ws(section_text)
    spans: list[tuple[int, int] | None] = []
    start = 0
    for text in texts:
        piece = normalize_ws(text)
        pos = source.find(piece, start)
        if pos == -1:
            pos = source.find(piece)
        if pos == -1:
            spans.append(None)
            continue
        spans.append((pos, pos + len(piece)))
        start = pos + 1
    return spans
