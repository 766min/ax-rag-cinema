from retrieval.normalize import AliasNormalizer
from shared.core.models import Alias, AliasKind


def _alias(ko: str, en: str) -> Alias:
    return Alias(alias_ko=ko, canonical_en=en, kind=AliasKind.WORK)


def test_한국어_이름_뒤에_영어_이름을_덧붙인다() -> None:
    n = AliasNormalizer([_alias("엔드게임", "Avengers: Endgame")])
    assert n.normalize("엔드게임에서 토니는?") == "엔드게임 (Avengers: Endgame)에서 토니는?"


def test_긴_이름을_먼저_쓰고_안에_든_짧은_이름은_건너뛴다() -> None:
    n = AliasNormalizer([_alias("아이언맨", "Iron Man"), _alias("아이언맨 2", "Iron Man 2")])
    assert n.normalize("아이언맨 2 줄거리") == "아이언맨 2 (Iron Man 2) 줄거리"


def test_사전에_없으면_그대로() -> None:
    assert AliasNormalizer([]).normalize("타노스는 누구야?") == "타노스는 누구야?"
