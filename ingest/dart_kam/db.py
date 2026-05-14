"""SQLite schema and helpers."""

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


def connect(db_path: Path, *, timeout_sec: float = 60.0) -> sqlite3.Connection:
    """Open SQLite with lock waits (helps on Windows / OneDrive folders)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=timeout_sec)
    conn.row_factory = sqlite3.Row
    # Milliseconds: how long to retry when the DB is busy (complements connect timeout).
    conn.execute("PRAGMA busy_timeout=60000")
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return {str(r[1]) for r in cur.fetchall()}


def ensure_parse_schema_migrations(conn: sqlite3.Connection) -> None:
    """Add columns introduced after first DB creation (SQLite ALTER TABLE)."""
    pr = _table_columns(conn, "parse_results")
    for col, decl in (
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
    ):
        if col not in pr:
            conn.execute(f"ALTER TABLE parse_results ADD COLUMN {col} {decl}")
    ki = _table_columns(conn, "kam_items")
    for col, decl in (
        ("kam_content", "TEXT"),
        ("selection_reason", "TEXT"),
    ):
        if col not in ki:
            conn.execute(f"ALTER TABLE kam_items ADD COLUMN {col} {decl}")
    conn.commit()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    ensure_parse_schema_migrations(conn)


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
