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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, nargs="+", required=True)
    parser.add_argument("--dst", type=Path, default=_V4)
    args = parser.parse_args(argv)

    settings = load_settings()
    print("로드:", flush=True)
    frames = [_load(_V4)]
    base_cols = list(frames[0].columns)
    for y in args.years:
        p = settings.data_dir / f"audit_reports_y{y}.xlsx"
        if not p.exists():
            print(f"ERR: {p} 없음 — fetch_year --year {y} 먼저 실행", file=sys.stderr)
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
