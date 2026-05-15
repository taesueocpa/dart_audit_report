"""본문 키워드 검색 — KAM/강조/기타/본문 4개 컬럼에서 부분일치."""
from __future__ import annotations

import html
import re

import pandas as pd
import streamlit as st

from data_loader import TEXT_COLS


def _highlight(text: str, kw: str, *, ctx: int = 80) -> str:
    """``kw`` 가 있는 위치 주변 ``ctx`` 자 발췌 + <mark> 강조 (HTML)."""
    if not text:
        return ""
    m = re.search(re.escape(kw), text, re.IGNORECASE)
    if not m:
        return ""
    s = max(0, m.start() - ctx)
    e = min(len(text), m.end() + ctx)
    snippet = text[s:e]
    safe = html.escape(snippet)
    safe_kw = re.escape(html.escape(kw))
    safe = re.sub(safe_kw, lambda x: f"<mark>{x.group(0)}</mark>", safe, flags=re.IGNORECASE)
    prefix = "…" if s > 0 else ""
    suffix = "…" if e < len(text) else ""
    return f"{prefix}{safe}{suffix}"


def render_search_tab(df: pd.DataFrame) -> None:
    st.subheader("📄 본문 키워드 검색")
    st.caption("필터 적용된 행에서 KAM·강조사항·기타사항·감사보고서 본문 전체 통합 검색")

    cols = st.columns([3, 1])
    with cols[0]:
        kw = st.text_input(
            "검색어",
            placeholder="예: 계속기업 불확실성 / 영업권 손상 / 매출 인식",
            key="search_kw",
        ).strip()
    with cols[1]:
        ctx_size = st.number_input(
            "발췌 길이", min_value=40, max_value=300, value=120, step=20
        )

    if not kw:
        st.info("검색어를 입력하세요.")
        return

    text_cols = [c for c in TEXT_COLS if c in df.columns]
    masks = pd.DataFrame({
        c: df[c].fillna("").str.contains(re.escape(kw), case=False, regex=True)
        for c in text_cols
    })
    matched_mask = masks.any(axis=1)
    matched = df[matched_mask].copy()

    st.markdown(
        f"### 🎯 매칭: **{int(matched_mask.sum()):,}** 행 "
        f"(전체 필터 결과 {len(df):,} 중)"
    )
    if matched.empty:
        return

    # 매칭 컬럼 요약 추가
    matched["매칭 영역"] = masks[matched_mask].apply(
        lambda r: ", ".join([c for c in text_cols if bool(r[c])]), axis=1
    )

    summary = matched[
        ["회사명", "종목코드", "시장구분", "보고서 종류", "감사의견", "감사인(회계법인)", "매칭 영역"]
    ].reset_index(drop=True)

    event = st.dataframe(
        summary,
        on_select="rerun",
        selection_mode="single-row",
        use_container_width=True,
        height=400,
        hide_index=False,
    )

    if event.selection.rows:
        idx = event.selection.rows[0]
        row = matched.iloc[idx]
        st.markdown(
            f"#### 📌 {row['회사명']} ({row['종목코드']}) · {row['보고서 종류']}"
        )
        for c in text_cols:
            text = str(row.get(c) or "")
            if not text:
                continue
            snippet = _highlight(text, kw, ctx=int(ctx_size))
            if snippet:
                st.markdown(f"**{c}**", help=f"전체 길이 {len(text):,}자")
                st.markdown(snippet, unsafe_allow_html=True)
                st.markdown("")
