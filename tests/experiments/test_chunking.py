"""청킹 실험 코드 테스트. 앞부분은 네트워크 · 데이터 없이, 뒷부분은 data/handoff 가 있을 때만 돈다."""

from collections import defaultdict

import pytest

from experiments.chunking.chunker import (
    R2_SECTION_KINDS,
    ExpChunk,
    build_variant,
    pack,
    split_text,
)
from experiments.chunking.data import (
    HANDOFF_DIR,
    SectionRef,
    load_handoff,
    load_questions,
    parse_question,
    section_of_plot,
)
from experiments.chunking.pipeline import normalize_query, row_id
from experiments.chunking.scoring import (
    build_probe_table,
    chunk_has_excerpt,
    excerpt_hit,
    excerpt_probes,
    excerpt_recall,
    excerpts_found,
    gold_in_index,
    normalize,
    section_hit,
    section_recall,
)


def _chunk(text: str, refs: list[SectionRef], cid: str = "c1") -> ExpChunk:
    return ExpChunk(
        chunk_id=cid,
        doc_id=refs[0].doc_id,
        refs=refs,
        doc_kind="content",
        title="t",
        header="[t]",
        text=text,
        source_url="u",
    )


# ---------- 자르기 ----------


def test_split_text_keeps_every_word_in_order() -> None:
    text = "First para. Second sentence here.\n\n" + ("Long sentence number. " * 80) + "\nLast."
    pieces = split_text(text, 200)
    assert all(len(p) <= 200 for p in pieces)
    assert normalize(" ".join(pieces)) == normalize(text)


def test_split_text_does_not_merge_when_full() -> None:
    a, b = "a" * 120, "b" * 120
    assert split_text(f"{a}\n{b}", 200) == [a, b]
    assert split_text(f"{a[:50]}\n{b[:50]}", 200) == [f"{a[:50]}\n{b[:50]}"]


def test_split_text_hard_splits_a_sentence_longer_than_limit() -> None:
    sentence = " ".join(["word"] * 100)  # 499자, 문장 끝 없음
    pieces = split_text(sentence, 100)
    assert all(len(p) <= 100 for p in pieces)
    assert normalize(" ".join(pieces)) == sentence


def test_split_text_rejects_tiny_limit() -> None:
    with pytest.raises(ValueError):
        split_text("abc", 10)


def test_pack_keeps_oversized_unit_alone() -> None:
    assert pack(["x" * 30, "y"], 10) == ["x" * 30, "y"]


# ---------- 평가셋 읽기 ----------

_EVIDENCE = (
    "[1] X\ndoc_0123456789abcdef / 섹션 23: Objects > Infinity Stones\nrevision 11\n"
    "\n[2] Y\ndoc_fedcba9876543210 / 섹션 1: Plot\nrevision 22"
)
_EXCERPT = (
    "[1] ld. The Time Stone, originally housed in the Eye of Agamotto, lets the user control time…"
    "\n\n[2] ter Thanos arrives on Titan and fights the heroes for the gauntlet in a long battle…"
)
_ROW = {
    "query_id": "Q999",
    "question": "타임 스톤이 뭐야?",
    "expected_answer": "시간 스톤",
    "answerable": True,
    "난이도": "초보",
    "채점 핵심 사실": "시간을 관장 / 아이 오브 아가모토",
    "근거 문서·절·리비전": _EVIDENCE,
    "원문 근거 발췌": _EXCERPT,
}


def test_parse_question_reads_refs_facts_and_excerpts() -> None:
    q = parse_question(_ROW)
    assert q.gold_refs == [
        SectionRef("doc_0123456789abcdef", 23),
        SectionRef("doc_fedcba9876543210", 1),
    ]
    assert q.gold_revisions == [11, 22]
    assert q.key_facts == ["시간을 관장", "아이 오브 아가모토"]
    assert set(q.excerpts) == {1, 2}
    assert q.excerpts[1].startswith("ld. The Time Stone")


# ---------- 채점 ----------


def test_excerpt_probes_drop_cut_word_and_ellipsis() -> None:
    probes = excerpt_probes(_ROW["원문 근거 발췌"].split("\n\n")[0][4:])
    assert probes
    # 잘린 첫 단어('ld.')와 '…' 는 빠지고, 영문자 · 숫자만 남는다
    assert probes[0].startswith("thetimestone")
    assert all(p.isalnum() and p == p.lower() for p in probes)


def test_letters_ignores_apostrophes_and_spacing() -> None:
    """되살린 글자(Darkhold's)와 되살리기 전 글자(Darkholds)를 같게 본다"""
    from experiments.chunking.scoring import chunk_has_excerpt, letters

    assert letters("the Darkhold's power") == letters("the Darkholds  power")
    probe = letters("Using the Darkholds power, Harkness demonstrates")
    assert chunk_has_excerpt("Using the Darkhold's power, Harkness demonstrates her", [probe])


def test_excerpt_hit_matches_even_when_chunk_cuts_the_excerpt() -> None:
    q = parse_question(_ROW)
    probes = build_probe_table(q, [])  # 다른 섹션이 없으니 아무 조각도 빠지지 않는다
    # 발췌의 뒤쪽 절반만 담은 청크 (청크 경계가 발췌를 자른 경우)
    tail = "originally housed in the Eye of Agamotto, lets the user control time"
    ref = SectionRef("doc_0123456789abcdef", 23)
    assert excerpt_hit([_chunk(f"Some intro. {tail}. More.", [ref])], probes)
    assert not excerpt_hit(
        [_chunk("Nothing about stones here at all, just filler text.", [ref])], probes
    )


def test_probe_table_drops_boilerplate_found_in_many_sections() -> None:
    q = parse_question(_ROW)
    probe = excerpt_probes(q.excerpts[1])[0]
    many = [f"other section {i} {probe} tail" for i in range(6)]
    table = build_probe_table(q, many, max_sections=5)
    assert probe not in table[1]
    assert table[1]  # 다른 조각은 남는다
    assert probe in build_probe_table(q, many[:5], max_sections=5)[1]


def test_recall_counts_fraction_of_gold() -> None:
    q = parse_question(_ROW)
    probes = build_probe_table(q, [])
    a, b = q.gold_refs
    only_a = [_chunk("originally housed in the Eye of Agamotto, lets the user control time", [a])]
    assert section_recall(only_a, q.gold_refs) == 0.5
    assert excerpt_recall(only_a, probes) == 0.5
    assert excerpts_found(only_a, probes) == [1]
    both = only_a + [_chunk("Thanos arrives on Titan and fights the heroes for the gauntlet", [b])]
    assert section_recall(both, q.gold_refs) == 1.0
    assert excerpt_recall(both, probes) == 1.0


def test_row_id_is_scoped_by_collection() -> None:
    """같은 청크가 두 컬렉션(R1 · R2)에 있어도 DB 행 ID 가 겹치지 않아야 한다"""
    assert row_id("exp_minji_r1_800", "d-s001-p001") != row_id("exp_minji_r2_800", "d-s001-p001")


def test_section_hit_uses_any_ref_of_any_top_chunk() -> None:
    gold = [SectionRef("d1", 3)]
    assert section_hit(
        [_chunk("x", [SectionRef("d2", 1)]), _chunk("y", [SectionRef("d1", 3)])], gold
    )
    assert not section_hit([_chunk("x", [SectionRef("d1", 4)])], gold)
    # 여러 섹션에 걸친 청크(R0 의 합쳐진 청크)도 하나만 맞으면 된다
    assert section_hit([_chunk("x", [SectionRef("d9", 1), SectionRef("d1", 3)])], gold)


def test_gold_in_index() -> None:
    q = parse_question(_ROW)
    assert gold_in_index({SectionRef("doc_fedcba9876543210", 1)}, q)
    assert not gold_in_index({SectionRef("doc_fedcba9876543210", 2)}, q)


# ---------- 이름 사전 치환 ----------

_ALIASES = [
    {"alias_ko": "아이언맨", "canonical_en": "Iron Man"},
    {"alias_ko": "아이언맨 2", "canonical_en": "Iron Man 2"},
    {"alias_ko": "엔드게임", "canonical_en": "Avengers: Endgame"},
]


def test_normalize_query_prefers_longest_and_keeps_original() -> None:
    assert normalize_query("아이언맨 2 결말", _ALIASES) == "아이언맨 2 (Iron Man 2) 결말"
    assert (
        normalize_query("아이언맨이랑 엔드게임", _ALIASES)
        == "아이언맨 (Iron Man)이랑 엔드게임 (Avengers: Endgame)"
    )
    assert normalize_query("타노스는?", _ALIASES) == "타노스는?"


def test_normalize_query_handles_repeated_names() -> None:
    assert (
        normalize_query("아이언맨 vs 아이언맨", _ALIASES)
        == "아이언맨 (Iron Man) vs 아이언맨 (Iron Man)"
    )


# ---------- 실제 데이터 (data/handoff 가 있을 때만) ----------

needs_data = pytest.mark.skipif(not HANDOFF_DIR.exists(), reason="data/handoff 없음")


@pytest.fixture(scope="module")
def handoff():  # type: ignore[no-untyped-def]
    return load_handoff()


@needs_data
def test_every_gold_ref_exists_with_same_revision(handoff) -> None:  # type: ignore[no-untyped-def]
    questions = load_questions()
    assert len(questions) == 20
    for q in questions:
        assert q.gold_refs, q.question_id
        assert len(q.gold_revisions) == len(q.gold_refs), q.question_id
        for ref, rev in zip(q.gold_refs, q.gold_revisions, strict=True):
            doc = handoff.documents[ref.doc_id]
            assert doc["revid"] == rev, q.question_id
            assert any(s["section_index"] == ref.section_index for s in doc["sections"])


@needs_data
def test_every_excerpt_is_found_inside_its_own_gold_section(handoff) -> None:  # type: ignore[no-untyped-def]
    """채점용 발췌 조각이 실제 정답 섹션 원문에 있어야 발췌 Hit 를 믿을 수 있다"""
    for q in load_questions():
        assert len(q.excerpts) == len(q.gold_refs), q.question_id
        for i, ref in enumerate(q.gold_refs, start=1):
            doc = handoff.documents[ref.doc_id]
            section = next(s for s in doc["sections"] if s["section_index"] == ref.section_index)
            probes = excerpt_probes(q.excerpts[i])
            assert probes, (q.question_id, i)
            assert all(chunk_has_excerpt(section["text"], [p]) for p in probes), (q.question_id, i)


@needs_data
def test_every_plot_maps_to_one_section(handoff) -> None:  # type: ignore[no-untyped-def]
    for plot in handoff.plots.values():
        ref = section_of_plot(plot, handoff.documents[plot["document_id"]])
        assert ref.doc_id == plot["document_id"]


@needs_data
@pytest.mark.parametrize("variant", ["R0", "R1", "R2"])
def test_variant_ids_unique_and_refs_valid(handoff, variant) -> None:  # type: ignore[no-untyped-def]
    chunks = build_variant(handoff, variant, 800)
    assert len({c.chunk_id for c in chunks}) == len(chunks)
    for c in chunks:
        assert c.text.strip()
        doc = handoff.documents[c.doc_id]
        indexes = {s["section_index"] for s in doc["sections"]}
        assert all(r.doc_id == c.doc_id and r.section_index in indexes for r in c.refs)


@needs_data
def test_r1_keeps_r0_headers(handoff) -> None:  # type: ignore[no-untyped-def]
    """머리말도 임베딩에 들어가므로, R1 은 R0 와 같은 문서·섹션에 같은 머리말을 써야 한다"""
    r0_headers = {
        (c.doc_id, ref): c.header for c in build_variant(handoff, "R0", 800) for ref in c.refs
    }
    for c in build_variant(handoff, "R1", 800):
        assert c.header, c.chunk_id
        assert c.header == r0_headers[(c.doc_id, c.refs[0])], c.chunk_id


@needs_data
def test_r1_is_r0_text_resized(handoff) -> None:  # type: ignore[no-untyped-def]
    """R1 은 R0 와 같은 줄거리를 크기만 바꾼 것이어야 비교가 공정하다"""
    r1 = build_variant(handoff, "R1", 800)
    assert max(len(c.text) for c in r1) <= 800
    assert {c.doc_id for c in r1} == {c["doc_id"] for c in handoff.chunks}
    # 줄거리마다 R1 조각을 이어 붙이면 원문과 같다 (공백 차이만 허용)
    by_section: dict[SectionRef, list[str]] = defaultdict(list)
    for c in r1:  # 같은 섹션 안에서는 만든 순서 = 원문 순서
        by_section[c.refs[0]].append(c.text)
    expected: dict[SectionRef, list[str]] = defaultdict(list)
    for plot in handoff.plots.values():
        expected[section_of_plot(plot, handoff.documents[plot["document_id"]])].append(plot["text"])
    assert set(by_section) == set(expected)
    for ref, texts in expected.items():
        assert normalize(" ".join(by_section[ref])) == normalize(" ".join(texts)), ref


@needs_data
def test_r2_adds_only_in_universe_sections_of_character_universe_docs(handoff) -> None:  # type: ignore[no-untyped-def]
    r1_ids = {c.chunk_id for c in build_variant(handoff, "R1", 800)}
    r2 = build_variant(handoff, "R2", 800)
    extra = [c for c in r2 if c.chunk_id not in r1_ids]
    assert len(r1_ids) + len(extra) == len(r2)
    assert {c.doc_kind for c in extra} == {"character", "character_list", "universe"}
    kinds = set()
    for c in extra:
        section = next(
            s
            for s in handoff.documents[c.doc_id]["sections"]
            if s["section_index"] == c.refs[0].section_index
        )
        kinds.add(section["section_kind"])
    # 코드의 목록(R2_SECTION_KINDS)을 그대로 쓰지 않고 직접 적는다. 목록이 바뀌면 여기서 걸린다
    assert kinds == {
        "lead",
        "biography",
        "personality",
        "abilities",
        "relationships",
        "alternate_versions",
        "other",
        "cast",
    }
    assert kinds == set(R2_SECTION_KINDS)
    assert not kinds & {"creation", "reception", "release", "see_also", "meta", "episodes"}


def test_section_of_plot_picks_innermost_section() -> None:
    """섹션이 겹치면(상위 > 하위) 가장 안쪽 섹션을 고른다"""
    doc = {
        "doc_id": "d",
        "sections": [
            {"section_index": 0, "section_path": ["Plot"], "start_char": 0, "end_char": 1000},
            {
                "section_index": 1,
                "section_path": ["Plot", "Act 1"],
                "start_char": 100,
                "end_char": 400,
            },
        ],
    }
    plot = {"plot_id": "p", "section_path": ["Plot"], "start_char": 150, "end_char": 300}
    assert section_of_plot(plot, doc) == SectionRef("d", 1)
    plot_outer = {"plot_id": "q", "section_path": ["Plot"], "start_char": 50, "end_char": 900}
    assert section_of_plot(plot_outer, doc) == SectionRef("d", 0)


@needs_data
def test_real_probe_table_drops_only_q016_boilerplate(handoff) -> None:  # type: ignore[no-untyped-def]
    """실제 데이터에서 상투 문장으로 빠지는 조각은 Q016 의 하나뿐이고, 모든 발췌에 조각이 남는다"""
    sections = [
        normalize(s["text"])
        for d in handoff.documents.values()
        for s in d["sections"]
        if s["text"].strip()
    ]
    dropped = []
    for q in load_questions():
        table = build_probe_table(q, sections)
        for i, excerpt in q.excerpts.items():
            assert table[i], (q.question_id, i)
            dropped += [(q.question_id, i) for p in excerpt_probes(excerpt) if p not in table[i]]
    assert dropped == [("Q016", 1)]


def test_refusal_on_answerable_question_is_not_correct() -> None:
    """거절 문장에 사실이 섞여 채점자가 맞다고 해도, 답변 가능 문항에서 거절은 정답이 아니다"""
    from experiments.chunking.run import answer_correct

    row = {"answerable": True, "status": "no_evidence", "judge": {"correct": True}}
    assert answer_correct(row) is False
    assert answer_correct({**row, "status": "answered"}) is True
    assert answer_correct({**row, "judge": {"correct": None}}) is None
    # 근거 없음 문항은 거절이 정답이다
    assert answer_correct(
        {"answerable": False, "status": "no_evidence", "judge": {"correct": True}}
    )


# ---------- 문장 나누기 · 겹치기 ----------

from experiments.chunking.chunker import (  # noqa: E402
    add_overlap,
    cut_report,
    is_whole_sentence,
    mid_sentence_cuts,
    sentences,
)


def test_sentences_do_not_split_on_abbreviations_or_initials() -> None:
    assert sentences("Dr. Strange meets Wong. They fight Kaecilius.") == [
        "Dr. Strange meets Wong.",
        "They fight Kaecilius.",
    ]
    assert sentences("Coulson joins S.H.I.E.L.D. He leaves later.") == [
        "Coulson joins S.H.I.E.L.D. He leaves later."
    ]
    assert sentences("Peter B. Parker arrives. Miles waits.") == [
        "Peter B. Parker arrives.",
        "Miles waits.",
    ]
    assert sentences('He said "Go." Then she left.') == ['He said "Go."', "Then she left."]
    # 소문자로 이어지면 문장 끝이 아니다
    assert sentences("It costs 3.5 million. Done.") == ["It costs 3.5 million.", "Done."]


def test_is_whole_sentence() -> None:
    assert is_whole_sentence("Thanos snaps.")
    assert is_whole_sentence('"Go," he said.')
    assert not is_whole_sentence("and Strange uses it.")
    assert not is_whole_sentence("Thanos snaps his fingers and")


def test_add_overlap_prepends_previous_last_sentence_only() -> None:
    pieces = ["First idea. Second idea here.", "Third part. The fourth one.", "Fifth comes."]
    out = add_overlap(pieces, 100)
    assert out[0] == ("First idea. Second idea here.", 0)
    assert out[1] == (
        "Second idea here.\nThird part. The fourth one.",
        len("Second idea here.") + 1,
    )
    assert out[2] == ("The fourth one.\nFifth comes.", len("The fourth one.") + 1)
    # 한 단어짜리 꼬리('Done.')는 문장으로 보지 않아 붙이지 않는다
    assert add_overlap(["Thor fights. Done.", "Next part here."], 100)[1] == ("Next part here.", 0)
    # 새 내용은 겹치기 없는 조각과 같다
    assert [t[ov:] for t, ov in out] == pieces


def test_add_overlap_skips_long_or_broken_last_sentence() -> None:
    long_last = "Short. " + "A" + " very long sentence" * 5 + "."
    out = add_overlap([long_last, "Next."], 60)
    assert out[1] == ("Next.", 0)  # 마지막 문장이 60 의 절반보다 길다
    out = add_overlap(["Ends without a stop", "Next."], 200)
    assert out[1] == ("Next.", 0)  # 온전한 문장이 아니다


@needs_data
@pytest.mark.parametrize("size", [400, 800, 1600])
def test_overlap_variant_keeps_same_cuts_and_only_prepends_whole_sentences(handoff, size) -> None:  # type: ignore[no-untyped-def]
    """겹치기 실험은 겹치기 없는 실험과 조각 경계 · ID 가 같고, 앞에 붙은 것은 앞 조각의 마지막 문장 하나뿐이다"""
    base = build_variant(handoff, "R2", size)
    ov = build_variant(handoff, "R2", size, overlap=True)
    assert [c.chunk_id for c in base] == [c.chunk_id for c in ov]
    prev = None
    for b, o in zip(base, ov, strict=True):
        assert b.refs == o.refs and b.header == o.header
        assert o.text[o.overlap_chars :] == b.text  # 새 내용은 같다
        if o.overlap_chars:
            added = o.text[: o.overlap_chars - 1]
            assert is_whole_sentence(added), (o.chunk_id, added)
            assert len(added) <= size // 2
            # 같은 섹션의 바로 앞 조각 끝에서 왔다 (다른 섹션 · 다른 줄거리에서 오지 않는다)
            assert prev is not None and prev.refs == b.refs, o.chunk_id
            assert prev.text.endswith(added), o.chunk_id
        prev = b


def test_whole_sentence_rejects_abbreviation_or_initial_endings() -> None:
    assert not is_whole_sentence("Mack and May recruit Dr.")
    assert not is_whole_sentence("Grant Ward, former S.H.I.E.L.D.")
    assert not is_whole_sentence(
        "3."
    )  # 숫자로 시작해도 'Vol. 3.' 의 꼬리처럼 내용이 없으면 막혀야 한다
    assert is_whole_sentence("Hill departs for the city.")


def test_long_paragraph_is_not_cut_after_abbreviations() -> None:
    from experiments.chunking.chunker import split_long_paragraph

    para = ("Coulson meets Dr. Simmons at the S.H.I.E.L.D. base. " * 6).strip()
    for piece in split_long_paragraph(para, 120):
        assert not piece.endswith(("Dr.", "S.H.I.E.L.D.")), piece
        assert piece.startswith("Coulson"), piece


@needs_data
@pytest.mark.parametrize("size", [400, 800, 1600])
@pytest.mark.parametrize("overlap", [False, True])
def test_c2_cuts_inside_paragraphs_fall_between_sentences(handoff, size, overlap) -> None:  # type: ignore[no-untyped-def]
    """문단 안을 자른 경계는 문장 사이여야 한다 (약어 · 이니셜 뒤에서 자르면 안 된다).

    한 문장이 크기보다 길어 단어에서 강제로 자른 경계(forced)만 예외로 둔다.
    """
    chunks = build_variant(handoff, "R2", size, overlap=overlap)
    report = mid_sentence_cuts(handoff, chunks)
    assert report["in_paragraph"] > 0
    # 출연진 표가 글자로 풀린 찌꺼기(colspan=…)는 문장이 없어 예외로 둔다 (데이터 한계로 따로 기록)
    prose_bad = [b for b in report["bad"] if "colspan=" not in b[1] + b[2]]
    assert prose_bad == [], prose_bad[:5]
    assert len(report["bad"]) <= 10


# ---------- 새 데이터 (data/schema 가 있을 때만) ----------

from experiments.chunking.schema import (  # noqa: E402
    SCHEMA_DIR,
    load_schema,
    remap_questions,
    s2_chunks,
    schema_paragraphs,
)

needs_schema = pytest.mark.skipif(not SCHEMA_DIR.exists(), reason="data/schema 없음")


@pytest.fixture(scope="module")
def schema():  # type: ignore[no-untyped-def]
    return load_schema()


@needs_schema
def test_remapped_gold_refs_point_to_same_named_sections(schema, handoff) -> None:  # type: ignore[no-untyped-def]
    """옛 문서 ID 를 새 ID 로 바꿔도 같은 문서 · 같은 제목의 절을 가리켜야 한다"""
    old_qs = load_questions()
    new_qs = remap_questions(old_qs, schema)
    by = {(s["doc_id"], s["section_index"]): s for s in schema.sections}
    for old_q, new_q in zip(old_qs, new_qs, strict=True):
        for o, n in zip(old_q.gold_refs, new_q.gold_refs, strict=True):
            old_sec = next(
                s
                for s in handoff.documents[o.doc_id]["sections"]
                if s["section_index"] == o.section_index
            )
            assert by[(n.doc_id, n.section_index)]["section_path"] == old_sec["section_path"]
            assert n.section_index == o.section_index


@needs_schema
def test_s2_keeps_only_mcu_scope_and_covers_all_gold(schema) -> None:  # type: ignore[no-untyped-def]
    chunks = s2_chunks(schema, 800)
    scopes = {schema.meta[c.doc_id].get("scope_status") for c in chunks}
    assert scopes <= {"mcu", None}, scopes
    refs = {r for c in chunks for r in c.refs}
    qs = remap_questions(load_questions(), schema)
    assert all(gold_in_index(refs, q) for q in qs if q.answerable)
    # 에피소드 요약도 재료로 들어간다
    assert sum(1 for c in chunks if " · plot]" in c.header and c.header.count(" · ") == 2) > 500


@needs_schema
def test_s2_excerpts_match_restored_text(schema) -> None:  # type: ignore[no-untyped-def]
    """새 데이터는 글자를 되살렸지만, 영문자 · 숫자만 비교하면 발췌 55건이 모두 제 절에 있다"""
    by = {(s["doc_id"], s["section_index"]): s for s in schema.sections}
    for q in remap_questions(load_questions(), schema):
        for i, ref in enumerate(q.gold_refs, start=1):
            probes = excerpt_probes(q.excerpts[i])
            assert all(
                chunk_has_excerpt(by[(ref.doc_id, ref.section_index)]["text"], [p]) for p in probes
            ), (
                q.question_id,
                i,
            )


@needs_schema
@pytest.mark.parametrize("size", [400, 800, 1600])
def test_s2_overlap_only_prepends_and_cuts_are_clean(schema, size) -> None:  # type: ignore[no-untyped-def]
    base = s2_chunks(schema, size)
    ov = s2_chunks(schema, size, overlap=True)
    assert [c.chunk_id for c in base] == [c.chunk_id for c in ov]
    for b, o in zip(base, ov, strict=True):
        assert o.text[o.overlap_chars :] == b.text
        if o.overlap_chars:
            assert not o.first_in_unit and is_whole_sentence(o.text[: o.overlap_chars - 1])
    assert max(len(c.text) for c in base) <= size
    report = cut_report(schema_paragraphs(schema), base)
    assert [b for b in report["bad"] if "colspan=" not in b[1] + b[2]] == []


def test_hard_split_prefers_initials_then_comma_then_space() -> None:
    """이니셜 때문에 문장 여러 개가 뭉쳐 너무 길어지면, 실제 문장 끝일 가능성이 큰 '이니셜 뒤 대문자'에서 먼저 자른다"""
    from experiments.chunking.chunker import _split_sentence_hard

    merged = (
        "Ward helps Malick take down S.H.I.E.L.D. At dinner with Coulson, Price reveals her plans."
    )
    pieces = _split_sentence_hard(merged, 70)
    assert pieces[0] == "Ward helps Malick take down S.H.I.E.L.D."
    assert pieces[1].startswith("At dinner")
    # 이니셜이 없으면 쉼표 뒤, 그것도 없으면 공백에서 자른다
    assert _split_sentence_hard("One part here, and then another long part follows on", 30)[0] == (
        "One part here,"
    )


def test_months_and_company_abbreviations_do_not_end_sentences() -> None:
    assert sentences("It debuted in Captain America #117 (Sept. 1969). He returns.") == [
        "It debuted in Captain America #117 (Sept. 1969).",
        "He returns.",
    ]
    assert len(sentences("It was released by Warner Bros. Pictures, which won.")) == 1


def test_overlap_skips_fragment_that_only_looks_like_a_sentence() -> None:
    """강제로 자른 조각의 꼬리('24, 2019, …')는 원문에서 문장 경계 뒤가 아니라 붙이지 않는다"""
    source = "The film opened on April 24, 2019, and the gross was $1.7 billion. Next fact here."
    pieces = [
        "The film opened on April",
        "24, 2019, and the gross was $1.7 billion.",
        "Next fact here.",
    ]
    out = add_overlap(pieces, 200, source)
    assert out[2] == ("Next fact here.", 0)


@needs_schema
def test_s2_includes_plot_sections_named_differently(schema) -> None:  # type: ignore[no-untyped-def]
    """'Story' · 'Premise' 처럼 제목이 달라 team_section 이 비어 있는 줄거리 절도 들어가야 한다"""
    refs = {r for c in s2_chunks(schema, 800) for r in c.refs}
    plot_like = [
        s
        for s in schema.sections
        if s["section_kind"] == "plot"
        and s["team_section"] is None
        and s["text"].strip()
        and schema.in_scope(s["doc_id"])
        and schema.documents[s["doc_id"]]["doc_type"]
        not in ("character", "character_list", "universe")
    ]
    assert plot_like
    assert all(SectionRef(s["doc_id"], s["section_index"]) in refs for s in plot_like)
    assert not any("None" in c.header for c in s2_chunks(schema, 800))


@needs_schema
def test_s2_overlap_prefixes_start_at_real_sentence_boundaries(schema) -> None:  # type: ignore[no-untyped-def]
    from experiments.chunking.chunker import _begins_after_sentence_end
    from experiments.chunking.schema import _units

    texts = {}
    for ref, _, header, text in _units(schema):
        texts.setdefault((ref, header), []).append(text)
    for c in s2_chunks(schema, 400, overlap=True):
        if c.overlap_chars:
            added = c.text[: c.overlap_chars - 1]
            assert any(
                _begins_after_sentence_end(t, added) for t in texts[(c.refs[0], c.header)]
            ), (c.chunk_id, added[:60])


# ---------- 옛 팀 레포에서 가져온 4가지: 실패 분류 · 후보 표시 · 함정 문항 · 두 표 내보내기 ----------

from experiments.chunking.data import PROJECT_ROOT, TRICK_SET  # noqa: E402
from experiments.chunking.run import answer_correct, export_rows, summarize  # noqa: E402
from experiments.chunking.scoring import FAILURE_TYPES, classify_failure, gold_rank  # noqa: E402


def _cf(**kw):
    base = dict(
        answerable=True,
        status="answered",
        correct=False,
        in_index=True,
        first_gold_rank=1,
        top_k=5,
        cited_gold_section=False,
    )
    base.update(kw)
    return classify_failure(**base)


def test_classify_failure_each_stage():
    assert _cf(status="retrieval_error") == "호출 오류"
    assert _cf(correct=True, cited_gold_section=True) == "정답"
    assert _cf(correct=True, cited_gold_section=False) == "잘못된 인용"
    assert _cf(in_index=False, first_gold_rank=None) == "색인에 근거 없음"
    assert _cf(first_gold_rank=None) == "검색 누락"
    assert _cf(first_gold_rank=6) == "순위 밀림"
    assert _cf(first_gold_rank=5, status="no_evidence") == "근거 받고 거절"
    assert _cf(first_gold_rank=5) == "생성 오류"
    assert _cf(answerable=False, correct=True) == "정답"
    assert _cf(answerable=False, correct=False) == "근거 없음 대응 실패"
    # 채점 실패(None)는 맞았다고 보지 않는다
    assert _cf(correct=None, first_gold_rank=2) == "생성 오류"


def test_classify_failure_only_known_labels():
    import itertools

    seen = set()
    for a, st, co, ii, gr, cg in itertools.product(
        (True, False),
        ("answered", "no_evidence", "generation_error"),
        (True, False, None),
        (True, False),
        (None, 1, 5, 6),
        (True, False),
    ):
        seen.add(
            classify_failure(
                answerable=a,
                status=st,
                correct=co,
                in_index=ii,
                first_gold_rank=gr,
                top_k=5,
                cited_gold_section=cg,
            )
        )
    assert seen <= set(FAILURE_TYPES)


def test_gold_rank_first_gold_position():
    g = SectionRef("d", 1)

    def c(ref):
        return ExpChunk(
            chunk_id="x",
            doc_id=ref.doc_id,
            refs=[ref],
            doc_kind="content",
            title="t",
            header="[t]",
            text="x",
            source_url="u",
            revid=None,
        )

    ranked = [c(SectionRef("d", 2)), c(SectionRef("e", 1)), c(g), c(g)]
    assert gold_rank(ranked, [g]) == 3
    assert gold_rank(ranked[:2], [g]) is None


def _row(qid, difficulty, correct, status="answered", answerable=True, failure="정답"):
    return {
        "question_id": qid,
        "answerable": answerable,
        "difficulty": difficulty,
        "needs_extra_docs": False,
        "status": status,
        "gold_in_index": True,
        "hit_section": True,
        "hit_excerpt": True,
        "section_recall": 1.0,
        "excerpt_recall": 1.0,
        "excerpts_found": [1],
        "context_chars": 100,
        "cited_gold": True,
        "judge": {"correct": correct},
        "latency_ms": {"total": 10, "generate": 5},
        "tokens": {"input": 1, "output": 1},
        "failure_type": failure,
        "gold_rank": 1,
        "candidates": [
            {
                "rank": 1,
                "chunk_id": "c",
                "doc_id": "d",
                "sections": ["d#1", "d#2"],
                "vector_score": 0.5,
                "used_in_model_input": True,
                "cited_in_answer": True,
                "gold_section": True,
                "gold_excerpt": False,
            }
        ],
    }


def test_summarize_keeps_trick_out_of_main_metrics():
    rows = [_row("Q1", "초보", True), _row("T1", "함정", False, failure="생성 오류")]
    s = summarize(rows)
    assert s["n_questions"] == 1 and s["answer_correct"] == 1.0
    assert s["failure_types"] == {"정답": 1}
    assert s["trick"] == {
        "n": 1,
        "answer_correct": 0.0,
        "failure_types": {"생성 오류": 1},
        "rows": {"T1": False},
    }


def test_refusal_is_not_correct_for_answerable():
    assert answer_correct(_row("Q", "초보", True, status="no_evidence")) is False


def test_export_rows_two_tables():
    runs, cands = export_rows({"run_id": "r", "rows": [_row("Q1", "초보", True)]})
    assert runs[0]["run_id"] == "r" and runs[0]["answer_correct"] is True
    assert runs[0]["latency_total_ms"] == 10 and runs[0]["failure_type"] == "정답"
    # 채점 근거(사실별 맞음/틀림 · 메모)도 표에 남는다
    assert runs[0]["judge_facts"] == "" and runs[0]["judge_note"] == ""
    noted = _row("Q2", "초보", False)
    noted["judge"] = {"correct": False, "facts": [True, False], "note": "하나 빠짐"}
    r2 = export_rows({"run_id": "r", "rows": [noted]})[0][0]
    assert r2["judge_facts"] == "1,0" and r2["judge_note"] == "하나 빠짐"
    assert cands[0]["question_id"] == "Q1" and cands[0]["sections"] == "d#1 d#2"
    # 후보 기록이 없는 옛 실행은 후보 표가 비어 있다 (에러가 아니라)
    old = _row("Q1", None, True)
    del old["candidates"]
    assert export_rows({"run_id": "r", "rows": [old]})[1] == []


def test_trick_questions_parse_and_point_to_new_data():
    qs = load_questions(TRICK_SET)
    assert [q.question_id for q in qs] == ["T001", "T002", "T003"]
    assert all(q.difficulty == "함정" and q.answerable and q.gold_refs and q.excerpts for q in qs)
    schema_dir = PROJECT_ROOT / "data" / "schema"
    if not schema_dir.exists():
        pytest.skip("data/schema 없음")
    from experiments.chunking.schema import load_schema, remap_questions
    from experiments.chunking.scoring import build_probe_table, chunk_has_excerpt, letters

    schema = load_schema()
    texts = {(s["doc_id"], s["section_index"]): s["text"] for s in schema.sections}
    for q in remap_questions(qs, schema):
        probes = build_probe_table(q, [])
        for i, ref in enumerate(q.gold_refs, start=1):
            body = texts[(ref.doc_id, ref.section_index)]
            assert chunk_has_excerpt(body, probes[i]), (q.question_id, i, letters(body)[:60])
