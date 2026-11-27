"""CineVerse 발표용 Streamlit 화면."""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import time
from pathlib import Path

import streamlit as st
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# ruff: noqa: E402, I001
from components import render_answer, render_citations
from eval_view import render_eval_dashboard
from trace_view import render_trace
from retrieve.pipeline import MODES, SearchConfig, effective_mode
from service.answer import make_retrieve
from service.movie_qa import answer_movie_question
from service.runtime import open_runtime

APP_DIR = Path(__file__).resolve().parent
ASSETS = APP_DIR / "assets"

st.set_page_config(
    page_title="CINEVERSE",
    page_icon=Image.open(ASSETS / "logo-icon.png"),
    layout="wide",
)

MODE_LABELS = {
    "A": "A · Vector",
    "B": "B · Hybrid",
    "C": "C · Hybrid + Re-ranking",
    "C+": "C+ · English Rewrite + Re-ranking",
    "A+": "A+ · English Rewrite + Vector",
    "B+": "B+ · English Rewrite + Hybrid",
}

GOLDEN_FILES = [ROOT / "eval" / "dev.json", ROOT / "eval" / "test.json"]
LEVELS = [
    ("초보", ":material/movie:"),
    ("중수", ":material/hub:"),
    ("고수", ":material/star:"),
    ("함정", ":material/report:"),
]

SEARCH_ICON = (
    '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round">'
    '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>'
)
USER_ICON = (
    '<svg viewBox="0 0 24 24" fill="none" stroke-width="1.8" stroke-linecap="round">'
    '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="10" r="3.2"/>'
    '<path d="M6.2 18.6c1.3-2.2 3.4-3.3 5.8-3.3s4.5 1.1 5.8 3.3"/></svg>'
)


@st.cache_data(show_spinner=False)
def b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


@st.cache_data(show_spinner=False)
def load_golden() -> list[dict]:
    """공식 평가셋(eval/dev.json + test.json) 31문항."""
    rows: list[dict] = []
    for path in GOLDEN_FILES:
        if path.exists():
            rows += json.loads(path.read_text(encoding="utf-8"))
    return rows


def load_css() -> str:
    css = (APP_DIR / "styles.css").read_text(encoding="utf-8")
    return css.replace("{{HERO_ART}}", b64(ASSETS / "hero-art.jpg"))


@st.cache_resource(show_spinner=False)
def load_runtime(mode_name: str):
    # 문맥(부모 문맥 기본)은 팀 기본값을 따른다 — scripts/ask.py 와 같은 경로
    return open_runtime([mode_name])


def make_web_fallback(runtime):
    """근거가 없을 때 부를 웹 검색 (scripts/ask.py --web 과 같은 경로).

    저장분 확인 → Brave → (WEB_STORE=true 면) 웹 컬렉션에 적재. 실패해도 예외 대신 web_failed 로 돌아온다.
    """
    from websearch import _store_from_settings, search_web_for_question

    store = _store_from_settings()

    async def web(question: str):
        return await search_web_for_question(question, aliases=runtime.aliases, store=store)

    return web


async def ask(question: str, name: str, rewrite: bool, use_web: bool) -> tuple[dict, float]:
    started = time.perf_counter()
    runtime = load_runtime(name)
    config = SearchConfig()  # 후보 수 · 생성 수 등은 팀 기본값
    result = await answer_movie_question(
        question,
        retrieve=make_retrieve(runtime.retriever, MODES[name], config),
        generate_en=runtime.generate_en,
        aliases=runtime.aliases,
        mode=MODES[name],
        top_k=config.generate_k,
        rewrite=rewrite,
        web=make_web_fallback(runtime) if use_web else None,
    )
    return result, (time.perf_counter() - started) * 1000


def run_question(question: str, name: str, rewrite: bool, use_web: bool) -> tuple[dict, float]:
    return asyncio.run(ask(question, name, rewrite, use_web))


def ask_golden(text: str) -> None:
    """골든셋 질문을 누르면 질문창에 채우고 바로 실행한다."""
    st.session_state["question"] = text
    st.session_state["auto_run"] = True


st.markdown(f"<style>{load_css()}</style>", unsafe_allow_html=True)

# ---------- 사이드바: 데모 설정 ----------
with st.sidebar:
    st.image(str(ASSETS / "logo-main.png"), width="stretch")
    st.markdown("### 데모 설정")
    mode_name = st.selectbox("검색 구성", list(MODES), format_func=MODE_LABELS.__getitem__)
    compare = st.checkbox("A/B/C 결과 비교", help="같은 질문을 A·B·C 구성으로 모두 실행합니다.")
    rewrite = st.toggle(
        "영문 리라이트",
        value=True,
        help="팀 기본값은 켜짐입니다. 켜면 A·B·C 가 A+·B+·C+ 로 실행됩니다.",
    )
    use_web = st.toggle(
        "웹 검색 보강",
        value=False,
        help="문서에서 근거를 못 찾았을 때만 Brave 웹 검색으로 보강합니다. "
        "웹 결과로 답하면 '미검증'으로 표시됩니다.",
    )

    golden = load_golden()
    st.markdown(
        '<div class="cv-side-title">어떤 이야기의 세계로 떠나볼까요?</div>'
        f'<div class="cv-side-sub">골든셋 {len(golden)}문항 · 누르면 바로 탐색합니다</div>',
        unsafe_allow_html=True,
    )
    current = st.session_state.get("question", "")
    with st.container(key="golden"):
        for level, icon in LEVELS:
            items = [row for row in golden if row.get("난이도") == level]
            if not items:
                continue
            st.markdown(
                f'<div class="cv-level">{level} <span>{len(items)}</span></div>',
                unsafe_allow_html=True,
            )
            for row in items:
                st.button(
                    row["question"],
                    key=f"golden_{row['query_id']}",
                    icon=icon,
                    type="primary" if row["question"] == current else "secondary",
                    on_click=ask_golden,
                    args=(row["question"],),
                    width="stretch",
                )

# ---------- 히어로 ----------
with st.container(key="hero"):
    st.markdown(
        f"""
<div class="cv-nav">
  <img src="data:image/png;base64,{b64(ASSETS / "logo-nav.png")}" alt="CINEVERSE" />
  <div class="cv-nav-icons">{SEARCH_ICON}{USER_ICON}</div>
</div>
""",
        unsafe_allow_html=True,
    )
    left, _ = st.columns([1.05, 1])
    with left:
        st.markdown(
            '<div class="cv-hero-title">당신의 영화 우주로,<br>시네버스를 타고</div>'
            '<div class="cv-hero-sub">취향으로 떠나는 영화 세계 탐험</div>',
            unsafe_allow_html=True,
        )
        with st.form("ask_form", border=False):
            question = st.text_input(
                "질문",
                key="question",
                placeholder="궁금한 영화나 세계관을 물어보세요",
                label_visibility="collapsed",
            )
            submitted = st.form_submit_button("탑승하기 →")

# ---------- 실행 ----------
# 리라이트가 켜져 있으면(기본) A · B · C 를 A+ · B+ · C+ 로 돌린다 (scripts/ask.py 와 같다)
modes = [effective_mode(name, rewrite) for name in (["A", "B", "C"] if compare else [mode_name])]
modes = list(dict.fromkeys(modes))
if submitted or st.session_state.pop("auto_run", False):
    if not question.strip():
        st.warning("질문을 입력해 주세요.")
    else:
        for name in modes:
            with st.spinner(f"{MODE_LABELS[name]} 구성으로 탐색 중..."):
                try:
                    result, elapsed_ms = run_question(question.strip(), name, rewrite, use_web)
                    st.session_state[f"result_{name}"] = result
                    st.session_state[f"elapsed_{name}"] = elapsed_ms
                except Exception as exc:
                    st.error(f"{name} 실행 실패: {type(exc).__name__}: {exc}")

# ---------- 결과 ----------
shown = [name for name in modes if st.session_state.get(f"result_{name}") is not None]
if shown:
    st.markdown(
        '<div class="cv-section-title">지금, 당신에게 도착한 답변</div>', unsafe_allow_html=True
    )
    for col, name in zip(st.columns(len(shown)), shown, strict=True):
        result = st.session_state[f"result_{name}"]
        elapsed = st.session_state.get(f"elapsed_{name}", 0.0)
        with col, st.container(key=f"result_{name.replace('+', 'p')}"):
            render_answer(result, MODE_LABELS[name], elapsed)
            render_citations(result.get("citations", []))
            render_trace(result, elapsed)

st.markdown('<div class="cv-section-title">평가 결과</div>', unsafe_allow_html=True)
render_eval_dashboard(ROOT / "results")
st.markdown(
    '<div class="cv-footer">CINEVERSE · 답변은 검색된 문서 근거를 바탕으로 생성됩니다.</div>',
    unsafe_allow_html=True,
)
