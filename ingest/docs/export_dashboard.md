# `export_dashboard.py` — Next.js 대시보드용 JSON 생성

## 개요

SQLite 의 집계 결과를 단일 JSON 파일(`dashboard/public/data/summary.json`)로 내보냅니다. Next.js 대시보드는 빌드 타임 또는 클라이언트 측에서 이 파일을 그대로 fetch 합니다.

JSON 스키마는 `dashboard/lib/types.ts` 의 `SummaryPayload` 와 **1:1 매칭**됩니다. 컬럼 추가 시 양쪽 모두 변경 필요.

## 출력 스키마

```ts
{
  generatedAt: string;          // UTC ISO timestamp
  filings: Array<{              // 최대 8000건, 최신순
    rcept_no, corp_code, corp_name, stock_code, report_nm, rcept_dt,
    pblntf_detail_ty, opinion_label, kam_count, auditor_firm,
    opinion_modification_reason, accounting_standard,
    auditor_name, cpa_partner_name,
    emphasis_of_matter_present, emphasis_of_matter_content,
    other_matters_present, other_matters_content,
    filing_year                 // rcept_dt[0:4]
  }>;
  summaryByYear: {              // 연도별 집계
    [year: string]: {
      filings: number;
      opinion_counts: { [opinion: string]: number };
      kam_avg: number | null;
      kam_median: number | null;
    }
  };
  kamItemsSample: Array<{       // KAM 본문 샘플, 최대 5000건
    rcept_no, corp_name, rcept_dt, ordinal, title,
    body_snippet, kam_content, selection_reason
  }>;
  ae00024Sample: Array<{        // 구조화 API 캐시 샘플, 최대 5000건
    corp_code, bsns_year, status, message, fetched_at
  }>;
}
```

## 구성 요소

| 함수 | 역할 |
|---|---|
| `export_dashboard(conn, out_dir)` | 메인 진입점. `out_dir/summary.json` 생성 |
| `_fetch_filings(conn)` | `filings + parse_results` 조인 + `filing_year` 부착 |
| `_aggregate_summary_by_year(filings)` | 연도별 의견·KAM 통계 |
| `_fetch_kam_sample(conn)` | KAM 본문 샘플 (최대 5000) |
| `_fetch_ae00024_sample(conn)` | 구조화 API 캐시 샘플 (최대 5000) |
| `_FILINGS_LIMIT = 8000` | filings 배열 상한 |
| `_KAM_CONTENT_LIMIT = 32000` | 개별 KAM 본문 길이 컷 |

## 예제

### CLI

```powershell
# 기본 경로로 출력
python -m dart_kam export-dashboard

# 출력 경로 지정
python -m dart_kam export-dashboard --out C:\tmp\dash
```

### 라이브러리

```python
from pathlib import Path
from dart_kam.config import load_settings
from dart_kam.db import connect, init_db
from dart_kam.export_dashboard import export_dashboard
from dart_kam.paths import db_path

settings = load_settings()
conn = connect(db_path(settings))
init_db(conn)
try:
    export_dashboard(conn, Path("./out"))
finally:
    conn.close()
```

### 출력 확인

```powershell
Get-Item dashboard\public\data\summary.json | Select-Object Length, LastWriteTime
```

```bash
jq '.generatedAt, .summaryByYear | keys' dashboard/public/data/summary.json
```

## FAQ

**Q. 파일이 매우 큽니다.**
A. 8000건 × 17 컬럼 + KAM 본문 5000개(개당 최대 32KB) 라 수십 MB 까지 갈 수 있습니다. 다음 옵션을 검토하세요:
- `_FILINGS_LIMIT` / `_KAM_CONTENT_LIMIT` 상수 조정
- 연도별 분할 (`summary_2024.json`, `summary_2025.json` …)
- 클라이언트에서 페이지네이션

**Q. 연도별 집계의 `kam_median` 계산 방식?**
A. 단순 인덱스 기반 — `kams_sorted[len(kams_sorted) // 2]`. 짝수 개수에서 두 중앙값 평균을 내지 않으므로 통계적으로 엄밀한 median 은 아닙니다. 시각화 용도라 충분합니다.

**Q. 한국어가 `\u` escape 로 인코딩됩니다.**
A. 그렇지 않습니다 — `ensure_ascii=False` 로 출력하므로 UTF-8 한글 그대로 저장됩니다. 만약 `\u` 가 보인다면 에디터 설정 문제입니다.

**Q. `filings` 의 정렬은?**
A. `rcept_dt DESC` (최신순). 8000건 상한도 이 정렬 기준으로 최신부터 절단됩니다.

**Q. 스키마 변경 시 어디를 같이 고쳐야 하나요?**
A. 다음 4곳:
1. `db.py` SCHEMA (또는 `_PARSE_RESULTS_NEW_COLUMNS` 마이그레이션)
2. `repository.py` UPSERT SQL
3. `export_dashboard.py` SELECT 쿼리
4. `dashboard/lib/types.ts` `SummaryPayload`

**Q. `parse_results` 가 없는 공시도 포함되나요?**
A. 네. `LEFT JOIN parse_results` 이므로 미파싱 공시도 한 행으로 나오며, 분석 컬럼은 모두 NULL 입니다. 대시보드 측에서 NULL 필터링 권장.
