"""Parse downloaded disclosure ZIPs into ``parse_results`` + ``kam_items`` rows.

이 모듈은 **파이프라인 오케스트레이션**만 담당한다:

1. :mod:`dart_kam.audit_text` 로 ZIP → 평문 텍스트 로딩.
2. :mod:`dart_kam.audit_extractors` 로 정보 추출.
3. :mod:`dart_kam.repository` 로 DB 업서트.

이전 버전에서 이 파일 한 곳에 섞여 있던 텍스트 추출 함수들은 모두
:mod:`dart_kam.audit_extractors` 로 옮겨졌다. 하위 호환을 위해 그 함수들을
이 모듈에서도 동일 이름으로 다시 노출한다 (re-export).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from dart_kam.audit_extractors import (
    analyze_audit_text,
    classify_opinion,
    detect_accounting_standard,
    extract_auditor_firm,
    extract_cpa_partner,
    extract_emphasis_of_matter,
    extract_kam_section_full,
    extract_opinion_modification_reason,
    extract_other_matters,
    extract_standalone_audit_report_body,
)
from dart_kam.audit_text import load_filing_flat_text
from dart_kam.config import Settings
from dart_kam.progress_util import BatchProgress
from dart_kam.repository import (
    mark_parse_failure,
    replace_kam_items,
    select_filings_for_parse,
    upsert_parse_result,
)


__all__ = [
    # Pipeline.
    "parse_filing_zip",
    "ingest_parse_results",
    # Re-exports (backward compat).
    "analyze_audit_text",
    "classify_opinion",
    "detect_accounting_standard",
    "extract_auditor_firm",
    "extract_cpa_partner",
    "extract_emphasis_of_matter",
    "extract_kam_section_full",
    "extract_opinion_modification_reason",
    "extract_other_matters",
    "extract_standalone_audit_report_body",
    "load_filing_flat_text",
]


# KAM 본문 일부를 ``kam_items.body_snippet`` 컬럼에 저장할 때 자르는 길이.
_KAM_SNIPPET_LIMIT = 800


def parse_filing_zip(settings: Settings, rcept_no: str) -> dict[str, Any]:
    """단일 공시 ZIP → ``parse_results`` 컬럼 dict.

    실패 시(ZIP 없음, XML 깨짐 등) 예외가 그대로 전파된다.
    상위 호출자(:func:`ingest_parse_results`)가 잡아서 ``parse_error`` 컬럼에 기록.
    """
    flat_text = load_filing_flat_text(settings, rcept_no)
    return analyze_audit_text(flat_text)


def _kam_items_for(rcept_no: str, kam_full: Any) -> list[dict[str, Any]]:
    """KAM 절 본문이 있으면 ``kam_items`` 한 줄을 만들기 위한 dict 리스트로 변환."""
    if not isinstance(kam_full, str):
        return []
    text = kam_full.strip()
    if not text:
        return []
    return [
        {
            "ordinal": 1,
            "title": "핵심감사사항",
            "body_snippet": text[:_KAM_SNIPPET_LIMIT],
            "kam_content": text,
            "selection_reason": None,
        }
    ]


def ingest_parse_results(
    settings: Settings,
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
    force: bool = False,
    progress: bool = True,
) -> tuple[int, int]:
    """다운로드 완료된 공시 ZIP들을 파싱해서 ``parse_results`` / ``kam_items`` 에 반영.

    :param force: ``True`` 면 이미 성공 파싱된 건도 재파싱.
    :returns: ``(성공 건수, 실패 건수)``.
    """
    rcept_nos = select_filings_for_parse(conn, force=force, limit=limit)
    bp = BatchProgress(label="ZIP 파싱", total=len(rcept_nos), enabled=progress)
    extra = "" if force else "(다운로드 완료·미파싱 또는 실패 재시도)"
    bp.start(extra=extra)
    if not rcept_nos:
        return 0, 0

    now = datetime.now(timezone.utc).isoformat()
    ok = 0
    bad = 0
    for idx, rcept_no in enumerate(rcept_nos, start=1):
        bp.tick(idx, detail=f"rcept_no={rcept_no} 파싱 중")
        try:
            result = parse_filing_zip(settings, rcept_no)
            replace_kam_items(
                conn,
                rcept_no=rcept_no,
                items=_kam_items_for(rcept_no, result.get("kam_section_full")),
            )
            upsert_parse_result(
                conn,
                rcept_no=rcept_no,
                parser_version=settings.parser_version,
                result=result,
                parsed_at=now,
            )
            ok += 1
            bp.done(
                idx,
                ok=ok,
                bad=bad,
                note=f"의견={result.get('opinion_label')!s} · KAM {result.get('kam_count')}건",
            )
        except Exception as e:  # noqa: BLE001 — 단건 실패는 다음 건으로 계속
            bad += 1
            bp.fail(idx, ok=ok, bad=bad, error=f"{rcept_no}: {e}")
            mark_parse_failure(
                conn,
                rcept_no=rcept_no,
                parser_version=settings.parser_version,
                error=str(e),
                parsed_at=now,
            )
        conn.commit()

    bp.finish(ok=ok, bad=bad)
    return ok, bad
