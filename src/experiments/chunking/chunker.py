"""세 가지 청크 묶음을 만든다. 네트워크를 쓰지 않는다.

- R0: 학용님 handoff 청크 그대로 (줄거리 하나 = 청크 하나)
- R1: 같은 줄거리를 문단 경계를 지키며 max_chars 이하로 다시 자른 것 (바뀌는 것: 청크 크기)
- R2: R1 + 인물·세계관·인물 목록 문서의 작품 속(in-universe) 섹션 (바뀌는 것: 문서 범위)
"""

import re
from collections import defaultdict
from typing import Any

from pydantic import BaseModel, Field

from experiments.chunking.data import Handoff, SectionRef, section_of_plot

# c1: 긴 문단을 '마침표 뒤 공백'에서 잘랐다 (Dr. · S.H.I.E.L.D. · Vol. 뒤에서도 잘려 문장 중간 경계가 생김)
# c2: 약어 · 이니셜을 피하는 sentences() 로 자른다
CHUNKER_VERSION = "c2"

# R2 에 더하는 문서 종류
R2_DOC_KINDS = frozenset({"character", "character_list", "universe"})
# R2 에 넣는 섹션 종류: 작품 속 내용만. 제작 뒷이야기(creation) · 평가(reception) · 공개(release) ·
# 관련 문서(see_also) · 빈 섹션(meta, episodes)은 뺀다
R2_SECTION_KINDS = frozenset(
    {
        "lead",
        "biography",
        "personality",
        "abilities",
        "relationships",
        "alternate_versions",
        "other",
        "cast",
    }
)


class ExpChunk(BaseModel):
    """실험용 청크. 채점은 refs(원문 섹션)로 하므로 청크 ID 모양에 기대지 않는다"""

    chunk_id: str
    doc_id: str
    refs: list[SectionRef] = Field(
        description="이 청크가 나온 원문 섹션 (여러 줄거리를 합친 R0 청크는 여럿)"
    )
    doc_kind: str
    title: str
    header: str = Field(description="검색용 머리말. 임베딩은 header + text 를 본다")
    text: str
    source_url: str
    revid: int | None = None
    overlap_chars: int = Field(
        0, description="앞 조각에서 가져와 붙인 문장의 글자 수 (겹치기 없으면 0)"
    )
    first_in_unit: bool = Field(
        True,
        description="자기 재료(줄거리 · 요약 · 섹션)의 첫 조각인가 (겹치기를 붙이지 않는 자리)",
    )

    def embed_text(self) -> str:
        return f"{self.header}\n{self.text}" if self.header else self.text


def split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in text.split("\n") if p.strip()]


# 이니셜(S.H.I.E.L.D.) 뒤에 대문자가 오는 곳: 문장 끝일 가능성이 크지만 sentences() 는 안전하게 자르지 않는다
_AMBIGUOUS_END = re.compile(r"(?:[A-Za-z]\.){2,}[\"”’')\]]*\s+(?=[\"“‘(\[]?[A-Z])")


def _best_cut(s: str, limit: int) -> int:
    """limit 안에서 자를 자리를 고른다: 이니셜 뒤 대문자 > 세미콜론 · 쉼표 뒤 > 공백 > 글자 수."""
    ends = [m.end() for m in _AMBIGUOUS_END.finditer(s) if len(s[: m.end()].rstrip()) <= limit]
    if ends:
        return ends[-1]
    for sep in ("; ", ", "):
        i = s.rfind(sep, 0, limit)
        if i > limit // 3:
            return i + len(sep)
    i = s.rfind(" ", 0, limit + 1)
    return i if i > 0 else limit


def _split_sentence_hard(sentence: str, max_chars: int) -> list[str]:
    """한 '문장'이 max_chars 보다 길면 나눈다. 대개 이니셜 때문에 여러 문장이 뭉친 경우라,
    이니셜 뒤 대문자(실제 문장 끝일 가능성이 큰 곳)부터 찾아 자르고, 없을 때만 쉼표 · 공백에서 자른다."""
    out: list[str] = []
    rest = sentence
    while len(rest) > max_chars:
        cut = _best_cut(rest, max_chars)
        out.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        out.append(rest)
    return out


def split_long_paragraph(paragraph: str, max_chars: int) -> list[str]:
    """max_chars 보다 긴 문단을 문장 경계로 나눠 max_chars 이하 조각으로 묶는다."""
    if len(paragraph) <= max_chars:
        return [paragraph]
    pieces: list[str] = []
    for sentence in sentences(paragraph):
        pieces.extend(_split_sentence_hard(sentence.strip(), max_chars))
    return pack(pieces, max_chars, joiner=" ")


def pack(units: list[str], max_chars: int, joiner: str = "\n") -> list[str]:
    """순서를 지키며 앞에서부터 max_chars 를 넘지 않게 묶는다 (하나가 이미 넘으면 그대로 둔다)."""
    out: list[str] = []
    current = ""
    for unit in units:
        if not current:
            current = unit
        elif len(current) + len(joiner) + len(unit) <= max_chars:
            current = f"{current}{joiner}{unit}"
        else:
            out.append(current)
            current = unit
    if current:
        out.append(current)
    return out


def split_text(text: str, max_chars: int) -> list[str]:
    """문단 경계를 지키며 max_chars 이하로 자른다. 긴 문단만 문장 단위로 쪼갠다."""
    if max_chars < 50:
        raise ValueError("max_chars 는 50 이상이어야 합니다")
    units: list[str] = []
    for paragraph in split_paragraphs(text):
        units.extend(split_long_paragraph(paragraph, max_chars))
    return pack(units, max_chars)


# ---------- 겹치기: 앞 조각의 마지막 문장 하나를 통째로 붙인다 ----------

OVERLAP_MODE = "o1s"  # overlap = 1 sentence
# 마침표가 문장 끝이 아닌 약어 (소문자, 마침표 뺀 꼴)
_ABBREVIATIONS = frozenset(
    {
        "mr", "mrs", "ms", "dr", "st", "jr", "sr", "vs", "etc", "no", "mt", "lt", "gen", "col",
        "sgt", "capt", "prof", "inc", "co", "ltd", "rev", "fr", "sen", "rep", "gov", "pres",
        "vol", "pt", "ep", "ch", "fig", "approx", "dept", "est", "bros", "corp",
        "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    }
)  # fmt: skip
# 문장 끝 후보: . ! ? (+ 닫는 따옴표·괄호) + 공백, 그리고 다음이 대문자 · 숫자 · 여는 따옴표
_BOUNDARY = re.compile(r"([.!?])([\"”’')\]]*)(\s+)(?=[\"“‘(\[]?[A-Z0-9])")
_INITIALS = re.compile(r"(?:[A-Za-z]\.)+")  # S.H.I.E.L.D. · U.S. · Peter B.
_OPENERS = "\"'(“‘["


def sentences(text: str) -> list[str]:
    """영어 문장으로 나눈다. 약어(Dr., St.)와 이니셜(S.H.I.E.L.D., B.) 뒤에서는 자르지 않는다.

    애매하면 자르지 않는 쪽을 택한다. 그러면 문장이 길어져 겹치기에서 빠질 뿐, 잘린 문장이 붙지는 않는다.
    """
    out: list[str] = []
    start = 0
    for m in _BOUNDARY.finditer(text):
        if m.group(1) == ".":
            before = text[start : m.start(1) + 1].split()
            token = before[-1].lstrip(_OPENERS) if before else ""
            if token[:-1].lower() in _ABBREVIATIONS or _INITIALS.fullmatch(token):
                continue
        out.append(text[start : m.end(2)].strip())
        start = m.end()
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return out


def starts_sentence(s: str) -> bool:
    """대문자 · 숫자로 (여는 따옴표 뒤에서라도) 시작하나"""
    body = s.lstrip(_OPENERS)
    return bool(body) and (body[0].isupper() or body[0].isdigit())


def is_whole_sentence(s: str) -> bool:
    """문장 시작으로 시작하고 문장 부호로 끝나며, 약어 · 이니셜로 끝나지 않아야 온전한 문장으로 본다."""
    if not starts_sentence(s) or not re.search(r"[.!?][\"”’')\]]*$", s):
        return False
    if len(s.split()) < 2:  # '3.' 같은 꼬리 조각은 문장으로 보지 않는다
        return False
    token = s.split()[-1].rstrip("\"”’')]")
    return not (token[:-1].lower() in _ABBREVIATIONS or _INITIALS.fullmatch(token))


def _begins_after_sentence_end(source: str, sentence: str) -> bool:
    """원문에서 이 문장 바로 앞이 문장 끝(또는 문단 시작)인가.

    조각 안에서 다시 문장으로 나누면, 긴 문장을 강제로 자른 조각의 꼬리('24, 2019, and …')도
    문장처럼 보일 수 있다. 원문에서 실제로 문장 경계 뒤에 오는지 한 번 더 확인한다.
    """
    pos = source.find(sentence)
    if pos < 0:
        return False
    before = source[:pos]
    if not before.strip() or before.rstrip(" ").endswith("\n"):
        return True
    tail = before.rstrip()
    if not re.search(r"[.!?][\"”’')\]]*$", tail):
        return False
    token = tail.split()[-1].rstrip("\"”’')]").lstrip(_OPENERS)
    return not (token[:-1].lower() in _ABBREVIATIONS or _INITIALS.fullmatch(token))


def add_overlap(
    pieces: list[str], max_chars: int, source: str | None = None
) -> list[tuple[str, int]]:
    """두 번째 조각부터 바로 앞 조각의 마지막 문장 하나를 통째로 앞에 붙인다.

    조각 자체(새 내용)는 건드리지 않으므로 겹치기 없는 실험과 경계가 같다.
    그 문장이 max_chars 의 절반보다 길거나 온전한 문장이 아니면 붙이지 않는다.
    돌려주는 값: (조각 글, 앞에 붙인 글자 수)
    """
    out: list[tuple[str, int]] = []
    for i, cur in enumerate(pieces):
        if i == 0:
            out.append((cur, 0))
            continue
        last = sentences(split_paragraphs(pieces[i - 1])[-1])[-1]
        # 경계가 문장 사이일 때만 붙인다 (긴 문장을 단어에서 강제로 자른 경계면 붙이지 않는다)
        ok = len(last) <= max_chars // 2 and is_whole_sentence(last) and starts_sentence(cur)
        if ok and source is not None:
            ok = _begins_after_sentence_end(source, last)
        if ok:
            out.append((f"{last}\n{cur}", len(last) + 1))
        else:
            out.append((cur, 0))
    return out


def _pieces(text: str, max_chars: int, overlap: bool) -> list[tuple[str, int]]:
    pieces = split_text(text, max_chars)
    return add_overlap(pieces, max_chars, text) if overlap else [(p, 0) for p in pieces]


def r0_chunks(handoff: Handoff) -> list[ExpChunk]:
    out = []
    for c in handoff.chunks:
        plots = [handoff.plots[pid] for pid in c["source_plot_ids"]]
        doc = handoff.documents[c["doc_id"]]
        refs: list[SectionRef] = []
        for p in plots:
            if p["document_id"] != c["doc_id"]:
                raise ValueError(f"{c['chunk_id']} 의 줄거리가 다른 문서에 있습니다")
            ref = section_of_plot(p, doc)
            if ref not in refs:
                refs.append(ref)
        out.append(
            ExpChunk(
                chunk_id=c["chunk_id"],
                doc_id=c["doc_id"],
                refs=refs,
                doc_kind=doc["doc_kind"],
                title=doc["title"],
                first_in_unit=True,  # R0 는 줄거리 하나 = 청크 하나
                header=c.get("header") or "",
                text=c["text"],
                overlap_chars=0,
                source_url=doc["source_url"],
                revid=doc.get("revid"),
            )
        )
    return out


def _numbered(doc_id: str, ref: SectionRef, counter: dict[SectionRef, int]) -> str:
    counter[ref] += 1
    return f"{doc_id}-s{ref.section_index:03d}-p{counter[ref]:03d}"


def r1_chunks(handoff: Handoff, max_chars: int, overlap: bool = False) -> list[ExpChunk]:
    """R0 와 같은 줄거리를 크기만 바꿔 다시 자른다. 머리말도 R0 의 것을 그대로 쓴다."""
    header_of_plot: dict[str, str] = {}
    for c in handoff.chunks:
        for pid in c["source_plot_ids"]:
            header_of_plot.setdefault(pid, c.get("header") or "")
    counter: dict[SectionRef, int] = defaultdict(int)
    out = []
    for pid, plot in handoff.plots.items():
        doc = handoff.documents[plot["document_id"]]
        ref = section_of_plot(plot, doc)
        for k, (piece, ov) in enumerate(_pieces(plot["text"], max_chars, overlap)):
            out.append(
                ExpChunk(
                    chunk_id=_numbered(doc["doc_id"], ref, counter),
                    doc_id=doc["doc_id"],
                    refs=[ref],
                    doc_kind=doc["doc_kind"],
                    title=doc["title"],
                    header=header_of_plot.get(pid, ""),
                    text=piece,
                    source_url=doc["source_url"],
                    revid=doc.get("revid"),
                    overlap_chars=ov,
                    first_in_unit=k == 0,
                )
            )
    return out


def section_header(doc: dict[str, Any], section: dict[str, Any]) -> str:
    return f"[{doc['title']} · {' > '.join(section['section_path'])}]"


def r2_extra_chunks(handoff: Handoff, max_chars: int, overlap: bool = False) -> list[ExpChunk]:
    """인물·세계관·인물 목록 문서의 작품 속 섹션을 R1 과 같은 규칙으로 자른다."""
    counter: dict[SectionRef, int] = defaultdict(int)
    out = []
    for doc in handoff.documents.values():
        if doc["doc_kind"] not in R2_DOC_KINDS:
            continue
        for section in doc["sections"]:
            if section["section_kind"] not in R2_SECTION_KINDS or not section["text"].strip():
                continue
            ref = SectionRef(doc["doc_id"], section["section_index"])
            for k, (piece, ov) in enumerate(_pieces(section["text"], max_chars, overlap)):
                out.append(
                    ExpChunk(
                        chunk_id=_numbered(doc["doc_id"], ref, counter),
                        doc_id=doc["doc_id"],
                        refs=[ref],
                        doc_kind=doc["doc_kind"],
                        title=doc["title"],
                        header=section_header(doc, section),
                        text=piece,
                        source_url=doc["source_url"],
                        revid=doc.get("revid"),
                        overlap_chars=ov,
                        first_in_unit=k == 0,
                    )
                )
    return out


def handoff_paragraphs(handoff: Handoff) -> dict[SectionRef, set[str]]:
    """원문 섹션마다 문단 모음 (줄거리 메모의 문단 포함). 경계 검사에서 '문단 사이 경계'를 가려낼 때 쓴다."""
    paras: dict[SectionRef, set[str]] = defaultdict(set)
    for p in handoff.plots.values():
        paras[section_of_plot(p, handoff.documents[p["document_id"]])].update(
            split_paragraphs(p["text"])
        )
    for d in handoff.documents.values():
        for s in d["sections"]:
            paras[SectionRef(d["doc_id"], s["section_index"])].update(split_paragraphs(s["text"]))
    return paras


def cut_report(paras: dict[SectionRef, set[str]], chunks: list[ExpChunk]) -> dict[str, Any]:
    """같은 섹션 안에서 '문단 안'을 자른 경계를 종류별로 센다.

    - sentence: 문장 사이에서 자름 (정상)
    - initials: 'S.H.I.E.L.D.' 같은 이니셜 뒤, 다음이 대문자 (문장 끝일 가능성이 큼)
    - forced: 한 문장이 크기보다 길어 쉼표 · 공백에서 자름 (막을 수 없어 따로 센다)
    - bad: 약어(Dr. · Vol. …) 뒤처럼 문장 중간이 분명한 곳에서 자름 (0 이어야 한다)
    """
    counts = {"in_paragraph": 0, "sentence": 0, "initials": 0, "forced": 0}
    bad: list[tuple[str, str, str]] = []
    for a, b in zip(chunks, chunks[1:], strict=False):
        if a.refs != b.refs or len(a.refs) != 1 or b.first_in_unit:
            continue  # 다른 섹션이거나, 새 재료(다른 줄거리 · 에피소드 요약)의 시작이라 우리가 자른 자리가 아니다
        last = split_paragraphs(a.text)[-1]
        first = split_paragraphs(b.text[b.overlap_chars :])[0]
        src = paras[a.refs[0]]
        # 원문의 문단 경계: 어떤 문단이 last 로 끝나고, 어떤 문단이 first 로 시작한다
        # (긴 문단은 문장 조각으로 나뉘어 last 가 문단의 끝부분일 수 있다)
        if any(p.endswith(last) for p in src) and any(p.startswith(first) for p in src):
            continue
        counts["in_paragraph"] += 1
        tail = sentences(last)[-1]
        token = tail.split()[-1].rstrip("\"”’')]") if tail.split() else ""
        if not re.search(r"[.!?][\"”’')\]]*$", tail):
            counts["forced"] += 1
        elif _INITIALS.fullmatch(token) and len(token) > 2 and starts_sentence(first):
            counts["initials"] += 1
        elif tail in sentences(f"{last} {first}"):
            counts["sentence"] += 1
        else:
            bad.append((b.chunk_id, last[-60:], first[:60]))
    return {**counts, "bad": bad}


def mid_sentence_cuts(handoff: Handoff, chunks: list[ExpChunk]) -> dict[str, Any]:
    return cut_report(handoff_paragraphs(handoff), chunks)


VARIANTS = ("R0", "R1", "R2")


def build_variant(
    handoff: Handoff, variant: str, max_chars: int, overlap: bool = False
) -> list[ExpChunk]:
    if variant == "R0":
        if overlap:
            raise ValueError("R0 는 학용님 청크 그대로라 겹치기를 넣을 수 없습니다")
        chunks = r0_chunks(handoff)
    elif variant == "R1":
        chunks = r1_chunks(handoff, max_chars, overlap)
    elif variant == "R2":
        chunks = r1_chunks(handoff, max_chars, overlap) + r2_extra_chunks(
            handoff, max_chars, overlap
        )
    else:
        raise ValueError(f"알 수 없는 실험: {variant} (가능: {', '.join(VARIANTS)})")
    ids = [c.chunk_id for c in chunks]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{variant}: chunk_id 가 겹칩니다")
    return chunks
