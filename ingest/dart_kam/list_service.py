"""Fetch the OPENDART disclosure list (``list.json``) with date chunking and pagination.

OPENDART ``list.json`` 의 제약:

- ``corp_code`` 없이 호출하면 ``bgn_de``~``end_de`` 범위가 **최대 약 3개월** 로 제한.
  → ``settings.list_chunk_days`` (기본 90일) 단위로 쪼개서 순차 호출.
- 페이지당 100건, 페이지네이션은 ``total_page`` 응답을 보고 진행.

이 모듈은 위 모든 분할/페이지네이션을 처리하고 결과를 ``filings`` 테이블에 업서트한다.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Iterator

from dart_kam.config import Settings
from dart_kam.dart_client import DartClient
from dart_kam.progress_util import progress_print
from dart_kam.repository import upsert_filing


_OPENDART_OK = "000"
_PAGE_COUNT = "100"


def _chunk_ranges(start: date, end: date, max_days: int) -> Iterator[tuple[date, date]]:
    """``[start, end]`` 를 최대 ``max_days`` 일짜리 inclusive 청크로 분할."""
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=max_days - 1))
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def _build_list_params(
    settings: Settings,
    *,
    bgn: date,
    end: date,
    page: int,
    detail_ty: str,
) -> dict[str, str]:
    params: dict[str, str] = {
        "bgn_de": bgn.strftime("%Y%m%d"),
        "end_de": end.strftime("%Y%m%d"),
        "page_no": str(page),
        "page_count": _PAGE_COUNT,
        "sort": "date",
        "sort_mth": "desc",
        "pblntf_ty": settings.pblntf_ty,
        "pblntf_detail_ty": detail_ty,
        "last_reprt_at": settings.last_reprt_at,
    }
    if settings.corp_cls:
        params["corp_cls"] = settings.corp_cls
    return params


def _upsert_items(
    conn: sqlite3.Connection,
    items: Iterable[dict[str, object]],
    *,
    pblntf_ty: str,
    detail_ty: str,
    fetched_at: str,
) -> int:
    """list.json 한 페이지의 ``items`` 를 업서트하고 반영 건수를 반환."""
    count = 0
    for raw in items:
        rcept_no = str(raw.get("rcept_no") or "").strip()
        corp_code = str(raw.get("corp_code") or "").strip()
        if not rcept_no or not corp_code:
            continue
        upsert_filing(
            conn,
            rcept_no=rcept_no,
            corp_code=corp_code,
            item=raw,
            pblntf_ty=pblntf_ty,
            pblntf_detail_ty=detail_ty,
            fetched_at=fetched_at,
        )
        count += 1
    return count


def _ingest_one_chunk(
    client: DartClient,
    settings: Settings,
    conn: sqlite3.Connection,
    *,
    detail_ty: str,
    bgn: date,
    end: date,
    detail_idx: tuple[int, int],
    chunk_idx: tuple[int, int],
    inserted_so_far: int,
    now: str,
    progress: bool,
) -> int:
    """단일 (detail_ty, chunk) 조합을 페이지네이션 끝까지 처리, 반영 건수 반환."""
    di, n_ty = detail_idx
    ci, n_chunks = chunk_idx
    inserted = 0
    page = 1
    total_page = 1
    pages_known = False

    while True:
        params = _build_list_params(
            settings, bgn=bgn, end=end, page=page, detail_ty=detail_ty
        )
        if pages_known:
            rem_pages = max(0, total_page - page)
            rem_label = f"{rem_pages}페이지"
            total_disp = str(total_page)
        else:
            rem_label = "첫 응답 후 표시"
            total_disp = "?"

        progress_print(
            f"list.json 조회 중 - 공시상세 {detail_ty} ({di}/{n_ty}) · "
            f"기간 {bgn:%Y%m%d}~{end:%Y%m%d} ({ci}/{n_chunks}구간) · "
            f"페이지 {page}/{total_disp} (이 구간 남은 페이지 약 {rem_label}) · "
            f"누적 반영 {inserted_so_far + inserted}건",
            enabled=progress,
        )
        data = client.get_json("list.json", params)
        status = str(data.get("status", ""))
        if status != _OPENDART_OK:
            progress_print(
                f"list.json 조회 종료 - 상태코드 {status} (데이터 없음/오류) · "
                f"누적 반영 {inserted_so_far + inserted}건",
                enabled=progress,
            )
            return inserted

        items = data.get("list") or []
        total_page = max(1, int(data.get("total_page") or 1))
        pages_known = True
        if not items:
            progress_print(
                f"list.json 조회 성공 - 반환 목록 0건으로 이 구간 종료 · "
                f"누적 반영 {inserted_so_far + inserted}건",
                enabled=progress,
            )
            return inserted

        page_inserted = _upsert_items(
            conn,
            items,
            pblntf_ty=settings.pblntf_ty,
            detail_ty=detail_ty,
            fetched_at=now,
        )
        inserted += page_inserted
        progress_print(
            f"조회 성공 - 이번 페이지 {page_inserted}건 반영 · "
            f"누적 {inserted_so_far + inserted}건 · "
            f"동일 구간 전체 {total_page}페이지 중 {page}페이지까지 완료",
            enabled=progress,
        )
        if page >= total_page:
            progress_print(
                f"기간 {bgn:%Y%m%d}~{end:%Y%m%d} · 상세 {detail_ty} - "
                f"전체 {total_page}페이지 수집 완료",
                enabled=progress,
            )
            return inserted
        page += 1


def ingest_filings(
    client: DartClient,
    settings: Settings,
    conn: sqlite3.Connection,
    start: date | None = None,
    end: date | None = None,
    *,
    progress: bool = True,
) -> int:
    """설정된 공시상세 코드들에 대해 ``filings`` 를 업서트하고 누적 반영 건수를 반환.

    :param start: ``None`` 이면 :meth:`Settings.default_start_date`.
    :param end: ``None`` 이면 :meth:`Settings.default_end_date`.
    """
    start = start or settings.default_start_date()
    end = end or settings.default_end_date()
    detail_types = tuple(settings.pblntf_detail_types)
    chunks = list(_chunk_ranges(start, end, settings.list_chunk_days))
    now = datetime.now(timezone.utc).isoformat()

    inserted_total = 0
    n_ty = len(detail_types)
    n_chunks = len(chunks)
    for di, detail_ty in enumerate(detail_types, start=1):
        for ci, (bgn, chunk_end) in enumerate(chunks, start=1):
            inserted_total += _ingest_one_chunk(
                client,
                settings,
                conn,
                detail_ty=detail_ty,
                bgn=bgn,
                end=chunk_end,
                detail_idx=(di, n_ty),
                chunk_idx=(ci, n_chunks),
                inserted_so_far=inserted_total,
                now=now,
                progress=progress,
            )
        conn.commit()

    progress_print(
        f"list 수집 마무리 - 총 반영(누적 카운트) 약 {inserted_total}건",
        enabled=progress,
    )
    return inserted_total
