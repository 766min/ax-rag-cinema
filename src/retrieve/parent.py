"""생성 모델에 넘길 근거 덩어리 — 청크 그대로, 또는 원문 위치로 자른 부모 문맥.

- 같은 부모의 검색 청크는 한 덩어리로 묶는다 (중복 제거). 덩어리 순서는 그 부모의 가장 높은 순위다.
- 부모가 max_chars 이하면 부모 전체. 넘으면 검색 청크들을 포함하는 끊기지 않은 구간을 앞뒤로 넓혀 max_chars 까지.
  구간 끝은 가능한 한 줄(문단) 경계에 맞춘다.
- 검색 청크끼리만으로 max_chars 를 넘으면 끊기지 않은 구간을 만들 수 없으므로 청크만 '…' 로 잇고
  contiguous=False 로 표시한다.
- 에피소드 요약은 부모가 에피소드 하나라 에피소드 경계를 넘지 않는다. 카드는 부모가 없어 그대로 넘긴다.
- 인용은 검색된 청크 ID 로만 한다 (ids). 겹침이 있는 색인은 같은 문장이 두 번 들어가므로 쓰지 않는다.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from shared.core.models import Chunk

PARENT_CHARS = 3000
GAP = "\n…\n"


@dataclass
class ContextBlock:
    ids: list[str]  # 이 덩어리 안의 검색 청크 ID (인용 키)
    header: str
    text: str
    parent_id: str | None = None
    span: tuple[int, int] | None = None  # 부모 원문 안의 [시작, 끝)
    contiguous: bool = True

    def render(self) -> str:
        head = f"[{', '.join(self.ids)}]" + (f" {self.header}" if self.header else "")
        return f"{head}\n{self.text}"


def chunk_blocks(top: Sequence[Chunk]) -> list[ContextBlock]:
    """청크만 넘기는 조건 (기본)"""
    return [ContextBlock([c.chunk_id], c.header, c.text) for c in top]


def _offsets(chunk: Chunk) -> tuple[int, int] | None:
    ids = chunk.source_ids
    if ids.get("char_start") is None or ids.get("char_end") is None:
        return None
    return int(ids["char_start"] or 0), int(ids["char_end"] or 0)


def _widen(source: str, start: int, end: int, max_chars: int) -> tuple[int, int]:
    """[start, end) 를 포함하며 max_chars 를 넘지 않게 앞뒤로 넓히고, 줄 경계에 맞춘다"""
    budget = max_chars - (end - start)
    lo = max(0, start - budget // 2)
    hi = min(len(source), end + (budget - (start - lo)))
    lo = max(0, min(lo, hi - max_chars))  # 뒤가 원문 끝에 닿았으면 남은 만큼 앞으로
    if lo > 0 and source[lo - 1] != "\n":
        nl = source.find("\n", lo, start)
        lo = nl + 1 if nl != -1 else lo
    if hi < len(source) and source[hi] != "\n":
        nl = source.rfind("\n", end, hi)
        hi = nl if nl != -1 else hi
    return lo, hi


def parent_blocks(
    top: Sequence[Chunk], sources: Mapping[str, str], max_chars: int = PARENT_CHARS
) -> list[ContextBlock]:
    """상위 청크를 부모 단위로. sources 는 build.chunks.parent_sources() (parent_id → 원문)"""
    overlapped = [c.chunk_id for c in top if (c.source_ids.get("overlap_chars") or "0") != "0"]
    if overlapped:
        raise ValueError(f"겹침이 있는 색인은 부모 문맥 실험에 쓰지 않습니다: {overlapped[:3]}")
    # 부모가 없거나 위치가 없는 청크(카드)는 자기 ID 를 열쇠로 혼자 묶인다.
    # dict 는 넣은 순서를 지키므로 덩어리 순서 = 그 부모의 가장 높은 검색 순위다
    groups: dict[str, list[Chunk]] = {}
    for chunk in top:
        parent = chunk.source_ids.get("parent_id")
        key = parent if parent and parent in sources and _offsets(chunk) else chunk.chunk_id
        groups.setdefault(key, []).append(chunk)
    blocks: list[ContextBlock] = []
    for key, chunks in groups.items():
        ids = [c.chunk_id for c in chunks]
        if key not in sources:
            blocks.append(ContextBlock(ids, chunks[0].header, chunks[0].text))
            continue
        source = sources[key]
        spans = sorted(o for c in chunks if (o := _offsets(c)))
        start, end = spans[0][0], max(e for _, e in spans)
        if len(source) <= max_chars:
            lo, hi = 0, len(source)
        elif end - start <= max_chars:
            lo, hi = _widen(source, start, end, max_chars)
        else:
            text = GAP.join(source[s:e] for s, e in spans)
            blocks.append(ContextBlock(ids, chunks[0].header, text, key, (start, end), False))
            continue
        # 잘린 쪽에 '…' 를 붙여 앞뒤가 더 있다는 것을 모델에 알린다
        text = ("…\n" if lo > 0 else "") + source[lo:hi] + ("\n…" if hi < len(source) else "")
        blocks.append(ContextBlock(ids, chunks[0].header, text, key, (lo, hi)))
    return blocks
