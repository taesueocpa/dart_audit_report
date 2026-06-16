"""XLSX 로딩 + 정규화 — Streamlit ``@cache_data`` 로 한 번만 읽고 재사용."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

# 산출물 위치 — Streamlit Cloud 도 동일 상대 경로.
# v4 는 v3 + 「내부통제에 관한 사항(사업보고서 본문)」 컬럼 병합본
# (ingest/fetch_iacm_api.py — 공식 document.xml API).
_DEFAULT_XLSX = Path(__file__).resolve().parent / "data" / "audit_reports_full_v4.xlsx"

# pandas 가 숫자처럼 보이는 컬럼을 int 로 추론해 ".0" 이 붙는 것을 막기 위해
# 강제로 str dtype 으로 로드할 컬럼들.
_STR_COLS: tuple[str, ...] = (
    "종목코드",
    "DART고유번호",
    "감사보고서제출 접수번호",
    "사업보고서 접수번호",
)

# 감사보고서 본문 컬럼 — 행 클릭 시 [감사보고서] 탭에 expander 로 표시.
AUDIT_BODY_COLS: tuple[str, ...] = (
    "핵심감사사항(본문)",
    "강조사항",
    "기타사항",
    "감사보고서 본문 전체",
)

# 내부회계관리제도 컬럼 — 행 클릭 시 [내부회계관리제도] 탭에 표시.
# 사업보고서 본문 「내부통제에 관한 사항」 절: 경영진의 내부회계관리제도 효과성
# 평가 결과(평가 결론·중요한 취약점·시정조치계획)와 감사인 의견 요약표.
# (공식 document.xml API 로 99.6% 수집 — ingest/fetch_iacm_api.py)
IACM_BODY_COLS: tuple[str, ...] = (
    "내부통제에 관한 사항(사업보고서 본문)",
)

# 본문(텍스트) 컬럼 합집합 — 사이드바 본문 키워드 검색 + 다운로드 "본문 포함"
# 토글이 동일 셋을 참조한다. (filter/download 는 그룹 구분 없이 합집합만 필요)
TEXT_COLS: tuple[str, ...] = AUDIT_BODY_COLS + IACM_BODY_COLS


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
            "ingest/fetch_iacm_api.py 를 실행해 audit_reports_full_v4.xlsx 를 생성하세요."
        )
    df = pd.read_excel(
        path, dtype={c: str for c in _STR_COLS}, engine="openpyxl"
    )
    return _clean_str_columns(df)
