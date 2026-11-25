"""이름 사전 치환 (architecture §5, 모든 구성 공통).

질문 속 한국어 이름 뒤에 영어 정식 이름을 덧붙인다. 원 질문은 지우지 않는다.
규칙 기반이라 같은 질문은 항상 같은 결과가 나온다.
"""

import json
from pathlib import Path

from shared.core.models import Alias

# 팀 데이터셋 aliases.jsonl 의 SHA-256 앞 8자 (V0 handoff 판과 내용이 같다)
ALIAS_VERSION = "dataset-aliases@830ed44f"


def load_aliases(path: Path) -> list[Alias]:
    with path.open(encoding="utf-8") as f:
        return [Alias.model_validate(json.loads(line)) for line in f if line.strip()]


class AliasNormalizer:
    """한국어 이름 → '한국어 이름 (English Name)'. 긴 이름부터 찾아 짧은 이름이 겹쳐 붙지 않게 한다."""

    def __init__(self, aliases: list[Alias]) -> None:
        # 같은 한국어 이름이 여러 번 있으면 처음 것을 쓴다
        mapping: dict[str, str] = {}
        for alias in aliases:
            mapping.setdefault(alias.alias_ko, alias.canonical_en)
        self._aliases = sorted(mapping.items(), key=lambda kv: len(kv[0]), reverse=True)

    def normalize(self, question: str) -> str:
        result = question
        taken: list[tuple[int, int]] = []  # 이미 치환한 구간 (긴 이름 안의 짧은 이름은 건너뜀)
        additions: list[tuple[int, str]] = []
        for ko, en in self._aliases:
            start = result.find(ko)
            while start != -1:
                end = start + len(ko)
                if not any(s <= start and end <= e for s, e in taken):
                    taken.append((start, end))
                    additions.append((end, f" ({en})"))
                start = result.find(ko, end)
        for position, text in sorted(additions, reverse=True):
            result = result[:position] + text + result[position:]
        return result
