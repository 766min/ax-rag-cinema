import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from build.cards import build_index_chunks
from build.chunks import Dataset, Recipe, parent_sources
from evals.items import to_items
from evals.recorder import Recorder
from evals.run import RunMeta, input_text, run_questions
from retrieve.pipeline import MODES, Retriever, SearchConfig, with_rewrite
from service.answer import make_generate_en
from shared.core.models import Alias, Document, SourceId, Work

# 같은 네 문장을 세 번 반복해 여러 청크가 나오게 한다
PLOT = "\n".join(
    [
        "Tony Stark is a billionaire who builds weapons for his company.",
        "He is captured by the Ten Rings in Afghanistan and held in a cave.",
        "With Yinsen he builds a suit of armor and escapes the cave.",
        "Back home he announces that Stark Industries will stop making weapons.",
    ]
    * 3
)


def dataset() -> Dataset:
    """작품 하나 · 문서 하나 · 절 둘(소개 · 줄거리)짜리 가짜 데이터셋."""
    work = Work(work_id="tt0371746", title_ko="아이언맨", title_original="Iron Man", format="movie",
                year=2008, phase="1", timeline_order=5, is_canon=True,
                source_ids={"imdb_tconst": "tt0371746"})  # fmt: skip
    doc = Document(doc_id="tt0371746-wikipedia", work_id="tt0371746", source_id=SourceId.WIKIPEDIA_EN,
                   source_url="https://en.wikipedia.org/w/index.php?oldid=1", revid=1, doc_type="film",
                   source_ids={"team_document_id": "doc_aa"})  # fmt: skip
    meta = {"status": "released", "collect_wave": "1", "doc_id": doc.doc_id, "canon_source": None,
            "canon_asof": None, "saga": None, "release_order": 1}  # fmt: skip
    sections = [
        {"doc_id": doc.doc_id, "section_index": 0, "team_section": "synopsis", "section_kind": "lead",
         "section_path": ["(lead)"], "text": "Iron Man is a 2008 superhero film.", "links": [], "episode_entries": []},
        {"doc_id": doc.doc_id, "section_index": 1, "team_section": "plot", "section_kind": "plot",
         "section_path": ["Plot"], "text": PLOT, "links": [], "episode_entries": []},
    ]  # fmt: skip
    return Dataset(
        works=[work],
        documents={doc.doc_id: doc},
        works_meta={work.work_id: meta},
        docs_meta={doc.doc_id: {"title": "Iron Man", "infobox": None, "scope_status": "mcu"}},
        sections={doc.doc_id: sections},
    )


# 정답 발췌가 'escapes the cave' 문장인 문항. SECRET 글자는 생성 입력에 새면 안 되는 표시
ROW = {
    "query_id": "Q900",
    "question": "아이언맨은 어떻게 탈출해?",
    "answerable": True,
    "expected_answer": "SECRET-REFERENCE 갑옷을 만들어 탈출한다.",
    "채점 핵심 사실": "SECRET-FACT 갑옷 / 탈출",
    "근거 문서·절·리비전": "[1] Iron Man\ndoc_aa / 섹션 1: Plot\nrevision 1\nhttps://x",
    "원문 근거 발췌": "[1] With Yinsen he builds a suit of armor and escapes the cave.",
}


class Store:
    """질문과 상관없이 정해진 순서를 돌려주는 가짜 벡터 저장소 (코사인 거리)."""

    def __init__(self, ids: list[str]) -> None:
        self.ids = ids

    def similarity_search_with_score(self, query: str, k: int) -> list:
        return [
            (SimpleNamespace(metadata={"chunk_id": c}), 0.1 * i) for i, c in enumerate(self.ids[:k])
        ]


class Reranker:
    """가짜 재정렬기. 'escapes' 가 든 청크에 높은 점수를 주고, 받은 후보 수를 inputs 에 남긴다."""

    name = "fake-reranker"
    info = {"model": "fake"}

    def __init__(self) -> None:
        self.inputs: list[int] = []

    def score(self, query: str, texts: list[str]) -> list[float]:
        self.inputs.append(len(texts))
        return [1.0 if "escapes" in t else 0.0 for t in texts]


class LLM:
    """가짜 생성 모델. 첫 근거 덩어리의 첫 청크 ID 를 인용하고, 받은 프롬프트를 prompts 에 남긴다."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def ainvoke(self, messages: list[Any]) -> Any:
        human = messages[1].content
        self.prompts.append(human)
        first = human.split("Evidence:\n[")[1].split("]")[0].split(",")[0]
        return SimpleNamespace(content=f"He builds armor and escapes [{first}].\nCOVERAGE: full")


def test_six_modes_branch_and_trace_citations(tmp_path: Path) -> None:
    """여섯 구성 각각: 리라이트 · 재정렬 분기, 인용 추적, 부모 문맥 복원, 정답표 미노출, 기록 파일 수."""
    ds = dataset()
    chunks = build_index_chunks(ds, Recipe("t", max_chars=140))
    body = [c.chunk_id for c in chunks if "-plot-" in c.chunk_id]
    sources = parent_sources(ds)
    items, _ = to_items([ROW], chunks)
    assert items[0].gold_chunk_ids

    reranker = Reranker()
    retriever = Retriever(Store(list(reversed(body))), chunks, reranker)
    aliases = [
        Alias(alias_ko="아이언맨", canonical_en="Iron Man", kind="work", target_id="tt0371746")
    ]
    rewrites: list[str] = []

    async def rewrite(_system: str, text: str) -> str:
        rewrites.append(text)
        return "How does [[TERM_0]] escape?"

    async def echo(_system: str, text: str) -> str:
        return text

    from build.chunks import index_version

    for name, mode in MODES.items():
        llm = LLM()
        before, before_rerank = len(rewrites), len(reranker.inputs)
        recorder = Recorder(f"it-{name.replace('+', 'plus')}-try1", results_dir=tmp_path)
        context = "parent" if name in ("C", "C+") else "chunk"

        [rec] = asyncio.run(
            run_questions(
                items, mode_name=name, retriever=retriever,
                generate_en=make_generate_en(llm, context=context, sources=sources),
                aliases=aliases, config=SearchConfig(top_k=5, pool=20), recorder=recorder,
                meta=RunMeta(index_version(chunks), "e", "m", "p3-gather", "a"),
                query_translator=rewrite, answer_translator=echo,
            )
        )  # fmt: skip

        # 리라이트(기본으로 모든 구성) · 재정렬 분기. 기록에는 실제로 돈 구성(A → A+ …)이 남는다
        assert len(rewrites) - before == 1, name
        assert rec.trace.query_en == "How does Iron Man escape?"
        assert rec.trace.retrieval_mode == with_rewrite(mode)
        reranked = len(reranker.inputs) - before_rerank
        assert reranked == (1 if name in ("C", "C+") else 0), name
        if reranked:
            assert reranker.inputs[-1] == len(rec.candidates)  # 결합 후보 전부를 재정렬

        # 인용 ⊆ 모델이 읽은 범위 ⊆ 상위 generate_k ⊆ 후보
        top = rec.top_chunk_ids(SearchConfig().generate_k)
        read_ids = [cid for block in rec.params["read"] for cid in block["ids"]]
        assert rec.cited_chunk_ids and set(rec.cited_chunk_ids) <= set(read_ids) <= set(top)
        assert set(top) <= {c.chunk_id for c in rec.candidates}

        # 기록만으로 모델 입력을 되살려도 인용한 청크 글이 들어 있다
        text = input_text(rec.params["read"], {c.chunk_id: c for c in chunks}, sources)
        for cid in rec.cited_chunk_ids:
            assert next(c for c in chunks if c.chunk_id == cid).text in text

        assert not any("SECRET" in p for p in llm.prompts)  # 정답표가 생성 입력에 없다
        assert items[0].hit_at(rec, 5) in (True, False)  # 색인 버전이 맞아 Hit@k 를 잴 수 있다

    assert len({f.name for f in tmp_path.iterdir()}) == len(MODES)

    # 리라이트를 끄면(비교 실험) 리라이트를 부르지 않고 원질문으로 검색하며, 기록에도 A 로 남는다
    before = len(rewrites)
    [plain] = asyncio.run(
        run_questions(
            items, mode_name="A", retriever=retriever, generate_en=make_generate_en(LLM()),
            aliases=aliases, config=SearchConfig(top_k=5, pool=20),
            recorder=Recorder("it-A-norewrite-try1", results_dir=tmp_path),
            meta=RunMeta(index_version(chunks), "e", "m", "p3-gather", "a"),
            query_translator=rewrite, answer_translator=echo, rewrite=False,
        )
    )  # fmt: skip
    assert len(rewrites) == before
    assert plain.trace.query_en == "" and plain.trace.retrieval_mode == MODES["A"]
    assert plain.params["mode"] == "A" and plain.params["rewrite"] is False
