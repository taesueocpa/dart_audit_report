"""매핑 행 → (구조화 API + 감사보고서제출 첨부 본문) → XLSX 행 리스트.

각 회사마다 0건~N건의 행이 나올 수 있다 (F001 + F002 둘 다 제출하면 2행).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Iterable

from opendartreader import OpenDartReader

from audit_xlsx.extractors import (
    extract_cpa_partner,
    extract_other_matters,
    extract_standalone_audit_report_body,
)
from audit_xlsx.fetch_audit import (
    Attachment,
    AuditOpinion,
    fetch_attachment_body,
    fetch_audit_opinion,
    html_to_text,
    list_audit_attachments,
    list_audit_disclosures,
)
from audit_xlsx.settings import Settings


_REQUEST_SLEEP = 0.12  # OPENDART API 호출 사이
_FETCH_SLEEP = 0.05  # dart.fss.or.kr (viewer) 호출 사이


def _dedupe_amended(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 (corp_code, report_kind) 그룹에서 정정본 우선 + 최신 1건만.

    - 첨부 제목에 "정정" 키워드가 있으면 정정본 → 우선 채택.
    - 같은 등급(정정/원본) 내에선 parent_rcept_no 가 큰 것 (시간순 후순위 = 최신).
    """
    from collections import defaultdict

    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        key = (r.get("corp_code", ""), r.get("report_kind", ""))
        groups[key].append(r)
    out: list[dict[str, Any]] = []
    for items in groups.values():
        if len(items) == 1:
            out.append(items[0])
            continue
        amended = [r for r in items if "정정" in (r.get("attach_title") or "")]
        pool = amended if amended else items
        pool.sort(key=lambda r: r.get("parent_rcept_no", ""), reverse=True)
        out.append(pool[0])
    return out


def _meta_row(stock: dict[str, str], op: AuditOpinion | None, *, skip_reason: str) -> dict[str, Any]:
    """API 데이터 없거나 첨부 0건일 때의 행 (구조화만, 본문 없음)."""
    return {
        "stock_code": stock.get("stock_code", ""),
        "corp_code": (op.corp_code if op else stock.get("corp_code", "")),
        "corp_name": (op.corp_name if op else stock.get("corp_name", "")),
        "market": stock.get("market", ""),
        "report_kind": "",
        "attach_title": "",
        "parent_rcept_no": "",
        "biz_report_rcept_no": (op.rcept_no if op else ""),
        "stlm_dt": (op.stlm_dt if op else ""),
        "bsns_year": (op.bsns_year if op else ""),
        "adt_opinion": (op.adt_opinion if op else ""),
        "adtor": (op.adtor if op else ""),
        "core_adt_matter": (op.core_adt_matter if op else ""),
        "emphs_matter": (op.emphs_matter if op else ""),
        "cpa_partner_name": "",
        "other_matters": "",
        "audit_report_body": "",
        "raw_text_length": 0,
        "flat_text_length": 0,
        "audit_report_body_length": 0,
        "skip_reason": skip_reason,
        "parse_error": None,
    }


def _process_attachment(
    op: AuditOpinion,
    stock: dict[str, str],
    parent_rcept_no: str,
    att: Attachment,
    *,
    save_raw_dir: Path | None,
) -> dict[str, Any]:
    """첨부 1건 → 본문 다운/슬라이스 → XLSX 행 dict."""
    cache: Path | None = None
    if save_raw_dir is not None:
        save_raw_dir.mkdir(parents=True, exist_ok=True)
        cache = save_raw_dir / f"{parent_rcept_no}_{att.dcm_no}.html"

    if cache is not None and cache.exists() and cache.stat().st_size > 0:
        raw_text = cache.read_text(encoding="utf-8")
    else:
        raw_text = fetch_attachment_body(att.main_url)
        if cache is not None and raw_text:
            cache.write_text(raw_text, encoding="utf-8")
        time.sleep(_FETCH_SLEEP)

    flat_text = html_to_text(raw_text)
    body = extract_standalone_audit_report_body(flat_text)
    cpa = extract_cpa_partner(body or flat_text)
    other = extract_other_matters(body or flat_text)
    return {
        "stock_code": stock.get("stock_code", ""),
        "corp_code": op.corp_code or stock.get("corp_code", ""),
        "corp_name": op.corp_name or stock.get("corp_name", ""),
        "market": stock.get("market", ""),
        "report_kind": att.report_kind,
        "attach_title": att.title,
        "parent_rcept_no": parent_rcept_no,
        "biz_report_rcept_no": op.rcept_no,
        "stlm_dt": op.stlm_dt,
        "bsns_year": op.bsns_year,
        "adt_opinion": op.adt_opinion,
        "adtor": op.adtor,
        "core_adt_matter": op.core_adt_matter,
        "emphs_matter": op.emphs_matter,
        "cpa_partner_name": cpa or "",
        "other_matters": other or "",
        "audit_report_body": body or "",
        "raw_text_length": len(raw_text),
        "flat_text_length": len(flat_text),
        "audit_report_body_length": len(body or ""),
        "skip_reason": "" if body else "no_body_match",
        "parse_error": None,
    }


def parse_one(
    dart: OpenDartReader,
    settings: Settings,
    stock: dict[str, str],
    *,
    bsns_year: str = "2025",
    reprt_code: str = "11011",
    start: str = "2026-01-01",
    end: str = "2026-04-30",
    fetch_body: bool = True,
    save_raw_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """매핑 1행 → XLSX 행 리스트 (회사당 N개)."""
    op = fetch_audit_opinion(
        dart, corp_code=stock["corp_code"], bsns_year=bsns_year, reprt_code=reprt_code
    )
    if op is None:
        return [_meta_row(stock, None, skip_reason="no_audit_opinion_data")]

    if not fetch_body:
        return [_meta_row(stock, op, skip_reason="no_body_requested")]

    # 1) 감사보고서제출 공시 목록
    disclosures = list_audit_disclosures(
        settings.api_key, corp_code=stock["corp_code"], start=start, end=end
    )

    rows: list[dict[str, Any]] = []
    seen_dcm: set[tuple[str, str]] = set()  # (parent_rcept, dcm_no) 중복 방지

    for d in disclosures:
        atts = list_audit_attachments(dart, d.rcept_no)
        for att in atts:
            key = (att.parent_rcept_no, att.dcm_no)
            if key in seen_dcm:
                continue
            seen_dcm.add(key)
            rows.append(
                _process_attachment(op, stock, d.rcept_no, att, save_raw_dir=save_raw_dir)
            )

    # 2) Fallback — 감사보고서제출 공시가 없거나 첨부가 안 잡힌 경우 사업보고서 첨부.
    if not rows and op.rcept_no:
        atts = list_audit_attachments(dart, op.rcept_no)
        for att in atts:
            key = (att.parent_rcept_no, att.dcm_no)
            if key in seen_dcm:
                continue
            seen_dcm.add(key)
            rows.append(
                _process_attachment(op, stock, op.rcept_no, att, save_raw_dir=save_raw_dir)
            )

    if not rows:
        return [_meta_row(stock, op, skip_reason="no_attachments")]
    return _dedupe_amended(rows)


def parse_many(
    settings: Settings,
    stocks: Iterable[dict[str, str]],
    *,
    bsns_year: str = "2025",
    reprt_code: str = "11011",
    start: str = "2026-01-01",
    end: str = "2026-04-30",
    fetch_body: bool = True,
    save_raw: bool = False,
    progress: bool = True,
) -> list[dict[str, Any]]:
    settings.require_key()
    dart = OpenDartReader(settings.api_key)
    save_dir = settings.raw_dir if save_raw else None
    items = list(stocks)
    all_rows: list[dict[str, Any]] = []
    for i, stock in enumerate(items, 1):
        try:
            rows = parse_one(
                dart,
                settings,
                stock,
                bsns_year=bsns_year,
                reprt_code=reprt_code,
                start=start,
                end=end,
                fetch_body=fetch_body,
                save_raw_dir=save_dir,
            )
        except Exception as e:  # noqa: BLE001
            r = _meta_row(stock, None, skip_reason="exception")
            r["parse_error"] = str(e)
            rows = [r]
        all_rows.extend(rows)
        if progress:
            kinds = "/".join(sorted({r.get("report_kind") or "—" for r in rows}))
            body_lens = ",".join(str(r.get("audit_report_body_length") or 0) for r in rows)
            print(
                f"[{i:>3}/{len(items)}] {stock.get('corp_name',''):<14} "
                f"({stock.get('stock_code','')}) · 행수={len(rows)} · "
                f"종류={kinds} · 본문={body_lens}자",
                flush=True,
            )
        time.sleep(_REQUEST_SLEEP)
    return all_rows
