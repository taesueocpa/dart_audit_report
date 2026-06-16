"""매핑 행(회사 1건) → (구조화 API + 감사보고서제출 첨부 본문) → XLSX 행 리스트.

각 회사마다 0건~N건의 행이 나올 수 있다 — 보통 F001 (감사보고서) + F002
(연결감사보고서) 둘 다 제출하면 2행. 같은 ``(corp_code, report_kind)`` 그룹에
정정본이 있으면 정정본을 우선 채택하고 최신 1건만 남긴다.

성능
~~~~

* OPENDART API 호출 사이 ``_REQUEST_SLEEP`` (rate-limit 보호).
* viewer.do 본문 fetch 는 ``_VIEWER_WORKERS`` 개의 ``ThreadPoolExecutor`` 로 병렬화.
* 디스크 캐시 (``data/raw_audit/{parent}_{dcm}.html``) hit 시 OPENDART 미호출.
"""
from __future__ import annotations

import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Iterable

from opendartreader import OpenDartReader

try:
    from tqdm import tqdm  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    tqdm = None  # type: ignore[assignment]

from audit_xlsx.extractors import (
    extract_audit_body_html,
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

# ---------------------------------------------------------------------------
# 튜닝 상수
# ---------------------------------------------------------------------------

_REQUEST_SLEEP = 0.06          # 회사 처리 사이 sleep (OPENDART rate-limit 보호)
_VIEWER_WORKERS = 4            # main.do/viewer.do GET 병렬도
_MIN_USABLE_CACHE_BYTES = 5_000  # 이 크기 미만 캐시는 표지만 받은 것으로 간주 → 재fetch
_MILESTONE_EVERY = 50          # 진행 마일스톤 출력 주기

# ---------------------------------------------------------------------------
# XLSX 행 dict 빌더
# ---------------------------------------------------------------------------
#
# `_meta_row` (API 데이터 없음/첨부 없음 케이스) 와 `_build_row` (본문 추출
# 성공 케이스) 가 동일한 컬럼 키 집합을 채워야 한다. 공통 부분을 `_base_row`
# 로 추출해서 누락 컬럼이 없도록 보장.


def _base_row(stock: dict[str, str], op: AuditOpinion | None) -> dict[str, Any]:
    """모든 행 dict 의 공통 필드 — 매핑 메타 + API 구조화 4필드."""
    return {
        "stock_code": stock.get("stock_code", ""),
        "corp_code": (op.corp_code if op else stock.get("corp_code", "")),
        "corp_name": (op.corp_name if op else stock.get("corp_name", "")),
        "market": stock.get("market", ""),
        "biz_report_rcept_no": op.rcept_no if op else "",
        "stlm_dt": op.stlm_dt if op else "",
        "bsns_year": op.bsns_year if op else "",
        "adt_opinion": op.adt_opinion if op else "",
        "adtor": op.adtor if op else "",
        "core_adt_matter": op.core_adt_matter if op else "",
        "emphs_matter": op.emphs_matter if op else "",
    }


def _meta_row(
    stock: dict[str, str], op: AuditOpinion | None, *, skip_reason: str
) -> dict[str, Any]:
    """API 데이터 없거나 첨부 0건일 때의 행 — 구조화만, 본문 없음."""
    return {
        **_base_row(stock, op),
        "report_kind": "",
        "attach_title": "",
        "parent_rcept_no": "",
        "dcm_no": "",
        "cpa_partner_name": "",
        "other_matters": "",
        "audit_report_body": "",
        "audit_report_body_html": "",
        # KAM 분리 키 — kam_api 는 OPENDART 원천, kam_body_full 은 본문 ▶ API.
        "kam_api": op.core_adt_matter if op else "",
        "kam_body_full": op.core_adt_matter if op else "",
        "raw_text_length": 0,
        "flat_text_length": 0,
        "audit_report_body_length": 0,
        "skip_reason": skip_reason,
        "parse_error": None,
    }


def _build_row(
    op: AuditOpinion,
    stock: dict[str, str],
    parent_rcept_no: str,
    att: Attachment,
    raw_text: str,
) -> dict[str, Any]:
    """raw HTML 본문 → 추출 결과로 채운 XLSX 행 dict.

    KAM 은 본문 추출본 우선, 없으면 API ``core_adt_matter`` fallback.
    """
    flat_text = html_to_text(raw_text)
    body = extract_standalone_audit_report_body(flat_text)
    work = body or flat_text  # 본문이 안 잡혔으면 평문 전체에서 부가 추출 시도

    cpa = extract_cpa_partner(work) or ""
    other = extract_other_matters(work) or ""
    kam_body = extract_kam_full_block(work)
    kam_final = kam_body if kam_body else op.core_adt_matter

    return {
        **_base_row(stock, op),
        "report_kind": att.report_kind,
        "attach_title": att.title,
        "parent_rcept_no": parent_rcept_no,
        "dcm_no": att.dcm_no,
        "core_adt_matter": kam_final,  # _base_row 의 값을 본문 우선으로 덮어씀
        "cpa_partner_name": cpa,
        "other_matters": other,
        "audit_report_body": body or "",
        "audit_report_body_html": extract_audit_body_html(raw_text) or "",
        # KAM 분리 키 — v4 스키마용: kam_api 는 OPENDART 원천 그대로,
        # kam_body_full 은 본문 추출 ▶ API fallback (core_adt_matter 와 동일 규칙).
        "kam_api": op.core_adt_matter,
        "kam_body_full": kam_final,
        "raw_text_length": len(raw_text),
        "flat_text_length": len(flat_text),
        "audit_report_body_length": len(body or ""),
        "skip_reason": "" if body else "no_body_match",
        "parse_error": None,
    }


# ---------------------------------------------------------------------------
# 정정공시 dedupe
# ---------------------------------------------------------------------------


def _dedupe_amended(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 (corp_code, report_kind) 그룹에서 정정본 우선 + 최신 1건만.

    - 그룹 내 첨부 제목에 "정정" 이 있는 행이 있으면 그것만 후보
    - 후보 중 ``parent_rcept_no`` 가 큰 것 (시간순 후순위 = 최신)
    """
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


# ---------------------------------------------------------------------------
# 디스크 캐시 + viewer fetch
# ---------------------------------------------------------------------------


def _fetch_attachment_text(
    parent_rcept_no: str, att: Attachment, save_raw_dir: Path | None
) -> str:
    """캐시 우선, 너무 짧은 캐시(<5KB)는 무시하고 viewer 재fetch."""
    cache: Path | None = None
    if save_raw_dir is not None:
        cache = save_raw_dir / f"{parent_rcept_no}_{att.dcm_no}.html"
    if cache and cache.exists() and cache.stat().st_size >= _MIN_USABLE_CACHE_BYTES:
        return cache.read_text(encoding="utf-8")
    raw_text = fetch_attachment_body(att.main_url)
    if cache is not None and raw_text:
        cache.write_text(raw_text, encoding="utf-8")
    return raw_text


# ---------------------------------------------------------------------------
# 회사 1건 처리
# ---------------------------------------------------------------------------


def _collect_pending_attachments(
    dart: OpenDartReader,
    settings: Settings,
    stock: dict[str, str],
    op: AuditOpinion,
    *,
    start: str,
    end: str,
) -> list[tuple[str, Attachment]]:
    """회사 1건의 처리 대상 첨부 목록 (parent_rcept_no, Attachment).

    1차: "감사보고서제출" 공시들의 첨부.
    2차 fallback: 첨부가 없으면 사업보고서 자체의 첨부 (op.rcept_no).
    중복 (parent, dcm) 키는 제거.
    """
    disclosures = list_audit_disclosures(
        settings.api_key, corp_code=stock["corp_code"], start=start, end=end
    )

    pending: list[tuple[str, Attachment]] = []
    seen: set[tuple[str, str]] = set()

    def _add_from(parent: str) -> None:
        for att in list_audit_attachments(dart, parent):
            key = (att.parent_rcept_no, att.dcm_no)
            if key in seen:
                continue
            seen.add(key)
            pending.append((parent, att))

    for d in disclosures:
        _add_from(d.rcept_no)
    if not pending and op.rcept_no:
        _add_from(op.rcept_no)
    return pending


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
    """매핑 1행 → XLSX 행 리스트 (회사당 N개, 보통 1~2).

    흐름: 회계감사 API → 첨부 메타 수집 (직렬) → viewer fetch (병렬) →
    본문 슬라이스/CPA/KAM/기타 추출 (직렬) → 정정공시 dedupe.
    """
    op = fetch_audit_opinion(
        dart, corp_code=stock["corp_code"], bsns_year=bsns_year, reprt_code=reprt_code
    )
    if op is None:
        return [_meta_row(stock, None, skip_reason="no_audit_opinion_data")]
    if not fetch_body:
        return [_meta_row(stock, op, skip_reason="no_body_requested")]

    pending = _collect_pending_attachments(
        dart, settings, stock, op, start=start, end=end
    )
    if not pending:
        return [_meta_row(stock, op, skip_reason="no_attachments")]

    if save_raw_dir is not None:
        save_raw_dir.mkdir(parents=True, exist_ok=True)

    # viewer fetch — 가능하면 worker pool 재사용
    fetch_one = partial(_fetch_attachment_text_tuple, save_raw_dir=save_raw_dir)
    raw_texts = list(pool.map(fetch_one, pending)) if pool else [fetch_one(p) for p in pending]

    rows = [
        _build_row(op, stock, parent, att, raw)
        for (parent, att), raw in zip(pending, raw_texts)
    ]
    return _dedupe_amended(rows)


def _fetch_attachment_text_tuple(
    pair: tuple[str, Attachment], *, save_raw_dir: Path | None
) -> str:
    """ThreadPoolExecutor.map 용 — 튜플 unpack."""
    parent, att = pair
    return _fetch_attachment_text(parent, att, save_raw_dir)


# ---------------------------------------------------------------------------
# 배치 처리 + 진행 출력
# ---------------------------------------------------------------------------


@dataclass
class _ProgressReporter:
    """tqdm bar + 50건마다 milestone 출력 양쪽을 캡슐화."""

    total: int
    use_bar: bool
    bar: Any = None  # tqdm 인스턴스 또는 None
    t0: float = 0.0

    def start(self) -> None:
        self.t0 = time.time()

    def update(self, idx: int, stock: dict[str, str], rows: list[dict[str, Any]],
               acc_count: int) -> None:
        if self.use_bar and self.bar is not None:
            kinds = "+".join(sorted({(r.get("report_kind") or "—") for r in rows}))
            self.bar.set_postfix_str(
                f"{stock.get('corp_name','')[:8]} ×{len(rows)} {kinds} acc={acc_count}",
                refresh=False,
            )
        else:
            kinds = "/".join(sorted({r.get("report_kind") or "—" for r in rows}))
            body_lens = ",".join(str(r.get("audit_report_body_length") or 0) for r in rows)
            print(
                f"[{idx:>4}/{self.total}] {stock.get('corp_name',''):<14} "
                f"({stock.get('stock_code','')}) · 행수={len(rows)} · "
                f"종류={kinds} · 본문={body_lens}자",
                flush=True,
            )

    def milestone(self, idx: int, acc_count: int) -> None:
        """매 N건마다 줄 단위 마일스톤 (모니터링 친화적)."""
        if idx % _MILESTONE_EVERY != 0:
            return
        elapsed = time.time() - self.t0
        rate = elapsed / idx
        remaining = rate * (self.total - idx)
        print(
            f"[milestone] {idx}/{self.total} ({100*idx/self.total:.1f}%) · "
            f"acc {acc_count} rows · {rate:.2f}s/co · "
            f"elapsed {elapsed/60:.1f}min · ETA {remaining/60:.1f}min",
            flush=True,
        )

    def finish(self, total_rows: int) -> None:
        elapsed = time.time() - self.t0
        per_co = elapsed / max(self.total, 1)
        print(
            f"\n[parse_many] elapsed {elapsed:.1f}s ({per_co:.2f}s/회사) · "
            f"총 {total_rows}행",
            flush=True,
        )


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
    """배치 처리 — 매핑 회사들을 순회하며 행을 모은다."""
    settings.require_key()
    dart = OpenDartReader(settings.api_key)
    save_dir = settings.raw_dir if save_raw else None
    items = list(stocks)

    reporter = _ProgressReporter(total=len(items), use_bar=progress and tqdm is not None)
    iterator = (
        tqdm(items, desc="회사", unit="개", dynamic_ncols=True, mininterval=0.5)
        if reporter.use_bar
        else items
    )
    reporter.bar = iterator if reporter.use_bar else None

    all_rows: list[dict[str, Any]] = []
    reporter.start()

    with ThreadPoolExecutor(max_workers=_VIEWER_WORKERS) as pool:
        for i, stock in enumerate(iterator, 1):
            rows = _parse_one_safe(
                dart, settings, stock,
                bsns_year=bsns_year, reprt_code=reprt_code,
                start=start, end=end, fetch_body=fetch_body,
                save_raw_dir=save_dir, pool=pool,
            )
            all_rows.extend(rows)
            if progress:
                reporter.update(i, stock, rows, len(all_rows))
                reporter.milestone(i, len(all_rows))
            time.sleep(_REQUEST_SLEEP)

    if progress:
        reporter.finish(len(all_rows))
    return all_rows


def _parse_one_safe(
    dart: OpenDartReader, settings: Settings, stock: dict[str, str], **kwargs: Any,
) -> list[dict[str, Any]]:
    """parse_one 호출 + 예외 발생 시 ``parse_error`` 가 채워진 메타 행 반환."""
    try:
        return parse_one(dart, settings, stock, **kwargs)
    except Exception as e:  # noqa: BLE001 — 단건 실패가 배치 전체를 막지 않도록
        row = _meta_row(stock, None, skip_reason="exception")
        row["parse_error"] = str(e)
        return [row]
