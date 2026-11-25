"""V7 (EXP-10): 생성 모델에 넣기 직전의 문맥 배치만 바꾼다. 검색 순위는 바꾸지 않는다.

- retrieval_rank: 검색(재정렬) 순위 (1부터), context_position: 프롬프트 안 [n] 번호 (1부터). 둘을 따로 기록한다
- 청크 ID · 본문은 그대로 옮기기만 한다. 인용 번호 → chunk_id 연결은 generation.answer.resolve 가 배치된
  순서대로 하므로 따로 고칠 것이 없다
- Liu et al. (2024, TACL) "Lost in the Middle" 에서 착안한 비교군이다 (그 논문의 방법을 구현한 것이 아니다)
"""

from typing import Literal, TypeVar

T = TypeVar("T")

# 5개 기준 배치 (검색 순위를 프롬프트 위치 순서로 나열)
STRATEGIES: dict[str, tuple[int, ...]] = {
    "CR01": (1, 2, 3, 4, 5),  # 순위순
    "CR02": (5, 4, 3, 2, 1),  # 역순
    "CR03": (1, 3, 5, 4, 2),  # 양 끝 우선 (1 · 2 위가 처음 · 끝)
    "CR04": (4, 2, 1, 3, 5),  # 중앙 우선 (1 위가 가운데)
}


def order_for(strategy: str, n: int) -> list[int]:
    """n 개일 때의 배치 (검색 순위 목록). 5개 미만이면 n 보다 큰 순위를 빼고 나머지 순서를 지킨다.

    예: CR04 · n=3 → [2, 1, 3]. 5개를 넘으면 정의하지 않았으므로 오류.
    """
    pattern = STRATEGIES[strategy]
    if not 0 <= n <= len(pattern):
        raise ValueError(f"{strategy} 는 {len(pattern)}개까지만 정의했습니다: n={n}")
    return [rank for rank in pattern if rank <= n]


def arrange(items: list[T], ranks: list[int]) -> list[T]:
    """검색 순위 목록(1부터)대로 items(검색 순위순)를 다시 늘어놓는다."""
    if sorted(ranks) != list(range(1, len(items) + 1)):
        raise ValueError(f"순위 목록이 항목 수와 맞지 않습니다: {ranks} (항목 {len(items)}개)")
    return [items[rank - 1] for rank in ranks]


Position = Literal["first", "middle", "last"]


def place(n: int, target_rank: int, position: Position) -> list[int]:
    """target_rank 하나만 처음 · 가운데 · 끝으로 옮기고 나머지는 검색 순위 순서를 지킨다.

    가운데 = n // 2 번째 칸 (0부터, 5개면 3번째 자리).
    """
    if not 1 <= target_rank <= n:
        raise ValueError(f"target_rank {target_rank} 가 1..{n} 밖입니다")
    others = [rank for rank in range(1, n + 1) if rank != target_rank]
    slot = {"first": 0, "middle": n // 2, "last": n - 1}[position]
    return others[:slot] + [target_rank] + others[slot:]


def token_layout(blocks: list[str], separator: str, encoding_name: str = "o200k_base") -> dict:
    """프롬프트 문맥 글에서 각 블록이 차지하는 토큰 구간 [시작, 끝) 과 전체 토큰 수.

    shortcut: gpt-6-luna 의 토크나이저를 알 수 없어 tiktoken o200k_base 로 센 근사값이다.
    """
    import tiktoken

    encoding = tiktoken.get_encoding(encoding_name)
    spans = []
    cursor = 0
    sep_tokens = len(encoding.encode(separator))
    for i, block in enumerate(blocks):
        if i:
            cursor += sep_tokens
        length = len(encoding.encode(block))
        spans.append((cursor, cursor + length))
        cursor += length
    return {"encoding": encoding_name, "spans": spans, "total": cursor}
