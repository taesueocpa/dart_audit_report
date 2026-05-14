"""Structured periodic-report API: accnutAdtorNmNdAdtOpinion (DE002 / AE00024)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from dart_kam.dart_client import DartClient
from dart_kam.progress_util import progress_print


def fetch_ae00024(client: DartClient, corp_code: str, bsns_year: str, reprt_code: str = "11011") -> dict:
    return client.get_json(
        "accnutAdtorNmNdAdtOpinion.json",
        {"corp_code": corp_code, "bsns_year": bsns_year, "reprt_code": reprt_code},
    )


def cache_ae00024_for_filings(client: DartClient, conn, *, limit: int | None = None, progress: bool = True) -> int:
    """Store AE00024 payloads keyed by corp_code + filing year (rcept_dt YYYY)."""
    now = datetime.now(timezone.utc).isoformat()
    q = """
    SELECT DISTINCT corp_code, substr(rcept_dt,1,4) AS y
    FROM filings
    WHERE length(rcept_dt) >= 4
    ORDER BY y DESC
    """
    cur = conn.execute(q)
    rows = cur.fetchall()
    if limit is not None:
        rows = rows[: max(0, limit)]
    total = len(rows)
    progress_print(f"AE00024(accnutAdtorNmNdAdtOpinion) 조회 — 대상 총 {total}건 (고유 법인·연도)", enabled=progress)
    if total == 0:
        progress_print("조회할 (corp_code, 연도) 조합이 없습니다.", enabled=progress)
        return 0

    n = 0
    for idx, (corp_code, y) in enumerate(rows, start=1):
        remain = total - idx
        if not corp_code or not y:
            continue
        progress_print(
            f"총 {total}건 중 {idx}번째 (남음 {remain}건) — corp_code={corp_code} · 사업연도 {y} 조회 중…",
            enabled=progress,
        )
        try:
            data = fetch_ae00024(client, corp_code, str(y), "11011")
            status = str(data.get("status", ""))
            msg = str(data.get("message", ""))
            payload = json.dumps(data, ensure_ascii=False)
            conn.execute(
                """
                INSERT INTO ae00024_cache(corp_code, bsns_year, reprt_code, status, payload_json, message, fetched_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(corp_code, bsns_year, reprt_code) DO UPDATE SET
                  status=excluded.status,
                  payload_json=excluded.payload_json,
                  message=excluded.message,
                  fetched_at=excluded.fetched_at
                """,
                (corp_code, str(y), "11011", status, payload if status == "000" else None, msg, now),
            )
            n += 1
            progress_print(
                f"조회·저장 완료 {idx}/{total} — API상태 {status} — 캐시 반영 누적 {n}건",
                enabled=progress,
            )
        except Exception as e:  # noqa: BLE001
            conn.execute(
                """
                INSERT INTO ae00024_cache(corp_code, bsns_year, reprt_code, status, payload_json, message, fetched_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(corp_code, bsns_year, reprt_code) DO UPDATE SET
                  status=excluded.status,
                  payload_json=excluded.payload_json,
                  message=excluded.message,
                  fetched_at=excluded.fetched_at
                """,
                (corp_code, str(y), "11011", "error", None, str(e), now),
            )
            progress_print(f"조회 실패 {idx}/{total} — {corp_code}/{y}: {str(e)[:160]}", enabled=progress)
        conn.commit()
    progress_print(f"AE00024 단계 종료 — 캐시 반영 {n}건 (시도 대상 {total}건)", enabled=progress)
    return n
