"""과거 사업연도 감사보고서 + 내부통제에 관한 사항 수집 → v4 스키마 연도별 XLSX.

기존 연도 파라미터화된 파이프라인(:func:`audit_xlsx.parse_audit.parse_many`)으로
구조화 4필드 + 감사보고서 본문(viewer, ``data/raw_audit`` 캐시)을 모으고,
공식 document.xml API 로 사업보고서 본문 「내부통제에 관한 사항」을 추출해
현재 v4 와 동일한 20컬럼 DataFrame 을 만든다.

내부통제 캐시는 접수번호 기반(``data/iacm_api/rcept_{rcept_no}.json``)이라
연도가 달라도 충돌하지 않고, API 일일한도(20,000건)에 걸려 중단돼도
재실행 시 이어서 수집한다.

사용법
~~~~~~

* dry-run:   ``python -m ingest.fetch_year --year 2024 --limit 5``
* 전체 실행: ``python -m ingest.fetch_year --year 2024``
  → ``data/audit_reports_y2024.xlsx`` (병합은 별도 단계)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from audit_xlsx.extractors import extract_internal_control_summary  # noqa: E402
from audit_xlsx.fetch_audit import html_to_text  # noqa: E402
from audit_xlsx.parse_audit import parse_many  # noqa: E402
from audit_xlsx.settings import load_settings  # noqa: E402
from audit_xlsx.stock_to_corp import load_mapping  # noqa: E402
from fetch_iacm_api import _zip_biz_report_flat, fetch_document_zip  # noqa: E402

# ---------------------------------------------------------------------------
# v4 스키마 — (영문 row key, 한글 컬럼) 순서 그대로
# ---------------------------------------------------------------------------

_COL_IC_SUMMARY = "내부통제에 관한 사항(사업보고서 본문)"
_V4_COLUMNS: tuple[tuple[str, str], ...] = (
    ("stock_code", "종목코드"),
    ("corp_code", "DART고유번호"),
    ("corp_name", "회사명"),
    ("market", "시장구분"),
    ("report_kind", "보고서 종류"),
    ("attach_title", "첨부 제목"),
    ("parent_rcept_no", "감사보고서제출 접수번호"),
    ("biz_report_rcept_no", "사업보고서 접수번호"),
    ("stlm_dt", "결산기준일"),
    ("bsns_year", "사업연도"),
    ("adt_opinion", "감사의견"),
    ("adtor", "감사인(회계법인)"),
    ("cpa_partner_name", "업무수행 공인회계사"),
    ("kam_api", "핵심감사사항(KAM)"),
    ("kam_body_full", "핵심감사사항(본문)"),
    ("emphs_matter", "강조사항"),
    ("other_matters", "기타사항"),
    ("audit_report_body", "감사보고서 본문 전체"),
    ("audit_report_body_html", "감사보고서 본문(HTML)"),
)

_XLSX_CELL_LIMIT = 32_767
_IC_WORKERS = 4
_IC_PROGRESS_EVERY = 200


def _truncate_cell(v: str) -> str:
    if len(v) <= _XLSX_CELL_LIMIT:
        return v
    return v[: _XLSX_CELL_LIMIT - 100] + "\n<!-- truncated at 32K limit -->"


# ---------------------------------------------------------------------------
# 내부통제에 관한 사항 — 접수번호 기반 (연도 충돌 없음, 한도 중단 시 재개)
# ---------------------------------------------------------------------------


def _ic_cache_path(cache_dir: Path, rcept_no: str) -> Path:
    return cache_dir / f"rcept_{rcept_no}.json"


def _ic_one(api_key: str, rcept_no: str) -> str:
    z = fetch_document_zip(api_key, rcept_no)
    if z is None:
        return ""
    main_flat = _zip_biz_report_flat(z)
    if not main_flat:
        return ""
    return extract_internal_control_summary(main_flat) or ""


def fetch_ic_summaries(
    api_key: str, rcept_nos: list[str], cache_dir: Path
) -> dict[str, str]:
    """사업보고서 접수번호별 내부통제에 관한 사항. 캐시 우선, 한도 초과 시 중단."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, str] = {}
    todo: list[str] = []
    for r in rcept_nos:
        p = _ic_cache_path(cache_dir, r)
        if p.exists():
            try:
                out[r] = json.loads(p.read_text(encoding="utf-8")).get("summary", "")
                continue
            except Exception:  # noqa: BLE001 — 손상 캐시는 재수집
                pass
        todo.append(r)
    print(f"[내부통제] 캐시 hit: {len(out)} / 신규: {len(todo)}", flush=True)

    t0 = time.time()
    quota_hit = False
    with ThreadPoolExecutor(max_workers=_IC_WORKERS) as pool:
        futs = {pool.submit(_ic_one, api_key, r): r for r in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            r = futs[fut]
            try:
                summary = fut.result()
            except RuntimeError as e:  # 사용한도 초과 — 남은 건 내일 재개
                if not quota_hit:
                    print(f"[내부통제] API 한도 도달, 잔여분 이월: {e}", flush=True)
                    quota_hit = True
                continue
            except Exception as e:  # noqa: BLE001
                print(f"  err {r}: {e}", flush=True)
                summary = ""
            out[r] = summary
            _ic_cache_path(cache_dir, r).write_text(
                json.dumps({"summary": summary}, ensure_ascii=False), encoding="utf-8"
            )
            if i % _IC_PROGRESS_EVERY == 0 or i == len(todo):
                rate = (time.time() - t0) / i
                print(
                    f"  [내부통제 {i}/{len(todo)}] 추출={sum(1 for v in out.values() if v)} "
                    f"· {rate:.2f}s/건 · ETA {(len(todo)-i)*rate/60:.1f}min",
                    flush=True,
                )
    return out


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True, help="사업연도 (예: 2024)")
    parser.add_argument("--limit", type=int, default=None, help="회사 수 제한 (dry-run)")
    parser.add_argument("--dst", type=Path, default=None, help="출력 XLSX")
    args = parser.parse_args(argv)

    settings = load_settings()
    settings.require_key()
    dst = args.dst or (settings.data_dir / f"audit_reports_y{args.year}.xlsx")
    start = f"{args.year + 1}-01-01"
    end = f"{args.year + 1}-04-30"

    mapping = load_mapping(settings)
    if args.limit is not None:
        mapping = mapping[: args.limit]
    print(
        f"year={args.year} (공시 {start}~{end}) · 회사 {len(mapping)} · dst={dst}",
        flush=True,
    )

    # 1) 구조화 + 감사보고서 본문 (viewer, raw_audit 캐시)
    rows = parse_many(
        settings, mapping,
        bsns_year=str(args.year), start=start, end=end,
        fetch_body=True, save_raw=True, progress=True,
    )
    # 해당 연도 데이터가 전혀 없는 회사(상장 전 등)는 제외
    rows = [r for r in rows if r.get("skip_reason") != "no_audit_opinion_data"]
    print(f"rows (데이터 보유): {len(rows)}", flush=True)

    # 2) 내부통제에 관한 사항 — 사업보고서 접수번호별
    rcepts = sorted({
        str(r.get("biz_report_rcept_no") or "").strip()
        for r in rows
        if str(r.get("biz_report_rcept_no") or "").strip()
    })
    ic_map = fetch_ic_summaries(settings.api_key, rcepts, settings.data_dir / "iacm_api")
    n_ic = sum(1 for v in ic_map.values() if v)
    print(f"[내부통제] 추출: {n_ic}/{len(rcepts)} 접수번호", flush=True)

    # 3) v4 스키마 DataFrame
    df = pd.DataFrame(
        {header: [str(r.get(key) or "") for r in rows] for key, header in _V4_COLUMNS}
    )
    df[_COL_IC_SUMMARY] = [
        ic_map.get(str(r.get("biz_report_rcept_no") or "").strip(), "") for r in rows
    ]
    for col in df.columns:  # openpyxl 셀 32K 한도 일괄 보호
        df[col] = df[col].map(_truncate_cell)

    tmp = dst.with_suffix(".tmp.xlsx")
    df.to_excel(tmp, index=False, engine="openpyxl")
    os.replace(tmp, dst)
    print(f"\nwrote: {dst} ({dst.stat().st_size/1024/1024:.2f} MB, {len(df)} rows)", flush=True)

    for col in ("감사의견", "핵심감사사항(본문)", "감사보고서 본문 전체", _COL_IC_SUMMARY):
        n = int((df[col].astype(str).str.strip() != "").sum())
        print(f"  {col}: {n}/{len(df)} ({100*n/max(len(df),1):.1f}%)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
