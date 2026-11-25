import logging

from langchain_core.documents import Document

from evals.search_eval import aggregate, http_calls, score_question, search_vector, summarize_calls
from shared.core.models import EvalItem

EXCERPT = "Thanos wants the stones"


def _item(gold: list[str]) -> EvalItem:
    return EvalItem(
        question_id="Q1", question="?", answerable=True, gold_chunk_ids=gold, gold_index_version="v"
    )


def _cite(chunk_id: str) -> dict:
    return {
        "citation": 1,
        "status": "contained",
        "excerpt": EXCERPT,
        "pieces": [{"chunk_id": chunk_id, "start": 0, "end": len(EXCERPT)}],
    }


HITS = [(f"c{i}", 1 - i / 100) for i in range(1, 21)]  # c1 … c20


def test_상위5_안이면_hit이고_순위와_근거회수를_잰다() -> None:
    row = score_question(
        _item(["c3"]), HITS, {"c3": {"similarity": 0.98, "rank": 3}}, [_cite("c3")]
    )
    assert (row["hit@5"], row["rr@5"], row["outcome"]) == (True, 1 / 3, "hit")
    assert row["first_gold_rank_in_collection"] == 3
    assert row["evidence"]["mean_recall"] == 1.0
    assert row["top1_similarity"] == 0.99


def test_후보_20개_안이지만_5위_밖이면_순위_실패_밖이면_검색_누락() -> None:
    ranked = score_question(_item(["c9"]), HITS, {"c9": {"similarity": 0.9, "rank": 9}}, [])
    assert (ranked["hit@5"], ranked["hit@pool"], ranked["outcome"]) == (False, True, "순위 실패")

    missed = score_question(_item(["zz"]), HITS, {"zz": {"similarity": 0.2, "rank": 812}}, [])
    assert missed["outcome"] == "검색 누락"
    assert missed["first_gold_rank_in_collection"] == 812
    assert missed["best_gold_similarity"] == 0.2


def test_요약_분모는_답변가능_문항이고_근거없음은_따로() -> None:
    hit = score_question(_item(["c1"]), HITS, {"c1": {"similarity": 0.99, "rank": 1}}, [])
    miss = score_question(_item(["zz"]), HITS, {"zz": {"similarity": 0.2, "rank": 900}}, [])
    none = score_question(EvalItem(question_id="Q9", question="?", answerable=False), HITS, {}, [])
    summary = aggregate([hit, miss, none])
    assert (summary["answerable_n"], summary["hit@5"], summary["mrr@5"]) == (2, 0.5, 0.5)
    assert summary["outcomes"] == {"hit": 1, "검색 누락": 1}
    assert summary["unanswerable_top1_similarity"] == [0.99]


def test_PGVector_거리를_유사도로_바꾼다() -> None:
    class Store:
        def similarity_search_with_score_by_vector(self, vector, k):  # noqa: ANN001, ANN202
            return [(Document(page_content="", metadata={"chunk_id": "a"}), 0.25)]

    assert search_vector(Store(), [0.1], 1) == [("a", 0.75)]


def test_HTTP_요청_로그를_센다() -> None:
    logger = logging.getLogger("httpx")
    logger.setLevel(logging.INFO)
    with http_calls() as calls:
        logger.info('HTTP Request: POST https://api.openai.com/v1/embeddings "HTTP/1.1 200 OK"')
        logger.info(
            'HTTP Request: POST https://api.openai.com/v1/chat/completions "HTTP/1.1 429 X"'
        )
        logger.info("관계없는 로그")
    summary = summarize_calls(calls)
    assert summary["requests"] == 2
    assert summary["by_endpoint"] == {"embeddings": 1, "completions": 1}
    assert [c["status"] for c in summary["non_2xx"]] == ["429"]


# --- V3 단계별 기록 ---------------------------------------------------------------


class _BM25:
    def __init__(self, ranked: list[tuple[str, float]]) -> None:
        self.ranked = ranked

    def search(self, query: str, k: int) -> list[tuple[str, float]]:
        return self.ranked[:k]


class _Reranker:
    name = "fake"

    def score(self, query: str, texts: list[str]) -> list[float]:
        return [float(len(t)) for t in texts]  # 긴 글일수록 위로

    def truncated(self, query: str, texts: list[str]) -> int:
        return 0


def test_단계별로_벡터_BM25_RRF_재정렬_순위를_남긴다() -> None:
    from evals.search_eval import gold_movement, run_stages

    texts = {"a": "x", "b": "xx", "c": "xxx", "d": "xxxx"}
    vector = [("a", 0.9), ("b", 0.8), ("c", 0.7)]
    stages = run_stages("q", vector, _BM25([("c", 5.0), ("d", 4.0)]), texts, 3, _Reranker())

    assert [c["chunk_id"] for c in stages["vector"]] == ["a", "b", "c"]
    assert [c["chunk_id"] for c in stages["bm25"]] == ["c", "d"]
    # c 는 두 목록에 모두 있어 RRF 1위. b(벡터 2위)와 d(BM25 2위)는 점수가 같고, 먼저 넣은 벡터 쪽이
    # 앞선다. 후보는 pool(3)개로 잘려 d 가 빠진다
    assert [c["chunk_id"] for c in stages["hybrid"]] == ["c", "a", "b"]
    assert [c["chunk_id"] for c in stages["hybrid_rerank"]] == ["c", "b", "a"]
    assert stages["hybrid_rerank"][1]["rank_before_rerank"] == 3
    assert stages["overlap_vector_bm25"] == 1

    moved = gold_movement(stages, ["d", "b"])
    assert moved["ranks"]["d"] == {"vector": None, "bm25": 2, "hybrid": None, "hybrid_rerank": None}
    assert moved["ranks"]["b"] == {"vector": 2, "bm25": None, "hybrid": 3, "hybrid_rerank": 2}
    assert (moved["only_bm25"], moved["only_vector"]) == (["d"], ["b"])


def test_후보가_0개인_문항도_요약할_수_있다() -> None:
    empty = score_question(_item(["c1"]), [], {"c1": {"similarity": 0.5, "rank": 7}}, [])
    summary = aggregate([empty])
    assert summary["hit@5"] == 0.0 and summary["top1_similarity_mean"] is None
    assert summary["empty_results"] == 1
