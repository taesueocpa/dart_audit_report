"""사이드바 필터 위젯 — 메타 필터 5종 + 본문 키워드 검색."""
from __future__ import annotations

import re

import pandas as pd
import streamlit as st

from data_loader import TEXT_COLS


def _opts(series: pd.Series) -> list[str]:
    """multiselect 옵션 — 빈 값 제외 + 정렬."""
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

    # 결산연도(사업연도) — 결산기준일(YYYY-MM-DD)의 연도. 빈 선택 = 전체. 최상단 배치.
    fy_series = df["결산기준일"].fillna("").astype(str).str[:4]
    fy_all = sorted({y for y in fy_series if y.isdigit()}, reverse=True)
    fiscal_years = st.sidebar.multiselect(
        "결산연도(사업연도)",
        fy_all,
        default=[],
        placeholder="전체" + (f" ({fy_all[-1]}~{fy_all[0]})" if fy_all else ""),
        help="결산기준일 기준 사업연도. 비우면 전체 연도.",
    )

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
    body_kw = st.sidebar.text_input(
        "본문 키워드 검색",
        placeholder="예: 계속기업 불확실성",
        help="KAM·강조사항·기타사항·감사보고서 본문 전체에서 부분 일치",
    ).strip()

    mask = pd.Series(True, index=df.index)
    if market:
        mask &= df["시장구분"].isin(market)
    if opinion:
        mask &= df["감사의견"].isin(opinion)
    if kind:
        mask &= df["보고서 종류"].isin(kind)
    if fiscal_years:
        mask &= fy_series.isin(fiscal_years)
    if firms:
        mask &= df["감사인(회계법인)"].isin(firms)
    if name_q:
        ql = name_q.lower()
        mask &= (
            df["회사명"].fillna("").str.lower().str.contains(ql)
            | df["종목코드"].fillna("").str.contains(name_q)
        )
    if body_kw:
        # KAM/강조/기타/본문 통합 검색 — 어느 한 컬럼이라도 매칭되면 채택
        text_cols = [c for c in TEXT_COLS if c in df.columns]
        body_mask = pd.Series(False, index=df.index)
        for c in text_cols:
            body_mask |= df[c].fillna("").str.contains(re.escape(body_kw), case=False, regex=True)
        mask &= body_mask

    selected = {
        "market": market,
        "opinion": opinion,
        "kind": kind,
        "fiscal_years": fiscal_years,
        "firms": firms,
        "name_q": name_q,
        "body_kw": body_kw,
    }
    st.sidebar.markdown(f"**필터 결과: {int(mask.sum()):,} / {len(df):,} 행**")
    return mask, selected
