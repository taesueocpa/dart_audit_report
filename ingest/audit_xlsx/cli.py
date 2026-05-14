"""CLI: build-mapping / run.

서브커맨드:
- ``build-mapping`` : market CSV → 종목코드/corp_code 매핑 CSV 생성
- ``run``           : 매핑 CSV → 회계감사 구조화 API + 본문 다운 → XLSX
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# `python -m audit_xlsx` 가 ingest/ 외부에서도 동작하도록 path 보강.
_INGEST_ROOT = Path(__file__).resolve().parents[1]
if str(_INGEST_ROOT) not in sys.path:
    sys.path.insert(0, str(_INGEST_ROOT))

from audit_xlsx.export_xlsx import write_xlsx
from audit_xlsx.parse_audit import parse_many
from audit_xlsx.settings import load_settings
from audit_xlsx.stock_to_corp import build_mapping, load_mapping


def _cmd_build_mapping(_args: argparse.Namespace) -> int:
    settings = load_settings()
    build_mapping(settings)
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    settings = load_settings()
    mapping = load_mapping(settings)
    if args.limit is not None:
        mapping = mapping[: max(0, args.limit)]
    print(f"[run] 처리 대상 회사: {len(mapping)} (corp_code 보유)", flush=True)

    rows = parse_many(
        settings,
        mapping,
        bsns_year=args.bsns_year,
        reprt_code=args.reprt_code,
        start=args.start,
        end=args.end,
        fetch_body=not args.no_body,
        save_raw=args.save_raw,
    )

    out = write_xlsx(rows, settings.output_xlsx)
    n_with_op = sum(1 for r in rows if r.get("adt_opinion"))
    n_with_body = sum(1 for r in rows if r.get("audit_report_body_length"))
    n_companies = len({r.get("corp_code") for r in rows if r.get("corp_code")})
    from collections import Counter
    kinds = Counter(r.get("report_kind") or "(없음)" for r in rows)
    print(
        f"[run] XLSX 생성 완료 → {out}\n"
        f"      회사 {n_companies}개 / 행 {len(rows)}개 / 의견 {n_with_op}건 / 본문 {n_with_body}건\n"
        f"      종류 분포: {dict(kinds)}",
        flush=True,
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="audit-xlsx")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_map = sub.add_parser("build-mapping", help="종목코드 → corp_code 매핑 CSV 생성")
    p_map.set_defaults(func=_cmd_build_mapping)

    p_run = sub.add_parser("run", help="회계감사 구조화 API + 본문 다운 → XLSX")
    p_run.add_argument("--bsns-year", default="2025", help="사업연도 (기본 2025)")
    p_run.add_argument(
        "--reprt-code",
        default="11011",
        choices=["11011", "11012", "11013", "11014"],
        help="11011=사업보고서(기본), 11012=반기, 11013=1분기, 11014=3분기",
    )
    p_run.add_argument("--start", default="2026-01-01", help="감사보고서제출 공시 검색 시작일 (YYYY-MM-DD)")
    p_run.add_argument("--end", default="2026-04-30", help="감사보고서제출 공시 검색 종료일 (YYYY-MM-DD)")
    p_run.add_argument("--limit", type=int, default=None, help="매핑 첫 N개만 처리")
    p_run.add_argument("--no-body", action="store_true", help="본문 다운로드 생략 (구조화 4필드만)")
    p_run.add_argument("--save-raw", action="store_true", help="raw HTML을 data/raw_audit/<parent>_<dcm>.html 로 저장")
    p_run.set_defaults(func=_cmd_run)

    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
