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

from data_loader import AUDIT_BODY_COLS, IACM_BODY_COLS, load_db  # noqa: E402
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

# ksox-fc-generator 와 일맥상통하는 디자인 시스템:
# Pretendard 폰트 · slate 팔레트 · 얇은(1px) #E5E7EB 보더 · 8px 라운드 ·
# slate-900 Primary 버튼. 선 굵기·폰트 크기를 정제해 투박함을 줄인다.
st.markdown(
    """
    <style>
    @import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css');

    :root {
      --slate-900:#0F172A; --slate-800:#1E293B; --slate-600:#475569;
      --slate-500:#64748B; --slate-400:#94A3B8; --slate-300:#CBD5E1;
      --gray-200:#E5E7EB; --gray-100:#F3F4F6; --gray-50:#F8FAFC; --gray-500:#6B7280;
    }
    html, body, [class*="css"], .stMarkdown, .stTextInput, .stSelectbox,
    .stMultiSelect, .stDataFrame, button, input, textarea {
      font-family:'Pretendard',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif !important;
    }
    .block-container { padding-top:1.4rem; padding-bottom:2.5rem; max-width:1520px; }

    /* 타이포 — 정제된 크기·슬레이트 색 */
    h1 { font-size:1.55rem !important; font-weight:700 !important; color:var(--slate-900) !important;
         letter-spacing:-0.01em; }
    h2 { font-size:1.2rem !important; font-weight:700 !important; color:var(--slate-900) !important; }
    h3 { font-size:1.05rem !important; font-weight:600 !important; color:var(--slate-900) !important; }
    [data-testid="stCaptionContainer"] p { color:var(--gray-500) !important; font-size:0.8rem !important; }

    /* 버튼 — 8px 라운드·weight 600·그림자 제거. 다운로드/Primary 는 slate-900 */
    .stButton > button, .stDownloadButton > button {
      border-radius:8px !important; font-weight:600 !important; box-shadow:none !important;
      white-space:nowrap !important; transition:background-color .15s ease, border-color .15s ease;
    }
    .stDownloadButton > button, .stButton > button[kind="primary"],
    [data-testid="stBaseButton-primary"] {
      background-color:var(--slate-900) !important; color:#fff !important;
      border:1px solid var(--slate-900) !important;
    }
    .stDownloadButton > button:hover, .stButton > button[kind="primary"]:hover,
    [data-testid="stBaseButton-primary"]:hover {
      background-color:var(--slate-800) !important; border-color:var(--slate-800) !important;
    }
    .stButton > button[kind="secondary"], [data-testid="stBaseButton-secondary"] {
      background:#fff !important; color:var(--slate-900) !important;
      border:1px solid var(--gray-200) !important;
    }
    .stButton > button[kind="secondary"]:hover { border-color:var(--slate-400) !important; }

    /* 입력/선택 위젯 — 라운드·옅은 보더 */
    .stTextInput input, .stSelectbox div[data-baseweb="select"] > div,
    .stMultiSelect div[data-baseweb="select"] > div {
      border-radius:8px !important; border-color:var(--gray-200) !important;
    }
    [data-baseweb="tag"] { background-color:var(--gray-100) !important; color:var(--slate-800) !important;
      border-radius:6px !important; }

    /* 사이드바 — 옅은 배경 + 얇은 우측 보더 */
    [data-testid="stSidebar"] { background:var(--gray-50) !important; border-right:1px solid var(--gray-200); }
    [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2 { font-size:1.0rem !important; }

    /* 데이터프레임 — 1px 보더·라운드 */
    [data-testid="stDataFrame"] { border:1px solid var(--gray-200) !important; border-radius:10px !important;
      overflow:hidden; }

    /* expander — 라운드·옅은 보더·그림자 제거 */
    [data-testid="stExpander"] { border:1px solid var(--gray-200) !important; border-radius:10px !important;
      box-shadow:none !important; }
    [data-testid="stExpander"] summary { font-weight:600 !important; color:var(--slate-800) !important; }

    /* 탭 — 얇은 하단선·슬레이트 강조 */
    .stTabs [data-baseweb="tab-list"] { gap:6px; border-bottom:1px solid var(--gray-200); }
    .stTabs [data-baseweb="tab"] { font-weight:600; color:var(--slate-500); }
    .stTabs [aria-selected="true"] { color:var(--slate-900) !important; }

    /* 구분선·알림 톤 정리 */
    hr { border-color:var(--gray-200) !important; margin:1rem 0 !important; }
    [data-testid="stAlert"] { border-radius:10px !important; }

    /* 본문 키워드 하이라이트 */
    mark { background:#FEF08A; padding:0 2px; border-radius:3px; }
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


def _render_tab(row: pd.Series, cols: tuple[str, ...], empty_msg: str) -> None:
    """탭 한 개 내부 — 컬럼별 expander. 모두 빈 값이면 안내 메시지."""
    present = [c for c in cols if str(row.get(c) or "").strip()]
    if not present:
        st.info(empty_msg)
        return
    for col_name in present:
        _render_text_expander(row, col_name)


def _render_row_detail(row: pd.Series) -> None:
    """행 클릭 시 상세 — 헤더 + 메타 + [감사보고서] [내부회계관리제도] 탭."""
    st.markdown("---")
    st.markdown(
        f"### 📄 {row['회사명']} ({row['종목코드']}) · {row.get('보고서 종류', '')}"
    )
    _render_meta_block(row)
    st.markdown("")

    tab_audit, tab_iacm = st.tabs(["📋 감사보고서", "🏛️ 내부회계관리제도"])
    with tab_audit:
        _render_tab(row, AUDIT_BODY_COLS, "감사보고서 본문이 없습니다.")
    with tab_iacm:
        _render_tab(
            row,
            IACM_BODY_COLS,
            "이 회사의 내부통제에 관한 사항 추출 결과가 없습니다.",
        )


# ---------------------------------------------------------------------------
# 메인 렌더
# ---------------------------------------------------------------------------

_fy_all = df_all["결산기준일"].fillna("").astype(str).str[:4]
_years = sorted({y for y in _fy_all if y.isdigit()})
_yr_label = f"{_years[0]}–{_years[-1]}" if len(_years) > 1 else (_years[0] if _years else "")

st.title("DART 감사보고서 대시보드")
st.caption(
    f"OPENDART 수집 · 코스피/코스닥 상장사 {_yr_label} 사업연도 감사보고서 "
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
