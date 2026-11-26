"""팀원 질문셋 네 출처를 하나의 평가셋 형식으로 합치고, 칸별 빈칸 표를 만든다 (평가셋 확장 1~2단계).

    uv run python scripts/build_eval_set.py
    uv run python scripts/build_eval_set.py --bae <경로> --minji <경로>

입력
    question_set.jsonl                       팀 공식 20문항 (형식의 기준)
    --bae / --chung                          배·정 평가셋 md (두 파일이 같으면 하나만 쓴다)
    --minji                                  민지 청킹 실험 md 의 함정 문항 T001~T003 (원본 jsonl 이 없어 표에서 복원)
    tmp/questions_blind_v2.jsonl             문서를 보지 않고 쓴 40문항 (선택)
    tmp/questions_from_chunks.jsonl          청크를 보고 쓴 60문항 (선택)
    tmp/chunks_dryrun.jsonl                  위 두 실험 세트의 청크 ID 를 문서·절·발췌로 바꿀 때만 쓴다 (선택)
    data/cineverse-dataset-schema/           documents · sections · works (필수)
    --extra                                  check_eval_gen.py 를 통과한 Codex 생성 문항 (여러 개 가능)

본체에는 청킹과 무관한 정답(문서 · 절 · 원문 발췌 · 핵심 사실)만 남긴다. gold · primary_gold 같은 청크 ID 는
넣지 않는다. 채점할 청킹 결과가 정해지면 scripts/attach_chunks.py 로 그 청킹 기준의 청크 ID 를 붙인다.
선택 입력이 없으면 그 출처만 건너뛰고 끝까지 돈다.

실험 세트(blind · chunk)는 원래 청크 ID 만 있다. 그 청크가 속한 절을 찾고, 청크 안에서 기준 답과 단어가
가장 많이 겹치는 문장을 원문 발췌로 고른다(근거.발췌_자동 = true, 사람이 확인할 것).

출력
    tmp/eval_set.json            최종 결과물. 팀 공식 형식(question_set.jsonl 칸 + 합의한 추가 칸 + 근거 + split)의
                                 JSON 배열 — eval/dev.json · eval/test.json 에 그대로 넣는 모양
    tmp/eval_merged.jsonl        작업용 전체 칸 (분류 방식 · 근거 확인 결과 등 포함)
    tmp/eval_merged_dupes.jsonl  중복으로 뺀 문항과 어느 문항의 중복인지
    docs/experiments/eval_set_gaps.md   칸별 현재 개수 · 목표 · 부족분, 검토할 목록

question_set.jsonl 에 더한 칸
    근거위치    줄거리 | 시놉시스 | 인물 | 세계관·사물 | 메타 | 추천·가치관 | 문서에 없음
    질문유형    사실 | 관계 | 인과 | 시간선 | 비교·가치관 | 추천 | 근거 검증
    eval_track  retrieval | answer_only (추천 · 비교·가치관은 answer_only: 검색 지표에서 빼고 루브릭 채점)
    variant_of  말투만 바꾼 변형이면 원본 query_id, 아니면 null (변형은 문항 수로 세지 않는다)
    작성자      출처 작성자
그 밖의 칸
    출처 · 원본_id · 원래_난이도 · 분류방식(수동|자동) · 전제 오류 · 근거(구조화 목록) · 근거확인(검사 결과)
    qid · question_ko · question_en · category — scripts/eval_retrieval.py 가 읽는 칸 (question_en 은 실험 세트에만)
근거 한 항목: {doc_id, team_doc_id, section_index, section_path, doc_type, team_section, 발췌, 발췌_확인}
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

from attach_chunks import ChunkIndex  # 같은 scripts/ 폴더
from baseline_chunks import build as build_baseline
from check_eval_gen import Corpus as DataCorpus
from check_eval_gen import render_text

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "cineverse-dataset-schema"
DOWNLOADS = Path.home() / "Downloads"

LOCS = ["줄거리", "시놉시스", "인물", "세계관·사물", "메타", "추천·가치관", "문서에 없음"]
LEVELS = ["초보", "중수", "고수", "함정"]
TYPES = ["사실", "관계", "인과", "시간선", "비교·가치관", "추천", "근거 검증"]
ANSWER_ONLY = {"추천", "비교·가치관"}

# 합의한 목표 분포 (None = 목표 없음). 칸마다 최소 5문항은 별도 규칙.
TARGET = {
    "줄거리": [25, 15, 10, 5],
    "시놉시스": [15, 5, None, 3],
    "인물": [20, 10, 10, 4],
    "세계관·사물": [15, 8, 5, 3],
    "메타": [10, 5, None, 2],
    "추천·가치관": [10, 10, 10, None],
    "문서에 없음": [None, None, None, None],  # 표에 없는 줄. 함정 중 '문서에 없음'만 따로 센다
}
MIN_PER_CELL = 5

# 출처 우선순위 (중복이면 앞쪽을 남긴다)
PRIORITY = ["team", "minji", "bae", "blind", "chunk", "gen"]


# ---------------------------------------------------------------- 공통 데이터


def read_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_corpus():
    docs = {d["doc_id"]: d for d in read_jsonl(DATA / "documents.jsonl")}
    team2wp = {
        d["source_ids"]["team_document_id"]: d["doc_id"]
        for d in docs.values()
        if d.get("source_ids", {}).get("team_document_id")
    }
    sections = {(s["doc_id"], s["section_index"]): s for s in read_jsonl(DATA / "sections.jsonl")}
    return docs, team2wp, sections


def loc_of(doc_type: str, team_section: str | None) -> str:
    """문서 종류 + 팀 섹션 → 근거위치.

    줄거리   = plot 절 (영화·단편·스페셜·시리즈·에피소드)
    시놉시스 = 에피소드·시즌·시리즈의 lead(synopsis)
    메타     = 영화·단편·스페셜의 lead (감독·개봉·제작 정보가 주로 여기 있다) + works.jsonl
    인물     = 인물 문서 전체
    세계관·사물 = 세계관(universe)·그 밖의 비작품 문서
    """
    if doc_type == "character":
        return "인물"
    if doc_type in ("film", "short", "special"):
        return "줄거리" if team_section == "plot" else "메타"
    if doc_type in ("series", "season", "episode"):
        return "줄거리" if team_section == "plot" else "시놉시스"
    return "세계관·사물"


def blank(**kw) -> dict:
    row = {
        "query_id": None,
        "question": None,
        "expected_answer": None,
        "answerable": True,
        "난이도": None,
        "채점 핵심 사실": "",
        "근거 문서·절·리비전": "",
        "검토 메모": "",
        "원문 근거 발췌": "",
        "근거위치": None,
        "질문유형": None,
        "eval_track": None,
        "variant_of": None,
        "작성자": None,
        "출처": None,
        "원본_id": None,
        "원래_난이도": None,
        "분류방식": "수동",
        "전제 오류": None,
        "근거": [],
        "근거확인": None,
        "question_en": None,
        "category": None,
    }
    row.update(kw)
    return row


def finish(row: dict) -> dict:
    row["eval_track"] = "answer_only" if row["질문유형"] in ANSWER_ONLY else "retrieval"
    # eval_retrieval.py 호환 칸
    row["qid"] = row["query_id"]
    row["question_ko"] = row["question"]
    if not row["category"]:
        row["category"] = "unanswerable" if not row["answerable"] else row["근거위치"]
    assert row["근거위치"] in LOCS, row
    assert row["난이도"] in LEVELS, row
    assert row["질문유형"] in TYPES, row
    return row


# ---------------------------------------------------------------- 1. 팀 question_set.jsonl

# 질문유형은 손으로 정했다. 근거위치는 근거 문서에서 자동으로 뽑고, 아래 표가 있으면 덮어쓴다.
TEAM_TYPE = {
    "Q001": "사실",
    "Q002": "사실",
    "Q003": "인과",
    "Q004": "사실",
    "Q007": "관계",
    "Q009": "관계",
    "Q011": "사실",
    "Q015": "사실",
    "Q016": "사실",
    "Q019": "근거 검증",
    "Q040": "인과",
    "Q042": "인과",
    "Q044": "시간선",
    "Q052": "인과",
    "Q056": "시간선",
    "Q041": "시간선",
    "Q045": "관계",
    "Q047": "시간선",
    "Q049": "시간선",
    "Q058": "시간선",
}
TEAM_LOC = {
    "Q019": "문서에 없음",  # 코믹스 설정은 문서 범위 밖
    "Q056": "메타",  # 작품 배치(시간순)는 works.jsonl timeline_order · 작품 lead
}
EVID_RE = re.compile(r"(doc_[0-9a-f]+) / 섹션 (\d+): ([^\n]+)")
EXCERPT_RE = re.compile(r"\[(\d+)\]\s*(.+?)(?=\n\s*\n\[\d+\]|\Z)", re.S)


def team_rows(path: Path, corpus) -> list[dict]:
    docs, team2wp, sections = corpus
    out = []
    for r in read_jsonl(path):
        qid = r["query_id"]
        evid = []
        for m in EVID_RE.finditer(r["근거 문서·절·리비전"]):
            wp = team2wp.get(m.group(1))
            sec = sections.get((wp, int(m.group(2)))) if wp else None
            evid.append(
                {
                    "doc_id": wp,
                    "team_doc_id": m.group(1),
                    "section_index": int(m.group(2)),
                    "section_path": m.group(3).strip(),
                    "doc_type": docs[wp]["doc_type"] if wp else None,
                    "team_section": sec["team_section"] if sec else None,
                    "절_일치": bool(sec and " > ".join(sec["section_path"]) == m.group(3).strip()),
                }
            )
        # 발췌가 그 절 본문에 실제로 있는지 (… 로 잘린 끝은 떼고 앞 60자로 본다)
        excerpts = {
            int(n): t.strip().rstrip("…").strip()
            for n, t in EXCERPT_RE.findall(r["원문 근거 발췌"])
        }
        for i, e in enumerate(evid, start=1):
            sec = sections.get((e["doc_id"], e["section_index"]))
            ex = excerpts.get(i, "")
            e["발췌"] = ex
            e["발췌_확인"] = bool(sec and ex and ex[:60] in sec["text"])
        works = {e["doc_id"] for e in evid}
        obj = {1: "초보", 2: "중수"}.get(len(works), "고수") if r["answerable"] else "함정"
        loc = TEAM_LOC.get(qid) or (
            loc_of(evid[0]["doc_type"], evid[0]["team_section"]) if evid else "문서에 없음"
        )
        level = r["난이도"] if r["answerable"] else "함정"
        row = blank(
            **{
                k: r[k]
                for k in (
                    "query_id",
                    "question",
                    "expected_answer",
                    "answerable",
                    "채점 핵심 사실",
                    "근거 문서·절·리비전",
                    "검토 메모",
                    "원문 근거 발췌",
                )
            }
        )
        row.update(
            {
                "난이도": level,
                "원래_난이도": r["난이도"],
                "근거위치": loc,
                "질문유형": TEAM_TYPE[qid],
                "작성자": "team",
                "출처": "question_set.jsonl",
                "원본_id": qid,
                "근거": evid,
                "근거확인": {
                    "문서_찾음": all(e["doc_id"] for e in evid),
                    "절_일치": all(e["절_일치"] for e in evid),
                    "발췌_확인": all(e["발췌_확인"] for e in evid),
                    "팀섹션_없는_근거": [
                        f"{e['doc_id']}#{e['section_index']} {e['section_path']}"
                        for e in evid
                        if not e["team_section"]
                    ],
                    "문서수_기준_난이도": obj,
                },
            }
        )
        out.append(finish(row))
    return out


# ---------------------------------------------------------------- 2. 배·정 md

# (근거위치, 난이도, 질문유형, 메모). 난이도는 합의한 객관 기준으로 다시 붙였다.
BAE_CLASS = {
    "B01": ("메타", "초보", "시간선", "개봉순 첫 작품. 근거는 works.jsonl year"),
    "B02": (
        "메타",
        "중수",
        "시간선",
        "개봉순과 극중 시간순을 구분. works.jsonl year·timeline_order",
    ),
    "B03": ("추천·가치관", "고수", "추천", "Phase 1 여러 작품 중 필수·선택 관람 구분"),
    "B04": ("세계관·사물", "초보", "사실", "'마블 영화는 전부 MCU?' 일반화 바로잡기 포함"),
    "B05": ("줄거리", "초보", "인과", "Iron Man plot"),
    "B06": ("줄거리", "중수", "관계", "스포일러 없이 두 작품 연결만 답해야 함"),
    "B07": ("메타", "중수", "시간선", "작품 3개지만 works.jsonl 한 곳에서 답이 나와 중수로 둠"),
    "B08": ("추천·가치관", "초보", "추천", "장르 칸이 데이터에 없음 → 줄거리로 판단해야 함"),
    "B09": ("추천·가치관", "초보", "추천", ""),
    "B10": ("추천·가치관", "초보", "추천", "관람등급 칸이 데이터에 없음 → 등급 근거는 못 댐"),
    "A01": ("추천·가치관", "중수", "비교·가치관", "Civil War + Tony·Steve 인물 문서"),
    "A02": ("줄거리", "중수", "인과", "Age of Ultron → Civil War"),
    "A03": ("추천·가치관", "고수", "비교·가치관", "Iron Man ~ Endgame 가치관 변화"),
    "A04": ("줄거리", "중수", "시간선", "IW 의 로키와 2012년 로키 구분"),
    "A05": ("추천·가치관", "중수", "비교·가치관", "가치관 비교는 해석임을 밝혀야 함"),
    "A06": ("추천·가치관", "중수", "추천", ""),
    "A07": ("추천·가치관", "중수", "추천", ""),
    "A08": ("추천·가치관", "중수", "추천", ""),
    "A09": ("추천·가치관", "중수", "추천", ""),
    "A10": ("줄거리", "함정", "근거 검증", "'모든 미래' 일반화 vs IW 의 14,000,605개"),
}
MD_ROW = re.compile(r"^\|\s*([AB]\d\d)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*$")


def bae_rows(path: Path) -> list[dict]:
    q, gt = {}, {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = MD_ROW.match(line)
        if not m:
            continue
        qid, a, b, c = m.groups()
        if qid not in q:
            q[qid] = (a, b, c)  # 질문 | 평가 유형 | 필요 데이터
        else:
            gt[qid] = (a, b)  # 기대 답변 | 근거 문서 후보
    out = []
    for qid, (question, kind, need) in q.items():
        loc, level, qtype, memo = BAE_CLASS[qid]
        answer, cand = gt.get(qid, ("", ""))
        row = blank(query_id=f"bae-{qid}", question=question, expected_answer=answer)
        row.update(
            {
                "채점 핵심 사실": "",  # 원본은 기대 답변 안에 조건으로 섞여 있다 → 4단계에서 3~4개로 나눈다
                "근거 문서·절·리비전": f"(후보) {cand}",
                "검토 메모": "; ".join(
                    x for x in (f"원래 평가 유형: {kind}", f"필요 데이터: {need}", memo) if x
                ),
                "난이도": level,
                "원래_난이도": "초보" if qid.startswith("B") else "중급·중고수",
                "근거위치": loc,
                "질문유형": qtype,
                "작성자": "Bae/Chung",
                "출처": path.name,
                "원본_id": qid,
                "근거확인": {
                    "문서_찾음": False,
                    "발췌_확인": False,
                    "메모": "근거 문서·절·발췌 미작성",
                },
            }
        )
        out.append(finish(row))
    return out


# ---------------------------------------------------------------- 3. 민지 함정 문항

MINJI_ROWS = [
    (
        "T001",
        "엔드게임에서 결국 타노스가 이겼지?",
        "줄거리",
        "세계관·사물",
        "아니다. 타노스는 엔드게임에서 패배한다. 토니 스타크가 스톤으로 타노스와 그 군대를 없애고 그 대가로 목숨을 잃는다.",
        "전제 바로잡기: 타노스는 졌다 / 토니가 스톤으로 타노스와 군대를 없앰 / 토니의 희생",
        "엔드게임에서 타노스가 이겼다",
        "Avengers: Endgame Plot",
    ),
    (
        "T002",
        "타임 스톤은 원래 테서랙트 안에 들어 있던 거지?",
        "세계관·사물",
        None,
        "아니다. 테서랙트에 들어 있던 것은 스페이스 스톤이고, 타임 스톤은 아이 오브 아가모토에 보관돼 있었다.",
        "전제 바로잡기: 테서랙트 = 스페이스 스톤 / 타임 스톤은 아이 오브 아가모토",
        "타임 스톤이 테서랙트 안에 있었다",
        "Features of the Marvel Cinematic Universe > Infinity Stones",
    ),
    (
        "T003",
        "로키는 오딘의 친아들이지?",
        "인물",
        None,
        "아니다. 로키는 서리 거인 라우페이의 아들이고 오딘이 입양해 키웠다.",
        "전제 바로잡기: 입양 / 친아버지는 라우페이(서리 거인)",
        "로키가 오딘의 친아들이다",
        "Loki (Marvel Cinematic Universe) > Early life",
    ),
]


def minji_rows(path: Path | None) -> list[dict]:
    if path is not None and path.exists():
        text = path.read_text(encoding="utf-8")
        missing = [t[0] for t in MINJI_ROWS if t[1] not in text]
        if missing:
            print(f"  ! 민지 md 에서 질문 문구를 못 찾음: {missing} (표가 바뀌었는지 확인)")
    out = []
    for qid, q, loc, _, ans, facts, premise, where in MINJI_ROWS:
        row = blank(query_id=f"minji-{qid}", question=q, expected_answer=ans)
        row.update(
            {
                "채점 핵심 사실": facts,
                "근거 문서·절·리비전": f"(후보) {where}",
                "검토 메모": "원본 trick_questions.jsonl 미확보 — 민지 md 1-6 표에서 복원. 전제 바로잡기를 따로 채점.",
                "난이도": "함정",
                "원래_난이도": "함정",
                "근거위치": loc,
                "질문유형": "근거 검증",
                "작성자": "minji",
                "출처": path.name if path else "2026-11-25-minji-chunking.md",
                "원본_id": qid,
                "전제 오류": premise,
                "근거확인": {
                    "문서_찾음": False,
                    "발췌_확인": False,
                    "메모": "근거 문서·절·발췌 미작성",
                },
            }
        )
        out.append(finish(row))
    return out


# ---------------------------------------------------------------- 4. 실험 세트 (blind_v2 · from_chunks)

# blind_v2 q035~q040 은 한글이 깨져 있어 영어 질문에서 다시 썼다.
BLIND_FIX = {
    "q035": (
        "《캡틴 아메리카: 시빌 워》에서 빈에서 티찰라 왕을 죽인 폭탄을 설치한 어벤져스 멤버는 누구야?",
        "어벤져스 멤버가 설치했다는 전제가 틀림. 폭탄은 제모가 설치하고 버키에게 누명을 씌웠다.",
        "줄거리",
    ),
    "q036": (
        "《어벤져스: 인피니티 워》에서 완다는 왜 비전 이마에 박힌 소울 스톤을 파괴했어?",
        "비전 이마의 스톤은 소울 스톤이 아니라 마인드 스톤이다.",
        "줄거리",
    ),
    "q037": (
        "《어벤져스: 시크릿 워즈》에서 닥터 둠에게 마지막 일격을 날리는 사람은 누구야?",
        None,
        "문서에 없음",
    ),
    "q038": ("《어벤져스: 둠스데이》 마지막 전투에서 희생하는 인물은 누구야?", None, "문서에 없음"),
    "q039": (
        "《닥터 스트레인지》에서 교통사고 뒤 스티븐 스트레인지를 처음 치료한 병원은 어디야?",
        None,
        "문서에 없음",
    ),
    "q040": (
        "《어벤져스: 엔드게임》에서 나타샤가 보르미르에서 희생할 때 나오는 노래는 뭐야?",
        None,
        "문서에 없음",
    ),
}
QTYPE_MAP = {"who": "사실", "what": "사실", "where": "사실", "why": "인과", "sequence": "시간선"}


def guess_type(q_en: str) -> str:
    s = q_en.lower()
    if s.startswith("why") or " why " in s:
        return "인과"
    if re.search(r"\b(in what order|which came first|precedes?|preceded)\b", s):
        return "시간선"  # before/after/first 는 '그때 일어난 일'을 묻는 사실 질문에도 흔해서 쓰지 않는다
    if re.search(
        r"\b(father|mother|brother|sister|twin|partner|related|relationship|friend|daughter|son)\b",
        s,
    ):
        return "관계"
    return "사실"


STOP = set(
    "the a an and or of to in on at for with from by is are was were be been his her their its this that "
    "who what which when where why how does did do into after before as he she they it has have had".split()
)


def words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", (s or "").lower()) if len(w) > 2 and w not in STOP}


def pick_excerpt(chunk_text: str, answer: str, question_en: str) -> str:
    """청크 안에서 기준 답(가중 3)·영어 질문(가중 1)과 단어가 가장 많이 겹치는 문장. 너무 짧으면 다음 문장을 붙인다."""
    sents = []
    for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\"“(])", chunk_text.strip()):
        # "Samuel L." · "Dr." · "S.H.I.E.L.D." 뒤에서 잘린 조각은 앞 문장에 다시 붙인다
        if sents and re.search(
            r"(\b[A-Z]|\b(?:Dr|Mr|Mrs|Ms|St|Jr|Sr|Lt|Col|Gen|Sgt|No|vs|Vol))\.$", sents[-1]
        ):
            sents[-1] = f"{sents[-1]} {s}"
        elif s.strip():
            sents.append(s)
    if not sents:
        return chunk_text[:300]
    aw, qw = words(answer), words(question_en)

    def alnum(s):
        return re.sub(r"[^a-z0-9]", "", s.lower())

    ans = alnum(re.sub(r"^(the|a|an)\s+", "", (answer or "").strip().rstrip("."), flags=re.I))

    def score(i: int):
        s = sents[i]
        whole = (
            10 if len(ans) >= 3 and ans in alnum(s) else 0
        )  # 짧은 답(S.H.I.E.L.D. 등)이 통째로 있으면 우선
        return (whole + 3 * len(words(s) & aw) + len(words(s) & qw), -i)

    best = max(range(len(sents)), key=score)
    out = sents[best]
    if len(out) < 40 and best + 1 < len(sents):
        out = f"{out} {sents[best + 1]}"
    return out


def section_of_chunk(c: dict, dc: DataCorpus) -> int | None:
    head = re.sub(r"\s+", " ", c["text"]).strip()[:80]
    for (doc_id, idx), sec in dc.sections.items():
        if (
            doc_id == c["doc_id"]
            and sec.get("team_section") == c.get("section")
            and head in dc.sec_ws[(doc_id, idx)]
        ):
            return idx
    return None


def exp_rows(path: Path, chunks: dict, dc: DataCorpus, prefix: str) -> list[dict]:
    out = []
    for r in read_jsonl(path):
        qid = r["qid"]
        q_ko = r.get("question_ko")
        premise = r.get("premise_error")
        loc_override = None
        memo = [r.get("label_note") or ""]
        if qid in BLIND_FIX and prefix == "blind":
            q_ko, premise, loc_override = BLIND_FIX[qid]
            memo.append("한국어 깨짐 → question_en 에서 다시 씀(검토 필요)")
        # 원래 청크 ID → 그 청크가 속한 절 + 답과 가장 많이 겹치는 문장 (청크 ID 는 본체에 남기지 않는다)
        prim = r.get("primary_gold") or r.get("gold") or []
        answer_en = r.get("expected_answer") or r.get("reference_answer") or ""
        evid, lost = [], []
        for cid in prim:
            c = chunks.get(cid)
            idx = section_of_chunk(c, dc) if c else None
            if idx is None:
                lost.append(cid)
                continue
            e = dc.evidence(c["doc_id"], idx, pick_excerpt(c["text"], answer_en, r["question_en"]))
            e["발췌_자동"] = True
            evid.append(e)
        if lost:
            memo.append(f"절을 못 찾은 원래 청크: {', '.join(lost)}")
        answerable = bool(r.get("answer_in_corpus", True)) and r.get("category") != "unanswerable"
        if premise:
            level = "함정"
        elif not answerable:
            level = "함정"
        else:
            level = "초보"  # gold/primary_gold 는 '그 청크 하나로 답할 수 있는' 청크라 근거 1곳
        if loc_override:
            loc = loc_override
        elif not answerable and not premise:
            loc = "문서에 없음"
        elif r.get("category") == "production":
            loc = "메타"
        elif evid and evid[0]["doc_type"]:
            loc = loc_of(evid[0]["doc_type"], evid[0]["team_section"])
        else:
            loc = "문서에 없음"
        qtype = (
            "근거 검증"
            if level == "함정"
            else (QTYPE_MAP.get(r.get("qtype")) or guess_type(r["question_en"]))
        )
        row = blank(
            query_id=f"{prefix}-{qid}",
            question=q_ko,
            expected_answer=r.get("expected_answer") or r.get("reference_answer"),
            answerable=answerable,
        )
        row.update(
            {
                "검토 메모": "; ".join(x for x in memo if x),
                "난이도": level,
                "원래_난이도": None,
                "근거위치": loc,
                "질문유형": qtype,
                "작성자": "ohmseok",
                "출처": str(path.relative_to(ROOT)).replace("\\", "/"),
                "원본_id": qid,
                "분류방식": "자동",
                "전제 오류": premise,
                "근거": evid,
                "근거확인": {
                    "문서_찾음": not lost,
                    "발췌_확인": all(e["발췌_확인"] for e in evid),
                    "발췌_자동": True,
                    "팀섹션_없는_근거": [],
                    "메모": "발췌는 원래 청크에서 자동으로 고른 문장 · 핵심 사실 미작성",
                },
                "question_en": r["question_en"],
                "category": r.get("category"),
            }
        )
        render_text(row, dc)
        out.append(finish(row))
    return out


# ---------------------------------------------------------------- 중복 제거


def norm(q: str) -> str:
    q = re.sub(r"\((스포|스포일러)\)", "", q or "")
    q = re.sub(r"[\s《》<>「」『』\"'“”‘’?!.,·:()\-]", "", q)
    for a, b in (("져", "저"), ("바키", "버키")):
        q = q.replace(a, b)
    return q


def bigrams(s: str) -> set[str]:
    return {s[i : i + 2] for i in range(len(s) - 1)}


def jaccard(a: str, b: str) -> float:
    x, y = bigrams(norm(a)), bigrams(norm(b))
    return len(x & y) / len(x | y) if x and y else 0.0


# 손으로 확인한 같은 질문 (뒤쪽이 앞쪽의 중복). 근거 범위가 넓은 쪽을 남긴다.
MANUAL_DUPES = {
    "blind-q002": "Q009",  # 윈터 솔저 정체
    "chunk-c051": "blind-q027",  # 비전 몸의 인피니티 스톤
    "chunk-c016": "blind-q010",  # 트찰라를 이긴 사람 (킬몽거)
    "chunk-c010": "blind-q014",  # 사고로 수술을 못 하게 된 의사 (스트레인지)
}
# 겹치지만 묻는 범위가 달라 남긴 쌍 — 4단계에서 둘 중 하나를 다른 대상으로 바꿀지 사람이 정한다.
MANUAL_NEAR = [
    ("bae-A02", "Q040", "울트론·소코비아 → 시빌 워. Q040 은 토니의 동기까지 포함"),
    ("bae-A02", "Q052", "소코비아 협정의 원인. Q052 는 결과(분열·수감)까지 포함"),
]


def dedupe(rows: list[dict], auto_th: float):
    rank = {s: i for i, s in enumerate(PRIORITY)}

    def src(r):
        return "team" if r["작성자"] == "team" else r["query_id"].split("-")[0]

    rows = sorted(rows, key=lambda r: rank[src(r)])
    kept, dupes, near = [], [], []
    seen_exact: dict[str, str] = {}
    for r in rows:
        key = norm(r["question"] or r["question_en"] or "")
        why = None
        if r["query_id"] in MANUAL_DUPES:
            why = (MANUAL_DUPES[r["query_id"]], "수동")
        elif key in seen_exact:
            why = (seen_exact[key], "문구 같음")
        else:
            best = max(
                ((jaccard(r["question"] or "", k["question"] or ""), k) for k in kept),
                key=lambda t: t[0],
                default=(0.0, None),
            )
            if best[0] >= auto_th:
                why = (best[1]["query_id"], f"문자 2-gram 유사도 {best[0]:.2f}")
            elif best[0] >= 0.30:
                near.append(
                    (
                        round(best[0], 2),
                        r["query_id"],
                        best[1]["query_id"],
                        r["question"],
                        best[1]["question"],
                    )
                )
        if why:
            dupes.append({**r, "중복_of": why[0], "중복_이유": why[1]})
        else:
            kept.append(r)
            seen_exact[key] = r["query_id"]
    return kept, dupes, sorted(near, reverse=True)


# ---------------------------------------------------------------- 빈칸 표


def gap_table(rows: list[dict]) -> list[str]:
    base = [r for r in rows if not r["variant_of"]]
    cnt = Counter((r["근거위치"], r["난이도"]) for r in base)
    lines = [
        "| 근거위치 | "
        + " | ".join(f"{lv} 현재/목표" for lv in LEVELS)
        + " | 합계 현재/목표 | 부족 합계 |",
        "|---|" + "---|" * (len(LEVELS) + 2),
    ]
    tot_now = tot_tgt = tot_gap = 0
    for loc in LOCS:
        cells, now_sum, tgt_sum, gap_sum = [], 0, 0, 0
        for i, lv in enumerate(LEVELS):
            n, t = cnt[(loc, lv)], TARGET[loc][i]
            now_sum += n
            if t is None:
                cells.append(f"{n} / –" if n else "–")
                continue
            tgt_sum += t
            gap = max(t - n, 0)
            gap_sum += gap
            mark = " ✅" if n >= t else (" ⚠️" if n < min(MIN_PER_CELL, t) else "")
            cells.append(f"**{n}** / {t}{mark}" if gap else f"{n} / {t}{mark}")
        tot_now += now_sum
        tot_tgt += tgt_sum
        tot_gap += gap_sum
        lines.append(
            f"| {loc} | "
            + " | ".join(cells)
            + f" | {now_sum} / {tgt_sum or '–'} | {gap_sum or '–'} |"
        )
    lines.append(f"| **합계** | | | | | **{tot_now} / {tot_tgt}** | **{tot_gap}** |")
    return lines


def type_table(rows: list[dict]) -> list[str]:
    base = [r for r in rows if not r["variant_of"]]
    cnt = Counter((r["근거위치"], r["질문유형"]) for r in base)
    lines = ["| 근거위치 | " + " | ".join(TYPES) + " |", "|---|" + "---|" * len(TYPES)]
    for loc in LOCS:
        lines.append(f"| {loc} | " + " | ".join(str(cnt[(loc, t)] or "") for t in TYPES) + " |")
    return lines


def official_section(official: list[dict], diag: list[dict]) -> list[str]:
    def yn(v):
        return "–" if v is None else ("예" if v else "아니오")

    lines = [
        "## 0. 공식 평가셋 30문항 (2026-11-26 확정)",
        "",
        "확정본은 `eval/dev.json`(10) · `eval/test.json`(20) 이다. 이 스크립트는 eval/ 을 쓰지 않는다 — 아래 표는 `tmp/eval_official.json` 기준이고,"
        " 확정 뒤 공식 문항을 바꾸려면 OFFICIAL_DEV · OFFICIAL_TEST 와 eval/ 파일을 함께 고친다(test 는 잠근 뒤 고치지 않음)."
        " 나머지는 확장 진단셋 `tmp/eval_diag.json`.",
        "",
        "고른 기준",
        "1. 루트 question_set.jsonl 20문항을 모두 넣는다 (팀이 정답 섹션 · 발췌 · 핵심 사실을 이미 검토함).",
        "2. 팀 20문항에 없는 칸을 10문항으로 채운다: 시놉시스 · 에피소드(이름표 약점), 메타(감독), 인물 소개,"
        " 인물 동기(인과), 함정 2(전제 바로잡기), 근거 없음 2(미공개 작품 · 문서 밖 세부).",
        "3. 청크를 보고 쓴 질문보다 보지 않고 쓴 질문을 먼저 고른다. 고른 팀 밖 문항은 기준 답 · 핵심 사실 · 원문 발췌를 손으로 다시 적었다.",
        "4. test 20 = 답변 가능 17 + 근거 없음 3 (architecture.md §8). dev 10 은 난이도를 고르게 섞고 함정 1을 둔다.",
        "",
        "| split | 문항 | 질문 | 근거위치 | 난이도 | 질문유형 | 필요 문서 수 | 경계 걸침 | 빠진 것 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in official:
        lines.append(
            f"| {r['split']} | {r['query_id']} | {r['question']} | {r['근거위치']} | {r['난이도']} | {r['질문유형']} "
            f"| {r.get('필요 문서 수') if r.get('필요 문서 수') is not None else '–'} | {yn(r.get('경계 걸침'))} "
            f"| {', '.join(r['검증_빠진것']) or '–'} |"
        )
    for split in ("dev", "test"):
        part = [r for r in official if r["split"] == split]
        lines += [
            "",
            f"**{split} {len(part)}문항** — 난이도 "
            + " · ".join(f"{k} {v}" for k, v in Counter(r["난이도"] for r in part).items())
            + " | 근거위치 "
            + " · ".join(f"{k} {v}" for k, v in Counter(r["근거위치"] for r in part).items())
            + f" | 근거 없음 {sum(not r['answerable'] for r in part)} · 다중 작품(필요 문서 2+) {sum((r.get('필요 문서 수') or 0) >= 2 for r in part)}"
            + f" · 경계 걸침 {sum(bool(r.get('경계 걸침')) for r in part)}",
        ]
    axes = ["인물 서사·사건 흐름", "경계 걸침", "다중 작품", "인물·세계관·에피소드"]
    lines += [
        "",
        "## 0-1. 약점 축별 문항 수 (팀 인사이트 취합의 비교 단계용)",
        "",
        "| 축 | 쓰임 | 공식 | 확장 진단셋 |",
        "|---|---|---|---|",
    ]
    use = {
        "인물 서사·사건 흐름": "부모 문맥 확장 효과",
        "경계 걸침": "문장 겹침(CK05 · CK09) 비교",
        "다중 작품": "다중 작품 답변 충족",
        "인물·세계관·에피소드": "이름 빠진 문단 · 작품 이름표",
    }
    for a in axes:
        lines.append(
            f"| {a} | {use[a]} | {sum(a in r['진단_축'] for r in official)} | {sum(a in r['진단_축'] for r in diag)} |"
        )
    need = Counter(m for r in diag for m in r["검증_빠진것"])
    lines += [
        "",
        "확장 진단셋에서 빠진 것 (문항 수): "
        + (" · ".join(f"{k} {v}" for k, v in need.most_common()) or "없음"),
        "- '정답 문장(자동 선택 · 검토)' = 실험 세트에서 기준 답과 단어가 가장 많이 겹치는 문장을 자동으로 고른 것",
        "- '경계 걸침' 은 기준선 청킹(최대 800자 · 문장 경계 · 무겹침, scripts/baseline_chunks.py)에서 정답 발췌가 청크 두 개 이상에 걸치는지다.",
        "",
    ]
    return lines


def write_report(
    path: Path, kept, dupes, near, sources: dict[str, int], args, official=(), diag=()
):
    by_src = Counter(r["작성자"] for r in kept)
    lines = (
        [
            "# 평가셋 빈칸 표 (자동 생성)",
            "",
            f"`scripts/build_eval_set.py` 가 {date.today().isoformat()} 에 만든 파일이다. 손으로 고치지 말고 스크립트를 다시 돌린다.",
            "",
        ]
        + official_section(list(official), list(diag))
        + [
            "- 통일 jsonl: `tmp/eval_merged.jsonl` · 중복으로 뺀 문항: `tmp/eval_merged_dupes.jsonl`",
            "- 표는 `variant_of` 가 있는 말투 변형을 세지 않는다.",
            "- 굵은 숫자 = 목표에 못 미친 칸, ⚠️ = 최소 5문항도 안 되는 칸, ✅ = 목표 달성.",
            "- '문서에 없음' 줄은 합의한 표에 없던 줄이다. 함정 중 근거가 아예 없는 문항(미공개 작품 · 문서 범위 밖 세부)을 따로 센다.",
            "  잘못된 전제처럼 바로잡을 근거가 문서에 있는 함정은 그 근거가 있는 줄에 센다.",
            "",
            "## 출처별 개수",
            "",
            "| 출처 | 읽은 문항 | 남긴 문항 | 분류 방식 |",
            "|---|---|---|---|",
        ]
    )
    names = {
        "team": "question_set.jsonl (팀)",
        "Bae/Chung": "배·정 md (두 파일 동일)",
        "minji": "민지 함정 T001~T003",
        "ohmseok": "실험 세트 blind_v2 + from_chunks",
        "codex": "Codex 생성 (check_eval_gen.py 통과분)",
    }
    how = {"ohmseok": "자동", "codex": "생성"}
    for k, n in sources.items():
        lines.append(f"| {names[k]} | {n} | {by_src[k]} | {how.get(k, '수동')} |")
    lines.append(f"| **합계** | {sum(sources.values())} | {len(kept)} | |")
    lines += ["", "## 1. 근거위치 × 난이도", ""] + gap_table(kept)
    lines += ["", "## 2. 근거위치 × 질문유형 (현재)", ""] + type_table(kept)
    track = Counter(r["eval_track"] for r in kept)
    lines += [
        "",
        f"eval_track: retrieval {track['retrieval']} · answer_only {track['answer_only']}",
        "",
    ]

    lines += [
        "## 3. 중복으로 뺀 문항",
        "",
        "| 뺀 문항 | 남긴 문항 | 이유 | 질문 |",
        "|---|---|---|---|",
    ]
    for d in dupes:
        lines.append(f"| {d['query_id']} | {d['중복_of']} | {d['중복_이유']} | {d['question']} |")
    lines += [
        "",
        f"## 4. 비슷하지만 남긴 쌍 (유사도 0.30 이상, 자동 제거 기준 {args.dup_threshold}) — 사람이 확인",
        "",
        "| 유사도 | 문항 | 비슷한 문항 | 질문 | 비슷한 질문 |",
        "|---|---|---|---|---|",
    ]
    for s, a, b, qa, qb in near:
        lines.append(f"| {s} | {a} | {b} | {qa} | {qb} |")
    lines += ["", "손으로 찾아 남긴 쌍:", "", "| 문항 | 비슷한 문항 | 판단 |", "|---|---|---|"]
    lines += [f"| {a} | {b} | {why} |" for a, b, why in MANUAL_NEAR]

    lines += ["", "## 5. 근거 확인이 필요한 문항", ""]
    team = [r for r in kept if r["작성자"] == "team"]
    bad_ex = [r["query_id"] for r in team if not r["근거확인"]["발췌_확인"]]
    bad_sec = [r["query_id"] for r in team if not r["근거확인"]["절_일치"]]
    nonchunk = [(r["query_id"], x) for r in team for x in r["근거확인"]["팀섹션_없는_근거"]]
    lvl_diff = [
        (r["query_id"], r["난이도"], r["근거확인"]["문서수_기준_난이도"])
        for r in team
        if r["answerable"] and r["난이도"] != r["근거확인"]["문서수_기준_난이도"]
    ]
    no_ev = Counter(r["작성자"] for r in kept if not r["근거확인"].get("발췌_확인"))
    lines += [
        f"- 팀 문항 중 발췌가 그 절 본문에 그대로 없는 것: {', '.join(bad_ex) or '없음'}",
        f"- 팀 문항 중 절 번호와 절 경로가 안 맞는 것: {', '.join(bad_sec) or '없음'}",
        f"- 팀 근거 중 팀 섹션(plot/synopsis/intro) 밖 절: {len(nonchunk)}곳 — 이 절을 청킹하지 않는 청킹에서는 못 찾는다"
        " (청킹별 결과는 attach_chunks.py 출력에서 확인)",
    ]
    lines += [f"  - {q}: {x}" for q, x in nonchunk]
    lines += (
        [
            "- 팀 난이도와 '근거 문서 수' 기준이 다른 문항 (팀 라벨 유지, 5단계 순위로 다시 판단):",
        ]
        + [f"  - {q}: 팀 {a} / 문서 수 기준 {b}" for q, a, b in lvl_diff]
        + [
            "- 발췌·핵심 사실이 아직 없는 문항: " + ", ".join(f"{k} {v}" for k, v in no_ev.items()),
            "  (4단계에서 문서·절 + 원문 발췌 + 핵심 사실 3~4개로 채운다)",
            "",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- 공식 30문항 · 확장 진단셋 (팀 인사이트 취합 2026-11-25)

# 공식 평가셋 (2026-11-26 사용자 확정 → eval/dev.json · eval/test.json). 팀 question_set.jsonl 20문항 + 빈 칸을 채우는 10문항.
# test 20 = 답변 가능 17 + 근거 없음 3 (architecture.md §8). 함정(전제 바로잡기)은 dev · test 에 하나씩.
OFFICIAL_DEV = [
    "Q001",
    "Q004",
    "Q009",
    "Q015",
    "Q040",
    "Q056",
    "Q045",
    "blind-q018",
    "chunk-c025",
    "minji-T003",
]
OFFICIAL_TEST = [
    "Q002",
    "Q003",
    "Q007",
    "Q011",
    "Q016",
    "Q019",
    "Q042",
    "Q044",
    "Q052",
    "Q041",
    "Q047",
    "Q049",
    "Q058",
    "chunk-c038",
    "blind-q031",
    "blind-q026",
    "chunk-c006",
    "minji-T001",
    "blind-q037",
    "blind-q040",
]

# 공식으로 고른 팀 밖 문항의 빈 칸 (한국어 기준 답 · 핵심 사실 · 손으로 고른 원문 발췌).
# 발췌는 sections.jsonl 본문에서 그대로 복사했고, 실행할 때 다시 확인한다.
OFFICIAL_FILL = {
    "blind-q018": {
        "expected_answer": "케이트 비숍이다. 클린트 바턴은 크리스마스까지 가족에게 돌아가기 위해 케이트 비숍과 손잡고 과거의 적들과 맞선다.",
        "채점 핵심 사실": "케이트 비숍 / 클린트 바턴과 함께 활동 / 크리스마스까지 가족에게 돌아가려 함",
        "근거": [
            (
                "tt10160804-wikipedia",
                0,
                "It sees Clint Barton as he partners with Kate Bishop to confront enemies from his past to be with his family in time for Christmas.",
            )
        ],
    },
    "chunk-c025": {
        "expected_answer": "테서랙트를 훔친 로키는 TVA(시간 변동 관리국)로 끌려가, 위험한 또 다른 버전(변종)의 자신을 잡는 일을 돕게 된다.",
        "채점 핵심 사실": "TVA 로 끌려감 / 테서랙트를 훔친 뒤 / 위험한 자신의 변종을 잡는 일을 도움",
        "근거": [
            (
                "tt9140554-S1-wikipedia",
                0,
                "sees Loki brought to the mysterious Time Variance Authority (TVA) after stealing the Tesseract during the events of Avengers: Endgame (2019), and is forced to help catch a dangerous variant version of himself.",
            )
        ],
    },
    "chunk-c038": {
        "expected_answer": "《완다비전》 에피소드에서 완다와 비전이 다투던 중, 죽은 오빠(쌍둥이 형제) '피에트로'가 문 앞에 나타난다. 다만 배우가 바뀌어(다른 얼굴) 나타난 인물이다.",
        "채점 핵심 사실": "피에트로(완다의 죽은 쌍둥이 형제) / 완다비전 에피소드의 장면 / 다른 배우로 바뀌어 등장",
        "근거": [
            (
                "wp-66628751",
                1,
                'They are interrupted again when her dead brother "Pietro" arrives at the front door. Watching the broadcast, Darcy Lewis notes that Pietro has been "recast".',
            )
        ],
    },
    "blind-q031": {
        "expected_answer": "앤서니 루소와 조 루소 형제가 연출했다.",
        "채점 핵심 사실": "앤서니 루소 / 조 루소",
        "근거": [
            (
                "tt4154796-wikipedia",
                0,
                "Directed by Anthony and Joe Russo and written by Christopher Markus and Stephen McFeely",
            )
        ],
    },
    "blind-q026": {
        "expected_answer": "미 공군 전투기 조종사였다. 광속 엔진 시험이 잘못돼 테서랙트의 우주 에너지에 노출되면서 초능력을 얻었다.",
        "채점 핵심 사실": "미 공군 전투기 조종사 / 광속 엔진 시험 사고 / 테서랙트 에너지 노출로 능력 획득",
        "근거": [
            (
                "wp-65464184",
                0,
                "Danvers is initially depicted as a U.S. Air Force fighter pilot who was given superhuman abilities when a light-speed engine test went wrong and she was exposed to the cosmic energy of the Tesseract.",
            )
        ],
    },
    "chunk-c006": {
        "expected_answer": "아내 리(Li)가 원우의 적인 아이언 갱에게 살해당했기 때문이다. 원우는 텐 링즈를 다시 끼고 아이언 갱을 몰살한 뒤 조직의 지휘를 다시 맡는다.",
        "채점 핵심 사실": "아내 리의 죽음 / 아이언 갱의 소행 / 텐 링즈를 다시 끼고 조직 지휘 재개",
        "근거": [
            (
                "tt9376612-wikipedia",
                1,
                "When Shang-Chi is seven years old, Li is murdered by Wenwu's enemies, the Iron Gang. Wenwu dons the rings once again, massacres the Iron Gang, and resumes leadership of his organization.",
            )
        ],
    },
    "minji-T001": {
        "근거": [
            (
                "tt4154796-wikipedia",
                1,
                "Stark steals the Stones and uses them to disintegrate Thanos and his army, sacrificing himself.",
            )
        ],
        "채점 핵심 사실": "전제 바로잡기: 타노스는 패배한다 / 토니가 스톤으로 타노스와 군대를 없앰 / 토니의 희생",
    },
    "minji-T003": {
        "근거": [
            ("wp-61651800", 0, "Loki Laufeyson, later known through adoption as Loki Odinson")
        ],
        "채점 핵심 사실": "전제 바로잡기: 친아들이 아니라 입양 / 본래 라우페이의 아들(로키 라우페이슨)",
    },
    "blind-q037": {
        "expected_answer": "문서에 근거가 없다. 《어벤져스: 시크릿 워즈》는 아직 공개되지 않은 작품이라 결말을 알 수 없다고 안내해야 한다.",
        "채점 핵심 사실": "근거 부족 안내 / 미공개 작품의 결말을 지어내지 않음",
    },
    "blind-q040": {
        "expected_answer": "문서에 근거가 없다. 보르미르 장면에 쓰인 음악은 수집한 줄거리 · 문서에 나오지 않는다고 안내해야 한다.",
        "채점 핵심 사실": "근거 부족 안내 / 곡 이름을 지어내지 않음",
    },
}
# 함정 · 일반화 문항의 '전제 오류' (원래 값이 없는 것만)
PREMISE = {
    "bae-A10": "닥터 스트레인지가 모든 미래를 볼 수 있다는 것은 일반화다. 인피니티 워에서는 14,000,605개의 가능한 미래를 살펴봤다.",
    "bae-B04": "마블 영화가 전부 MCU 에 속한다는 전제가 틀림 (MCU 는 마블 작품 일부의 연결된 세계관)",
}


def apply_fills(rows: list[dict], dc: DataCorpus) -> None:
    by_id = {r["query_id"]: r for r in rows}
    for qid, fill in OFFICIAL_FILL.items():
        r = by_id.get(qid)
        if not r:
            continue
        for k in ("expected_answer", "채점 핵심 사실"):
            if k in fill:
                r[k] = fill[k]
        if "근거" in fill:
            r["근거"] = [dc.evidence(d, i, ex) for d, i, ex in fill["근거"]]
            render_text(r, dc)
            r["근거확인"] = {
                **(r.get("근거확인") or {}),
                "문서_찾음": True,
                "발췌_확인": all(e["발췌_확인"] for e in r["근거"]),
                "발췌_자동": False,
            }
        r["검토 메모"] = "; ".join(
            x for x in (r.get("검토 메모"), "공식 후보로 빈 칸을 손으로 채움(검토 필요)") if x
        )
    for qid, text in PREMISE.items():
        if qid in by_id and not by_id[qid].get("전제 오류"):
            by_id[qid]["전제 오류"] = text


def add_columns(rows: list[dict], baseline) -> None:
    """경계 걸침 · 필요 문서 수 칸을 채운다."""
    for r in rows:
        ev = [e for e in r.get("근거") or [] if e.get("발췌") and e.get("doc_id") != "works.jsonl"]
        hows = []
        for e in ev:
            how, ids = baseline.locate(e)
            e["기준선_청크"] = ids
            hows.append(how)
        r["경계 걸침"] = (
            None
            if not hows or "없음" in hows or not r.get("answerable", True)
            else ("걸침" in hows)
        )
        if not r.get("answerable", True) or r["근거위치"] == "문서에 없음" and not r.get("근거"):
            r["필요 문서 수"] = 0
        elif not r.get("근거"):
            r["필요 문서 수"] = None  # 근거 미작성
        elif (
            r["query_id"].split("-")[0] in ("blind", "chunk") and r["query_id"] not in OFFICIAL_FILL
        ):
            r["필요 문서 수"] = 1  # 실험 세트의 정답 청크는 '하나만 읽어도 답이 되는' 청크
        else:
            r["필요 문서 수"] = len({e.get("work_id") or e.get("doc_id") for e in r["근거"]})


def validate(r: dict) -> list[str]:
    """정답 섹션 · 정답 문장 · 핵심 사실이 다 있는가. 빠진 것 목록."""
    miss = []
    if not str(r.get("question") or "").strip() or not re.search(r"[가-힣]", r["question"]):
        miss.append("한국어 질문")
    if not [f for f in str(r.get("채점 핵심 사실") or "").split("/") if f.strip()]:
        miss.append("핵심 사실")
    if r.get("answerable", True):
        ev = r.get("근거") or []
        if not ev or not all(
            e.get("doc_id") and (e.get("section_index") is not None or e.get("work_id")) for e in ev
        ):
            miss.append("정답 섹션")
        if not ev or not all(e.get("발췌") for e in ev):
            miss.append("정답 문장")
        elif not all(e.get("발췌_확인", True) for e in ev):
            miss.append("정답 문장(본문 불일치)")
        elif any(e.get("발췌_자동") for e in ev):
            miss.append("정답 문장(자동 선택 · 검토)")
    if r.get("난이도") == "함정" and r.get("answerable", True) and not r.get("전제 오류"):
        miss.append("전제 오류")
    return miss


def focus_tags(r: dict) -> list[str]:
    """확장 진단셋에서 비중을 높일 약점 축 (팀 인사이트 취합)"""
    tags = []
    if r["근거위치"] in ("인물", "줄거리") and r["질문유형"] in ("인과", "시간선", "관계"):
        tags.append("인물 서사·사건 흐름")
    if r.get("경계 걸침"):
        tags.append("경계 걸침")
    if (r.get("필요 문서 수") or 0) >= 2:
        tags.append("다중 작품")
    if r["근거위치"] in ("인물", "세계관·사물") or any(
        e.get("doc_type") == "episode" for e in r.get("근거") or []
    ):
        tags.append("인물·세계관·에피소드")
    return tags


# ---------------------------------------------------------------- 팀 공식 형식으로 내보내기

# 루트 question_set.jsonl 의 칸 (순서 그대로) + 합의한 추가 칸. eval/dev.json · eval/test.json 에 그대로 넣는 모양.
TEAM_COLS = [
    "query_id",
    "question",
    "expected_answer",
    "answerable",
    "난이도",
    "채점 핵심 사실",
    "근거 문서·절·리비전",
    "검토 메모",
    "원문 근거 발췌",
]
ADDED_COLS = [
    "근거위치",
    "질문유형",
    "eval_track",
    "variant_of",
    "작성자",
    "전제 오류",
    "경계 걸침",
    "필요 문서 수",
    "근거",
    "split",
]
EVID_KEYS = ["doc_id", "team_doc_id", "section_index", "section_path", "발췌"]


def to_team_format(r: dict) -> dict:
    out = {k: r.get(k) for k in TEAM_COLS + ADDED_COLS}
    out["근거"] = [
        e if e.get("doc_id") == "works.jsonl" else {k: e.get(k) for k in EVID_KEYS}
        for e in r.get("근거") or []
    ]
    out["split"] = r.get("split")  # dev | test — 7단계에서 정한다
    return out


# ---------------------------------------------------------------- main


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--team", default=str(ROOT / "question_set.jsonl"))
    ap.add_argument("--bae", default=str(DOWNLOADS / "CineVerse_Evaluation_Dataset_Bae.md"))
    ap.add_argument("--chung", default=str(DOWNLOADS / "CineVerse_Evaluation_Dataset_Chung.md"))
    ap.add_argument("--minji", default=str(DOWNLOADS / "2026-11-25-minji-chunking.md"))
    ap.add_argument("--blind", default=str(ROOT / "tmp" / "questions_blind_v2.jsonl"))
    ap.add_argument("--chunks-set", default=str(ROOT / "tmp" / "questions_from_chunks.jsonl"))
    ap.add_argument(
        "--exp-chunks",
        default=str(ROOT / "tmp" / "chunks_dryrun.jsonl"),
        help="실험 세트의 청크 ID 를 문서·절·발췌로 바꿀 때만 쓰는 청킹 결과 (없으면 실험 세트를 건너뜀)",
    )
    ap.add_argument("--out", default=str(ROOT / "tmp" / "eval_merged.jsonl"))
    ap.add_argument("--dupes", default=str(ROOT / "tmp" / "eval_merged_dupes.jsonl"))
    ap.add_argument("--report", default=str(ROOT / "docs" / "experiments" / "eval_set_gaps.md"))
    ap.add_argument(
        "--official",
        default=str(ROOT / "tmp" / "eval_official.json"),
        help="공식 평가셋 후보 30문항 (팀 공식 형식 JSON 배열, split 칸에 dev/test)",
    )
    ap.add_argument(
        "--diag", default=str(ROOT / "tmp" / "eval_diag.json"), help="확장 진단셋 (같은 형식)"
    )
    ap.add_argument(
        "--dup-threshold",
        type=float,
        default=0.6,
        help="문자 2-gram 유사도가 이 값 이상이면 자동 중복",
    )
    ap.add_argument(
        "--extra",
        nargs="*",
        default=[],
        help="check_eval_gen.py 를 통과한 생성 문항 파일 (예: tmp/eval_gen_passed.jsonl)",
    )
    args = ap.parse_args()

    corpus = load_corpus()
    dc = DataCorpus()
    rows, sources = [], {}

    def skip(what: str, path: str):
        print(f"  - 건너뜀: {what} ({path} 없음)")

    team = team_rows(Path(args.team), corpus)
    rows += team
    sources["team"] = len(team)

    bae, chung = Path(args.bae), Path(args.chung)
    if bae.exists():
        if chung.exists() and sha(chung) != sha(bae):
            raise SystemExit(
                "배·정 md 내용이 다르다. 지금 스크립트는 같은 파일이라고 보고 하나만 읽는다 — BAE_CLASS 를 나눠야 함"
            )
        b = bae_rows(bae)
        rows += b
        sources["Bae/Chung"] = len(b)
    else:
        skip("배·정 평가셋", args.bae)

    m = minji_rows(Path(args.minji))  # 문항은 스크립트 안에 있다. md 는 문구 대조용
    rows += m
    sources["minji"] = len(m)

    chunk_file = Path(args.exp_chunks)
    exp_sets = [(Path(args.blind), "blind"), (Path(args.chunks_set), "chunk")]
    if not chunk_file.exists():
        skip(
            "실험 세트 blind · chunk (청크 ID 를 문서·절로 바꿀 청킹 결과가 없음)", str(chunk_file)
        )
    else:
        chunks = {c["chunk_id"]: c for c in read_jsonl(chunk_file)}
        e = []
        for p, prefix in exp_sets:
            if p.exists():
                e += exp_rows(p, chunks, dc, prefix)
            else:
                skip(f"실험 세트 {prefix}", str(p))
        if e:
            rows += e
            sources["ohmseok"] = len(e)

    gen = []
    for p in args.extra:
        for r in read_jsonl(Path(p)):
            if not str(r["query_id"]).startswith("gen-"):
                raise SystemExit(
                    f"--extra 문항 query_id 는 gen- 으로 시작해야 한다: {r['query_id']}"
                )
            r = {k: v for k, v in r.items() if k not in ("gold", "primary_gold")}
            gen.append(finish({**blank(), **r}))
    if gen:
        rows += gen
        sources["codex"] = len(gen)

    ids = Counter(r["query_id"] for r in rows)
    assert all(v == 1 for v in ids.values()), [k for k, v in ids.items() if v > 1]

    apply_fills(rows, dc)
    kept, dupes, near = dedupe(rows, args.dup_threshold)
    add_columns(kept, ChunkIndex(build_baseline(800)))

    by_id = {r["query_id"]: r for r in kept}
    official_ids = OFFICIAL_DEV + OFFICIAL_TEST
    missing_official = [q for q in official_ids if q not in by_id]
    for q in OFFICIAL_DEV:
        if q in by_id:
            by_id[q]["split"] = "dev"
    for q in OFFICIAL_TEST:
        if q in by_id:
            by_id[q]["split"] = "test"
    for r in kept:
        r["검증_빠진것"] = validate(r)
        r["진단_축"] = focus_tags(r)
    official = [by_id[q] for q in official_ids if q in by_id]
    diag = [r for r in kept if r["query_id"] not in set(official_ids)]

    for path, data in ((args.out, kept), (args.dupes, dupes)):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for r in data:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    for path, data in ((args.official, official), (args.diag, diag)):
        Path(path).write_text(
            json.dumps([to_team_format(r) for r in data], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    write_report(Path(args.report), kept, dupes, near, sources, args, official, diag)

    print(f"읽음 {len(rows)} → 남김 {len(kept)} · 중복 {len(dupes)} · 비슷한 쌍 {len(near)}")
    print(f"  {args.out}\n  {args.dupes}\n  {args.report}")
    print(
        f"  {args.official} (공식 후보 {len(official)}문항: dev {sum(r['split'] == 'dev' for r in official)}"
        f" · test {sum(r['split'] == 'test' for r in official)})"
    )
    print(f"  {args.diag} (확장 진단셋 {len(diag)}문항)")
    if missing_official:
        print(f"  ! 공식 후보 중 이번 입력에 없는 문항: {', '.join(missing_official)}")
    bad = [(r["query_id"], r["검증_빠진것"]) for r in official if r["검증_빠진것"]]
    print(
        "  공식 후보 검증: "
        + (
            "모두 정답 섹션 · 정답 문장 · 핵심 사실 있음"
            if not bad
            else "; ".join(f"{q} {m}" for q, m in bad)
        )
    )
    print("\n".join(gap_table(kept)))


if __name__ == "__main__":
    main()
