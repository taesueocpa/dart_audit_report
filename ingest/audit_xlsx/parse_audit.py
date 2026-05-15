"""매핑 행 → (구조화 API + 감사보고서제출 첨부 본문) → XLSX 행 리스트.

각 회사마다 0건~N건의 행이 나올 수 있다 (F001 + F002 둘 다 제출하면 2행).

성능:
- OPENDART API 호출 사이 sleep ``_REQUEST_SLEEP`` 적용 (회사당 2~3회).
- viewer.do 본문 fetch 는 ``_VIEWER_WORKERS`` 개의 ThreadPoolExecutor 로 병렬화.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable

from opendartreader import OpenDartReader

try:
    from tqdm import tqdm  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    tqdm = None  # type: ignore[assignment]

from audit_xlsx.extractors import (
    extract_cpa_partner,
    extract_kam_full_block,
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


_REQUEST_SLEEP = 0.06  # OPENDART API (report + list) 호출 사이
_VIEWER_WORKERS = 4    # main.do/viewer.do GET 병렬도


def _dedupe_amended(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 (corp_code, report_kind) 그룹에서 정정본 우선 + 최신 1건만."""
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


# 5KB 이하 캐시는 표지/목차만 받은 케이스로 간주하고 무시 — 새 multi-page fetch 로 재시도.
_MIN_USABLE_CACHE_BYTES = 5_000


def _fetch_attachment_text(
    parent_rcept_no: str, att: Attachment, save_raw_dir: Path | None
) -> str:
    """디스크 캐시 우선, 너무 짧은 캐시는 무시하고 viewer fetch 재시도."""
    cache: Path | None = None
    if save_raw_dir is not None:
        cache = save_raw_dir / f"{parent_rcept_no}_{att.dcm_no}.html"
    if cache is not None and cache.exists() and cache.stat().st_size >= _MIN_USABLE_CACHE_BYTES:
        return cache.read_text(encoding="utf-8")
    raw_text = fetch_attachment_body(att.main_url)
    if cache is not None and raw_text:
        cache.write_text(raw_text, encoding="utf-8")
    return raw_text


def _build_row(
    op: AuditOpinion,
    stock: dict[str, str],
    parent_rcept_no: str,
    att: Attachment,
    raw_text: str,
) -> dict[str, Any]:
    """raw 본문 → 슬라이스/CPA/other 추출 → row dict."""
    flat_text = html_to_text(raw_text)
    body = extract_standalone_audit_report_body(flat_text)
    cpa = extract_cpa_partner(body or flat_text)
    other = extract_other_matters(body or flat_text)
    # KAM: 본문에서 추출한 전체 문단 우선, 없으면 OPENDART API 의 core_adt_matter.
    kam_body = extract_kam_full_block(body or flat_text)
    kam_final = kam_body if kam_body else op.core_adt_matter
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
        "core_adt_matter": kam_final,
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
    pool: ThreadPoolExecutor | None = None,
) -> list[dict[str, Any]]:
    """매핑 1행 → XLSX 행 리스트 (회사당 N개)."""
    op = fetch_audit_opinion(
        dart, corp_code=stock["corp_code"], bsns_year=bsns_year, reprt_code=reprt_code
    )
    if op is None:
        return [_meta_row(stock, None, skip_reason="no_audit_opinion_data")]

    if not fetch_body:
        return [_meta_row(stock, op, skip_reason="no_body_requested")]

    # 1) 감사보고서제출 공시 + 첨부 메타 수집 (직렬, 가벼운 OPENDART/attach_docs API)
    disclosures = list_audit_disclosures(
        settings.api_key, corp_code=stock["corp_code"], start=start, end=end
    )
    pending: list[tuple[str, Attachment]] = []
    seen: set[tuple[str, str]] = set()
    for d in disclosures:
        for att in list_audit_attachments(dart, d.rcept_no):
            key = (att.parent_rcept_no, att.dcm_no)
            if key in seen:
                continue
            seen.add(key)
            pending.append((d.rcept_no, att))

    # Fallback: 감사보고서제출 공시가 없거나 첨부가 없으면 사업보고서 첨부.
    if not pending and op.rcept_no:
        for att in list_audit_attachments(dart, op.rcept_no):
            key = (att.parent_rcept_no, att.dcm_no)
            if key in seen:
                continue
            seen.add(key)
            pending.append((op.rcept_no, att))

    if not pending:
        return [_meta_row(stock, op, skip_reason="no_attachments")]

    if save_raw_dir is not None:
        save_raw_dir.mkdir(parents=True, exist_ok=True)

    # 2) viewer fetch 병렬 (worker pool 재사용)
    raw_texts: list[str]
    if pool is not None:
        raw_texts = list(
            pool.map(
                lambda x: _fetch_attachment_text(x[0], x[1], save_raw_dir), pending
            )
        )
    else:
        raw_texts = [_fetch_attachment_text(p, a, save_raw_dir) for p, a in pending]

    # 3) CPU-bound 추출 (직렬)
    rows = [_build_row(op, stock, parent, att, raw) for (parent, att), raw in zip(pending, raw_texts)]
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

    use_bar = progress and tqdm is not None
    iterator = (
        tqdm(items, desc="회사", unit="개", dynamic_ncols=True, mininterval=0.5)
        if use_bar
        else items
    )

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=_VIEWER_WORKERS) as pool:
        for i, stock in enumerate(iterator, 1):
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
                    pool=pool,
                )
            except Exception as e:  # noqa: BLE001
                r = _meta_row(stock, None, skip_reason="exception")
                r["parse_error"] = str(e)
                rows = [r]
            all_rows.extend(rows)

            if use_bar:
                kinds = "+".join(sorted({(r.get("report_kind") or "—") for r in rows}))
                iterator.set_postfix_str(  # type: ignore[union-attr]
                    f"{stock.get('corp_name','')[:8]} ×{len(rows)} {kinds} acc={len(all_rows)}",
                    refresh=False,
                )
            elif progress:
                kinds = "/".join(sorted({r.get("report_kind") or "—" for r in rows}))
                body_lens = ",".join(str(r.get("audit_report_body_length") or 0) for r in rows)
                print(
                    f"[{i:>4}/{len(items)}] {stock.get('corp_name',''):<14} "
                    f"({stock.get('stock_code','')}) · 행수={len(rows)} · "
                    f"종류={kinds} · 본문={body_lens}자",
                    flush=True,
                )
            # 매 50건마다 별도 한 줄로 진행 마일스톤 출력 (모니터 친화적, 줄 단위)
            if progress and i % 50 == 0:
                el = time.time() - t0
                rate = el / i
                remaining = rate * (len(items) - i)
                print(
                    f"[milestone] {i}/{len(items)} ({100*i/len(items):.1f}%) · "
                    f"acc {len(all_rows)} rows · {rate:.2f}s/co · "
                    f"elapsed {el/60:.1f}min · ETA {remaining/60:.1f}min",
                    flush=True,
                )
            time.sleep(_REQUEST_SLEEP)

    elapsed = time.time() - t0
    if progress:
        print(
            f"\n[parse_many] elapsed {elapsed:.1f}s ({elapsed/max(len(items),1):.2f}s/회사) · "
            f"총 {len(all_rows)}행",
            flush=True,
        )
    return all_rows
