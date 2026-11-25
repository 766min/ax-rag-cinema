from etl.chunking import DEFAULT_RECIPE, RECIPES, ChunkRecipe, chunk_spans, split_text

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
    assert list(RECIPES) == ["CK01", "CK02", "CK03", "CK04"]
    assert [(r.size, r.overlap) for r in RECIPES.values()] == [
        (0, 0),
        (500, 100),
        (1000, 100),
        (1500, 100),
    ]
    assert DEFAULT_RECIPE is RECIPES["CK03"]
    assert DEFAULT_RECIPE.version == "CK03-rcts1000-o100-min10"
    assert CK01.version == "CK01-para-m150-min10"


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
