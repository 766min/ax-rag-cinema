import asyncio
from pathlib import Path

import pytest

import tools.translation as translation
from shared.core.models import Alias
from tools.translation import (
    load_aliases,
    normalize_query,
    rewrite_movie_query,
    translate_answer_to_korean,
)

ALIASES = [
    Alias(alias_ko="아이언맨", canonical_en="Iron Man", kind="work", target_id="tt0371746"),
    Alias(alias_ko="아이언맨 2", canonical_en="Iron Man 2", kind="work", target_id="tt1228705"),
]


def test_dataset_aliases_jsonl_is_the_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = tmp_path / "cineverse-dataset-schema" / "aliases.jsonl"
    dataset.parent.mkdir()
    dataset.write_text(ALIASES[0].model_dump_json() + "\n", encoding="utf-8")
    monkeypatch.delenv("CINEVERSE_ALIASES_PATH", raising=False)
    monkeypatch.setattr(translation, "ALIAS_CANDIDATES", (tmp_path / "missing.csv", dataset))
    assert load_aliases() == ALIASES[:1]


def test_missing_alias_file_says_where_to_put_the_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CINEVERSE_ALIASES_PATH", raising=False)
    monkeypatch.setattr(translation, "ALIAS_CANDIDATES", (tmp_path / "none.jsonl",))
    with pytest.raises(FileNotFoundError, match="cineverse-dataset-schema"):
        load_aliases()


def test_real_team_dataset_aliases_load_when_present() -> None:
    if not translation.DATASET_ALIASES.exists():
        pytest.skip("팀 데이터셋이 data/ 에 없습니다 (gitignore)")
    aliases = load_aliases(translation.DATASET_ALIASES)
    assert len(aliases) == 112
    assert all(alias.target_id and alias.target_id.startswith("tt") for alias in aliases)


def test_team_csv_aliases_are_supported(tmp_path: Path) -> None:
    aliases = tmp_path / "aliases_ko.csv"
    aliases.write_text(
        "alias_ko,canonical_en,kind,target_id\n"
        "캡틴 아메리카,Captain America,character,char-captain-america\n",
        encoding="utf-8",
    )
    loaded = load_aliases(aliases)
    assert len(loaded) == 1
    assert loaded[0].canonical_en == "Captain America"


def test_normalization_keeps_korean_and_prefers_longest_name() -> None:
    normalized, matched = normalize_query("아이언맨 2와 아이언맨의 차이는?", ALIASES)
    assert normalized == "아이언맨 2와 아이언맨의 차이는? Iron Man 2 Iron Man"
    assert matched == [
        {"alias_ko": "아이언맨 2", "canonical_en": "Iron Man 2"},
        {"alias_ko": "아이언맨", "canonical_en": "Iron Man"},
    ]


def test_unspaced_captain_america_alias() -> None:
    normalized, matched = normalize_query("캡틴아메리카는 누구야?", ALIASES)
    assert normalized == "캡틴아메리카는 누구야? Captain America"
    assert matched == [{"alias_ko": "캡틴아메리카", "canonical_en": "Captain America"}]


def test_rewrite_protects_movie_and_character_names() -> None:
    async def fake(_system: str, text: str) -> str:
        assert text == "Did [[TERM_0]] meet [[TERM_1]]?"
        return text

    result = asyncio.run(rewrite_movie_query("Did 아이언맨 meet 캡틴 아메리카?", ALIASES, fake))
    assert result["query_en"] == "Did Iron Man meet Captain America?"
    assert result["rewrite_failed"] is False


def test_rewrite_failure_preserves_normalized_fallback() -> None:
    async def drop_token(_system: str, _text: str) -> str:
        return "Who saved the world?"

    result = asyncio.run(rewrite_movie_query("아이언맨은 누구야?", ALIASES, drop_token))
    assert result["query_en"] == ""
    assert result["query_normalized"] == "아이언맨은 누구야? Iron Man"
    assert result["rewrite_failed"] is True


def test_answer_preserves_name_citation_and_url() -> None:
    async def fake(_system: str, text: str) -> str:
        assert "[[TERM_0]]" in text
        assert "[[TERM_1]]" in text
        assert "[[TERM_2]]" in text
        return text.replace("saved", "구했다")

    answer = "Iron Man saved them. [tt0371746-wikipedia-plot-p01] https://example.org/source"
    result = asyncio.run(translate_answer_to_korean(answer, ALIASES, fake))
    assert result == {
        "answer_ko": "아이언맨 구했다 them. [tt0371746-wikipedia-plot-p01] https://example.org/source",
        "translation_failed": False,
    }


def test_answer_protects_season_and_character_citations() -> None:
    seen: list[str] = []

    async def fake(_system: str, text: str) -> str:
        seen.append(text)
        # A model that mangles raw IDs must never see them.
        return text.replace("-S1-", "-s1-").replace("wp-", "WP-")

    answer = "It aired. [tt9140554-S1-wikipedia-plot-p01] [wp-52234178-intro-p02]"
    result = asyncio.run(translate_answer_to_korean(answer, [], fake))
    assert "tt9140554" not in seen[0] and "wp-52234178" not in seen[0]
    assert "[tt9140554-S1-wikipedia-plot-p01]" in result["answer_ko"]
    assert "[wp-52234178-intro-p02]" in result["answer_ko"]


def test_answer_rejects_lost_citation() -> None:
    async def fake(_system: str, _text: str) -> str:
        return "아이언맨이 구했다."

    result = asyncio.run(
        translate_answer_to_korean("Iron Man saved them. https://example.org", ALIASES, fake)
    )
    assert result["translation_failed"] is True
    assert result["answer_ko"] == ""


def test_answer_does_not_treat_lowercase_common_word_as_title() -> None:
    echo = Alias(alias_ko="에코", canonical_en="Echo", kind="work", target_id="tt13966962")

    async def fake(_system: str, text: str) -> str:
        assert text == "The echo followed [[TERM_0]]."
        return "그 울림은 [[TERM_0]]을 따라갔다."

    result = asyncio.run(
        translate_answer_to_korean("The echo followed Iron Man.", [echo, *ALIASES], fake)
    )
    assert result["answer_ko"] == "그 울림은 아이언맨을 따라갔다."


THOR = Alias(alias_ko="토르", canonical_en="Thor", kind="work", target_id="tt0800369")


def test_answer_keeps_names_inside_urls_untranslated() -> None:
    async def fake(_system: str, text: str) -> str:
        assert "wikipedia.org" not in text  # the whole URL is hidden from the model
        return text

    answer = "Thor returns. https://en.wikipedia.org/wiki/Thor [tt0800369-wikipedia-plot-p01]"
    result = asyncio.run(translate_answer_to_korean(answer, [THOR], fake))
    assert result["translation_failed"] is False
    assert result["answer_ko"] == (
        "토르 returns. https://en.wikipedia.org/wiki/Thor [tt0800369-wikipedia-plot-p01]"
    )


def test_korean_alias_is_not_matched_inside_another_word() -> None:
    normalized, matched = normalize_query("빅토르는 누구야?", [THOR])
    assert normalized == "빅토르는 누구야?"
    assert matched == []


def test_korean_alias_still_matches_with_particles() -> None:
    normalized, matched = normalize_query("토르의 망치와 토르가 만난 사람은?", [THOR])
    assert normalized == "토르의 망치와 토르가 만난 사람은? Thor"
    assert matched == [{"alias_ko": "토르", "canonical_en": "Thor"}]


def test_rewrite_does_not_protect_alias_inside_another_word() -> None:
    sent: list[str] = []

    async def fake(_system: str, text: str) -> str:
        sent.append(text)
        return "Who is Viktor?"

    result = asyncio.run(rewrite_movie_query("빅토르는 누구야?", [THOR], fake))
    assert "[[TERM_" not in sent[0]
    assert result["query_en"] == "Who is Viktor?"


def test_chunk_ids_with_three_digit_positions_are_protected() -> None:
    seen: list[str] = []

    async def fake(_system: str, text: str) -> str:
        seen.append(text)
        return text.replace("p123", "p12")  # a model that would damage a raw ID

    answer = "Long plot. [tt4154796-wikipedia-plot-p123]"
    result = asyncio.run(translate_answer_to_korean(answer, [], fake))
    assert "p123" not in seen[0]
    assert result["answer_ko"] == "Long plot. [tt4154796-wikipedia-plot-p123]"


THUNDERBOLTS = Alias(
    alias_ko="썬더볼츠*", canonical_en="Thunderbolts*", kind="work", target_id="tt20969586"
)
WHAT_IF = Alias(
    alias_ko="왓 이프...?", canonical_en="What If...?", kind="work", target_id="tt10168312"
)


def test_query_matches_alias_without_trailing_symbols() -> None:
    _, matched = normalize_query("썬더볼츠 흥행 수익은? 왓 이프는?", [THUNDERBOLTS, WHAT_IF])
    assert matched == [
        {"alias_ko": "썬더볼츠", "canonical_en": "Thunderbolts*"},
        {"alias_ko": "왓 이프", "canonical_en": "What If...?"},
    ]


def test_query_still_matches_alias_with_its_symbols() -> None:
    _, matched = normalize_query("썬더볼츠* 흥행 수익은?", [THUNDERBOLTS])
    assert matched == [{"alias_ko": "썬더볼츠*", "canonical_en": "Thunderbolts*"}]


def test_answer_keeps_official_title_with_symbols() -> None:
    async def fake(_system: str, text: str) -> str:
        return text

    result = asyncio.run(
        translate_answer_to_korean("Thunderbolts* was a hit.", [THUNDERBOLTS], fake)
    )
    assert result["answer_ko"] == "썬더볼츠* was a hit."
