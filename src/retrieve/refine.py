import os
from pathlib import Path
from typing import Any, Protocol

from shared.core.models import Candidate

# 임시 기본 모델 (로컬 캐시에 있음, 이용 조건 미확인 — 팀 결정 사항)
DEFAULT_RERANKER = "Dongjin-kr/ko-reranker"
# 질문 + 청크를 합친 토큰 상한 (XLM-RoBERTa 계열의 최대 길이). 넘으면 뒤가 잘린다
DEFAULT_MAX_LENGTH = 512


# [역할] 재정렬기의 '모양' 약속: name(기록용 이름) + score(질문, 글 목록) → 점수 목록
class Reranker(Protocol):
    """질문과 후보 본문 쌍마다 관련도 점수 (클수록 관련 높음)"""

    name: str

    def score(self, query: str, texts: list[str]) -> list[float]: ...


# [역할] 후보 전부를 재정렬 점수 순으로 다시 매긴다 (순수 함수 — 모델은 인자로 받는다)
#   이전 순위를 rank_before_rerank 에 남겨, 재정렬이 정답을 끌어올렸는지 내렸는지 나중에 볼 수 있다
def rerank(
    query: str, candidates: list[Candidate], texts: dict[str, str], reranker: Reranker
) -> list[Candidate]:
    """후보 전부를 재정렬기 점수 순으로 다시 매긴다. 이전 순위는 rank_before_rerank 에 남긴다"""
    if not candidates:
        return []
    # 후보 순서대로 글을 넘기고, 같은 순서로 점수를 받는다
    scores = reranker.score(query, [texts[c.chunk_id] for c in candidates])
    if len(scores) != len(candidates):
        raise ValueError(f"재정렬 점수 개수가 후보와 다릅니다: {len(scores)} != {len(candidates)}")
    # zip(strict=True): 후보와 점수 개수가 다르면 바로 오류 (짝이 어긋나는 버그 방지)
    ordered = sorted(zip(candidates, scores, strict=True), key=lambda x: x[1], reverse=True)
    return [
        c.model_copy(
            update={"rank": rank, "rank_before_rerank": c.rank, "rerank_score": round(score, 4)}
        )
        for rank, (c, score) in enumerate(ordered, start=1)
    ]


# [역할] 로컬에 받아 둔 모델의 판(스냅샷 해시)을 찾는다 — 실행 기록에 '어느 판으로 돌렸나' 를 남기기 위해
def cached_revision(model_id: str) -> str:
    """Hugging Face 로컬 캐시의 판(스냅샷 해시). 캐시에 없으면 'not-cached'"""
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return "unknown"
    found = try_to_load_from_cache(model_id, "config.json")
    if not isinstance(found, str):
        return "not-cached"
    parts = Path(found).parts
    return parts[parts.index("snapshots") + 1] if "snapshots" in parts else "unknown"


def pick_device(torch: Any, requested: str | None = None) -> str:
    """재정렬 장치: 인자 > RERANKER_DEVICE > NVIDIA GPU(cuda) > Apple GPU(mps) > CPU.

    CPU 에서는 후보 80개 재정렬이 질문당 약 49초 걸린다 (2026-11-26 실측). GPU 가 있으면 자동으로 쓴다.
    GPU 를 쓰려면 CUDA 용 torch 가 깔려 있어야 한다 (`uv sync --locked --group rerank-cu128`, README 시작하기).
    """
    choice = requested or os.getenv("RERANKER_DEVICE")
    if choice:
        return choice
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


# [역할] 실제 Cross-Encoder 모델을 감싼 재정렬기 (로컬에서 돌아 API 비용이 없다)
class CrossEncoderReranker:
    """로컬 Cross-Encoder (API 비용 없음). info 에 이름 · 판 · 장치 · 로컬 캐시 여부를 남긴다"""

    def __init__(
        self,
        model_id: str | None = None,
        max_length: int = DEFAULT_MAX_LENGTH,
        device: str | None = None,
    ) -> None:
        # 모델 이름 우선순위: 인자 > 환경변수 > 기본값
        model_id = model_id or os.getenv("RERANKER_MODEL") or DEFAULT_RERANKER
        try:
            # 지연 import: 이 클래스를 만들 때만 무거운 라이브러리를 불러온다. 없으면 설치 방법을 알려 준다
            import torch
            from sentence_transformers import CrossEncoder
        except ImportError as exc:
            raise RuntimeError(
                "재정렬(C · C+ 구성)에는 선택 설치 그룹이 필요합니다 (기본 설치에는 없음). "
                "CPU · macOS: uv sync --locked --group rerank / "
                "NVIDIA GPU: uv sync --locked --group rerank-cu128 (README '시작하기' 참고)"
            ) from exc
        # NVIDIA GPU(cuda) → Apple GPU(mps) → CPU 순으로 고른다. 고른 장치는 info["device"] 로 기록에 남는다
        device = pick_device(torch, device)
        self.model: Any = CrossEncoder(model_id, max_length=max_length, device=device)
        revision = cached_revision(model_id)
        self.name = f"{model_id}@{revision[:12]}"
        # 실행 기록용 정보: 모델 · 판 · 최대 길이 · 장치 · 오프라인 여부 · 이용 조건
        self.info = {
            "model": model_id,
            "revision": revision,
            "max_length": max_length,
            "device": device,
            "offline": os.getenv("HF_HUB_OFFLINE") == "1",
            "license": "미확인",
        }

    # [역할] (질문, 글) 쌍마다 관련도 점수. predict 는 쌍 목록을 한 번에 받아 배치로 계산한다
    def score(self, query: str, texts: list[str]) -> list[float]:
        return [float(s) for s in self.model.predict([(query, t) for t in texts])]
