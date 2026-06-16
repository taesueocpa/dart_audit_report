"""OPENDART 공시서류원본파일(document.xml) API 로 「내부통제에 관한 사항」 수집.

**공식 API 만 사용한다** — anti-bot 차단이 없고 일 20,000건 한도 내에서 전 종목을
수십 분에 수집한다 (viewer 스크래핑 경로는 2026-06 제거).

수집 경로
~~~~~~~~~

``document.xml?rcept_no=<사업보고서 접수번호>`` ZIP 의 사업보고서 본문 XML 에서
「내부통제에 관한 사항」(경영진의 내부회계관리제도 효과성 평가결과·중요한 취약점·
시정조치계획·감사인 의견 요약표) 절을 추출해 ``내부통제에 관한 사항(사업보고서
본문)`` 컬럼에 채운다.

한계: 외감법 제8조제4~6항 원형 3절(운영실태보고서/감사 평가보고서/감사인 보고서)
중 감사(위원회) 평가보고서(제8조제5항)는 사업보고서 첨부 「내부회계관리제도운영
보고서」에만 있고 document.xml ZIP 에 미포함이라 어느 경로로도 수집하지 않는다.

캐시: 회사별 추출 결과 JSON 을 `data/iacm_api/{corp_code}.json` 에 저장 —
재실행 시 API 호출 없이 재병합만 수행한다.

사용법
~~~~~~

* dry-run (12 회사):  ``python -m ingest.fetch_iacm_api --limit 12``
* 전체 실행:          ``python -m ingest.fetch_iacm_api``
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd
import requests

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from audit_xlsx.extractors import extract_internal_control_summary  # noqa: E402
from audit_xlsx.fetch_audit import html_to_text  # noqa: E402
from audit_xlsx.settings import load_settings  # noqa: E402

# ---------------------------------------------------------------------------
# 경로 / 상수
# ---------------------------------------------------------------------------

_REPO_ROOT = _HERE.parent
_DEFAULT_SRC = _REPO_ROOT / "dashboard" / "data" / "audit_reports_full_v4.xlsx"

_COL_CORP = "DART고유번호"
_COL_BIZ_RCEPT = "사업보고서 접수번호"
_COL_IC_SUMMARY = "내부통제에 관한 사항(사업보고서 본문)"

_DOC_URL = "https://opendart.fss.or.kr/api/document.xml"
_DOC_TIMEOUT = 90.0
_HTTP_RETRIES = 3
_HTTP_BACKOFF_SEC = 3.0
# 공식 API 전역 최소 호출 간격 — 일 20,000건·분당 한도 대비 보수적으로.
_MIN_API_INTERVAL = 0.15
_WORKERS = 4
_PROGRESS_EVERY = 100

_SESSION = requests.Session()
_THROTTLE_LOCK = threading.Lock()
_last_request_ts = 0.0

_RCEPT_14 = re.compile(r"^\d{14}$")
_DOC_NAME = re.compile(r"<DOCUMENT-NAME[^>]*>([^<]*)")


def _throttle() -> None:
    global _last_request_ts
    with _THROTTLE_LOCK:
        wait = _MIN_API_INTERVAL - (time.monotonic() - _last_request_ts)
        if wait > 0:
            time.sleep(wait)
        _last_request_ts = time.monotonic()


def _norm_rcept(v) -> str:
    """접수번호 정규화 — '2.02603E+13' 같은 float 표기를 14자리 문자열로."""
    s = str(v or "").strip()
    if _RCEPT_14.match(s):
        return s
    try:
        s2 = str(int(float(s)))
        return s2 if _RCEPT_14.match(s2) else ""
    except (ValueError, OverflowError):
        return ""


# ---------------------------------------------------------------------------
# document.xml 호출 + ZIP 파싱
# ---------------------------------------------------------------------------


def fetch_document_zip(api_key: str, rcept_no: str) -> zipfile.ZipFile | None:
    """공시서류원본 ZIP. 오류 응답(XML)·연결오류 시 ``None``."""
    for attempt in range(_HTTP_RETRIES):
        _throttle()
        try:
            r = _SESSION.get(
                _DOC_URL,
                params={"crtfc_key": api_key, "rcept_no": rcept_no},
                timeout=_DOC_TIMEOUT,
            )
        except requests.RequestException:
            if attempt == _HTTP_RETRIES - 1:
                return None
            time.sleep(_HTTP_BACKOFF_SEC * (attempt + 1))
            continue
        if r.content[:2] == b"PK":
            try:
                return zipfile.ZipFile(io.BytesIO(r.content))
            except zipfile.BadZipFile:
                return None
        # XML 오류 응답 — 사용한도 초과(020/021)는 재시도 무의미, 즉시 중단 신호.
        body = r.content[:500].decode("utf-8", "ignore")
        if "020" in body or "021" in body:
            raise RuntimeError(f"OPENDART 사용한도 초과 응답: {body[:200]}")
        return None
    return None


def _zip_biz_report_flat(z: zipfile.ZipFile) -> str:
    """ZIP 멤버 중 사업보고서 본문 XML 을 찾아 평문으로. 없으면 빈 문자열."""
    for name in z.namelist():
        try:
            txt = z.read(name).decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001 — 멤버 1개 손상이 전체를 막지 않도록
            continue
        m = _DOC_NAME.search(txt)
        title = (m.group(1).strip() if m else "")
        if "사업보고서" in title:
            return html_to_text(txt)
    return ""


# ---------------------------------------------------------------------------
# 회사 1건 처리
# ---------------------------------------------------------------------------


@dataclass
class _ApiResult:
    summary: str = ""
    status: str = ""  # ok / no_doc / no_main_xml / no_extract / err

    @property
    def any_extracted(self) -> bool:
        return bool(self.summary)


def _process_one(api_key: str, biz_rcept: str) -> _ApiResult:
    """사업보고서 접수번호 → 「내부통제에 관한 사항」 추출 결과."""
    if not biz_rcept:
        return _ApiResult(status="no_doc")
    z = fetch_document_zip(api_key, biz_rcept)
    if z is None:
        return _ApiResult(status="no_doc")
    main_flat = _zip_biz_report_flat(z)
    if not main_flat:
        return _ApiResult(status="no_main_xml")
    summary = extract_internal_control_summary(main_flat) or ""
    return _ApiResult(summary=summary, status="ok" if summary else "no_extract")


# ---------------------------------------------------------------------------
# 캐시
# ---------------------------------------------------------------------------


def _cache_path(cache_dir: Path, corp_code: str) -> Path:
    return cache_dir / f"{corp_code}.json"


def _cache_load(cache_dir: Path, corp_code: str) -> _ApiResult | None:
    p = _cache_path(cache_dir, corp_code)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        # 구 스키마(sec2/sec4 포함) 캐시도 호환 — 알려진 필드만 취한다.
        return _ApiResult(summary=d.get("summary", ""), status=d.get("status", ""))
    except Exception:  # noqa: BLE001 — 손상 캐시는 무시하고 재수집
        return None


def _cache_save(cache_dir: Path, corp_code: str, r: _ApiResult) -> None:
    _cache_path(cache_dir, corp_code).write_text(
        json.dumps(asdict(r), ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# 배치
# ---------------------------------------------------------------------------


def _unique_companies(df: pd.DataFrame) -> dict[str, str]:
    """corp_code → 사업보고서 접수번호 (첫 등장 1건)."""
    pairs: dict[str, str] = {}
    for _, row in df.iterrows():
        cc = str(row.get(_COL_CORP) or "").strip()
        biz = _norm_rcept(row.get(_COL_BIZ_RCEPT))
        if cc and biz and cc not in pairs:
            pairs[cc] = biz
    return pairs


def _run_batch(
    api_key: str,
    pairs: dict[str, str],
    cache_dir: Path,
    workers: int = _WORKERS,
) -> dict[str, _ApiResult]:
    results: dict[str, _ApiResult] = {}
    todo: dict[str, str] = {}
    for cc, biz in pairs.items():
        cached = _cache_load(cache_dir, cc)
        # ok 캐시는 재사용; 실패 캐시(no_doc 등)는 재시도
        if cached is not None and cached.status == "ok":
            results[cc] = cached
        else:
            todo[cc] = biz
    print(f"캐시 hit: {len(results)} / 신규 수집: {len(todo)}", flush=True)

    total = len(todo)
    t0 = time.time()
    counts = {"ok": 0, "no_doc": 0, "no_main_xml": 0, "no_extract": 0, "err": 0}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        fut_to_cc = {
            pool.submit(_process_one, api_key, biz): cc for cc, biz in todo.items()
        }
        for i, fut in enumerate(as_completed(fut_to_cc), 1):
            cc = fut_to_cc[fut]
            try:
                r = fut.result()
            except RuntimeError:
                raise  # 사용한도 초과 — 전체 중단 (캐시는 보존됨)
            except Exception as e:  # noqa: BLE001
                print(f"  err {cc}: {e}", flush=True)
                r = _ApiResult(status="err")
            results[cc] = r
            _cache_save(cache_dir, cc, r)
            counts[r.status] = counts.get(r.status, 0) + 1

            if i % _PROGRESS_EVERY == 0 or i == total:
                elapsed = time.time() - t0
                rate = elapsed / i
                eta = rate * (total - i)
                print(
                    f"  [{i}/{total}] ok={counts['ok']} no_doc={counts['no_doc']} "
                    f"no_main={counts['no_main_xml']} no_ext={counts['no_extract']} "
                    f"err={counts['err']} · {rate:.2f}s/co · ETA {eta/60:.1f}min",
                    flush=True,
                )
    return results


# ---------------------------------------------------------------------------
# 병합 — 「내부통제에 관한 사항」 컬럼만 (API 가 유일한 소스)
# ---------------------------------------------------------------------------


def _merge_into_df(df: pd.DataFrame, results: dict[str, _ApiResult]) -> pd.DataFrame:
    if _COL_IC_SUMMARY not in df.columns:
        df[_COL_IC_SUMMARY] = ""

    # 이번 실행에서 처리된 회사는 항상 최신 추출값으로 교체 (추출기 개선분 반영).
    corp = df[_COL_CORP].astype(str).str.strip()
    mask = corp.isin(results.keys())
    df.loc[mask, _COL_IC_SUMMARY] = corp[mask].map(
        lambda c: results[c].summary if results.get(c) else ""
    )
    ns = int((df.loc[mask, _COL_IC_SUMMARY].astype(str).str.strip() != "").sum())
    print(f"병합: 내부통제요약 {ns}행 (교체)", flush=True)
    return df


def _fill_rate(df: pd.DataFrame) -> str:
    if _COL_IC_SUMMARY not in df.columns:
        return f"  {_COL_IC_SUMMARY}: 컬럼 없음"
    s = df[_COL_IC_SUMMARY].fillna("").astype(str).str.strip()
    n = int((s != "").sum())
    return f"  {_COL_IC_SUMMARY}: {n}/{len(df)}행 ({100*n/max(len(df),1):.1f}%)"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="회사 수 제한 (dry-run)")
    parser.add_argument("--src", type=Path, default=_DEFAULT_SRC, help="입력 XLSX")
    parser.add_argument("--dst", type=Path, default=None, help="출력 XLSX (기본: src 와 동일)")
    parser.add_argument("--workers", type=int, default=_WORKERS)
    args = parser.parse_args(argv)
    dst = args.dst or args.src

    settings = load_settings()
    settings.require_key()
    if not args.src.exists():
        print(f"ERR: src not found: {args.src}", file=sys.stderr)
        return 1

    cache_dir = settings.data_dir / "iacm_api"
    cache_dir.mkdir(parents=True, exist_ok=True)
    print(f"src: {args.src}\ncache: {cache_dir}", flush=True)

    df = pd.read_excel(
        args.src,
        dtype={_COL_CORP: str, _COL_BIZ_RCEPT: str, "종목코드": str},
        engine="openpyxl",
    ).fillna("")
    print(f"rows: {len(df)} cols: {len(df.columns)}", flush=True)

    pairs = _unique_companies(df)
    if args.limit is not None:
        pairs = dict(list(pairs.items())[: args.limit])
    print(f"대상 회사: {len(pairs)}", flush=True)

    results = _run_batch(settings.api_key, pairs, cache_dir, workers=args.workers)

    ns = sum(1 for r in results.values() if r.summary)
    print(f"\n회사 단위 추출: 내부통제요약={ns} / {len(pairs)}", flush=True)

    df = _merge_into_df(df, results)

    tmp = dst.with_suffix(".tmp.xlsx")
    df.to_excel(tmp, index=False, engine="openpyxl")
    os.replace(tmp, dst)
    size_mb = dst.stat().st_size / 1024 / 1024
    print(f"\nwrote: {dst} ({size_mb:.2f} MB, {len(df.columns)} columns)", flush=True)
    print("행 단위 충족률:\n" + _fill_rate(df), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
