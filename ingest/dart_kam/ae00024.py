"""Structured periodic-report API: ``accnutAdtorNmNdAdtOpinion`` (DE002 / AE00024).

OPENDART 의 정기보고 *구조화 API* 중 회계감사인 명·감사의견 정보를 제공하는
``accnutAdtorNmNdAdtOpinion`` 호출 결과를 (corp_code × 사업연도 × 보고서 코드) 단위로
캐시한다. 휴리스틱 파싱 결과(``parse_results``) 와 교차검증할 때 사용한다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from dart_kam.dart_client import DartClient
from dart_kam.progress_util import BatchProgress
from dart_kam.repository import select_distinct_corp_years, upsert_ae00024


# 1분기보고서/반기보고서/3분기보고서/사업보고서 중 사업보고서 코드.
_DEFAULT_REPRT_CODE = "11011"


def fetch_ae00024(
    client: DartClient,
    corp_code: str,
    bsns_year: str,
    reprt_code: str = _DEFAULT_REPRT_CODE,
) -> dict[str, Any]:
    """단일 (corp_code, 사업연도, 보고서코드) 조회. raw JSON 반환."""
    return client.get_json(
        "accnutAdtorNmNdAdtOpinion.json",
        {"corp_code": corp_code, "bsns_year": bsns_year, "reprt_code": reprt_code},
    )


def cache_ae00024_for_filings(
    client: DartClient,
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
    progress: bool = True,
) -> int:
    """``filings`` 에 등장한 모든 ``(corp_code, YYYY)`` 조합에 대해 AE00024 캐시 적재.

    :returns: 캐시에 반영된 건수 (성공 + 실패 모두 포함).
    """
    pairs = select_distinct_corp_years(conn, limit=limit)
    bp = BatchProgress(
        label="AE00024(accnutAdtorNmNdAdtOpinion) 조회",
        total=len(pairs),
        enabled=progress,
    )
    bp.start(extra="(고유 법인·연도)")
    if not pairs:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    cached = 0
    for idx, (corp_code, bsns_year) in enumerate(pairs, start=1):
        if not corp_code or not bsns_year:
            continue
        bp.tick(
            idx,
            detail=f"corp_code={corp_code} · 사업연도 {bsns_year} 조회 중",
        )
        try:
            data = fetch_ae00024(client, corp_code, bsns_year, _DEFAULT_REPRT_CODE)
            status = str(data.get("status", ""))
            message = str(data.get("message", ""))
            upsert_ae00024(
                conn,
                corp_code=corp_code,
                bsns_year=bsns_year,
                reprt_code=_DEFAULT_REPRT_CODE,
                status=status,
                payload=data,
                message=message,
                fetched_at=now,
            )
            cached += 1
            bp.done(
                idx,
                ok=cached,
                bad=0,
                note=f"API상태 {status} - 캐시 반영 누적 {cached}건",
            )
        except Exception as e:  # noqa: BLE001 — 단건 실패는 다음 건으로 계속
            upsert_ae00024(
                conn,
                corp_code=corp_code,
                bsns_year=bsns_year,
                reprt_code=_DEFAULT_REPRT_CODE,
                status="error",
                payload=None,
                message=str(e),
                fetched_at=now,
            )
            bp.fail(idx, ok=cached, bad=idx - cached, error=f"{corp_code}/{bsns_year}: {e}")
        conn.commit()

    bp.finish(ok=cached, bad=len(pairs) - cached)
    return cached
