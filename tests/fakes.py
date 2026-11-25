"""테스트용 가짜 부품 (실제 DB · API 를 부르지 않는다)."""

from typing import Any

from langchain_core.documents import Document
from langchain_core.runnables import RunnableLambda

from generation.answer import CitedAnswer


class FakeStore:
    """similarity_search_with_relevance_scores 만 흉내 내는 벡터 스토어."""

    def __init__(self, ranked: list[tuple[str, float]], fail: bool = False) -> None:
        self.ranked = ranked
        self.fail = fail
        self.queries: list[str] = []

    def similarity_search_with_relevance_scores(self, query: str, k: int) -> list[Any]:
        if self.fail:
            raise ConnectionError("DB down")
        self.queries.append(query)
        return [
            (Document(page_content="", metadata={"chunk_id": c}), s) for c, s in self.ranked[:k]
        ]


class FakeLLM:
    """with_structured_output 이 정해진 CitedAnswer 를 돌려주는 모델."""

    def __init__(self, answer: CitedAnswer | None = None, fail: bool = False) -> None:
        self.answer = answer or CitedAnswer(answer="답 [1]", cited_numbers=[1])
        self.fail = fail
        self.calls = 0

    def with_structured_output(self, schema: Any) -> RunnableLambda:
        def run(_: Any) -> CitedAnswer:
            self.calls += 1
            if self.fail:
                raise TimeoutError("LLM timeout")
            return self.answer

        return RunnableLambda(run)
