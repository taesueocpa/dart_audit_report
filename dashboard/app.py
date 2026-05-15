"""DART 감사보고서 대시보드 (Streamlit) — 표 + 본문 조회 단일 페이지.

로컬 실행:
    cd dashboard
    streamlit run app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# 로컬/Streamlit Cloud 모두 dashboard/ 패키지 import 가능하게.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from data_loader import TEXT_COLS, load_db  # noqa: E402
from download import render_download  # noqa: E402
from filters import render_sidebar  # noqa: E402


# --- 페이지 설정 ----------------------------------------------------------------
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


# --- 데이터 로드 + 사이드바 -------------------------------------------------------
try:
    df_all = load_db()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()

mask, _selected = render_sidebar(df_all)
df = df_all[mask].reset_index(drop=True)

# --- 헤더 + 다운로드 ------------------------------------------------------------
st.title("📊 DART 감사보고서 대시보드")
st.caption(
    "OPENDART 수집 · 코스피/코스닥 상장사 2025 사업연도 감사보고서 "
    f"(전체 {len(df_all):,} 행 · {df_all['DART고유번호'].nunique():,} 회사)"
)

render_download(df, key="main")

# --- 메인 표 -------------------------------------------------------------------
st.markdown("")
table_cols = [
    "회사명",
    "종목코드",
    "시장구분",
    "보고서 종류",
    "감사의견",
    "감사인(회계법인)",
    "업무수행 공인회계사",
    "결산기준일",
    "감사보고서 본문 전체",
]
available_cols = [c for c in table_cols if c in df.columns]
view = df[available_cols].copy()
# 본문은 길이만 표시 (긴 텍스트 그대로 셀에 넣으면 무거움 — 본문은 행 선택 시 expander 로).
if "감사보고서 본문 전체" in view.columns:
    view = view.rename(columns={"감사보고서 본문 전체": "본문 길이"})
    view["본문 길이"] = (
        df["감사보고서 본문 전체"].fillna("").map(len).map(lambda n: f"{n:,}자")
    )

event = st.dataframe(
    view,
    on_select="rerun",
    selection_mode="single-row",
    use_container_width=True,
    height=560,
    hide_index=False,
)

# --- 행 클릭 → 본문 조회 ---------------------------------------------------------
if event.selection.rows:
    idx = event.selection.rows[0]
    row = df.iloc[idx]
    st.markdown("---")
    st.markdown(
        f"### 📄 {row['회사명']} ({row['종목코드']}) · "
        f"{row.get('보고서 종류', '')}"
    )

    meta_left, meta_right = st.columns(2)
    with meta_left:
        st.markdown(f"- **시장**: {row.get('시장구분', '')}")
        st.markdown(f"- **결산기준일**: {row.get('결산기준일', '')}")
        st.markdown(f"- **감사인**: {row.get('감사인(회계법인)', '')}")
        st.markdown(
            f"- **업무수행 공인회계사**: {row.get('업무수행 공인회계사', '')}"
        )
    with meta_right:
        st.markdown(f"- **DART고유번호**: {row.get('DART고유번호', '')}")
        st.markdown(
            f"- **감사보고서제출 접수번호**: "
            f"{row.get('감사보고서제출 접수번호', '')}"
        )
        st.markdown(f"- **감사의견**: {row.get('감사의견', '')}")
        st.markdown(f"- **첨부 제목**: {row.get('첨부 제목', '')}")

    st.markdown("")
    for col in TEXT_COLS:
        text = str(row.get(col) or "").strip()
        if not text:
            continue
        is_body = col == "감사보고서 본문 전체"
        with st.expander(
            f"📝 {col} ({len(text):,}자)",
            expanded=is_body,  # 본문 전체는 기본 펼침
        ):
            st.text(text)
else:
    st.info("👆 표에서 행을 클릭하면 감사보고서 본문 전체를 볼 수 있습니다.")


# --- footer ---------------------------------------------------------------------
st.markdown("---")
st.caption(
    "🤖 데이터 출처: OPENDART API · 파이프라인: `audit_xlsx` 패키지 · "
    "감사인/CPA/문단 추출은 휴리스틱이므로 법적·회계적 판단을 대체하지 않습니다."
)
