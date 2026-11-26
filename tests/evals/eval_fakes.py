from build.chunks import index_version
from shared.core.models import Candidate, Chunk, RunRecord, SourceId, Trace
from shared.core.schemas import AnswerStatus, RetrievalMode, Section


def chunk(n: int, text: str) -> Chunk:
    return Chunk(
        chunk_id=f"tt0371746-wikipedia-plot-p{n:02d}",
        doc_id="tt0371746-wikipedia",
        work_id="tt0371746",
        source_id=SourceId.WIKIPEDIA_EN,
        section=Section.PLOT,
        position=n,
        total=3,
        text=text,
        source_url="https://en.wikipedia.org/w/index.php?oldid=1",
        source_ids={"team_document_id": "doc_aa", "team_section_index": "1", "overlap_chars": "0"},
    )


# 아이언맨 줄거리 세 청크 — 두 번째가 정답 발췌를 담고 있다
CHUNKS = [
    chunk(1, "Tony Stark is captured by the Ten Rings in Afghanistan."),
    chunk(2, "He builds a suit of armor to escape the cave."),
    chunk(3, "Back home, Stark announces that his company will stop making weapons."),
]
# question_set 형식의 답변 가능 문항 (근거 칸 · 발췌 칸 포함)
ROW = {
    "query_id": "Q900",
    "question": "아이언맨은 어떻게 탈출해?",
    "answerable": True,
    "expected_answer": "갑옷을 만들어 탈출한다.",
    "채점 핵심 사실": "갑옷 제작 / 동굴 탈출",
    "난이도": "초보",
    "근거 문서·절·리비전": "[1] Iron Man\ndoc_aa / 섹션 1: Plot\nrevision 1\nhttps://x",
    "원문 근거 발췌": "[1] He builds a suit of armor to escape the cave.",
}
# 근거 없음 문항
NO_ROW = {**ROW, "query_id": "Q901", "answerable": False, "채점 핵심 사실": "단정하지 않기"}


def record(status: AnswerStatus, ranked: list[str], cited: list[str], error: str | None = None) -> RunRecord:  # fmt: skip
    return RunRecord(
        run_id="r-try1",
        question_id="Q900",
        status=status,
        candidates=[Candidate(chunk_id=c, rank=i, rank_before_rerank=None, vector_score=None,
                              bm25_score=None, rrf_score=None, rerank_score=None)
                    for i, c in enumerate(ranked, 1)],
        cited_chunk_ids=cited,
        error=error,
        embedding_model="e", llm_model="m", prompt_version="p", params={"mode": "B"},
        trace=Trace(query_ko="q", query_normalized="q", retrieval_mode=RetrievalMode.HYBRID,
                    evidence_source="documents" if cited else "none",
                    index_version=index_version(CHUNKS)),
    )  # fmt: skip
