import pytest

from etl.chunking import (
    DEFAULT_RECIPE,
    RECIPES,
    ChunkRecipe,
    chunk_spans,
    normalize_ws,
    split_sentences,
    split_text,
)

LONG = "A" * 600  # 두 개를 합치면 기본 레시피(CK03) 크기 1000 을 넘는 문단
SHORT = "short line"
CK01 = RECIPES["CK01"]


def test_짧은_문단들은_한_청크로_합친다() -> None:
    assert split_text("first line\nsecond line") == ["first line\nsecond line"]


def test_긴_글은_청크_크기_안으로_나눈다() -> None:
    text = " ".join(f"Sentence number {i} is here." for i in range(200))  # 약 5,800자
    pieces = split_text(text)

    assert len(pieces) > 1
    assert all(len(p) <= DEFAULT_RECIPE.size for p in pieces)


def test_빈_글은_청크가_없다() -> None:
    assert split_text("") == []
    assert split_text("\n\n") == []


def test_레시피_목록과_기본값() -> None:
    assert list(RECIPES) == ["CK01", "CK02", "CK03", "CK04", "CK05", "CK06", "CK07", "CK08", "CK09"]
    assert DEFAULT_RECIPE is RECIPES["CK03"]


def test_V1_레시피_버전은_바뀌지_않는다() -> None:
    # V1 컬렉션의 index_version · 정답 키와 맞아야 한다 (바뀌면 V1 결과와 비교할 수 없다)
    assert [r.version for r in list(RECIPES.values())[:4]] == [
        "CK01-para-m150-min10",
        "CK02-rcts500-o100-min10",
        "CK03-rcts1000-o100-min10",
        "CK04-rcts1500-o100-min10",
    ]
    assert RECIPES["CK05"].version == "CK05-sent1000-os1-fb100-min10"
    assert RECIPES["CK06"].version == (
        "CK06-section[synopsis=WHOLE,intro=WHOLE,plot=CK01,biography=CK04,lore=CK04]-min10"
    )


def test_레시피_설정값은_방식에_맞는_칸만_채운다() -> None:
    assert CK01.settings()["chunk_size"] is None
    assert CK01.settings()["merge_min"] == 150
    assert RECIPES["CK02"].settings()["chunk_overlap"] == 100
    assert RECIPES["CK02"].settings()["merge_min"] is None


def test_CK01_150자_미만_문단은_다음_문단과_합친다() -> None:
    assert split_text(f"{SHORT}\n{LONG}\n{LONG}", CK01) == [f"{SHORT}\n{LONG}", LONG]


def test_CK01_마지막_짧은_문단은_앞에_붙고_길이_상한은_없다() -> None:
    huge = "B" * 5000
    assert split_text(f"{huge}\n{SHORT}", CK01) == [f"{huge}\n{SHORT}"]


def test_레시피마다_크기_상한을_지킨다() -> None:
    text = " ".join(f"Sentence number {i} is here." for i in range(300))
    for recipe in (RECIPES["CK02"], RECIPES["CK03"], RECIPES["CK04"]):
        assert all(len(p) <= recipe.size for p in split_text(text, recipe))


def test_짧은_조각은_버리고_무엇을_버렸는지_알려준다() -> None:
    recipe = ChunkRecipe("T", "paragraph", merge_min=1, min_chars=10)
    kept, dropped = recipe.split("W.\nThis line is long enough.")
    assert (kept, dropped) == (["This line is long enough."], ["W."])


def test_청크_위치는_공백을_정규화한_원문_기준이고_못_찾으면_None() -> None:
    section = "Alpha  beta.\nGamma delta."
    assert chunk_spans(section, ["Alpha beta.", "Gamma\ndelta.", "nowhere"]) == [
        (0, 11),
        (12, 24),
        None,
    ]


# --- CK05 문장 단위 ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Dr. Strange met Wong. He left.", ["Dr. Strange met Wong.", "He left."]),
        ("J. R. R. Tolkien wrote. Then rest.", ["J. R. R. Tolkien wrote.", "Then rest."]),
        ("He joined S.H.I.E.L.D. agents later.", ["He joined S.H.I.E.L.D. agents later."]),
        ("It cost $3.5 million. Wow.", ["It cost $3.5 million.", "Wow."]),
        ("Born Jan. 5, 1990. Died.", ["Born Jan. 5, 1990.", "Died."]),
        ('"Run," he said. "Now!" She ran.', ['"Run," he said.', '"Now!"', "She ran."]),
        ("Thanos vs. Thor. End.", ["Thanos vs. Thor.", "End."]),
        ("lower case. after stop. Upper.", ["lower case. after stop.", "Upper."]),
    ],
)
def test_문장_경계(text: str, expected: list[str]) -> None:
    assert split_sentences(text) == expected


def _sentences(n: int, width: int = 90) -> str:
    return " ".join(f"Sentence {i:02d} " + "x" * (width - 13) + "." for i in range(n))


def test_CK05_최대_크기_안에서_문장을_채우고_1문장_겹친다() -> None:
    ck05 = RECIPES["CK05"]
    chunks = split_text(_sentences(30), ck05)

    assert all(len(c) <= ck05.size for c in chunks)
    for prev, cur in zip(chunks, chunks[1:], strict=False):
        assert split_sentences(cur)[0] == split_sentences(prev)[-1]  # 앞 청크 마지막 문장으로 시작
    # 모든 문장이 어딘가에 있다
    joined = " ".join(chunks)
    assert all(f"Sentence {i:02d}" in joined for i in range(30))


def test_CK05_너무_긴_문장은_RCTS로_자르고_문단은_줄바꿈으로_잇는다() -> None:
    ck05 = ChunkRecipe("T", "sentence", size=100, overlap=10, overlap_sentences=1, min_chars=1)
    long_sentence = " ".join(["word"] * 60) + "."  # 300자 한 문장
    chunks = split_text(f"Short one. Next one.\n{long_sentence} Tail.", ck05)

    assert chunks[0] == "Short one. Next one."
    assert all(len(c) <= 100 for c in chunks)
    assert chunks[-1] == "Tail."
    assert split_text("First para.\nSecond para.", ck05) == ["First para.\nSecond para."]


def test_CK05_청크는_원문_위치로_찾을_수_있다() -> None:
    text = _sentences(25) + "\n" + _sentences(5)
    chunks = split_text(text, RECIPES["CK05"])
    assert None not in chunk_spans(text, chunks)
    assert normalize_ws(chunks[0]) in normalize_ws(text)


# --- CK06 절 종류별 ---------------------------------------------------------------


def test_CK06은_절_종류마다_다른_레시피를_쓴다() -> None:
    ck06 = RECIPES["CK06"]
    long = "\n".join(["A" * 600] * 5)  # 3000자

    assert split_text(long, ck06, "synopsis") == [long]  # 절 전체
    assert split_text(long, ck06, "intro") == [long]
    assert split_text(long, ck06, "plot") == split_text(long, CK01)
    assert split_text(long, ck06, "biography") == split_text(long, RECIPES["CK04"])
    assert split_text(long, ck06, "lore") == split_text(long, RECIPES["CK04"])
    with pytest.raises(ValueError, match="card"):
        split_text(long, ck06, "card")


# --- V6: 겹침 문장 수만 다른 CK07 · CK08 · CK09 --------------------------------------------


def test_CK07은_겹침이_없고_CK08은_앞_청크의_마지막_두_문장으로_시작한다() -> None:
    text = _sentences(30)
    ck07 = split_text(text, RECIPES["CK07"])
    assert all(
        not set(split_sentences(a)) & set(split_sentences(b))
        for a, b in zip(ck07, ck07[1:], strict=False)
    )
    ck08 = split_text(text, RECIPES["CK08"])
    for prev, cur in zip(ck08, ck08[1:], strict=False):
        assert split_sentences(cur)[:2] == split_sentences(prev)[-2:]
    assert all(len(c) <= 1000 for c in ck07 + ck08)


def test_CK09는_절마다_겹침_문장_수가_다르다() -> None:
    text = _sentences(30)
    ck09 = RECIPES["CK09"]
    assert split_text(text, ck09, "synopsis") == split_text(text, RECIPES["CK07"])
    assert split_text(text, ck09, "intro") == split_text(text, RECIPES["CK07"])
    assert split_text(text, ck09, "plot") == split_text(text, RECIPES["CK08"])
    assert split_text(text, ck09, "biography") == split_text(text, RECIPES["CK05"])
    assert split_text(text, ck09, "lore") == split_text(text, RECIPES["CK05"])
    assert RECIPES["CK07"].version == "CK07-sent1000-os0-fb100-min10"
    assert RECIPES["CK08"].version == "CK08-sent1000-os2-fb100-min10"
