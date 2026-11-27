from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import streamlit as st


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def render_eval_dashboard(results_dir: Path) -> None:
    with st.expander("평가 결과 대시보드", icon=":material/monitoring:"):
        files = sorted(results_dir.glob("*.jsonl")) if results_dir.exists() else []
        if not files:
            st.caption("results/에 저장된 평가 결과가 없습니다.")
            return
        selected = st.selectbox("평가 실행 선택", files, format_func=lambda path: path.name)
        try:
            rows = _read_jsonl(selected)
        except (OSError, json.JSONDecodeError) as exc:
            st.error(f"평가 결과를 읽지 못했습니다: {exc}")
            return
        status_counts = Counter(str(row.get("status", "unknown")) for row in rows)
        answered = sum(status_counts.get(status, 0) for status in ("answered", "web_unverified"))
        c1, c2, c3 = st.columns(3)
        c1.metric("실행 문항", len(rows))
        c2.metric("답변 완료", answered)
        c3.metric("실패·근거 없음", len(rows) - answered)
        st.dataframe(rows, width="stretch", hide_index=True)
