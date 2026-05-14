"""KRX 상장사 CSV → DART 고유번호(corp_code) 매핑 CSV 생성.

입력 CSV(market/data_2214_20260514.csv)의 cp949 인코딩, B열(단축코드) 6자리 종목코드를
OpenDartReader 의 ``find_corp_code`` 로 8자리 corp_code 에 매핑한다.

필터:
- 시장구분: KOSPI / KOSDAQ / KOSDAQ GLOBAL
- 증권구분: 주권 (보통주·우선주)

산출 CSV 컬럼:
    stock_code, corp_code, corp_name, market, security_kind
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Iterable

from opendartreader import OpenDartReader

from audit_xlsx.settings import Settings


_INPUT_ENCODING = "cp949"
_TARGET_MARKETS = {"KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"}
_TARGET_KIND = "주권"


def _read_market_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open("r", encoding=_INPUT_ENCODING, newline="") as f:
        reader = csv.DictReader(f)
        return [dict(row) for row in reader]


def _filter_listed_stocks(rows: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in rows:
        market = (row.get("시장구분") or "").strip()
        kind = (row.get("증권구분") or "").strip()
        stock_code = (row.get("단축코드") or "").strip()
        if market not in _TARGET_MARKETS or kind != _TARGET_KIND:
            continue
        if not stock_code or not stock_code.isdigit() or len(stock_code) != 6:
            continue
        out.append(
            {
                "stock_code": stock_code,
                "corp_name": (row.get("한글 종목약명") or row.get("한글 종목명") or "").strip(),
                "market": market,
                "security_kind": kind,
            }
        )
    return out


def build_mapping(settings: Settings, *, progress: bool = True) -> Path:
    """매핑 CSV를 생성하고 경로 반환. 이미 있으면 덮어쓴다."""
    settings.require_key()
    rows = _filter_listed_stocks(_read_market_rows(settings.market_csv))
    if progress:
        print(f"[매핑] 입력 종목 수(필터 후): {len(rows)}")

    dart = OpenDartReader(settings.api_key)
    settings.mapping_csv.parent.mkdir(parents=True, exist_ok=True)

    mapped = 0
    missing = 0
    with settings.mapping_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["stock_code", "corp_code", "corp_name", "market", "security_kind"],
        )
        writer.writeheader()
        for i, row in enumerate(rows, 1):
            try:
                corp_code = dart.find_corp_code(row["stock_code"])
            except Exception as e:  # noqa: BLE001
                corp_code = None
                if progress and missing < 5:
                    print(f"[매핑] 실패 {row['stock_code']} ({row['corp_name']}): {e}", file=sys.stderr)
            if corp_code:
                mapped += 1
            else:
                missing += 1
            writer.writerow(
                {
                    "stock_code": row["stock_code"],
                    "corp_code": corp_code or "",
                    "corp_name": row["corp_name"],
                    "market": row["market"],
                    "security_kind": row["security_kind"],
                }
            )
            if progress and i % 200 == 0:
                print(f"[매핑] 진행 {i}/{len(rows)} · 성공 {mapped} · 누락 {missing}")

    if progress:
        print(f"[매핑] 완료 → {settings.mapping_csv} (성공 {mapped} · 누락 {missing})")
    return settings.mapping_csv


def load_mapping(settings: Settings) -> list[dict[str, str]]:
    """매핑 CSV를 읽어 corp_code가 있는 행만 반환."""
    if not settings.mapping_csv.exists():
        raise FileNotFoundError(
            f"매핑 CSV가 없습니다: {settings.mapping_csv}. 먼저 `audit-xlsx build-mapping` 을 실행하세요."
        )
    with settings.mapping_csv.open("r", encoding="utf-8-sig", newline="") as f:
        return [r for r in csv.DictReader(f) if (r.get("corp_code") or "").strip()]
