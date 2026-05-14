"""Centralized DB upsert/query helpers (one place for all SQL writes).

이전에는 다음 위치에 INSERT/UPSERT SQL이 흩어져 있었다:

- ``corp_codes.refresh_corp_codes`` → ``companies``
- ``list_service.ingest_filings`` → ``filings``
- ``document_service.ingest_documents`` → ``document_fetch`` (성공/실패 각각 동일 SQL)
- ``parse_audit.ingest_parse_results`` → ``parse_results`` + ``kam_items`` (성공/실패 80+줄 중복)
- ``ae00024.cache_ae00024_for_filings`` → ``ae00024_cache`` (성공/실패 중복)

이 모듈은 그 모든 SQL을 한곳에 모아 호출부를 1~2줄로 줄인다.
SQL 본문(특히 ``parse_results`` 의 17개 컬럼 upsert)이 변할 일이 거의 없으므로
모듈 상수로 둬서 한 곳만 보면 되도록 했다.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Mapping


# --------------------------------------------------------------------------- companies

_UPSERT_COMPANY_SQL = """
INSERT INTO companies(corp_code, corp_name, stock_code, corp_cls, updated_at)
VALUES(?,?,?,?,?)
ON CONFLICT(corp_code) DO UPDATE SET
  corp_name=excluded.corp_name,
  stock_code=excluded.stock_code,
  updated_at=excluded.updated_at
"""


def upsert_company(
    conn: sqlite3.Connection,
    *,
    corp_code: str,
    corp_name: str,
    stock_code: str,
    corp_cls: str | None,
    updated_at: str,
) -> None:
    conn.execute(
        _UPSERT_COMPANY_SQL,
        (corp_code, corp_name, stock_code, corp_cls, updated_at),
    )


# --------------------------------------------------------------------------- filings

_UPSERT_FILING_SQL = """
INSERT INTO filings(
  rcept_no, corp_code, corp_name, stock_code, report_nm, rcept_dt,
  corp_cls, pblntf_ty, pblntf_detail_ty, flr_nm, rm, fetched_at
) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(rcept_no) DO UPDATE SET
  corp_name=excluded.corp_name,
  stock_code=excluded.stock_code,
  report_nm=excluded.report_nm,
  rcept_dt=excluded.rcept_dt,
  corp_cls=excluded.corp_cls,
  pblntf_ty=excluded.pblntf_ty,
  pblntf_detail_ty=excluded.pblntf_detail_ty,
  flr_nm=excluded.flr_nm,
  rm=excluded.rm,
  fetched_at=excluded.fetched_at
"""


def upsert_filing(
    conn: sqlite3.Connection,
    *,
    rcept_no: str,
    corp_code: str,
    item: Mapping[str, Any],
    pblntf_ty: str,
    pblntf_detail_ty: str,
    fetched_at: str,
) -> None:
    """OPENDART ``list.json`` 의 단일 ``item`` 을 ``filings`` 에 업서트."""
    conn.execute(
        _UPSERT_FILING_SQL,
        (
            rcept_no,
            corp_code,
            item.get("corp_name"),
            item.get("stock_code"),
            item.get("report_nm"),
            str(item.get("rcept_dt") or ""),
            item.get("corp_cls"),
            pblntf_ty,
            pblntf_detail_ty,
            item.get("flr_nm"),
            item.get("rm"),
            fetched_at,
        ),
    )


# --------------------------------------------------------------------------- document_fetch

_UPSERT_DOCUMENT_FETCH_SQL = """
INSERT INTO document_fetch(rcept_no, status, zip_path, error, updated_at)
VALUES(?,?,?,?,?)
ON CONFLICT(rcept_no) DO UPDATE SET
  status=excluded.status,
  zip_path=excluded.zip_path,
  error=excluded.error,
  updated_at=excluded.updated_at
"""


def upsert_document_fetch(
    conn: sqlite3.Connection,
    *,
    rcept_no: str,
    status: str,
    zip_path: str | None,
    error: str | None,
    updated_at: str,
) -> None:
    conn.execute(
        _UPSERT_DOCUMENT_FETCH_SQL,
        (rcept_no, status, zip_path, error, updated_at),
    )


def mark_document_downloaded(
    conn: sqlite3.Connection,
    *,
    rcept_no: str,
    zip_path: str,
    updated_at: str,
) -> None:
    upsert_document_fetch(
        conn,
        rcept_no=rcept_no,
        status="downloaded",
        zip_path=zip_path,
        error=None,
        updated_at=updated_at,
    )


def mark_document_failed(
    conn: sqlite3.Connection,
    *,
    rcept_no: str,
    error: str,
    updated_at: str,
) -> None:
    upsert_document_fetch(
        conn,
        rcept_no=rcept_no,
        status="failed",
        zip_path=None,
        error=error,
        updated_at=updated_at,
    )


# --------------------------------------------------------------------------- parse_results & kam_items

# 17개 컬럼을 다루는 거대한 UPSERT. 성공/실패 두 경로에서 동일 SQL 을 공유한다 (이전엔 80+줄 중복).
_UPSERT_PARSE_RESULT_SQL = """
INSERT INTO parse_results(
  rcept_no, parser_version, opinion_label, opinion_raw_snippet,
  opinion_modification_reason, accounting_standard,
  auditor_firm, auditor_name, cpa_partner_name, kam_count,
  emphasis_of_matter_present, emphasis_of_matter_content,
  other_matters_present, other_matters_content,
  kam_section_full, audit_report_body,
  parsed_at, parse_error
) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(rcept_no) DO UPDATE SET
  parser_version=excluded.parser_version,
  opinion_label=excluded.opinion_label,
  opinion_raw_snippet=excluded.opinion_raw_snippet,
  opinion_modification_reason=excluded.opinion_modification_reason,
  accounting_standard=excluded.accounting_standard,
  auditor_firm=excluded.auditor_firm,
  auditor_name=excluded.auditor_name,
  cpa_partner_name=excluded.cpa_partner_name,
  kam_count=excluded.kam_count,
  emphasis_of_matter_present=excluded.emphasis_of_matter_present,
  emphasis_of_matter_content=excluded.emphasis_of_matter_content,
  other_matters_present=excluded.other_matters_present,
  other_matters_content=excluded.other_matters_content,
  kam_section_full=excluded.kam_section_full,
  audit_report_body=excluded.audit_report_body,
  parsed_at=excluded.parsed_at,
  parse_error=excluded.parse_error
"""

# 실패 시에는 모든 분석 컬럼을 NULL/0 로 비운다.
_UPSERT_PARSE_RESULT_FAILURE_SQL = """
INSERT INTO parse_results(
  rcept_no, parser_version, opinion_label, opinion_raw_snippet,
  opinion_modification_reason, accounting_standard,
  auditor_firm, auditor_name, cpa_partner_name, kam_count,
  emphasis_of_matter_present, emphasis_of_matter_content,
  other_matters_present, other_matters_content,
  kam_section_full, audit_report_body,
  parsed_at, parse_error
) VALUES(?,?,NULL,NULL,NULL,NULL,NULL,NULL,NULL,0,0,NULL,0,NULL,NULL,NULL,?,?)
ON CONFLICT(rcept_no) DO UPDATE SET
  parser_version=excluded.parser_version,
  opinion_label=NULL,
  opinion_raw_snippet=NULL,
  opinion_modification_reason=NULL,
  accounting_standard=NULL,
  auditor_firm=NULL,
  auditor_name=NULL,
  cpa_partner_name=NULL,
  kam_count=0,
  emphasis_of_matter_present=0,
  emphasis_of_matter_content=NULL,
  other_matters_present=0,
  other_matters_content=NULL,
  kam_section_full=NULL,
  audit_report_body=NULL,
  parsed_at=excluded.parsed_at,
  parse_error=excluded.parse_error
"""


def _i(value: Any) -> int:
    """None/문자열 0/잘못된 값 → 0 으로 안전 캐스팅."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def upsert_parse_result(
    conn: sqlite3.Connection,
    *,
    rcept_no: str,
    parser_version: str,
    result: Mapping[str, Any],
    parsed_at: str,
) -> None:
    """파싱 *성공* 결과를 ``parse_results`` 에 업서트.

    ``result`` 는 :func:`dart_kam.parse_audit.parse_filing_zip` 가 반환하는 dict.
    """
    conn.execute(
        _UPSERT_PARSE_RESULT_SQL,
        (
            rcept_no,
            parser_version,
            result.get("opinion_label"),
            result.get("opinion_raw_snippet"),
            result.get("opinion_modification_reason"),
            result.get("accounting_standard"),
            result.get("auditor_firm"),
            result.get("auditor_name"),
            result.get("cpa_partner_name"),
            _i(result.get("kam_count")),
            _i(result.get("emphasis_of_matter_present")),
            result.get("emphasis_of_matter_content"),
            _i(result.get("other_matters_present")),
            result.get("other_matters_content"),
            result.get("kam_section_full"),
            result.get("audit_report_body"),
            parsed_at,
            None,
        ),
    )


def mark_parse_failure(
    conn: sqlite3.Connection,
    *,
    rcept_no: str,
    parser_version: str,
    error: str,
    parsed_at: str,
) -> None:
    """파싱 *실패* 마킹. 기존 분석 컬럼은 모두 NULL/0 로 리셋된다."""
    conn.execute(
        _UPSERT_PARSE_RESULT_FAILURE_SQL,
        (rcept_no, parser_version, parsed_at, error),
    )


_INSERT_KAM_ITEM_SQL = """
INSERT INTO kam_items(
  rcept_no, ordinal, title, body_snippet, kam_content, selection_reason
) VALUES(?,?,?,?,?,?)
"""


def replace_kam_items(
    conn: sqlite3.Connection,
    *,
    rcept_no: str,
    items: list[Mapping[str, Any]],
) -> None:
    """``rcept_no`` 에 해당하는 ``kam_items`` 를 모두 삭제 후 ``items`` 로 재삽입."""
    conn.execute("DELETE FROM kam_items WHERE rcept_no = ?", (rcept_no,))
    for it in items:
        conn.execute(
            _INSERT_KAM_ITEM_SQL,
            (
                rcept_no,
                it.get("ordinal"),
                it.get("title"),
                it.get("body_snippet"),
                it.get("kam_content"),
                it.get("selection_reason"),
            ),
        )


# --------------------------------------------------------------------------- ae00024_cache

_UPSERT_AE00024_SQL = """
INSERT INTO ae00024_cache(
  corp_code, bsns_year, reprt_code, status, payload_json, message, fetched_at
) VALUES(?,?,?,?,?,?,?)
ON CONFLICT(corp_code, bsns_year, reprt_code) DO UPDATE SET
  status=excluded.status,
  payload_json=excluded.payload_json,
  message=excluded.message,
  fetched_at=excluded.fetched_at
"""


def upsert_ae00024(
    conn: sqlite3.Connection,
    *,
    corp_code: str,
    bsns_year: str,
    reprt_code: str,
    status: str,
    payload: Mapping[str, Any] | None,
    message: str,
    fetched_at: str,
) -> None:
    """AE00024 (회계감사인·감사의견) 응답을 캐시 테이블에 업서트.

    ``status == '000'`` 인 경우에만 ``payload`` JSON 을 저장한다.
    실패시(``payload=None``) 페이로드 컬럼은 NULL.
    """
    payload_json = json.dumps(payload, ensure_ascii=False) if (payload and status == "000") else None
    conn.execute(
        _UPSERT_AE00024_SQL,
        (corp_code, bsns_year, reprt_code, status, payload_json, message, fetched_at),
    )


# --------------------------------------------------------------------------- selection queries

def select_filings_for_download(
    conn: sqlite3.Connection,
    *,
    skip_downloaded: bool,
    limit: int | None,
) -> list[str]:
    """다운로드 대상 ``rcept_no`` 목록을 접수일 내림차순으로 반환."""
    if skip_downloaded:
        cur = conn.execute(
            """
            SELECT f.rcept_no FROM filings f
            LEFT JOIN document_fetch d ON d.rcept_no = f.rcept_no
            WHERE d.rcept_no IS NULL OR d.status != 'downloaded'
            ORDER BY f.rcept_dt DESC
            """
        )
    else:
        cur = conn.execute("SELECT f.rcept_no FROM filings f ORDER BY f.rcept_dt DESC")
    rows = [str(r[0]) for r in cur.fetchall()]
    if limit is not None:
        rows = rows[: max(0, limit)]
    return rows


def select_filings_for_parse(
    conn: sqlite3.Connection,
    *,
    force: bool,
    limit: int | None,
) -> list[str]:
    """파싱 대상 ``rcept_no`` 목록. ``force=False`` 면 미파싱·실패만."""
    where_parse = "WHERE 1=1" if force else "WHERE p.rcept_no IS NULL OR p.parse_error IS NOT NULL"
    cur = conn.execute(
        f"""
        SELECT f.rcept_no
        FROM filings f
        JOIN document_fetch d ON d.rcept_no = f.rcept_no AND d.status = 'downloaded'
        LEFT JOIN parse_results p ON p.rcept_no = f.rcept_no
        {where_parse}
        ORDER BY f.rcept_dt DESC
        """
    )
    rows = [str(r[0]) for r in cur.fetchall()]
    if limit is not None:
        rows = rows[: max(0, limit)]
    return rows


def select_distinct_corp_years(
    conn: sqlite3.Connection,
    *,
    limit: int | None,
) -> list[tuple[str, str]]:
    """AE00024 조회 대상이 되는 (corp_code, YYYY) 고유 조합. 최신 연도 우선."""
    cur = conn.execute(
        """
        SELECT DISTINCT corp_code, substr(rcept_dt,1,4) AS y
        FROM filings
        WHERE length(rcept_dt) >= 4
        ORDER BY y DESC
        """
    )
    rows: list[tuple[str, str]] = [
        (str(r[0] or ""), str(r[1] or "")) for r in cur.fetchall()
    ]
    if limit is not None:
        rows = rows[: max(0, limit)]
    return rows
