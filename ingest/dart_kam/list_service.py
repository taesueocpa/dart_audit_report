"""Fetch disclosure list (list.json) with date chunking and pagination."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from dart_kam.config import Settings
from dart_kam.dart_client import DartClient
from dart_kam.progress_util import progress_print


def _chunk_ranges(start: date, end: date, max_days: int) -> Iterable[tuple[date, date]]:
    cur = start
    while cur <= end:
        chunk_end = min(end, cur + timedelta(days=max_days - 1))
        yield cur, chunk_end
        cur = chunk_end + timedelta(days=1)


def ingest_filings(
    client: DartClient,
    settings: Settings,
    conn,
    start: date | None = None,
    end: date | None = None,
    *,
    progress: bool = True,
) -> int:
    """Upsert filings for configured disclosure types. Returns inserted/updated row count (approx)."""
    start = start or settings.default_start_date()
    end = end or settings.default_end_date()
    inserted = 0
    now = datetime.now(timezone.utc).isoformat()

    detail_types = tuple(settings.pblntf_detail_types)
    chunks = list(_chunk_ranges(start, end, settings.list_chunk_days))
    n_ty = len(detail_types)
    n_chunks = len(chunks)

    for di, detail_ty in enumerate(detail_types, start=1):
        for ci, (bgn, chunk_end) in enumerate(chunks, start=1):
            page = 1
            total_page = 1
            pages_known = False
            while True:
                params: dict[str, str] = {
                    "bgn_de": bgn.strftime("%Y%m%d"),
                    "end_de": chunk_end.strftime("%Y%m%d"),
                    "page_no": str(page),
                    "page_count": "100",
                    "sort": "date",
                    "sort_mth": "desc",
                    "pblntf_ty": settings.pblntf_ty,
                    "pblntf_detail_ty": detail_ty,
                    "last_reprt_at": settings.last_reprt_at,
                }
                if settings.corp_cls:
                    params["corp_cls"] = settings.corp_cls

                tp_disp = str(total_page) if pages_known else "?"
                if pages_known:
                    rem_pages = max(0, total_page - page)
                    rem_s = f"{rem_pages}페이지"
                else:
                    rem_s = "첫 응답 후 표시"

                progress_print(
                    f"list.json 조회 중 — 공시상세 {detail_ty} ({di}/{n_ty}) · "
                    f"기간 {bgn.strftime('%Y%m%d')}~{chunk_end.strftime('%Y%m%d')} ({ci}/{n_chunks}구간) · "
                    f"페이지 {page}/{tp_disp} (이 구간 남은 페이지 약 {rem_s}) · 누적 반영 {inserted}건",
                    enabled=progress,
                )
                data = client.get_json("list.json", params)
                st = str(data.get("status", ""))
                if st != "000":
                    progress_print(
                        f"list.json 조회 종료 — 상태코드 {st} (데이터 없음 또는 오류로 이 구간 페이지네이션 중단) · "
                        f"누적 반영 {inserted}건",
                        enabled=progress,
                    )
                    break
                items = data.get("list") or []
                total_page = max(1, int(data.get("total_page") or 1))
                pages_known = True
                if not items:
                    progress_print(
                        f"list.json 조회 성공 — 반환 목록 0건으로 이 구간 종료 · 누적 반영 {inserted}건",
                        enabled=progress,
                    )
                    break
                page_inserted = 0
                for it in items:
                    rcept_no = str(it.get("rcept_no") or "").strip()
                    corp_code = str(it.get("corp_code") or "").strip()
                    if not rcept_no or not corp_code:
                        continue
                    conn.execute(
                        """
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
                        """,
                        (
                            rcept_no,
                            corp_code,
                            it.get("corp_name"),
                            it.get("stock_code"),
                            it.get("report_nm"),
                            str(it.get("rcept_dt") or ""),
                            it.get("corp_cls"),
                            settings.pblntf_ty,
                            detail_ty,
                            it.get("flr_nm"),
                            it.get("rm"),
                            now,
                        ),
                    )
                    inserted += 1
                    page_inserted += 1
                progress_print(
                    f"조회 성공 — 이번 페이지 {page_inserted}건 반영 · 누적 {inserted}건 · "
                    f"동일 구간 전체 {total_page}페이지 중 {page}페이지까지 완료",
                    enabled=progress,
                )
                if page >= total_page:
                    progress_print(
                        f"기간 {bgn.strftime('%Y%m%d')}~{chunk_end.strftime('%Y%m%d')} · 상세 {detail_ty} — "
                        f"전체 {total_page}페이지 수집 완료",
                        enabled=progress,
                    )
                    break
                page += 1
        conn.commit()
    progress_print(f"list 수집 마무리 — 총 반영(누적 카운트) 약 {inserted}건", enabled=progress)
    return inserted
