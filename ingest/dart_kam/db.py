"""SQLite schema and connection helpers.

스키마 정의 + 신규 컬럼을 위한 마이그레이션을 보관한다.
구체적인 INSERT/UPSERT 는 :mod:`dart_kam.repository` 로 분리되어 있다.

운영상 주의: 파일 경로가 OneDrive 등 동기화 폴더에 있으면 Windows 에서 쓰기 잠금
경합이 잦아진다. :func:`connect` 가 ``busy_timeout=60000`` 을 켜는 이유.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS companies (
  corp_code TEXT PRIMARY KEY,
  corp_name TEXT,
  stock_code TEXT,
  corp_cls TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS filings (
  rcept_no TEXT PRIMARY KEY,
  corp_code TEXT NOT NULL,
  corp_name TEXT,
  stock_code TEXT,
  report_nm TEXT,
  rcept_dt TEXT NOT NULL,
  corp_cls TEXT,
  pblntf_ty TEXT,
  pblntf_detail_ty TEXT,
  flr_nm TEXT,
  rm TEXT,
  fetched_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_filings_corp ON filings(corp_code);
CREATE INDEX IF NOT EXISTS idx_filings_dt ON filings(rcept_dt);

CREATE TABLE IF NOT EXISTS document_fetch (
  rcept_no TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  zip_path TEXT,
  error TEXT,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (rcept_no) REFERENCES filings(rcept_no)
);

CREATE TABLE IF NOT EXISTS parse_results (
  rcept_no TEXT PRIMARY KEY,
  parser_version TEXT NOT NULL,
  opinion_label TEXT,
  opinion_raw_snippet TEXT,
  opinion_modification_reason TEXT,
  accounting_standard TEXT,
  auditor_firm TEXT,
  auditor_name TEXT,
  cpa_partner_name TEXT,
  kam_count INTEGER,
  emphasis_of_matter_present INTEGER,
  emphasis_of_matter_content TEXT,
  other_matters_present INTEGER,
  other_matters_content TEXT,
  kam_section_full TEXT,
  audit_report_body TEXT,
  parsed_at TEXT NOT NULL,
  parse_error TEXT,
  FOREIGN KEY (rcept_no) REFERENCES filings(rcept_no)
);

CREATE TABLE IF NOT EXISTS kam_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  rcept_no TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  title TEXT,
  body_snippet TEXT,
  kam_content TEXT,
  selection_reason TEXT,
  UNIQUE(rcept_no, ordinal),
  FOREIGN KEY (rcept_no) REFERENCES filings(rcept_no)
);

CREATE TABLE IF NOT EXISTS ae00024_cache (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  corp_code TEXT NOT NULL,
  bsns_year TEXT NOT NULL,
  reprt_code TEXT NOT NULL,
  status TEXT NOT NULL,
  payload_json TEXT,
  message TEXT,
  fetched_at TEXT NOT NULL,
  UNIQUE(corp_code, bsns_year, reprt_code)
);

CREATE INDEX IF NOT EXISTS idx_kam_rcept ON kam_items(rcept_no);
"""


# DB 첫 생성 이후 추가된 컬럼들. ``ALTER TABLE ... ADD COLUMN`` 으로 점진 적용한다.
_PARSE_RESULTS_NEW_COLUMNS: tuple[tuple[str, str], ...] = (
    ("opinion_modification_reason", "TEXT"),
    ("accounting_standard", "TEXT"),
    ("auditor_name", "TEXT"),
    ("cpa_partner_name", "TEXT"),
    ("emphasis_of_matter_present", "INTEGER"),
    ("emphasis_of_matter_content", "TEXT"),
    ("other_matters_present", "INTEGER"),
    ("other_matters_content", "TEXT"),
    ("kam_section_full", "TEXT"),
    ("audit_report_body", "TEXT"),
)
_KAM_ITEMS_NEW_COLUMNS: tuple[tuple[str, str], ...] = (
    ("kam_content", "TEXT"),
    ("selection_reason", "TEXT"),
)


def connect(db_path: Path, *, timeout_sec: float = 60.0) -> sqlite3.Connection:
    """SQLite 연결. Windows / OneDrive 환경의 잠금 경합을 견딜 수 있도록 대기를 길게 설정.

    :param db_path: ``.sqlite3`` 파일 경로 (부모 디렉터리 자동 생성).
    :param timeout_sec: ``connect()`` 자체의 대기.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=timeout_sec)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return {str(row[1]) for row in cur.fetchall()}


def _add_missing_columns(
    conn: sqlite3.Connection,
    table: str,
    columns: tuple[tuple[str, str], ...],
) -> None:
    existing = _table_columns(conn, table)
    for name, decl in columns:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def ensure_parse_schema_migrations(conn: sqlite3.Connection) -> None:
    """첫 DB 생성 이후 추가된 ``parse_results`` / ``kam_items`` 컬럼들을 보장."""
    _add_missing_columns(conn, "parse_results", _PARSE_RESULTS_NEW_COLUMNS)
    _add_missing_columns(conn, "kam_items", _KAM_ITEMS_NEW_COLUMNS)
    conn.commit()


def init_db(conn: sqlite3.Connection) -> None:
    """스키마 + 마이그레이션을 멱등 적용."""
    conn.executescript(SCHEMA)
    ensure_parse_schema_migrations(conn)


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
