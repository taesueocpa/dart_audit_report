"""사이드바 필터 위젯. 모든 탭이 공유하는 boolean mask 생성."""
from __future__ import annotations

import pandas as pd
import streamlit as st


def _opts(series: pd.Series) -> list[str]:
    """multiselect 옵션 — 빈 값 제외 + 알파벳/가나다 정렬."""
    vals = (
        series.dropna()
        .astype(str)
        .map(str.strip)
        .replace({"": None})
        .dropna()
        .unique()
        .tolist()
    )
    return sorted(vals)


def render_sidebar(df: pd.DataFrame) -> tuple[pd.Series, dict]:
    """사이드바 위젯 렌더 + (mask, 선택값 dict) 반환."""
    st.sidebar.title("🔎 필터")

    market = st.sidebar.multiselect(
        "시장구분", _opts(df["시장구분"]), default=_opts(df["시장구분"])
    )
    opinion = st.sidebar.multiselect(
        "감사의견", _opts(df["감사의견"]), default=_opts(df["감사의견"])
    )
    kind = st.sidebar.multiselect(
        "보고서 종류", _opts(df["보고서 종류"]), default=_opts(df["보고서 종류"])
    )
    firms_all = _opts(df["감사인(회계법인)"])
    firms = st.sidebar.multiselect(
        f"감사인(회계법인) — {len(firms_all)}종", firms_all, default=[]
    )
    name_q = st.sidebar.text_input(
        "회사명/종목코드 검색", placeholder="예: 삼성, 005930"
    ).strip()

    st.sidebar.markdown("---")
    st.sidebar.caption("💡 본문 키워드 검색은 '본문 검색' 탭에서 사용")

    mask = pd.Series(True, index=df.index)
    if market:
        mask &= df["시장구분"].isin(market)
    if opinion:
        mask &= df["감사의견"].isin(opinion)
    if kind:
        mask &= df["보고서 종류"].isin(kind)
    if firms:
        mask &= df["감사인(회계법인)"].isin(firms)
    if name_q:
        ql = name_q.lower()
        mask &= (
            df["회사명"].fillna("").str.lower().str.contains(ql)
            | df["종목코드"].fillna("").str.contains(name_q)
        )

    selected = {
        "market": market,
        "opinion": opinion,
        "kind": kind,
        "firms": firms,
        "name_q": name_q,
    }
    st.sidebar.markdown(f"**필터 결과: {int(mask.sum()):,} / {len(df):,} 행**")
    return mask, selected
