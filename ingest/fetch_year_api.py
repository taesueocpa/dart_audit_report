"""과거 사업연도 감사보고서 — 공식 document.xml API **전용** 수집 → v4 스키마 연도별 XLSX.

viewer 스크래핑 없이 OPENDART 공식 API 만 사용한다:

* ``dart.report(corp, '회계감사', year, '11011')`` → 구조화 4필드(감사인/의견/KAM/강조)
* ``document.xml(사업보고서 접수번호)`` ZIP →
    - 감사보고서 / 연결감사보고서 XML → 본문 평문·KAM본문·CPA·기타사항
    - 사업보고서 본문 XML → 「내부통제에 관한 사항」

API 한계 (viewer 가 아니면 불가 — 과거연도에 비는 항목)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* ``감사보고서 본문(HTML)`` 서식보존본은 viewer 렌더 결과라 비움 (평문만 채움).
* ``첨부 제목``·``감사보고서제출 접수번호``는 attach_docs(viewer) 기반이라 비움.
  (``사업보고서 접수번호``는 report() 응답으로 채움)
* 일부(~8%) 회사는 document.xml ZIP 에 감사보고서 XML 미포함(첨부 별도/정정/6월결산)
  → 본문 없이 구조화+내부통제만.

캐시: (year, corp_code) 단위 JSON → ``data/year_api/{year}_{corp_code}.json``.
한도 초과(020/021) 시 graceful 중단, 재실행 시 캐시로 이어받기.

사용법
~~~~~~
* dry-run:  ``python -m ingest.fetch_year_api --year 2024 --limit 5``
* 전체:     ``python -m ingest.fetch_year_api --year 2024``
  → ``data/audit_reports_y{year}.xlsx`` (병합은 ``merge_years`` 가 담당)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pandas as pd
from opendartreader import OpenDartReader

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from audit_xlsx.extractors import (  # noqa: E402
    extract_cpa_partner,
    extract_internal_control_summary,
    extract_kam_full_block,
    extract_other_matters,
    extract_standalone_audit_report_body,
)
from audit_xlsx.fetch_audit import (  # noqa: E402
    AuditOpinion,
    fetch_audit_opinion,
    html_to_text,
)
from audit_xlsx.settings import load_settings  # noqa: E402
from audit_xlsx.stock_to_corp import load_mapping  # noqa: E402
from fetch_iacm_api import _DOC_NAME, fetch_document_zip  # noqa: E402
from fetch_year import _COL_IC_SUMMARY, _V4_COLUMNS, _truncate_cell  # noqa: E402

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

_REPRT_CODE = "11011"          # 사업보고서
_WORKERS = 4
_PROGRESS_EVERY = 50           # 매 50개사마다 진행상황 보고
_REPORT_THROTTLE = 0.1         # report() API 호출 간 최소 간격(초) — 전역

_report_lock = threading.Lock()
_last_report_ts = 0.0
_QUOTA = threading.Event()  # 한도 초과 시 set → 제출된 잔여 작업을 API 호출 없이 즉시 단락


def _throttle_report() -> None:
    """report() API 전역 최소 간격 (document.xml 은 fetch_document_zip 자체 throttle)."""
    global _last_report_ts
    with _report_lock:
        wait = _REPORT_THROTTLE - (time.monotonic() - _last_report_ts)
        if wait > 0:
            time.sleep(wait)
        _last_report_ts = time.monotonic()


# ---------------------------------------------------------------------------
# document.xml ZIP → (사업보고서 본문, 감사보고서, 연결감사보고서) 평문
# ---------------------------------------------------------------------------


def _split_zip_reports(z) -> tuple[str, str, str]:
    """ZIP 멤버를 DOCUMENT-NAME 으로 분류 → (biz_flat, audit_flat, consol_flat)."""
    biz = audit = consol = ""
    for name in z.namelist():
        try:
            txt = z.read(name).decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001 — 멤버 1개 손상이 전체를 막지 않도록
            continue
        m = _DOC_NAME.search(txt)
        title = (m.group(1).strip() if m else "")
        if "사업보고서" in title and not biz:
            biz = html_to_text(txt)
        elif "감사보고서" in title:
            if ("연결" in title or "결합" in title):
                if not consol:
                    consol = html_to_text(txt)
            elif not audit:
                audit = html_to_text(txt)
    return biz, audit, consol


# ---------------------------------------------------------------------------
# 행 빌더 (v4 스키마, 영문 row key)
# ---------------------------------------------------------------------------


def _body_fields(flat: str) -> dict[str, str]:
    """감사보고서 평문 → 본문/KAM본문/CPA/기타사항."""
    body = extract_standalone_audit_report_body(flat) or ""
    work = body or flat
    return {
        "audit_report_body": body,
        "kam_body_full": extract_kam_full_block(work) or "",
        "cpa_partner_name": extract_cpa_partner(work) or "",
        "other_matters": extract_other_matters(work) or "",
    }


def _make_row(
    stock: dict[str, str], op: AuditOpinion, report_kind: str, flat: str, ic: str
) -> dict[str, Any]:
    """매핑 메타 + report() 구조화 + 본문 추출 → v4 행 dict (영문 key + ic_summary)."""
    bf = (
        _body_fields(flat)
        if flat
        else {"audit_report_body": "", "kam_body_full": "", "cpa_partner_name": "", "other_matters": ""}
    )
    return {
        "stock_code": stock.get("stock_code", ""),
        "corp_code": op.corp_code or stock.get("corp_code", ""),
        "corp_name": op.corp_name or stock.get("corp_name", ""),
        "market": stock.get("market", ""),
        "report_kind": report_kind,
        "attach_title": "",                       # viewer 전용 — API 불가
        "parent_rcept_no": "",                    # viewer 전용 — API 불가
        "biz_report_rcept_no": op.rcept_no,
        "stlm_dt": op.stlm_dt,
        "bsns_year": op.bsns_year,
        "adt_opinion": op.adt_opinion,
        "adtor": op.adtor,
        "cpa_partner_name": bf["cpa_partner_name"],
        "kam_api": op.core_adt_matter,
        "kam_body_full": bf["kam_body_full"] or op.core_adt_matter,  # 본문 ▶ API fallback
        "emphs_matter": op.emphs_matter,
        "other_matters": bf["other_matters"],
        "audit_report_body": bf["audit_report_body"],
        "audit_report_body_html": "",             # viewer 렌더 전용 — API 불가
        "ic_summary": ic,
    }


# ---------------------------------------------------------------------------
# 회사 1건 처리 (report API + document.xml API)
# ---------------------------------------------------------------------------


def _process_company(
    api_key: str, dart: OpenDartReader, stock: dict[str, str], year: int
) -> list[dict[str, Any]] | None:
    """반환: 행 리스트(데이터 있음) 또는 ``None``(해당 연도 데이터 없음).

    한도 초과로 ``_QUOTA`` 가 set 되면 API 호출 없이 즉시 RuntimeError — 캐시도
    남기지 않아 재실행 시 이어받는다.
    """
    if _QUOTA.is_set():
        raise RuntimeError("quota reached — skipped")
    _throttle_report()
    op = fetch_audit_opinion(
        dart, corp_code=stock["corp_code"], bsns_year=str(year), reprt_code=_REPRT_CODE
    )
    if op is None:
        return None

    biz = audit = consol = ""
    if op.rcept_no:
        z = fetch_document_zip(api_key, op.rcept_no)  # 한도초과 시 RuntimeError → 상위 중단
        if z is not None:
            biz, audit, consol = _split_zip_reports(z)

    ic = (extract_internal_control_summary(biz) or "") if biz else ""

    rows: list[dict[str, Any]] = []
    if audit:
        rows.append(_make_row(stock, op, "감사보고서", audit, ic))
    if consol:
        rows.append(_make_row(stock, op, "연결감사보고서", consol, ic))
    if not rows:  # 본문 없음 — 구조화+내부통제만 1행
        rows.append(_make_row(stock, op, "감사보고서", "", ic))
    return rows


# ---------------------------------------------------------------------------
# 캐시 ((year, corp_code) 단위)
# ---------------------------------------------------------------------------


def _cache_path(cache_dir: Path, year: int, corp_code: str) -> Path:
    return cache_dir / f"{year}_{corp_code}.json"


def _cache_load(cache_dir: Path, year: int, corp_code: str) -> dict | None:
    p = _cache_path(cache_dir, year, corp_code)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 손상 캐시 무시
        return None


def _cache_save(cache_dir: Path, year: int, corp_code: str, payload: dict) -> None:
    _cache_path(cache_dir, year, corp_code).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# 배치
# ---------------------------------------------------------------------------


def _run_batch(
    api_key: str,
    dart: OpenDartReader,
    mapping: list[dict[str, str]],
    year: int,
    cache_dir: Path,
    workers: int,
) -> list[dict[str, Any]]:
    """매핑 회사들을 처리해 v4 행 리스트(영문 key) 반환. 캐시 우선·한도초과 시 중단."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    _QUOTA.clear()
    all_rows: list[dict[str, Any]] = []
    todo: list[dict[str, str]] = []

    for stock in mapping:
        cached = _cache_load(cache_dir, year, stock["corp_code"])
        if cached is not None:
            all_rows.extend(cached.get("rows", []))
        else:
            todo.append(stock)
    print(f"[{year}] 캐시 hit: {len(mapping) - len(todo)} / 신규 수집: {len(todo)}", flush=True)

    total = len(todo)
    t0 = time.time()
    done = with_body = no_data = no_doc = 0
    quota_hit = False

    with ThreadPoolExecutor(max_workers=workers) as pool:
        fut_to_stock = {
            pool.submit(_process_company, api_key, dart, s, year): s for s in todo
        }
        for fut in as_completed(fut_to_stock):
            stock = fut_to_stock[fut]
            cc = stock["corp_code"]
            try:
                rows = fut.result()
            except RuntimeError as e:  # 사용한도 초과 — 잔여 작업 단락 후 이월
                _QUOTA.set()
                if not quota_hit:
                    print(f"[{year}] API 한도 도달, 잔여 작업 단락·이월: {e}", flush=True)
                    quota_hit = True
                continue
            except Exception as e:  # noqa: BLE001 — 단건 실패가 배치를 막지 않도록
                print(f"  err {cc}: {e}", flush=True)
                continue

            if rows is None:  # 해당 연도 데이터 없음 (상장 전 등)
                _cache_save(cache_dir, year, cc, {"rows": []})
                no_data += 1
            else:
                _cache_save(cache_dir, year, cc, {"rows": rows})
                all_rows.extend(rows)
                if any(r.get("audit_report_body") for r in rows):
                    with_body += 1
                else:
                    no_doc += 1

            done += 1
            if done % _PROGRESS_EVERY == 0 or done == total:
                elapsed = time.time() - t0
                rate = elapsed / done
                eta = rate * (total - done)
                print(
                    f"  [{year} {done}/{total}] 본문={with_body} 본문없음={no_doc} "
                    f"데이터없음={no_data} · 누적행={len(all_rows)} · "
                    f"{rate:.2f}s/co · ETA {eta/60:.1f}min",
                    flush=True,
                )
    return all_rows


# ---------------------------------------------------------------------------
# DataFrame 빌드 (v4 20컬럼) + 저장
# ---------------------------------------------------------------------------


def _rows_to_df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(
        {header: [str(r.get(key) or "") for r in rows] for key, header in _V4_COLUMNS}
    )
    df[_COL_IC_SUMMARY] = [str(r.get("ic_summary") or "") for r in rows]
    for col in df.columns:  # openpyxl 셀 32K 한도 보호
        df[col] = df[col].map(_truncate_cell)
    return df


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True, help="사업연도 (예: 2024)")
    parser.add_argument("--limit", type=int, default=None, help="회사 수 제한 (dry-run)")
    parser.add_argument("--dst", type=Path, default=None, help="출력 XLSX")
    parser.add_argument("--workers", type=int, default=_WORKERS)
    args = parser.parse_args(argv)

    settings = load_settings()
    settings.require_key()
    dst = args.dst or (settings.data_dir / f"audit_reports_y{args.year}.xlsx")
    cache_dir = settings.data_dir / "year_api"

    mapping = load_mapping(settings)
    if args.limit is not None:
        mapping = mapping[: args.limit]
    print(
        f"year={args.year} · 회사 {len(mapping)} · workers={args.workers} · "
        f"cache={cache_dir} · dst={dst}",
        flush=True,
    )

    dart = OpenDartReader(settings.api_key)
    rows = _run_batch(settings.api_key, dart, mapping, args.year, cache_dir, args.workers)
    if not rows:
        print("수집된 행이 없습니다.", file=sys.stderr)
        return 1

    df = _rows_to_df(rows)
    df = df.sort_values(["회사명", "보고서 종류"], kind="stable").reset_index(drop=True)

    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".tmp.xlsx")
    df.to_excel(tmp, index=False, engine="openpyxl")
    os.replace(tmp, dst)
    print(f"\nwrote: {dst} ({dst.stat().st_size/1024/1024:.2f} MB, {len(df)}행)", flush=True)

    for col in ("감사의견", "감사보고서 본문 전체", "핵심감사사항(본문)", _COL_IC_SUMMARY):
        n = int((df[col].astype(str).str.strip() != "").sum())
        print(f"  {col}: {n}/{len(df)} ({100*n/max(len(df),1):.1f}%)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
