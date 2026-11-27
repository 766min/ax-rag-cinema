from __future__ import annotations

import re
from html import escape
from typing import Any

import streamlit as st

STATUS = {
    "answered": ("답변 완료", "ok"),
    "web_unverified": ("웹 근거 답변", "warn"),
    "no_evidence": ("근거 없음", "warn"),
    "retrieval_error": ("검색 오류", "err"),
    "generation_error": ("생성 오류", "err"),
}


def _bold(text: str) -> str:
    """답변의 **굵게** 표시를 HTML 로 바꾼다 (본문은 escape 한 뒤라 안전하다)."""
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)


def render_answer(result: dict[str, Any], mode_label: str, elapsed_ms: float) -> None:
    status = str(result.get("status", "unknown"))
    label, tone = STATUS.get(status, (status, "warn"))
    st.markdown(
        f"""
<div class="cv-result-head">
  <span class="cv-mode">{escape(mode_label)}</span>
  <span class="cv-badge {tone}">{escape(label)}</span>
  <span class="cv-badge time">{elapsed_ms / 1000:.1f}s</span>
</div>
<div class="cv-question">Q. {escape(str(result.get("query_ko", "")))}</div>
""",
        unsafe_allow_html=True,
    )
    if result.get("answer_ko"):
        st.markdown(
            f'<div class="cv-answer">{_bold(escape(result["answer_ko"]))}</div>',
            unsafe_allow_html=True,
        )
    if result.get("error"):
        st.error(result["error"])
    if result.get("web_called") or result.get("web_cache_hit"):
        how = (
            "저장된 웹 검색 결과를 재사용"
            if result.get("web_cache_hit")
            else "Brave 웹 검색을 호출"
        )
        st.caption(f"🌐 문서에 근거가 없어 {how}했습니다.")
    elif result.get("web_failed"):
        st.caption("🌐 웹 검색 보강을 시도했지만 실패했습니다.")
    if result.get("no_evidence_reason"):
        st.warning(f"근거 없음 사유: `{result['no_evidence_reason']}`")


def render_citations(citations: list[dict[str, Any]]) -> None:
    st.markdown('<div class="cv-label">출처</div>', unsafe_allow_html=True)
    if not citations:
        st.caption("사용된 문서 출처가 없습니다.")
        return
    for index, citation in enumerate(citations, 1):
        title = citation.get("title") or citation.get("title_ko") or "제목 없음"
        section = citation.get("section") or "섹션 없음"
        web = citation.get("kind") == "web_snippet"
        mark = " · 🌐 웹 · 미검증" if web else ""
        with st.expander(f"[{index}] {title} · {section}{mark}"):
            if web:
                st.warning(citation.get("label") or "검색 결과 발췌 · 미검증")
            st.caption(
                f"청크 `{citation.get('chunk_id', '-')}` · 문서 `{citation.get('doc_id', '-')}`"
            )
            if citation.get("source_url"):
                st.markdown(f"[원문 보기]({citation['source_url']})")
            st.write(citation.get("excerpt") or "발췌문 없음")
