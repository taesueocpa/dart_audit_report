"""XLSX 로딩 유틸 — Streamlit 캐싱 + 정수 컬럼 str 강제."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st


# 파일 위치: dashboard/data_loader.py 기준 상대.
_DEFAULT_XLSX = Path(__file__).resolve().parent / "data" / "audit_reports_full_v3.xlsx"

# 표시 시 ".0" suffix 방지를 위해 강제로 str 처리할 컬럼.
_STR_COLS: tuple[str, ...] = (
    "종목코드",
    "DART고유번호",
    "감사보고서제출 접수번호",
    "사업보고서 접수번호",
)

# 본문 컬럼 (필터/검색 시 텍스트 대상 + 행 클릭 시 expander 로 표시).
# '핵심감사사항(본문)' 은 raw HTML 본문에서 직접 추출한 KAM 전체 문단 (97.8% 보유).
# '핵심감사사항(KAM)' (OPENDART API) 는 데이터에는 남아 있지만 표시는 본문 우선.
TEXT_COLS: tuple[str, ...] = (
    "핵심감사사항(본문)",
    "강조사항",
    "기타사항",
    "감사보고서 본문 전체",
)


@st.cache_data(show_spinner="감사보고서 DB 로딩 중…")
def load_db(xlsx_path: str | None = None) -> pd.DataFrame:
    """``audit_reports_full_v2.xlsx`` → DataFrame (정수 컬럼은 str 강제)."""
    path = Path(xlsx_path) if xlsx_path else _DEFAULT_XLSX
    if not path.exists():
        raise FileNotFoundError(
            f"DB 파일이 없습니다: {path}\n"
            "ingest 파이프라인으로 audit_reports_full_v2.xlsx 를 생성한 뒤 "
            "dashboard/data/ 에 복사하세요."
        )
    df = pd.read_excel(path, dtype={c: str for c in _STR_COLS}, engine="openpyxl")
    # NaN 통일.
    for col in df.select_dtypes(include="object").columns:
        df[col] = df[col].astype(object).where(df[col].notna(), None)
    # 정수형 NaN → 빈 문자열로 (str 캐스트한 컬럼 중 'nan' 텍스트 정리).
    for c in _STR_COLS:
        if c in df.columns:
            df[c] = df[c].astype(str).replace({"nan": "", "None": ""}).str.strip()
    return df


def split_meta_text(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """본문 컬럼을 메타와 분리해 표시용 메타 DF 와 본문 lookup DF 반환."""
    meta_cols = [c for c in df.columns if c not in TEXT_COLS]
    return df[meta_cols].copy(), df[list(TEXT_COLS)].copy()
