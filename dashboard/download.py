"""필터 결과 → 다운로드 버튼 (XLSX BytesIO).

본문 컬럼은 옵션으로 포함/제외 (긴 텍스트로 파일이 커지는 것 방지).
"""
from __future__ import annotations

import io
from datetime import date

import pandas as pd
import streamlit as st

from data_loader import TEXT_COLS

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@st.cache_data(show_spinner=False)
def to_xlsx_bytes(df: pd.DataFrame, *, sheet_name: str = "audit_reports") -> bytes:
    """DataFrame → XLSX bytes. 동일 입력에 대해 캐시."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name=sheet_name)
    return buf.getvalue()


def render_download(df: pd.DataFrame, *, key: str = "dl") -> None:
    """본문 포함 체크박스 + 필터된 결과 Excel 다운로드 버튼."""
    available_text_cols = [c for c in TEXT_COLS if c in df.columns]

    info_col, body_col, btn_col = st.columns([2, 1, 1])
    with info_col:
        st.write(f"**{len(df):,}** 행 / **{len(df.columns)}** 컬럼 다운로드 가능")
    with body_col:
        include_body = st.checkbox(
            "본문 포함",
            value=False,
            help="감사보고서 본문 전체 등 긴 텍스트 포함 (파일 큼)",
            key=f"{key}_body",
        )
    payload = df if include_body else df.drop(columns=available_text_cols, errors="ignore")
    with btn_col:
        st.download_button(
            "📥 Excel 다운로드",
            data=to_xlsx_bytes(payload),
            file_name=f"audit_reports_{date.today().isoformat()}.xlsx",
            mime=_XLSX_MIME,
            key=f"{key}_btn",
            use_container_width=True,
        )
