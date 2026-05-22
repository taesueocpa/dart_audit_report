"""DART 감사보고서 대시보드 (Streamlit) — 표 + 본문 조회 단일 페이지.

화면 구성
~~~~~~~~~

* 사이드바: 5종 필터 + 본문 키워드 통합 검색 (``filters.render_sidebar``)
* 메인 영역: 헤더 + Excel 다운로드 버튼 + 회사목록 표
* 행 선택 시: 메타 정보 + 4개 본문 expander (KAM/강조/기타/본문 전체)
* 본문 전체는 HTML 컬럼이 있으면 ``st.html`` 로 표·문단·스타일 보존

로컬 실행:
    cd dashboard && streamlit run app.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

# 로컬/Streamlit Cloud 모두에서 dashboard/ 패키지 import 가능하게.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from data_loader import TEXT_COLS, load_db  # noqa: E402
from download import render_download  # noqa: E402
from filters import render_sidebar  # noqa: E402

# ---------------------------------------------------------------------------
# 컬럼명 상수 (오타/변경 방지)
# ---------------------------------------------------------------------------

_COL_BODY = "감사보고서 본문 전체"
_COL_BODY_HTML = "감사보고서 본문(HTML)"

# 회사목록 표에 표시할 컬럼 (존재하는 것만).
_TABLE_COLS: tuple[str, ...] = (
    "회사명", "종목코드", "시장구분", "보고서 종류",
    "감사의견", "감사인(회계법인)", "업무수행 공인회계사",
    "결산기준일", _COL_BODY,
)

# ---------------------------------------------------------------------------
# 페이지 설정
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="DART 감사보고서 대시보드",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      .block-container { padding-top: 1.0rem; padding-bottom: 1rem; }
      mark { background: #fff3a3; padding: 0 2px; border-radius: 2px; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# 데이터 로드 + 사이드바
# ---------------------------------------------------------------------------

try:
    df_all = load_db()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()

mask, _selected = render_sidebar(df_all)
df = df_all[mask].reset_index(drop=True)


# ---------------------------------------------------------------------------
# 회사목록 표 — 본문 컬럼은 길이만 표시 (긴 텍스트는 행 클릭 시 expander 로)
# ---------------------------------------------------------------------------


def _build_table_view(df: pd.DataFrame) -> pd.DataFrame:
    """표시용 DataFrame — 본문 컬럼은 길이로 치환."""
    cols = [c for c in _TABLE_COLS if c in df.columns]
    view = df[cols].copy()
    if _COL_BODY in view.columns:
        view = view.rename(columns={_COL_BODY: "본문 길이"})
        view["본문 길이"] = (
            df[_COL_BODY].fillna("").map(len).map(lambda n: f"{n:,}자")
        )
    return view


# ---------------------------------------------------------------------------
# 행 클릭 시 상세 패널
# ---------------------------------------------------------------------------

_META_LEFT: tuple[tuple[str, str], ...] = (
    ("시장", "시장구분"),
    ("결산기준일", "결산기준일"),
    ("감사인", "감사인(회계법인)"),
    ("업무수행 공인회계사", "업무수행 공인회계사"),
)
_META_RIGHT: tuple[tuple[str, str], ...] = (
    ("DART고유번호", "DART고유번호"),
    ("감사보고서제출 접수번호", "감사보고서제출 접수번호"),
    ("감사의견", "감사의견"),
    ("첨부 제목", "첨부 제목"),
)


def _render_meta_block(row: pd.Series) -> None:
    """좌·우 2단 메타 정보."""
    left, right = st.columns(2)
    for col, items in ((left, _META_LEFT), (right, _META_RIGHT)):
        with col:
            for label, key in items:
                st.markdown(f"- **{label}**: {row.get(key, '')}")


def _render_body_section(row: pd.Series, col_name: str, text: str) -> None:
    """본문 전체 expander 내부 — HTML 우선, 평문 fallback."""
    html_body = str(row.get(_COL_BODY_HTML) or "").strip()
    if html_body:
        st.html(html_body)
        with st.expander("📄 평문 버전 (줄바꿈만)", expanded=False):
            st.text(text)
    else:
        st.info("HTML 본문이 없어 평문으로 표시합니다.")
        st.text(text)


def _render_text_expander(row: pd.Series, col_name: str) -> None:
    """단일 본문 컬럼을 expander 로 렌더링."""
    text = str(row.get(col_name) or "").strip()
    if not text:
        return
    is_body = col_name == _COL_BODY
    with st.expander(f"📝 {col_name} ({len(text):,}자)", expanded=is_body):
        if is_body and _COL_BODY_HTML in row.index:
            _render_body_section(row, col_name, text)
        else:
            st.text(text)


def _render_row_detail(row: pd.Series) -> None:
    """행 클릭 시 상세 — 헤더 + 메타 + 본문 expander 4개."""
    st.markdown("---")
    st.markdown(
        f"### 📄 {row['회사명']} ({row['종목코드']}) · {row.get('보고서 종류', '')}"
    )
    _render_meta_block(row)
    st.markdown("")
    for col_name in TEXT_COLS:
        _render_text_expander(row, col_name)


# ---------------------------------------------------------------------------
# 메인 렌더
# ---------------------------------------------------------------------------

st.title("📊 DART 감사보고서 대시보드")
st.caption(
    "OPENDART 수집 · 코스피/코스닥 상장사 2025 사업연도 감사보고서 "
    f"(전체 {len(df_all):,} 행 · {df_all['DART고유번호'].nunique():,} 회사)"
)

render_download(df, key="main")

st.markdown("")
event = st.dataframe(
    _build_table_view(df),
    on_select="rerun",
    selection_mode="single-row",
    use_container_width=True,
    height=560,
    hide_index=False,
)

if event.selection.rows:
    _render_row_detail(df.iloc[event.selection.rows[0]])
else:
    st.info("👆 표에서 행을 클릭하면 감사보고서 본문 전체를 볼 수 있습니다.")

st.markdown("---")
st.caption(
    "🤖 데이터 출처: OPENDART API · 파이프라인: `audit_xlsx` 패키지 · "
    "감사인/CPA/문단 추출은 휴리스틱이므로 법적·회계적 판단을 대체하지 않습니다."
)
