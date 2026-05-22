"""XLSX 로딩 + 정규화 — Streamlit ``@cache_data`` 로 한 번만 읽고 재사용."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

# 산출물 위치 — Streamlit Cloud 도 동일 상대 경로.
_DEFAULT_XLSX = Path(__file__).resolve().parent / "data" / "audit_reports_full_v3.xlsx"

# pandas 가 숫자처럼 보이는 컬럼을 int 로 추론해 ".0" 이 붙는 것을 막기 위해
# 강제로 str dtype 으로 로드할 컬럼들.
_STR_COLS: tuple[str, ...] = (
    "종목코드",
    "DART고유번호",
    "감사보고서제출 접수번호",
    "사업보고서 접수번호",
)

# 본문(텍스트) 컬럼 — 사이드바 본문 검색 대상 + 행 클릭 시 expander 표시.
# '핵심감사사항(본문)' 은 raw HTML 에서 직접 추출한 KAM 전체 문단 (97~98% 보유).
# 같은 KAM 의 OPENDART API 버전 ('핵심감사사항(KAM)') 도 XLSX 에 남아 있지만
# 표시는 본문 우선이므로 여기엔 포함 X.
TEXT_COLS: tuple[str, ...] = (
    "핵심감사사항(본문)",
    "강조사항",
    "기타사항",
    "감사보고서 본문 전체",
)


def _clean_str_columns(df: pd.DataFrame) -> pd.DataFrame:
    """``_STR_COLS`` 에 남은 'nan'/'None' 텍스트를 빈 문자열로."""
    for col in _STR_COLS:
        if col in df.columns:
            df[col] = df[col].astype(str).replace({"nan": "", "None": ""}).str.strip()
    return df


@st.cache_data(show_spinner="감사보고서 DB 로딩 중…")
def load_db(xlsx_path: str | None = None) -> pd.DataFrame:
    """XLSX → DataFrame (정수형 컬럼 str 강제, NaN 통일).

    :raises FileNotFoundError: XLSX 파일이 없을 때 (대시보드 첫 실행 안내용).
    """
    path = Path(xlsx_path) if xlsx_path else _DEFAULT_XLSX
    if not path.exists():
        raise FileNotFoundError(
            f"DB 파일이 없습니다: {path}\n"
            "ingest 파이프라인으로 audit_reports_full_v3.xlsx 를 생성한 뒤 "
            "dashboard/data/ 에 복사하세요."
        )
    df = pd.read_excel(
        path, dtype={c: str for c in _STR_COLS}, engine="openpyxl"
    )
    return _clean_str_columns(df)
