# `repository.py` — DB UPSERT / 선택 쿼리 집중

## 개요

이전에는 INSERT/UPSERT SQL 이 다음 5개 모듈에 흩어져 있었고, 특히 `parse_audit.py` 의 성공/실패 분기에서 17개 컬럼짜리 UPSERT 가 두 번 거의 동일하게 반복되어 *80+줄의 중복* 이 있었습니다.

이 모듈은 **모든 쓰기 SQL을 한 곳에** 모아 호출부를 1~2줄로 줄입니다. 호출자는 함수 이름과 키워드 인자만 신경 쓰면 됩니다.

## 구성 요소

### Upsert 함수

| 함수 | 대상 테이블 |
|---|---|
| `upsert_company(conn, *, corp_code, corp_name, stock_code, corp_cls, updated_at)` | `companies` |
| `upsert_filing(conn, *, rcept_no, corp_code, item, pblntf_ty, pblntf_detail_ty, fetched_at)` | `filings` |
| `upsert_document_fetch(conn, *, rcept_no, status, zip_path, error, updated_at)` | `document_fetch` |
| `mark_document_downloaded(conn, ...)` | `document_fetch` (status='downloaded' 편의 함수) |
| `mark_document_failed(conn, ...)` | `document_fetch` (status='failed' 편의 함수) |
| `upsert_parse_result(conn, *, rcept_no, parser_version, result, parsed_at)` | `parse_results` (성공) |
| `mark_parse_failure(conn, *, rcept_no, parser_version, error, parsed_at)` | `parse_results` (실패, 분석 컬럼 NULL/0 리셋) |
| `replace_kam_items(conn, *, rcept_no, items)` | `kam_items` (DELETE → INSERT) |
| `upsert_ae00024(conn, *, corp_code, bsns_year, reprt_code, status, payload, message, fetched_at)` | `ae00024_cache` |

### 선택 쿼리

| 함수 | 반환 |
|---|---|
| `select_filings_for_download(conn, *, skip_downloaded, limit)` | `list[str]` rcept_no — 다운로드 대상 |
| `select_filings_for_parse(conn, *, force, limit)` | `list[str]` rcept_no — 파싱 대상 |
| `select_distinct_corp_years(conn, *, limit)` | `list[tuple[str, str]]` (corp_code, YYYY) — AE00024 조회 대상 |

### 내부 헬퍼

| 심볼 | 역할 |
|---|---|
| `_i(value)` | None/잘못된 값 → 0 으로 안전 캐스팅 |
| `_UPSERT_*_SQL` | 각 테이블별 UPSERT SQL 상수 |
| `_UPSERT_PARSE_RESULT_FAILURE_SQL` | 분석 컬럼을 NULL/0 으로 리셋하는 SQL |

## 예제

### 파싱 결과 저장 (성공)

```python
from dart_kam.repository import upsert_parse_result, replace_kam_items

result = {
    "opinion_label": "적정의견",
    "opinion_raw_snippet": "...",
    "kam_count": 1,
    "kam_section_full": "핵심감사사항 ...",
    # ... 17개 컬럼 ...
}

upsert_parse_result(
    conn,
    rcept_no="20260331002433",
    parser_version="v3",
    result=result,
    parsed_at="2026-05-14T03:00:00Z",
)

replace_kam_items(
    conn,
    rcept_no="20260331002433",
    items=[{
        "ordinal": 1,
        "title": "핵심감사사항",
        "body_snippet": "...",
        "kam_content": "...",
        "selection_reason": None,
    }],
)
conn.commit()
```

### 파싱 실패 마킹

```python
from dart_kam.repository import mark_parse_failure

try:
    parse_filing_zip(settings, rcept_no)
except Exception as e:
    mark_parse_failure(
        conn,
        rcept_no=rcept_no,
        parser_version="v3",
        error=str(e),
        parsed_at="2026-05-14T03:00:00Z",
    )
    conn.commit()
```

### 다운로드 대상 조회

```python
from dart_kam.repository import select_filings_for_download

# 아직 다운로드 안 된 최근 100건
targets = select_filings_for_download(conn, skip_downloaded=True, limit=100)
print(targets[:3])
# ['20260331002433', '20260331003230', ...]
```

### AE00024 캐시 적재

```python
from dart_kam.repository import upsert_ae00024

upsert_ae00024(
    conn,
    corp_code="00126308",
    bsns_year="2025",
    reprt_code="11011",
    status="000",
    payload={"status": "000", "list": [...]},
    message="정상",
    fetched_at="2026-05-14T03:00:00Z",
)
```

## FAQ

**Q. 왜 INSERT 만 하는 단순 함수도 `*_SQL` 상수로 빼나요?**
A. SQL 본문 자체가 변경되어야 할 때(컬럼 추가 등) 검색·변경 지점이 한 곳이라는 이점이 큽니다. 또한 동일 SQL 을 호출부 두 곳에서 반복하지 않게 됩니다 (성공/실패 분기 등).

**Q. `replace_kam_items` 가 DELETE → INSERT 인데, INSERT OR REPLACE 가 안 되나요?**
A. `kam_items` 의 PK 는 자동증가 `id` 이고 UNIQUE 는 `(rcept_no, ordinal)` 입니다. 파싱이 다시 돌 때 *항목 개수가 줄 수도* 있어서, "기존을 전부 지우고 새로 채우는" 시멘틱이 안전합니다.

**Q. `upsert_parse_result` 가 받는 `result` dict 의 스키마는 어디 정의돼 있나요?**
A. `dart_kam.audit_extractors.analyze_audit_text()` 가 반환하는 dict 와 동일합니다. 키는 `parse_results` 컬럼명과 1:1 매칭됩니다.

**Q. `upsert_ae00024` 가 `payload=None` 이면 어떻게 되나요?**
A. `payload_json` 컬럼이 NULL 로 저장됩니다. `status='error'` 등 실패 경로용. 또한 `status != '000'` 일 때도 페이로드를 저장하지 않습니다 (오류 응답은 의미없는 빈 리스트 등을 담고 있어 저장가치가 낮음).

**Q. 트랜잭션은 어디서 commit 되나요?**
A. 이 모듈은 commit 하지 않습니다. 서비스 레이어(`*_service.py`, `parse_audit.py`)가 단건 처리 후 명시적으로 `conn.commit()` 을 호출합니다. 이렇게 하면 배치 중간 실패 시에도 직전 성공 분이 보존됩니다.
