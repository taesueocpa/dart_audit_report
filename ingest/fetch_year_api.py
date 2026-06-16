"""과거 사업연도 감사보고서 수집 → v4 스키마 연도별 XLSX (API 우선 + viewer 폴백).

수집 전략
~~~~~~~~~
회사별로 **공식 API 우선**, 본문을 못 얻으면 **viewer 폴백**(웹 조회)으로 채운다.

1. `dart.report(corp, '회계감사', year, '11011')` → 구조화 4필드(감사인/의견/KAM/강조).
2. `document.xml(사업보고서 접수번호)` ZIP →
   - 감사보고서 / 연결감사보고서 XML(마크업) → 본문 평문·HTML본문·KAM본문·CPA·기타사항
   - 사업보고서 본문 XML → 「내부통제에 관한 사항」
3. document.xml ZIP 에 감사보고서가 없거나(별도 「감사보고서제출」 공시로 제출) 본문
   추출 실패 시 → **viewer 폴백**: `parse_one`(2025 본문 파이프라인) 재사용으로
   attach_docs+viewer.do 에서 본문·HTML·첨부제목·제출번호까지 수집.

`감사보고서 본문(HTML)` 은 (2) 의 경우 document.xml 마크업 슬라이스(서식은 DART CSS
미동봉이라 평문 표 수준), (3) 의 경우 viewer 렌더 HTML.

캐시: (year, corp_code) JSON → `data/year_api/{year}_{corp}.json`. viewer raw HTML 은
`data/raw_audit` 공용 캐시. 한도초과(020/021) 시 단락·이어받기.

사용법
~~~~~~
* dry-run:  ``python -m ingest.fetch_year_api --year 2024 --limit 30``
* 전체:     ``python -m ingest.fetch_year_api --year 2024``
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
    extract_audit_body_html,
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
from audit_xlsx.parse_audit import parse_one  # noqa: E402
from audit_xlsx.settings import Settings, load_settings  # noqa: E402
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
_QUOTA = threading.Event()     # 한도 초과 시 set → 잔여 작업을 API 호출 없이 즉시 단락


def _throttle_report() -> None:
    """report() API 전역 최소 간격 (document.xml 은 fetch_document_zip 자체 throttle)."""
    global _last_report_ts
    with _report_lock:
        wait = _REPORT_THROTTLE - (time.monotonic() - _last_report_ts)
        if wait > 0:
            time.sleep(wait)
        _last_report_ts = time.monotonic()


# ---------------------------------------------------------------------------
# document.xml ZIP → (사업보고서, 감사보고서, 연결감사보고서) RAW 마크업
# ---------------------------------------------------------------------------


def _split_zip_reports(z) -> tuple[str, str, str]:
    """ZIP 멤버를 DOCUMENT-NAME 으로 분류 → (사업보고서, 감사보고서, 연결감사보고서) RAW.

    평문화·HTML 슬라이스는 호출부에서 수행한다 (HTML 본문 컬럼용 마크업 보존).
    """
    biz = audit = consol = ""
    for name in z.namelist():
        try:
            txt = z.read(name).decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001 — 멤버 1개 손상이 전체를 막지 않도록
            continue
        m = _DOC_NAME.search(txt)
        title = (m.group(1).strip() if m else "")
        if "사업보고서" in title and not biz:
            biz = txt
        elif "감사보고서" in title:
            if ("연결" in title or "결합" in title):
                if not consol:
                    consol = txt
            elif not audit:
                audit = txt
    return biz, audit, consol


# ---------------------------------------------------------------------------
# 행 빌더 (v4 스키마, 영문 row key)
# ---------------------------------------------------------------------------


def _body_fields(raw: str) -> dict[str, str]:
    """감사보고서 RAW 마크업 → 본문(평문)·HTML본문·KAM본문·CPA·기타사항."""
    flat = html_to_text(raw) if raw else ""
    body = extract_standalone_audit_report_body(flat) or ""
    work = body or flat
    return {
        "audit_report_body": body,
        "audit_report_body_html": (extract_audit_body_html(raw) or "") if raw else "",
        "kam_body_full": extract_kam_full_block(work) or "",
        "cpa_partner_name": extract_cpa_partner(work) or "",
        "other_matters": extract_other_matters(work) or "",
    }


def _make_row(
    stock: dict[str, str], op: AuditOpinion, report_kind: str, raw: str, ic: str
) -> dict[str, Any]:
    """매핑 메타 + report() 구조화 + document.xml 마크업 추출 → v4 행 dict."""
    bf = _body_fields(raw)
    return {
        "stock_code": stock.get("stock_code", ""),
        "corp_code": op.corp_code or stock.get("corp_code", ""),
        "corp_name": op.corp_name or stock.get("corp_name", ""),
        "market": stock.get("market", ""),
        "report_kind": report_kind,
        "attach_title": "",                       # API 경로 — 첨부 메타 없음
        "parent_rcept_no": "",
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
        "audit_report_body_html": bf["audit_report_body_html"],
        "ic_summary": ic,
    }


# ---------------------------------------------------------------------------
# viewer 폴백 — document.xml 에 감사보고서가 없을 때 (parse_one 재사용)
# ---------------------------------------------------------------------------


def _viewer_fallback(
    dart: OpenDartReader, settings: Settings, stock: dict[str, str],
    year: int, start: str, end: str, ic: str,
) -> list[dict[str, Any]]:
    """attach_docs+viewer.do 로 본문 수집(검증된 parse_one 재사용) → v4 키 사상 + 내부통제 주입."""
    rows = parse_one(
        dart, settings, stock,
        bsns_year=str(year), reprt_code=_REPRT_CODE,
        start=start, end=end, fetch_body=True, save_raw_dir=settings.raw_dir,
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        v = {key: r.get(key, "") for key, _ in _V4_COLUMNS}
        v["ic_summary"] = ic
        out.append(v)
    return out


# ---------------------------------------------------------------------------
# 회사 1건 처리 (API 우선 + viewer 폴백)
# ---------------------------------------------------------------------------


def _tag(rows: list[dict[str, Any]], via: str) -> list[dict[str, Any]]:
    for r in rows:
        r["_via"] = via  # 통계용 — _rows_to_df 가 v4 키만 골라 쓰므로 출력엔 안 들어감
    return rows


def _process_company(
    api_key: str, dart: OpenDartReader, settings: Settings,
    stock: dict[str, str], year: int, start: str, end: str,
) -> list[dict[str, Any]] | None:
    """반환: 행 리스트(데이터 있음) 또는 None(해당 연도 데이터 없음). 한도초과 시 RuntimeError."""
    if _QUOTA.is_set():
        raise RuntimeError("quota reached — skipped")
    _throttle_report()
    op = fetch_audit_opinion(
        dart, corp_code=stock["corp_code"], bsns_year=str(year), reprt_code=_REPRT_CODE
    )
    if op is None:
        return None

    biz_raw = audit_raw = consol_raw = ""
    if op.rcept_no:
        z = fetch_document_zip(api_key, op.rcept_no)  # 한도초과 시 RuntimeError
        if z is not None:
            biz_raw, audit_raw, consol_raw = _split_zip_reports(z)

    ic = (extract_internal_control_summary(html_to_text(biz_raw)) or "") if biz_raw else ""

    api_rows: list[dict[str, Any]] = []
    if audit_raw:
        api_rows.append(_make_row(stock, op, "감사보고서", audit_raw, ic))
    if consol_raw:
        api_rows.append(_make_row(stock, op, "연결감사보고서", consol_raw, ic))
    if any(r["audit_report_body"] for r in api_rows):
        return _tag(api_rows, "api")

    # 본문 미확보 → viewer 폴백 (웹 조회)
    vrows = _viewer_fallback(dart, settings, stock, year, start, end, ic)
    if any(r.get("audit_report_body") for r in vrows):
        return _tag(vrows, "viewer")

    return _tag(api_rows or [_make_row(stock, op, "감사보고서", "", ic)], "none")


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
    settings: Settings,
    mapping: list[dict[str, str]],
    year: int,
    start: str,
    end: str,
    cache_dir: Path,
    workers: int,
) -> list[dict[str, Any]]:
    """매핑 회사들을 처리해 v4 행 리스트(영문 key) 반환. 캐시 우선·한도초과 시 단락."""
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
    done = with_body = no_body = no_data = via_viewer = 0
    quota_hit = False

    with ThreadPoolExecutor(max_workers=workers) as pool:
        fut = {
            pool.submit(_process_company, api_key, dart, settings, s, year, start, end): s
            for s in todo
        }
        for f in as_completed(fut):
            cc = fut[f]["corp_code"]
            try:
                rows = f.result()
            except RuntimeError as e:  # 사용한도 초과 — 잔여 작업 단락 후 이월
                _QUOTA.set()
                if not quota_hit:
                    print(f"[{year}] API 한도 도달, 잔여 작업 단락·이월: {e}", flush=True)
                    quota_hit = True
                continue
            except Exception as e:  # noqa: BLE001 — 단건 실패가 배치를 막지 않도록
                print(f"  err {cc}: {e}", flush=True)
                continue

            if rows is None:
                _cache_save(cache_dir, year, cc, {"rows": []})
                no_data += 1
            else:
                _cache_save(cache_dir, year, cc, {"rows": rows})
                all_rows.extend(rows)
                if any(r.get("audit_report_body") for r in rows):
                    with_body += 1
                else:
                    no_body += 1
                if rows and rows[0].get("_via") == "viewer":
                    via_viewer += 1

            done += 1
            if done % _PROGRESS_EVERY == 0 or done == total:
                elapsed = time.time() - t0
                rate = elapsed / done
                eta = rate * (total - done)
                print(
                    f"  [{year} {done}/{total}] 본문={with_body}(viewer폴백 {via_viewer}) "
                    f"본문없음={no_body} 데이터없음={no_data} · 누적행={len(all_rows)} · "
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
    # 감사보고서제출 공시는 사업연도+1 의 1~6월에 제출 (viewer 폴백 list.json 기간)
    start = f"{args.year + 1}-01-01"
    end = f"{args.year + 1}-06-30"

    mapping = load_mapping(settings)
    if args.limit is not None:
        mapping = mapping[: args.limit]
    print(
        f"year={args.year} · 회사 {len(mapping)} · workers={args.workers} · "
        f"viewer기간 {start}~{end} · cache={cache_dir} · dst={dst}",
        flush=True,
    )

    dart = OpenDartReader(settings.api_key)
    rows = _run_batch(settings.api_key, dart, settings, mapping, args.year,
                      start, end, cache_dir, args.workers)
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

    for col in ("감사의견", "감사보고서 본문 전체", "감사보고서 본문(HTML)",
                "핵심감사사항(본문)", _COL_IC_SUMMARY):
        n = int((df[col].astype(str).str.strip() != "").sum())
        print(f"  {col}: {n}/{len(df)} ({100*n/max(len(df),1):.1f}%)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
