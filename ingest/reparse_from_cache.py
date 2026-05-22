"""기존 XLSX + raw_audit/ 캐시만으로 새 컬럼을 추가한 v3 XLSX 생성.

OPENDART API 호출 0회 — 캐시된 raw HTML 을 다시 평문화·슬라이스 + KAM/HTML
추출. 추출 로직 (extractors / fetch_audit.html_to_text) 변경 후 빠르게 재처리할
때 사용한다.

추가 / 갱신되는 컬럼
~~~~~~~~~~~~~~~~~

* '핵심감사사항(본문)' (신규) : raw HTML 본문에서 추출한 KAM 전체 문단
* '감사보고서 본문 전체' (덮어씀) : 줄바꿈 보존 평문 슬라이스
* '감사보고서 본문(HTML)' (신규) : 태그 보존 슬라이스 (Streamlit st.html 용)

매칭 로직
~~~~~~~~~

v2 XLSX 에 ``dcm_no`` 컬럼이 없어서 raw 파일과 1:1 매칭이 모호하다.
같은 ``parent_rcept_no`` 의 raw 들을 *이전 방식* (단일 공백 정규화) 으로
평문화한 뒤, v2 의 본문 첫 200자가 substring 으로 포함된 raw 를 채택.
"""
from __future__ import annotations

import html
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from audit_xlsx.extractors import (  # noqa: E402
    extract_audit_body_html,
    extract_kam_full_block,
    extract_standalone_audit_report_body,
)
from audit_xlsx.fetch_audit import html_to_text  # noqa: E402

# ---------------------------------------------------------------------------
# 경로 / 상수
# ---------------------------------------------------------------------------

_REPO_ROOT = _HERE.parent
_SRC_XLSX = _REPO_ROOT / "dashboard" / "data" / "audit_reports_full_v2.xlsx"
_DST_XLSX = _REPO_ROOT / "dashboard" / "data" / "audit_reports_full_v3.xlsx"
_RAW_DIR = _REPO_ROOT / "data" / "raw_audit"

_XLSX_CELL_LIMIT = 32_767      # openpyxl 셀 한도
_MATCH_HEAD_LEN = 200          # v2 본문 첫 N자로 raw 매칭
_PROGRESS_EVERY = 500          # 진행 출력 주기

# 컬럼명 (오타/변경 방지용 상수화)
_COL_PARENT = "감사보고서제출 접수번호"
_COL_BODY = "감사보고서 본문 전체"
_COL_KAM_API = "핵심감사사항(KAM)"
_COL_KAM_BODY = "핵심감사사항(본문)"
_COL_BODY_HTML = "감사보고서 본문(HTML)"

# ---------------------------------------------------------------------------
# v2 매칭용 — 이전 방식 (단일 공백) 의 평문화
# ---------------------------------------------------------------------------

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _html_to_text_legacy(raw: str) -> str:
    """이전 v2 파이프라인의 평문화 (모든 공백 단일화) — 매칭 전용."""
    if not raw:
        return ""
    s = _TAG.sub(" ", raw)
    s = html.unescape(s)
    return _WS.sub(" ", s).strip()


def _normalize_for_match(text: str) -> str:
    return _WS.sub(" ", text or "").strip()


# ---------------------------------------------------------------------------
# 단계 1: raw_audit/ 인덱싱
# ---------------------------------------------------------------------------


def _index_raw_files(raw_dir: Path) -> dict[str, list[tuple[str, str]]]:
    """raw_audit/*.html → parent_rcept_no 별 [(dcm_no, raw_text), ...] 인덱스."""
    index: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for fp in raw_dir.glob("*.html"):
        stem = fp.stem
        if "_" not in stem:
            continue
        parent, dcm = stem.split("_", 1)
        try:
            index[parent].append((dcm, fp.read_text(encoding="utf-8")))
        except Exception as e:  # noqa: BLE001
            print(f"  read err {fp.name}: {e}")
    return index


# ---------------------------------------------------------------------------
# 단계 2: v2 행 ↔ raw 파일 매칭
# ---------------------------------------------------------------------------


def _match_raw_for_row(
    body_head: str, candidates: list[tuple[str, str]]
) -> str | None:
    """v2 본문 첫 ``_MATCH_HEAD_LEN`` 자가 substring 으로 포함된 raw 채택.

    candidates 가 비어 있지 않고 본문이 없으면 첫 후보 fallback.
    """
    if not candidates:
        return None
    if not body_head:
        return candidates[0][1]
    for _dcm, raw in candidates:
        flat_v2 = _html_to_text_legacy(raw)
        if body_head in flat_v2 or body_head[:100] in flat_v2:
            return raw
    return None


# ---------------------------------------------------------------------------
# 단계 3: 행 1건 재처리
# ---------------------------------------------------------------------------


@dataclass
class _Stats:
    """처리 통계 — 진행 출력 + 최종 요약용."""

    total: int = 0
    matched: int = 0
    no_parent: int = 0
    no_match: int = 0
    kam_from_body: int = 0
    kam_from_api: int = 0
    html_extracted: int = 0
    html_truncated: int = 0

    def progress_line(self, idx: int) -> str:
        return (
            f"  [{idx}/{self.total}] matched={self.matched} no_parent={self.no_parent} "
            f"no_match={self.no_match} kam(body)={self.kam_from_body} "
            f"kam(api)={self.kam_from_api} html={self.html_extracted} "
            f"(truncated {self.html_truncated})"
        )

    def summary(self) -> str:
        kam_total = self.kam_from_body + self.kam_from_api
        return (
            f"\n총 {self.total} | matched {self.matched} | no_parent {self.no_parent} "
            f"| no_match {self.no_match}\n"
            f"KAM source: 본문 {self.kam_from_body} + API {self.kam_from_api} "
            f"= {kam_total} / {self.total}\n"
            f"HTML 본문 추출: {self.html_extracted} "
            f"(32K 초과 truncate {self.html_truncated})"
        )


@dataclass
class _RowResult:
    """행 1건의 재처리 결과 — DataFrame 컬럼에 그대로 들어가는 값들."""

    kam: str = ""
    body: str = ""
    body_html: str = ""


def _truncate_for_xlsx(html_value: str, stats: _Stats) -> str:
    """XLSX 셀 한도 초과 시 truncate + 안내 주석. stats 업데이트."""
    if len(html_value) <= _XLSX_CELL_LIMIT:
        return html_value
    stats.html_truncated += 1
    return html_value[: _XLSX_CELL_LIMIT - 100] + (
        "\n<!-- truncated at 32K limit; 전체 본문은 raw_audit/ 캐시 참조 -->"
    )


def _reparse_row(
    row: pd.Series,
    raw_index: dict[str, list[tuple[str, str]]],
    stats: _Stats,
) -> _RowResult:
    """v2 행 1건 → 새 컬럼 3개로 채워진 ``_RowResult``."""
    parent = str(row.get(_COL_PARENT) or "").strip()
    old_body = str(row.get(_COL_BODY) or "").strip()
    old_kam_api = str(row.get(_COL_KAM_API) or "").strip()

    if not parent or parent not in raw_index:
        stats.no_parent += 1
        return _RowResult(kam=old_kam_api, body=old_body, body_html="")

    body_head = _normalize_for_match(old_body)[:_MATCH_HEAD_LEN]
    matched_raw = _match_raw_for_row(body_head, raw_index[parent])
    if matched_raw is None:
        stats.no_match += 1
        return _RowResult(kam=old_kam_api, body=old_body, body_html="")

    stats.matched += 1
    flat = html_to_text(matched_raw)
    body = extract_standalone_audit_report_body(flat)
    kam_body = extract_kam_full_block(body or flat)
    body_html = extract_audit_body_html(matched_raw) or ""

    # KAM 우선순위: 본문 > API > 빈값
    if kam_body:
        kam_value = kam_body
        stats.kam_from_body += 1
    elif old_kam_api:
        kam_value = old_kam_api
        stats.kam_from_api += 1
    else:
        kam_value = ""

    if body_html:
        stats.html_extracted += 1

    return _RowResult(
        kam=kam_value,
        body=body or old_body,
        body_html=_truncate_for_xlsx(body_html, stats) if body_html else "",
    )


# ---------------------------------------------------------------------------
# 단계 4: 컬럼 순서 정리 후 저장
# ---------------------------------------------------------------------------


def _reorder_columns(df: pd.DataFrame) -> pd.DataFrame:
    """KAM(본문) 은 KAM(API) 옆, HTML 본문은 본문 전체 옆에 배치."""
    cols = list(df.columns)

    def _move_after(col: str, anchor: str) -> None:
        if col not in cols:
            return
        cols.remove(col)
        if anchor in cols:
            cols.insert(cols.index(anchor) + 1, col)
        else:
            cols.append(col)

    _move_after(_COL_KAM_BODY, _COL_KAM_API)
    _move_after(_COL_BODY_HTML, _COL_BODY)
    return df[cols]


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    if not _SRC_XLSX.exists():
        print(f"ERR: src xlsx not found: {_SRC_XLSX}")
        return 1
    if not _RAW_DIR.exists():
        print(f"ERR: raw_audit not found: {_RAW_DIR}")
        return 1

    print(f"src: {_SRC_XLSX}")
    print(f"raw: {_RAW_DIR}")

    # 1) v2 로드
    df = pd.read_excel(_SRC_XLSX, dtype=str, engine="openpyxl").fillna("")
    print(f"v2 rows: {len(df)} cols: {len(df.columns)}", flush=True)

    # 2) raw 인덱싱
    raw_index = _index_raw_files(_RAW_DIR)
    raw_count = sum(len(v) for v in raw_index.values())
    print(f"indexed raw files: {raw_count} ({len(raw_index)} unique parents)", flush=True)

    # 3) 행 재처리
    stats = _Stats(total=len(df))
    new_kam: list[str] = []
    new_body: list[str] = []
    new_html: list[str] = []

    for i, row in df.iterrows():
        result = _reparse_row(row, raw_index, stats)
        new_kam.append(result.kam)
        new_body.append(result.body)
        new_html.append(result.body_html)
        if (i + 1) % _PROGRESS_EVERY == 0:
            print(stats.progress_line(i + 1), flush=True)

    print(stats.summary(), flush=True)

    # 4) 컬럼 추가 + 순서 정리 + 저장
    df[_COL_KAM_BODY] = new_kam
    df[_COL_BODY] = new_body
    df[_COL_BODY_HTML] = new_html
    df = _reorder_columns(df)

    df.to_excel(_DST_XLSX, index=False, engine="openpyxl")
    size_mb = _DST_XLSX.stat().st_size / 1024 / 1024
    print(f"\nwrote: {_DST_XLSX} ({size_mb:.2f} MB)")

    n_kam = (df[_COL_KAM_BODY].astype(str).str.strip() != "").sum()
    print(f"KAM(본문) 추출: {n_kam}/{len(df)} ({100 * n_kam / len(df):.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
