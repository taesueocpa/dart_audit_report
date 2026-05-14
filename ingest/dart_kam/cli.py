"""Command-line entrypoint for the DART KAM ingest pipeline.

서브커맨드 한눈 보기:

- ``init-db`` — SQLite 스키마 생성/마이그레이션.
- ``sync-corp`` — corpCode.zip 다운로드, ``companies`` 업서트.
- ``list`` — 공시목록(``filings``) 인제스트.
- ``documents`` — 공시 원본 ZIP 다운로드.
- ``parse`` — 다운로드된 ZIP 파싱 → ``parse_results`` / ``kam_items``.
- ``ae00024`` — 구조화 API 캐시 적재.
- ``export-dashboard`` — ``dashboard/public/data/summary.json`` 생성.

공통 옵션:

- ``--verbose-http`` : OPENDART 호출마다 한 줄 로그.
- ``-q / --quiet``    : ``[진행]`` 메시지 숨김.

이전 버전의 각 ``_cmd_*`` 함수에 반복되던 4줄짜리 보일러플레이트
(``load_settings → require_key → connect → DartClient → close``) 는 :func:`with_client`
컨텍스트 매니저로 통합되었다. 부수적으로 종전에 누락되었던 ``conn.close()`` 가 정상 호출된다.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Callable, Iterator

# ``python -m dart_kam.cli`` 를 별도 설치 없이 ``ingest/`` 에서 실행할 수 있게 path 보강.
_INGEST_ROOT = Path(__file__).resolve().parents[1]
if str(_INGEST_ROOT) not in sys.path:
    sys.path.insert(0, str(_INGEST_ROOT))

from dart_kam.ae00024 import cache_ae00024_for_filings
from dart_kam.config import Settings, load_settings
from dart_kam.corp_codes import refresh_corp_codes
from dart_kam.dart_client import DartClient
from dart_kam.db import connect, init_db, set_meta
from dart_kam.document_service import ingest_documents
from dart_kam.export_dashboard import export_dashboard
from dart_kam.list_service import ingest_filings
from dart_kam.parse_audit import ingest_parse_results
from dart_kam.paths import db_path


# --------------------------------------------------------------------------- argparse helpers

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
        help="진행 메시지([진행] 접두사) 숨김 - 마지막 요약 출력만 유지",
    )


def _parse_yyyymmdd(s: str) -> date:
    if len(s) != 8 or not s.isdigit():
        raise argparse.ArgumentTypeError(f"YYYYMMDD expected, got {s!r}")
    return date(int(s[0:4]), int(s[4:6]), int(s[6:8]))


# --------------------------------------------------------------------------- shared setup

def _open_conn(settings: Settings) -> sqlite3.Connection:
    """DB 연결 + 스키마 마이그레이션 + 메타 키 기록."""
    conn = connect(db_path(settings))
    init_db(conn)
    set_meta(conn, "scope.default_years_back", str(settings.years_back))
    set_meta(conn, "scope.pblntf_ty", settings.pblntf_ty)
    set_meta(conn, "scope.pblntf_detail", ",".join(settings.pblntf_detail_types))
    return conn


@contextmanager
def _db_session(*, require_key: bool) -> Iterator[tuple[Settings, sqlite3.Connection]]:
    """``load_settings → (optional require_key) → open conn`` 를 묶고 정리까지 책임진다."""
    settings = load_settings()
    if require_key:
        settings.require_key()
    conn = _open_conn(settings)
    try:
        yield settings, conn
    finally:
        conn.close()


@contextmanager
def _with_client(
    args: argparse.Namespace, *, require_key: bool = True
) -> Iterator[tuple[Settings, sqlite3.Connection, DartClient]]:
    """대부분의 OPENDART 서브커맨드에서 쓰는 공통 진입.

    - ``--verbose-http`` 플래그 반영.
    - settings + DB conn + HTTP client 셋업.
    - 종료 시 client / conn 모두 close (이전 cli.py 의 conn 누수 버그 해결).
    """
    _apply_verbose_http(args)
    with _db_session(require_key=require_key) as (settings, conn):
        with DartClient(settings) as client:
            yield settings, conn, client


# --------------------------------------------------------------------------- subcommands

def _cmd_init_db(_args: argparse.Namespace) -> int:
    with _db_session(require_key=False) as (settings, _conn):
        print("OK:", db_path(settings))
    return 0


def _cmd_sync_corp(args: argparse.Namespace) -> int:
    with _with_client(args) as (settings, conn, client):
        n = refresh_corp_codes(client, settings, conn, progress=not args.quiet)
    print("companies upserted:", n)
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    with _with_client(args) as (settings, conn, client):
        start = _parse_yyyymmdd(args.bgn_de) if args.bgn_de else None
        end = _parse_yyyymmdd(args.end_de) if args.end_de else None
        n = ingest_filings(
            client, settings, conn, start=start, end=end, progress=not args.quiet
        )
    print("filings touched:", n)
    return 0


def _cmd_documents(args: argparse.Namespace) -> int:
    with _with_client(args) as (settings, conn, client):
        ok, bad = ingest_documents(
            client,
            settings,
            conn,
            limit=args.limit,
            skip_downloaded=not getattr(args, "all", False),
            verbose=args.verbose,
            progress=not args.quiet,
        )
    print("downloaded:", ok, "failed:", bad)
    # 부분 실패도 0 으로 반환 (다음 파이프라인 단계 계속 진행을 보장).
    return 0


def _cmd_parse(args: argparse.Namespace) -> int:
    # parse 는 OPENDART 호출이 없으므로 API 키 불필요.
    with _db_session(require_key=False) as (settings, conn):
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
    with _with_client(args) as (_settings, conn, client):
        n = cache_ae00024_for_filings(
            client, conn, limit=args.limit, progress=not args.quiet
        )
    print("ae00024 upserts:", n)
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    with _db_session(require_key=False) as (_settings, conn):
        export_dashboard(conn, args.out)
    print("OK:", args.out / "summary.json")
    return 0


# --------------------------------------------------------------------------- parser wiring

# (서브커맨드 이름, 도움말, 추가 인자 등록 콜백, 핸들러) 튜플.
_CommandSpec = tuple[
    str,
    str,
    Callable[[argparse.ArgumentParser], None],
    Callable[[argparse.Namespace], int],
]


def _setup_sync_corp(p: argparse.ArgumentParser) -> None:
    _add_verbose_http(p)
    _add_quiet(p)


def _setup_list(p: argparse.ArgumentParser) -> None:
    _add_verbose_http(p)
    _add_quiet(p)
    p.add_argument("--bgn-de", type=str, default=None, help="YYYYMMDD")
    p.add_argument("--end-de", type=str, default=None, help="YYYYMMDD")


def _setup_documents(p: argparse.ArgumentParser) -> None:
    _add_verbose_http(p)
    _add_quiet(p)
    p.add_argument("--limit", type=int, default=None, help="처리할 최대 건수(접수일 내림차순)")
    p.add_argument("--all", action="store_true", help="Re-download including already downloaded")
    p.add_argument("--verbose", "-v", action="store_true", help="Print first failure messages")


def _setup_parse(p: argparse.ArgumentParser) -> None:
    _add_quiet(p)
    p.add_argument("--limit", type=int, default=None, help="처리할 최대 건수(접수일 내림차순)")
    p.add_argument(
        "--force",
        action="store_true",
        help="이미 파싱 성공한 건도 ZIP부터 다시 파싱",
    )


def _setup_ae(p: argparse.ArgumentParser) -> None:
    _add_verbose_http(p)
    _add_quiet(p)
    p.add_argument("--limit", type=int, default=None)


def _setup_export(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "dashboard" / "public" / "data",
    )


_COMMANDS: tuple[_CommandSpec, ...] = (
    ("init-db", "Create SQLite schema under DART_KAM_DATA_DIR", lambda p: None, _cmd_init_db),
    ("sync-corp", "Download corpCode.zip and upsert companies table", _setup_sync_corp, _cmd_sync_corp),
    ("list", "Ingest disclosure list (F001/F002/F003 by default)", _setup_list, _cmd_list),
    ("documents", "Download document ZIPs for filings", _setup_documents, _cmd_documents),
    ("parse", "Parse downloaded ZIPs into opinions/KAM tables", _setup_parse, _cmd_parse),
    ("ae00024", "Fetch structured auditor/opinion API for distinct corp+year", _setup_ae, _cmd_ae),
    ("export-dashboard", "Write dashboard/public/data/summary.json", _setup_export, _cmd_export),
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dart-kam")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, help_text, setup, handler in _COMMANDS:
        p = sub.add_parser(name, help=help_text)
        setup(p)
        p.set_defaults(func=handler)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
