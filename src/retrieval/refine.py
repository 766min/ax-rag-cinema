"""재정렬 (C 구성, architecture §5).

V3 개인 실험은 다국어 Cross-Encoder(BAAI/bge-reranker-v2-m3)를 쓴다 (CrossEncoderReranker).
sentence-transformers 는 팀 pyproject 에 없다. 팀 의존성을 바꾸지 않고 실행할 때만 겹쳐 설치한다:
    uv run --with sentence-transformers==6.3.0 --with torch==2.15.0 \
           --with transformers==5.24.1 --with huggingface_hub==2.4.0 python scripts/...
어떤 재정렬기든 Reranker 모양만 맞추면
rerank() 와 평가 기록(재정렬 전후 순위 · 점수 · 시간 · 토큰)을 그대로 쓸 수 있다.
- 시간: pipeline 이 rerank() 호출을 @measure_time(label="rerank") 로 감싼다
- 토큰: LLM 재정렬기라면 LangChain 호출이 recorder 에 rerank_input · rerank_output 으로 잡힌다
"""

from typing import Any, Protocol

from shared.core.models import Candidate


class Reranker(Protocol):
    """질문과 후보 본문 쌍마다 관련도 점수를 낸다 (클수록 관련 높음)."""

    name: str

    def score(self, query: str, texts: list[str]) -> list[float]: ...


def rerank(
    query: str,
    candidates: list[Candidate],
    texts: dict[str, str],
    reranker: Reranker,
) -> list[Candidate]:
    """후보를 재정렬기 점수 순으로 다시 매긴다. 이전 순위는 rank_before_rerank 에 남긴다."""
    if not candidates:
        return []
    scores = reranker.score(query, [texts[c.chunk_id] for c in candidates])
    if len(scores) != len(candidates):
        raise ValueError(f"재정렬 점수 개수가 후보와 다릅니다: {len(scores)} != {len(candidates)}")
    ordered = sorted(zip(candidates, scores, strict=True), key=lambda x: x[1], reverse=True)
    return [
        c.model_copy(update={"rank": rank, "rank_before_rerank": c.rank, "rerank_score": score})
        for rank, (c, score) in enumerate(ordered, start=1)
    ]


RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
RERANKER_REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"  # 모델 파일을 이 커밋으로 고정
RERANKER_MAX_LENGTH = 1024  # 질문 + 청크(머리말 포함) 토큰 상한. 넘으면 잘린다


class CrossEncoderReranker:
    """질문 · 청크 쌍을 한 번에 읽어 관련도를 낸다 (로컬 실행, API 비용 없음)."""

    def __init__(
        self,
        model_id: str = RERANKER_MODEL,
        revision: str = RERANKER_REVISION,
        max_length: int = RERANKER_MAX_LENGTH,
        device: str | None = None,
    ) -> None:
        import torch  # 팀 의존성 밖이라 쓸 때만 불러온다
        from sentence_transformers import CrossEncoder

        # shortcut: 8GB 맥에서 fp32 를 MPS 에 올리면 스왑으로 20쌍에 36~53초 걸려 MPS 는 fp16 으로 돌린다
        # (실측 20쌍 3.4초, 점수 차이 소수 넷째 자리). GPU 서버에서 돌리면 fp32 로 바꾼다
        device = device or ("mps" if torch.backends.mps.is_available() else "cpu")
        dtype = torch.float16 if device == "mps" else torch.float32
        self.model: Any = CrossEncoder(
            model_id,
            revision=revision,
            max_length=max_length,
            device=device,
            model_kwargs={"torch_dtype": dtype},
        )
        self.max_length = max_length
        self.device = str(self.model.device)
        self.dtype = str(dtype).removeprefix("torch.")
        self.name = f"{model_id}@{revision[:12]}/{self.dtype}"

    def score(self, query: str, texts: list[str]) -> list[float]:
        return [float(s) for s in self.model.predict([(query, text) for text in texts])]

    def truncated(self, query: str, texts: list[str]) -> int:
        """max_length 를 넘어 잘리는 쌍의 수 (기록용)."""
        tokenizer = self.model.tokenizer
        return sum(len(tokenizer(query, text)["input_ids"]) > self.max_length for text in texts)
