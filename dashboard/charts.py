"""요약 탭의 KPI + Plotly 차트들."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st


def render_kpis(df: pd.DataFrame) -> None:
    """4개의 KPI 카드 렌더."""
    n_rows = len(df)
    n_companies = df["DART고유번호"].nunique() if n_rows else 0
    if n_rows:
        clean = (df["감사의견"] == "적정의견").sum()
        clean_rate = 100 * clean / n_rows
        n_disclaim = ((df["감사의견"] == "의견거절") | (df["감사의견"] == "한정의견")
                       | (df["감사의견"] == "부적정의견")).sum()
        kam_have = df["핵심감사사항(KAM)"].fillna("").str.strip().astype(bool).sum()
        kam_rate = 100 * kam_have / n_rows
    else:
        clean_rate = 0.0
        n_disclaim = 0
        kam_rate = 0.0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("총 행 수", f"{n_rows:,}", f"고유 회사 {n_companies:,}")
    c2.metric("적정의견 비율", f"{clean_rate:.1f}%")
    c3.metric("비적정 의견", f"{int(n_disclaim):,} 건")
    c4.metric("KAM 보유율", f"{kam_rate:.1f}%")


def render_charts(df: pd.DataFrame) -> None:
    """4종 차트 — 의견/시장×종류/감사인 TOP/의견별 EOM 보유율."""
    if df.empty:
        st.info("필터 결과가 없습니다.")
        return

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("감사의견 분포")
        opin = df["감사의견"].fillna("(미분류)").value_counts().reset_index()
        opin.columns = ["감사의견", "건수"]
        fig = px.pie(opin, names="감사의견", values="건수", hole=0.45)
        fig.update_traces(textposition="inside", textinfo="percent+label")
        fig.update_layout(height=380, showlegend=True, margin=dict(t=10, b=0))
        st.plotly_chart(fig, use_container_width=True)

    with c2:
        st.subheader("시장 × 보고서 종류")
        cross = (
            df.groupby(["시장구분", "보고서 종류"]).size().reset_index(name="건수")
        )
        fig = px.bar(
            cross, x="시장구분", y="건수", color="보고서 종류", barmode="stack"
        )
        fig.update_layout(height=380, margin=dict(t=10, b=0))
        st.plotly_chart(fig, use_container_width=True)

    c3, c4 = st.columns(2)
    with c3:
        st.subheader("감사인(회계법인) TOP 15")
        firms = df["감사인(회계법인)"].fillna("").value_counts().head(15).reset_index()
        firms.columns = ["회계법인", "건수"]
        firms = firms[firms["회계법인"] != ""]
        fig = px.bar(firms, x="건수", y="회계법인", orientation="h")
        fig.update_layout(
            height=420, yaxis=dict(autorange="reversed"), margin=dict(t=10, b=0)
        )
        st.plotly_chart(fig, use_container_width=True)

    with c4:
        st.subheader("감사의견별 강조사항 보유율")
        emph = (
            df.assign(_eom=df["강조사항"].fillna("").str.strip().astype(bool))
            .groupby("감사의견")
            .agg(rate=("_eom", "mean"), n=("_eom", "size"))
            .reset_index()
        )
        emph["보유율(%)"] = (emph["rate"] * 100).round(1)
        emph["감사의견(n)"] = emph["감사의견"].astype(str) + " (" + emph["n"].astype(str) + ")"
        fig = px.bar(emph, x="감사의견(n)", y="보유율(%)")
        fig.update_layout(height=420, margin=dict(t=10, b=0))
        st.plotly_chart(fig, use_container_width=True)
