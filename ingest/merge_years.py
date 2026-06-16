"""연도별 XLSX(fetch_year 산출)를 v4 에 병합 — 동일 20컬럼 concat + 정렬.

v4 의 기존 행(사업연도 2025)은 그대로 두고, ``data/audit_reports_y{YYYY}.xlsx``
들을 이어 붙인 뒤 (회사명 ↑, 결산기준일 ↓, 보고서 종류 ↑) 로 정렬한다.
같은 (DART고유번호, 결산기준일, 보고서 종류) 중복은 첫 행만 남긴다
(재실행 안전 — 같은 연도 파일을 두 번 병합해도 행이 불어나지 않음).

사용법: ``python -m ingest.merge_years --years 2024 2023``
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from audit_xlsx.settings import load_settings  # noqa: E402

_V4 = _HERE.parent / "dashboard" / "data" / "audit_reports_full_v4.xlsx"
_STR_COLS = ("종목코드", "DART고유번호", "감사보고서제출 접수번호", "사업보고서 접수번호")
_DEDUP_KEYS = ["DART고유번호", "결산기준일", "보고서 종류"]


def _load(path: Path) -> pd.DataFrame:
    df = pd.read_excel(
        path, dtype={c: str for c in _STR_COLS}, engine="openpyxl"
    ).fillna("")
    print(f"  {path.name}: {len(df)}행 {len(df.columns)}컬럼", flush=True)
    return df


_HTML_COL = "감사보고서 본문(HTML)"

# 감사의견 표준 토큰(공백 제거 기준) → 통일 표기. 연도/회사별 표기 흔들림 해소.
_STD_OPINION = {
    "적정": "적정의견", "적정의견": "적정의견",
    "한정": "한정의견", "한정의견": "한정의견",
    "부적정": "부적정의견", "부적정의견": "부적정의견",
    "의견거절": "의견거절",
}


def _norm_opinion(v: str) -> str:
    """감사의견 표준화 — 공백 제거 후 표준 토큰이면 통일('적 정'→'적정의견'), 아니면 공백만 단일화."""
    nospace = re.sub(r"\s+", "", v)
    if nospace in _STD_OPINION:
        return _STD_OPINION[nospace]
    return re.sub(r"\s+", " ", v).strip()


def _clean_df(df: pd.DataFrame) -> pd.DataFrame:
    r"""줄바꿈/표기 정규화 — 깔끔한 단일 DB.

    - 사업연도: 내부 공백·개행 단일화 + '(' 앞 공백 제거 ("제35기\n(당기)" → "제35기(당기)")
    - 감사의견: 공백·개행 정리 + 표기 통일(적정→적정의견 등) — 연도별 표기 불일치 해소
    - 전 컬럼: 줄끝 공백·3연속+ 빈줄 정리 + 외곽 strip (본문 문단 개행은 보존, HTML 원형 보존)
    """
    if "사업연도" in df.columns:
        df["사업연도"] = (
            df["사업연도"].fillna("").astype(str)
            .str.replace(r"\s+", " ", regex=True).str.strip()
            .str.replace(r"\s*\(", "(", regex=True)
        )
    if "감사의견" in df.columns:
        df["감사의견"] = df["감사의견"].fillna("").astype(str).map(_norm_opinion)
    for c in df.columns:
        col = df[c].fillna("").astype(str).str.replace(r"[ \t]+\n", "\n", regex=True)
        if c != _HTML_COL:  # HTML 마크업은 빈줄 정리 제외(원형 보존)
            col = col.str.replace(r"\n{3,}", "\n\n", regex=True)
        df[c] = col.str.strip()
    return df


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, nargs="+", required=True)
    parser.add_argument("--dst", type=Path, default=_V4)
    args = parser.parse_args(argv)

    settings = load_settings()
    print("로드:", flush=True)
    base = _load(_V4)
    base_cols = list(base.columns)

    # 재병합 시 해당 연도 기존 행 제거 → 연도 파일이 최신본을 대체(구버전·중복 방지)
    drop_years = {str(y) for y in args.years}
    fy_base = base["결산기준일"].fillna("").astype(str).str[:4]
    kept = base[~fy_base.isin(drop_years)]
    if len(kept) != len(base):
        print(f"  v4 기존 {len(base) - len(kept)}행 제거(연도 {sorted(drop_years)} 대체)", flush=True)

    frames = [kept]
    for y in args.years:
        p = settings.data_dir / f"audit_reports_y{y}.xlsx"
        if not p.exists():
            print(f"ERR: {p} 없음 — fetch_year_api --year {y} 먼저 실행", file=sys.stderr)
            return 1
        df = _load(p)
        if list(df.columns) != base_cols:
            print(f"ERR: {p.name} 컬럼 불일치", file=sys.stderr)
            return 1
        frames.append(df)

    merged = pd.concat(frames, ignore_index=True)
    before = len(merged)
    merged = merged.drop_duplicates(_DEDUP_KEYS, keep="first")
    if len(merged) != before:
        print(f"중복 제거: {before - len(merged)}행", flush=True)
    merged = merged.sort_values(
        ["회사명", "결산기준일", "보고서 종류"],
        ascending=[True, False, True],
        kind="stable",
    ).reset_index(drop=True)
    merged = _clean_df(merged)
    print("정규화 완료(사업연도/감사의견/줄바꿈)", flush=True)

    tmp = args.dst.with_suffix(".tmp.xlsx")
    merged.to_excel(tmp, index=False, engine="openpyxl")
    os.replace(tmp, args.dst)
    print(
        f"\nwrote: {args.dst} ({args.dst.stat().st_size/1024/1024:.2f} MB, "
        f"{len(merged)}행)",
        flush=True,
    )
    fy = merged["결산기준일"].astype(str).str[:4]
    print("결산연도 분포:", fy.value_counts().sort_index(ascending=False).to_dict(), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
