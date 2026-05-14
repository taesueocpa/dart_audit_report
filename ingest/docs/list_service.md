# `list_service.py` — 공시목록 수집

## 개요

OPENDART `list.json` 호출로 공시 목록을 받아 `filings` 테이블에 업서트합니다. API 제약 두 가지를 자동 처리합니다:

1. **기간 제한** — `corp_code` 없이 호출하면 `bgn_de`~`end_de` 범위가 최대 약 3개월. → `settings.list_chunk_days` (기본 90일) 단위로 자동 분할.
2. **페이지네이션** — 페이지당 100건. 응답의 `total_page` 를 보고 끝까지 반복.

또한 여러 공시상세유형(`F001`, `F002`, `F003`)을 각각 따로 조회하여 모든 조합을 커버합니다.

## 구성 요소

| 심볼 | 역할 |
|---|---|
| `ingest_filings(client, settings, conn, start=None, end=None, *, progress=True)` | 메인 진입점 |
| `_chunk_ranges(start, end, max_days)` | 기간을 청크로 분할하는 generator |
| `_build_list_params(settings, *, bgn, end, page, detail_ty)` | `list.json` 쿼리 파라미터 빌더 |
| `_upsert_items(conn, items, *, ...)` | 한 페이지의 응답을 업서트 |
| `_ingest_one_chunk(...)` | 단일 (detail_ty, chunk) 페이지네이션 처리 |

## 처리 단위 매트릭스

```
                 F001    F002    F003    (detail_type)
  2023-Q1 ~ Q1   page1   page1   page1
                 page2   page2   page2
                 ...     ...     ...
  2023-Q2 ~ Q2   page1   page1   page1
                 ...
  ...
  → 각 cell 은 (detail_type × 기간 청크) 조합, 그 안에서 페이지 끝까지
```

기본 설정으로 "최근 3년 × 3종류 = 약 36개 청크 × 평균 N페이지" 만큼 호출합니다.

## 예제

### CLI — 기본 (최근 3년)

```powershell
python -m dart_kam list
```

### CLI — 기간 지정

```powershell
python -m dart_kam list --bgn-de 20250101 --end-de 20250331
```

### CLI — HTTP 로그 켜고 진행 메시지 숨김

```powershell
python -m dart_kam list --verbose-http --quiet
```

### 라이브러리

```python
from datetime import date
from dart_kam.config import load_settings
from dart_kam.dart_client import DartClient
from dart_kam.db import connect, init_db
from dart_kam.list_service import ingest_filings
from dart_kam.paths import db_path

settings = load_settings()
settings.require_key()
conn = connect(db_path(settings))
init_db(conn)
with DartClient(settings) as client:
    n = ingest_filings(
        client, settings, conn,
        start=date(2025, 1, 1),
        end=date(2025, 3, 31),
    )
print(f"touched {n:,} filings")
conn.close()
```

### 결과 확인

```sql
SELECT rcept_no, corp_name, report_nm, rcept_dt, pblntf_detail_ty
FROM filings
ORDER BY rcept_dt DESC
LIMIT 10;
```

## FAQ

**Q. 누적 카운트가 실제 행 수보다 많아 보입니다.**
A. `ingest_filings` 의 반환값은 *호출 단위 누적 처리 수* 입니다. 같은 `rcept_no` 가 여러 detail_type 에서 잡히는 경우 중복 카운트가 발생할 수 있어 "약 N건" 으로 표기합니다. 실제 행 수는 `SELECT COUNT(*) FROM filings` 로 확인하세요.

**Q. API status 가 `000` 이 아닐 때 어떻게 되나요?**
A. 해당 청크의 페이지네이션을 중단하고 다음 (detail_type, chunk) 조합으로 넘어갑니다. 오류 메시지가 진행 라인에 표시됩니다.

**Q. 특정 시장(KOSPI/KOSDAQ)만 보고 싶어요.**
A. `DART_CORP_CLS=Y` (코스피) 또는 `K` (코스닥) 환경변수를 설정하세요. `_build_list_params` 에서 자동 반영됩니다.

**Q. 청크 크기를 줄이면 어떻게 되나요?**
A. `DART_LIST_CHUNK_DAYS=30` 처럼 줄이면 호출 횟수가 늘어나 안정성은 높아지지만 총 소요 시간도 늘어납니다. 기본 90이 OPENDART 제약 상한과 일치.

**Q. 정렬을 바꿀 수 있나요?**
A. 현재 `sort=date, sort_mth=desc` 로 고정입니다(최신순). 필요하면 `_build_list_params` 를 수정하세요.

**Q. 페이지당 100건은 OPENDART 상한인가요?**
A. 네. `page_count=100` 이 OPENDART 가 허용하는 최대입니다.
