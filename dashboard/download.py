"""필터 결과 → BytesIO XLSX (st.download_button data 인자)."""
from __future__ import annotations

import io
from datetime import date

import pandas as pd
import streamlit as st


@st.cache_data(show_spinner=False)
def to_xlsx_bytes(df: pd.DataFrame, *, sheet_name: str = "audit_reports") -> bytes:
    """DataFrame → XLSX bytes. 동일 입력에 대해 결과 캐시."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name=sheet_name)
    return buf.getvalue()


def render_download(df: pd.DataFrame, *, key: str = "dl") -> None:
    """체크박스로 본문 컬럼 포함 여부 선택 + 다운로드 버튼."""
    text_cols = [
        "핵심감사사항(KAM)",
        "강조사항",
        "기타사항",
        "감사보고서 본문 전체",
    ]
    available = [c for c in text_cols if c in df.columns]

    cols = st.columns([2, 1, 1])
    with cols[0]:
        st.write(
            f"**{len(df):,}** 행 / **{len(df.columns)}** 컬럼 다운로드 가능"
        )
    with cols[1]:
        include_body = st.checkbox(
            "본문 포함",
            value=False,
            help="감사보고서 본문 전체 등 긴 텍스트 포함 (파일 큼)",
            key=f"{key}_body",
        )
    out = df.copy() if include_body else df.drop(columns=available, errors="ignore")
    with cols[2]:
        st.download_button(
            "📥 Excel 다운로드",
            data=to_xlsx_bytes(out),
            file_name=f"audit_reports_{date.today().isoformat()}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"{key}_btn",
            use_container_width=True,
        )
