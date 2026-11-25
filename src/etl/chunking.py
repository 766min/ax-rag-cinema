"""청킹 레시피 (CK01~CK04)와 청크 · 원문 위치 대조.

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
    method: Literal["paragraph", "rcts"]
    size: int = 0  # rcts: chunk_size (글자 수, length_function=len)
    overlap: int = 0  # rcts: chunk_overlap
    merge_min: int = 150  # paragraph: 이보다 짧은 문단은 다음 문단과 합친다
    min_chars: int = 10  # 공통: 이보다 짧은 최종 조각은 버린다 (표 잔해)
    separators: tuple[str, ...] = ("\n", ". ", " ", "")  # rcts: 문단 → 문장 → 단어 → 글자
    keep_separator: Literal["start", "end"] = "end"

    @property
    def version(self) -> str:
        if self.method == "paragraph":
            return f"{self.recipe_id}-para-m{self.merge_min}-min{self.min_chars}"
        return f"{self.recipe_id}-rcts{self.size}-o{self.overlap}-min{self.min_chars}"

    def split(self, text: str) -> tuple[list[str], list[str]]:
        """(남긴 조각, min_chars 미만이라 버린 조각)."""
        if self.method == "paragraph":
            raw = _paragraphs(text, self.merge_min)
        else:
            raw = [piece.strip() for piece in _rcts(self).split_text(text)]
        kept = [p for p in raw if len(p) >= self.min_chars]
        dropped = [p for p in raw if p and len(p) < self.min_chars]
        return kept, dropped

    def settings(self) -> dict[str, object]:
        """결과 파일에 남길 설정값 (방식과 상관없는 칸은 None)."""
        rcts = self.method == "rcts"
        return {
            "method": self.method,
            "chunk_size": self.size if rcts else None,
            "chunk_overlap": self.overlap if rcts else None,
            "merge_min": None if rcts else self.merge_min,
            "min_chars": self.min_chars,
            "separators": list(self.separators) if rcts else None,
            "keep_separator": self.keep_separator if rcts else None,
            "length_function": "len (문자 수)",
        }


RECIPES: dict[str, ChunkRecipe] = {
    r.recipe_id: r
    for r in (
        ChunkRecipe("CK01", "paragraph"),
        ChunkRecipe("CK02", "rcts", size=500, overlap=100),
        ChunkRecipe("CK03", "rcts", size=1000, overlap=100),
        ChunkRecipe("CK04", "rcts", size=1500, overlap=100),
    )
}
DEFAULT_RECIPE = RECIPES["CK03"]


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


def split_text(text: str, recipe: ChunkRecipe = DEFAULT_RECIPE) -> list[str]:
    """절 본문을 레시피대로 나눈다 (짧은 조각은 버린 뒤)."""
    return recipe.split(text)[0]


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
