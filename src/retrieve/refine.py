# ══════════════════════════════════════════════════════════════════════════════
# [학습 안내] src/retrieve/refine.py — 재정렬(Re-ranking): C · C+ 구성의 마지막 단계
#
# 이 파일의 목적
#   벡터 · BM25 · RRF 는 질문과 청크를 '따로' 보고 점수를 낸다. 작품 이름만 겹쳐도 위로 올라올 수 있다.
#   Cross-Encoder 는 (질문, 청크) 한 쌍을 함께 읽고 '이 청크가 질문에 답하는가' 를 점수로 낸다 → 더 정확하지만 느리다.
#   그래서 색인 전체가 아니라 결합 후보 20개에만 쓰고, 그중 상위 5개를 생성 모델에 넘긴다.
#
# 전체 흐름
#   결합 후보(B 의 결과) → reranker.score(질문, 각 후보 글) → 점수 순으로 다시 정렬 → 이전 순위도 기록
#
# 설계 포인트
#   - Reranker 는 Protocol(모양만 정한 약속)이다. score() 만 있으면 진짜 모델이든 테스트용 가짜든 끼울 수 있다.
#   - sentence-transformers · torch 는 무거워서 팀 의존성에 없다. 실제 모델을 만들 때만 불러온다 (지연 import).
#   - 한국어 질문 + 영어 문서를 함께 읽어야 하므로 다국어 모델이 필요하다 (영어 전용 MS MARCO 모델은 쓰지 않는다).
#
# 누가 쓰나: retrieve/pipeline.py(Retriever) · service/runtime.py(C 계열일 때 모델 로드)
# ══════════════════════════════════════════════════════════════════════════════
"""재정렬 (C · C+, architecture.md §5).

출처: 서인 `origin/exp/journey:src/retrieval/refine.py` (Reranker Protocol · rerank, 원본 이식) +
영민 `retrieval.Reranker` (기본 모델 · 없을 때의 안내 문구). 학용 `evaluate_cross_encoder.py` 는 영어 전용
MS MARCO 모델이라 C 에 쓰지 않는다 (한국어 질문 + 영어 문서).

- 모델 이름은 설정값이다: 인자 > RERANKER_MODEL 환경변수 > 임시값 Dongjin-kr/ko-reranker
  (로컬 캐시에 있는 다국어 XLM-RoBERTa Cross-Encoder, 이용 조건 미확인. 팀 기본 모델은 팀 결정 사항)
- sentence-transformers · torch 는 팀 의존성에 없다. 쓸 때만 불러온다:
      HF_HUB_OFFLINE=1 uv run --with sentence-transformers==5.2.0 ...
"""

# ── 표준 라이브러리 ──
# 환경변수: RERANKER_MODEL(모델 바꾸기) · HF_HUB_OFFLINE(인터넷 없이 로컬 캐시만 쓰기)
import os

# Hugging Face 캐시 경로에서 모델 판(스냅샷 해시)을 꺼낼 때
from pathlib import Path

# Protocol: '이 메서드만 있으면 된다' 는 모양 약속 / Any: 외부 라이브러리 객체
from typing import Any, Protocol

# ── 팀 공통 모델 ──
# 재정렬 전후 순위 · 재정렬 점수를 Candidate 칸(rank_before_rerank · rerank_score)에 담는다
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
                "재정렬에는 sentence-transformers 가 필요합니다 (팀 의존성 밖): "
                "uv run --with sentence-transformers==5.2.0 ..."
            ) from exc
        # Apple 실리콘 GPU(mps)가 있으면 쓰고, 없으면 CPU
        device = device or ("mps" if torch.backends.mps.is_available() else "cpu")
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
