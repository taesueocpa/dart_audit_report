"""Command-line entrypoint."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

# Allow running as `python -m dart_kam.cli` from ingest/ without install
_INGEST_ROOT = Path(__file__).resolve().parents[1]
if str(_INGEST_ROOT) not in sys.path:
    sys.path.insert(0, str(_INGEST_ROOT))

from dart_kam.ae00024 import cache_ae00024_for_filings
from dart_kam.config import load_settings
from dart_kam.corp_codes import refresh_corp_codes
from dart_kam.db import connect, init_db, set_meta
from dart_kam.document_service import ingest_documents
from dart_kam.dart_client import DartClient
from dart_kam.export_dashboard import export_dashboard
from dart_kam.list_service import ingest_filings
from dart_kam.parse_audit import ingest_parse_results
from dart_kam.paths import db_path


def _add_verbose_http(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--verbose-http",
        action="store_true",
        help="OPENDART 요청마다 터미널에 요약 로그 (인증키 마스킹). 또는 환경변수 DART_HTTP_LOG=1",
    )


def _apply_verbose_http(args: argparse.Namespace) -> None:
    if getattr(args, "verbose_http", False):
        os.environ["DART_HTTP_LOG"] = "1"


def _add_quiet(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="진행 메시지([진행] 접두사) 숨김 — 마지막 요약 출력만 유지",
    )


def _parse_yyyymmdd(s: str) -> date:
    if len(s) != 8 or not s.isdigit():
        raise argparse.ArgumentTypeError(f"YYYYMMDD expected, got {s!r}")
    return date(int(s[0:4]), int(s[4:6]), int(s[6:8]))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="dart-kam")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init-db", help="Create SQLite schema under DART_KAM_DATA_DIR")
    p_init.set_defaults(func=_cmd_init_db)

    p_corp = sub.add_parser("sync-corp", help="Download corpCode.zip and upsert companies table")
    _add_verbose_http(p_corp)
    _add_quiet(p_corp)
    p_corp.set_defaults(func=_cmd_sync_corp)

    p_list = sub.add_parser("list", help="Ingest disclosure list (F001/F002/F003 by default)")
    _add_verbose_http(p_list)
    _add_quiet(p_list)
    p_list.add_argument("--bgn-de", type=str, default=None, help="YYYYMMDD")
    p_list.add_argument("--end-de", type=str, default=None, help="YYYYMMDD")
    p_list.set_defaults(func=_cmd_list)

    p_doc = sub.add_parser("documents", help="Download document ZIPs for filings")
    _add_verbose_http(p_doc)
    _add_quiet(p_doc)
    p_doc.add_argument("--limit", type=int, default=None, help="처리할 최대 건수(접수일 내림차순)")
    p_doc.add_argument("--all", action="store_true", help="Re-download including already downloaded")
    p_doc.add_argument("--verbose", "-v", action="store_true", help="Print first failure messages")
    p_doc.set_defaults(func=_cmd_documents)

    p_parse = sub.add_parser("parse", help="Parse downloaded ZIPs into opinions/KAM tables")
    _add_quiet(p_parse)
    p_parse.add_argument("--limit", type=int, default=None, help="처리할 최대 건수(접수일 내림차순)")
    p_parse.add_argument(
        "--force",
        action="store_true",
        help="이미 파싱 성공한 건도 ZIP부터 다시 파싱",
    )
    p_parse.set_defaults(func=_cmd_parse)

    p_ae = sub.add_parser("ae00024", help="Fetch structured auditor/opinion API for distinct corp+year")
    _add_verbose_http(p_ae)
    _add_quiet(p_ae)
    p_ae.add_argument("--limit", type=int, default=None)
    p_ae.set_defaults(func=_cmd_ae)

    p_exp = sub.add_parser("export-dashboard", help="Write dashboard/public/data/summary.json")
    p_exp.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "dashboard" / "public" / "data",
    )
    p_exp.set_defaults(func=_cmd_export)

    args = p.parse_args(argv)
    return int(args.func(args))


def _conn(settings):
    conn = connect(db_path(settings))
    init_db(conn)
    set_meta(conn, "scope.default_years_back", str(settings.years_back))
    set_meta(conn, "scope.pblntf_ty", settings.pblntf_ty)
    set_meta(conn, "scope.pblntf_detail", ",".join(settings.pblntf_detail_types))
    return conn


def _cmd_init_db(args: argparse.Namespace) -> int:
    settings = load_settings()
    conn = _conn(settings)
    conn.close()
    print("OK:", db_path(settings))
    return 0


def _cmd_sync_corp(args: argparse.Namespace) -> int:
    _apply_verbose_http(args)
    settings = load_settings()
    settings.require_key()
    conn = _conn(settings)
    client = DartClient(settings)
    try:
        n = refresh_corp_codes(client, settings, conn, progress=not args.quiet)
    finally:
        client.close()
    print("companies upserted:", n)
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    _apply_verbose_http(args)
    settings = load_settings()
    settings.require_key()
    conn = _conn(settings)
    client = DartClient(settings)
    try:
        start = _parse_yyyymmdd(args.bgn_de) if args.bgn_de else None
        end = _parse_yyyymmdd(args.end_de) if args.end_de else None
        n = ingest_filings(client, settings, conn, start=start, end=end, progress=not args.quiet)
    finally:
        client.close()
    print("filings touched:", n)
    return 0


def _cmd_documents(args: argparse.Namespace) -> int:
    _apply_verbose_http(args)
    settings = load_settings()
    settings.require_key()
    conn = _conn(settings)
    client = DartClient(settings)
    try:
        ok, bad = ingest_documents(
            client,
            settings,
            conn,
            limit=args.limit,
            skip_downloaded=not getattr(args, "all", False),
            verbose=args.verbose,
            progress=not args.quiet,
        )
    finally:
        client.close()
    print("downloaded:", ok, "failed:", bad)
    return 0 if bad == 0 else 0


def _cmd_parse(args: argparse.Namespace) -> int:
    settings = load_settings()
    conn = _conn(settings)
    ok, bad = ingest_parse_results(
        settings,
        conn,
        limit=args.limit,
        force=getattr(args, "force", False),
        progress=not args.quiet,
    )
    print("parsed:", ok, "failed:", bad)
    return 0


def _cmd_ae(args: argparse.Namespace) -> int:
    _apply_verbose_http(args)
    settings = load_settings()
    settings.require_key()
    conn = _conn(settings)
    client = DartClient(settings)
    try:
        n = cache_ae00024_for_filings(client, conn, limit=args.limit, progress=not args.quiet)
    finally:
        client.close()
    print("ae00024 upserts:", n)
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    settings = load_settings()
    conn = _conn(settings)
    try:
        export_dashboard(conn, args.out)
    finally:
        conn.close()
    print("OK:", args.out / "summary.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
