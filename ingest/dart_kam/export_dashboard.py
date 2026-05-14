"""Export SQLite aggregates to ``summary.json`` consumed by the Next.js dashboard.

대시보드(Next.js)가 빌드 타임에 읽어가는 단일 JSON 파일을 생성한다.
스키마는 ``dashboard/lib/types.ts`` 의 ``SummaryPayload`` 와 1:1 매칭된다.

구조:

- ``generatedAt``: 생성 시각 (UTC ISO).
- ``filings``: 공시 행(최신순) 최대 8000건.
- ``summaryByYear``: ``filing_year`` 별 의견·KAM 카운트/평균/중앙값.
- ``kamItemsSample``: KAM 본문이 있는 항목 최대 5000건.
- ``ae00024Sample``: AE00024 캐시 행 최대 5000건.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_FILINGS_LIMIT = 8000
_KAM_SAMPLE_LIMIT = 5000
_AE_SAMPLE_LIMIT = 5000
# 큰 KAM 본문이 응답을 비대하게 만들지 않도록 자르는 길이.
_KAM_CONTENT_LIMIT = 32000
_KAM_BODY_SNIPPET_LIMIT = 800


def _fetch_filings(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cur = conn.execute(
        """
        SELECT f.rcept_no, f.corp_code, f.corp_name, f.stock_code, f.report_nm, f.rcept_dt,
               f.pblntf_detail_ty, p.opinion_label, p.kam_count, p.auditor_firm,
               p.opinion_modification_reason, p.accounting_standard,
               p.auditor_name, p.cpa_partner_name,
               p.emphasis_of_matter_present, p.emphasis_of_matter_content,
               p.other_matters_present, p.other_matters_content
        FROM filings f
        LEFT JOIN parse_results p ON p.rcept_no = f.rcept_no
        ORDER BY f.rcept_dt DESC
        """
    )
    rows = [dict(r) for r in cur.fetchall()]
    for row in rows:
        rd = str(row.get("rcept_dt") or "")
        row["filing_year"] = rd[:4] if len(rd) >= 4 else None
    return rows


def _aggregate_summary_by_year(
    filings: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """``filing_year`` 별 의견 카운트와 KAM 통계."""
    opinion_by_year: dict[str, Counter[str]] = defaultdict(Counter)
    kam_by_year: dict[str, list[int]] = defaultdict(list)
    filings_by_year: Counter[str] = Counter()

    for row in filings:
        year = row.get("filing_year")
        if not year:
            continue
        filings_by_year[year] += 1
        opinion = row.get("opinion_label")
        if opinion:
            opinion_by_year[year][str(opinion)] += 1
        kam_count = row.get("kam_count")
        if kam_count is None:
            continue
        try:
            kam_by_year[year].append(int(kam_count))
        except (TypeError, ValueError):
            continue

    summary: dict[str, dict[str, Any]] = {}
    for year in sorted(set(opinion_by_year) | set(kam_by_year)):
        kams = kam_by_year.get(year, [])
        kams_sorted = sorted(kams)
        summary[year] = {
            "filings": int(filings_by_year[year]),
            "opinion_counts": dict(opinion_by_year.get(year, Counter())),
            "kam_avg": (sum(kams) / len(kams)) if kams else None,
            "kam_median": (kams_sorted[len(kams_sorted) // 2]) if kams_sorted else None,
        }
    return summary


def _fetch_kam_sample(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cur = conn.execute(
        f"""
        SELECT k.rcept_no, f.corp_name, f.rcept_dt, k.ordinal, k.title,
               substr(coalesce(k.body_snippet, ''), 1, {_KAM_BODY_SNIPPET_LIMIT}) AS body_snippet,
               substr(coalesce(k.kam_content, ''), 1, {_KAM_CONTENT_LIMIT}) AS kam_content,
               k.selection_reason
        FROM kam_items k
        JOIN filings f ON f.rcept_no = k.rcept_no
        WHERE k.kam_content IS NOT NULL AND length(trim(k.kam_content)) > 0
        ORDER BY f.rcept_dt DESC, k.rcept_no, k.ordinal
        LIMIT {_KAM_SAMPLE_LIMIT}
        """
    )
    return [dict(r) for r in cur.fetchall()]


def _fetch_ae00024_sample(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cur = conn.execute(
        f"""
        SELECT corp_code, bsns_year, status, message, fetched_at
        FROM ae00024_cache
        ORDER BY bsns_year DESC, corp_code
        LIMIT {_AE_SAMPLE_LIMIT}
        """
    )
    return [dict(r) for r in cur.fetchall()]


def export_dashboard(conn: sqlite3.Connection, out_dir: Path) -> None:
    """``out_dir/summary.json`` 을 (덮어쓰기) 생성."""
    out_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()

    filings = _fetch_filings(conn)
    payload = {
        "generatedAt": generated_at,
        "filings": filings[:_FILINGS_LIMIT],
        "summaryByYear": _aggregate_summary_by_year(filings),
        "kamItemsSample": _fetch_kam_sample(conn),
        "ae00024Sample": _fetch_ae00024_sample(conn),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
