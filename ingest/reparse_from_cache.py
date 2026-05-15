"""기존 v2 XLSX + raw_audit/ 캐시만 활용해 새 컬럼 2개를 추가한 v3 XLSX 생성.

OPENDART API 호출 0회. 캐시된 raw HTML 만 다시 평문화·슬라이스 + KAM 추출.

추가되는 컬럼:
- '핵심감사사항(본문)' : raw HTML 본문에서 직접 추출한 KAM 전체 문단
- '감사보고서 본문 전체' : 줄바꿈 보존 버전으로 갱신 (덮어쓰기)

매칭 로직:
  v2 XLSX 의 행은 (감사보고서제출 접수번호, 보고서 종류) 가 unique 이지만
  XLSX 에 dcm_no 가 없어서 캐시 파일과 1:1 매칭이 모호하다. 같은
  parent_rcept_no 의 raw 파일들을 *이전 방식 (단일 공백 정규화)* 으로 평문화한 뒤
  v2 의 '감사보고서 본문 전체' 첫 200자를 substring 으로 매칭한다.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from audit_xlsx.extractors import (  # noqa: E402
    extract_kam_full_block,
    extract_standalone_audit_report_body,
)
from audit_xlsx.fetch_audit import html_to_text  # noqa: E402  (NEW: 줄바꿈 보존 버전)


# 이전 v2 방식의 평문화 — 매칭용 (캐시 raw 와 v2 본문이 같은 정규화로 만들어졌었음).
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def html_to_text_v2(raw: str) -> str:
    """이전 (줄바꿈 단일 공백 합침) 방식 — 매칭 전용."""
    if not raw:
        return ""
    import html

    s = _TAG_RE.sub(" ", raw)
    s = html.unescape(s)
    return _WS_RE.sub(" ", s).strip()


def normalize_for_match(text: str) -> str:
    """매칭용 — 모든 공백을 단일 공백으로."""
    return _WS_RE.sub(" ", text or "").strip()


def main() -> int:
    repo_root = _HERE.parent
    src_xlsx = repo_root / "dashboard" / "data" / "audit_reports_full_v2.xlsx"
    dst_xlsx = repo_root / "dashboard" / "data" / "audit_reports_full_v3.xlsx"
    raw_dir = repo_root / "data" / "raw_audit"

    if not src_xlsx.exists():
        print(f"ERR: src xlsx not found: {src_xlsx}")
        return 1
    if not raw_dir.exists():
        print(f"ERR: raw_audit not found: {raw_dir}")
        return 1

    print(f"src: {src_xlsx}")
    print(f"raw: {raw_dir}")

    # 1) v2 로드 (모든 컬럼 그대로)
    df = pd.read_excel(src_xlsx, dtype=str, engine="openpyxl")
    df = df.fillna("")
    print(f"v2 rows: {len(df)} cols: {len(df.columns)}", flush=True)

    # 2) raw 인덱싱: parent_rcept -> [(dcm, raw_text), ...]
    raw_index: dict[str, list[tuple[str, str]]] = defaultdict(list)
    raw_files = list(raw_dir.glob("*.html"))
    for fp in raw_files:
        stem = fp.stem
        if "_" not in stem:
            continue
        parent, dcm = stem.split("_", 1)
        try:
            raw_index[parent].append((dcm, fp.read_text(encoding="utf-8")))
        except Exception as e:  # noqa: BLE001
            print(f"  read err {fp.name}: {e}")
    print(f"indexed raw files: {sum(len(v) for v in raw_index.values())} ({len(raw_index)} unique parents)", flush=True)

    # 3) 각 행 처리 — 매칭 → 새 컬럼 채움
    new_kam: list[str] = []
    new_body: list[str] = []
    matched = 0
    no_parent = 0
    no_match = 0

    for i, row in df.iterrows():
        parent = str(row.get("감사보고서제출 접수번호") or "").strip()
        old_body = str(row.get("감사보고서 본문 전체") or "").strip()
        old_kam_api = str(row.get("핵심감사사항(KAM)") or "").strip()

        if not parent or parent not in raw_index:
            no_parent += 1
            new_kam.append("")
            new_body.append(old_body)
            continue

        candidates = raw_index[parent]
        old_norm = normalize_for_match(old_body)[:200]
        matched_raw: str | None = None
        if not old_norm:
            # 본문이 없는 행 — 그냥 첫 후보 사용 (rare)
            matched_raw = candidates[0][1] if candidates else None
        else:
            for _dcm, raw in candidates:
                flat_v2 = html_to_text_v2(raw)
                if old_norm in flat_v2 or old_norm[:100] in flat_v2:
                    matched_raw = raw
                    break

        if matched_raw is None:
            no_match += 1
            new_kam.append("")
            new_body.append(old_body)
            continue

        matched += 1
        flat_new = html_to_text(matched_raw)  # NEW: 줄바꿈 보존
        body_new = extract_standalone_audit_report_body(flat_new)
        kam_full = extract_kam_full_block(body_new or flat_new)

        new_body.append(body_new or old_body)
        new_kam.append(kam_full or "")

        if (i + 1) % 500 == 0:
            print(
                f"  [{i+1}/{len(df)}] matched={matched} no_parent={no_parent} no_match={no_match}",
                flush=True,
            )

    print(
        f"\n총 {len(df)} | matched {matched} | no_parent {no_parent} | no_match {no_match}",
        flush=True,
    )

    # 4) 컬럼 추가/갱신
    df["핵심감사사항(본문)"] = new_kam
    df["감사보고서 본문 전체"] = new_body  # 줄바꿈 보존 버전으로 덮어씀

    # 컬럼 순서: 핵심감사사항(KAM) 옆에 (본문) 배치.
    cols = list(df.columns)
    if "핵심감사사항(본문)" in cols:
        cols.remove("핵심감사사항(본문)")
        if "핵심감사사항(KAM)" in cols:
            idx = cols.index("핵심감사사항(KAM)") + 1
            cols.insert(idx, "핵심감사사항(본문)")
        else:
            cols.append("핵심감사사항(본문)")
    df = df[cols]

    # 5) 저장
    df.to_excel(dst_xlsx, index=False, engine="openpyxl")
    print(f"\nwrote: {dst_xlsx} ({dst_xlsx.stat().st_size/1024/1024:.2f} MB)")

    # 6) KAM 추출 통계
    n_kam = (df["핵심감사사항(본문)"].astype(str).str.strip() != "").sum()
    print(f"KAM(본문) 추출: {n_kam}/{len(df)} ({100*n_kam/len(df):.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
