"""Export SQLite aggregates to JSON consumed by the Next.js dashboard."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


def export_dashboard(conn: sqlite3.Connection, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()

    cur = conn.execute(
        """
        SELECT f.rcept_no, f.corp_code, f.corp_name, f.stock_code, f.report_nm, f.rcept_dt,
               f.pblntf_detail_ty, p.opinion_label, p.kam_count, p.auditor_firm,
               p.opinion_modification_reason, p.accounting_standard, p.auditor_name, p.cpa_partner_name,
               p.emphasis_of_matter_present, p.emphasis_of_matter_content,
               p.other_matters_present, p.other_matters_content
        FROM filings f
        LEFT JOIN parse_results p ON p.rcept_no = f.rcept_no
        ORDER BY f.rcept_dt DESC
        """
    )
    filings = [dict(r) for r in cur.fetchall()]
    for row in filings:
        rd = row.get("rcept_dt") or ""
        row["filing_year"] = str(rd)[:4] if len(str(rd)) >= 4 else None

    opinion_by_year: dict[str, Counter[str]] = defaultdict(Counter)
    kam_by_year: dict[str, list[int]] = defaultdict(list)
    for row in filings:
        y = row.get("filing_year")
        if not y:
            continue
        if row.get("opinion_label"):
            opinion_by_year[y][str(row["opinion_label"])] += 1
        if row.get("kam_count") is not None:
            try:
                kam_by_year[y].append(int(row["kam_count"]))
            except (TypeError, ValueError):
                pass

    summary_by_year: dict[str, dict[str, object]] = {}
    for y in sorted(set(opinion_by_year.keys()) | set(kam_by_year.keys())):
        kams = kam_by_year.get(y, [])
        summary_by_year[y] = {
            "filings": sum(1 for r in filings if r.get("filing_year") == y),
            "opinion_counts": dict(opinion_by_year.get(y, Counter())),
            "kam_avg": (sum(kams) / len(kams)) if kams else None,
            "kam_median": sorted(kams)[len(kams) // 2] if kams else None,
        }

    cur = conn.execute(
        """
        SELECT k.rcept_no, f.corp_name, f.rcept_dt, k.ordinal, k.title,
               substr(coalesce(k.body_snippet, ''), 1, 800) AS body_snippet,
               substr(coalesce(k.kam_content, ''), 1, 32000) AS kam_content,
               k.selection_reason
        FROM kam_items k
        JOIN filings f ON f.rcept_no = k.rcept_no
        WHERE k.kam_content IS NOT NULL AND length(trim(k.kam_content)) > 0
        ORDER BY f.rcept_dt DESC, k.rcept_no, k.ordinal
        LIMIT 5000
        """
    )
    kam_rows = [dict(r) for r in cur.fetchall()]

    cur = conn.execute(
        """
        SELECT corp_code, bsns_year, status, message, fetched_at
        FROM ae00024_cache
        ORDER BY bsns_year DESC, corp_code
        LIMIT 5000
        """
    )
    ae24 = [dict(r) for r in cur.fetchall()]

    payload = {
        "generatedAt": now,
        "filings": filings[:8000],
        "summaryByYear": summary_by_year,
        "kamItemsSample": kam_rows,
        "ae00024Sample": ae24,
    }
    (out_dir / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
