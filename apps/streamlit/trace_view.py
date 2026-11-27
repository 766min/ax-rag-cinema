from __future__ import annotations

from typing import Any

import streamlit as st


def _dump(value: Any) -> dict[str, Any]:
    return value.model_dump() if hasattr(value, "model_dump") else value


def render_trace(result: dict[str, Any], elapsed_ms: float) -> None:
    with st.expander("검색 과정 보기", icon=":material/travel_explore:"):
        st.markdown("#### 질의 처리")
        st.write(f"원 질문: {result.get('query_ko', '-')}")
        st.write(f"정규화 질문: {result.get('query_normalized', '-')}")
        st.write(f"영문 검색어: {result.get('query_en') or '(사용하지 않음)'}")
        st.write(f"실제 검색어: {result.get('search_query', '-')}")
        st.write(f"검색 모드: `{result.get('mode', '-')}`")
        st.write(f"전체 실행 시간: `{elapsed_ms:.0f} ms`")

        candidates = [_dump(item) for item in result.get("candidates", [])]
        if candidates:
            rows = [
                {
                    "chunk_id": item.get("chunk_id"),
                    "최종 순위": item.get("rank"),
                    "재정렬 전": item.get("rank_before_rerank"),
                    "Vector": item.get("vector_score"),
                    "BM25": item.get("bm25_score"),
                    "RRF": item.get("rrf_score"),
                    "Cross-Encoder": item.get("rerank_score"),
                    "인용": item.get("chunk_id") in result.get("cited_chunk_ids", []),
                }
                for item in candidates
            ]
            st.markdown("#### 검색 후보와 순위 변화")
            st.dataframe(rows, width="stretch", hide_index=True)
        else:
            st.caption("검색 후보가 없습니다.")

        if result.get("read"):
            st.markdown("#### 모델에 전달된 문맥")
            st.dataframe(result["read"], width="stretch", hide_index=True)
        if result.get("dropped_citations"):
            st.warning(f"근거 밖 인용 제거: {result['dropped_citations']}")
