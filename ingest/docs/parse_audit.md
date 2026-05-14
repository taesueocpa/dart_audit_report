# `parse_audit.py` — 파싱 파이프라인 + DB 저장

## 개요

`audit_text` + `audit_extractors` + `repository` 를 묶어 "공시 ZIP → DB" 파이프라인을 실행합니다. 이 모듈 자체에는 정규식이나 SQL 이 거의 없고, 오케스트레이션만 담당합니다.

이전 버전에서 이 파일에 모두 들어있던 **정규식 추출 함수들**은 [`audit_extractors.py`](./audit_extractors.md) 로, **ZIP 로딩**은 [`audit_text.py`](./audit_text.md) 로, **DB 쓰기**는 [`repository.py`](./repository.md) 로 분리되었습니다.

하위 호환을 위해 추출 함수들은 이 모듈에서도 동일 이름으로 다시 노출됩니다 (re-export).

## 구성 요소

| 심볼 | 종류 | 역할 |
|---|---|---|
| `parse_filing_zip(settings, rcept_no)` | 함수 | 단건: ZIP → 분석 dict |
| `ingest_parse_results(settings, conn, *, limit, force, progress)` | 함수 | 배치: DB 대상 → 분석 → 저장 |
| `_kam_items_for(rcept_no, kam_full)` | 내부 | KAM 절을 `kam_items` 행 dict 리스트로 변환 |
| `_KAM_SNIPPET_LIMIT = 800` | 상수 | `kam_items.body_snippet` 컬럼 길이 |
| **re-export** | — | `analyze_audit_text`, `classify_opinion`, `extract_*`, `load_filing_flat_text` |

## 파이프라인 흐름

```
ingest_parse_results
        │
        ▼
select_filings_for_parse(conn, force=...)
        │
        ▼   (rcept_no 목록)
for rcept_no in targets:
    │
    ├─ parse_filing_zip(settings, rcept_no)
    │       └─ load_filing_flat_text ─► analyze_audit_text
    │
    ├─ 성공: replace_kam_items + upsert_parse_result
    └─ 실패: mark_parse_failure
    │
    └─ conn.commit()
```

## 예제

### CLI

```powershell
# 미파싱 또는 실패한 ZIP만
python -m dart_kam parse

# 최근 50건만, 이미 성공한 것도 다시 파싱
python -m dart_kam parse --limit 50 --force

# 진행 메시지 숨기고 마지막 요약만
python -m dart_kam parse --quiet
```

### 단건 파싱

```python
from dart_kam.config import load_settings
from dart_kam.parse_audit import parse_filing_zip

settings = load_settings()
result = parse_filing_zip(settings, "20260331002433")
print(result["opinion_label"])      # '적정의견'
print(result["accounting_standard"])  # '한국채택국제회계기준'
print(bool(result["kam_section_full"]))  # True/False
```

### 배치 (라이브러리)

```python
from dart_kam.config import load_settings
from dart_kam.db import connect, init_db
from dart_kam.parse_audit import ingest_parse_results
from dart_kam.paths import db_path

settings = load_settings()
conn = connect(db_path(settings))
init_db(conn)
ok, bad = ingest_parse_results(settings, conn, limit=100, force=False)
print(f"OK={ok}, FAIL={bad}")
conn.close()
```

### 하위 호환 import

```python
# 이전 코드와 그대로 호환
from dart_kam.parse_audit import (
    classify_opinion,
    extract_standalone_audit_report_body,
    extract_kam_section_full,
)
```

## FAQ

**Q. `force` 와 기본 동작 차이는?**
A. `force=False` (기본) — `parse_results` 가 없거나 `parse_error IS NOT NULL` 인 행만 처리. `force=True` — 모든 다운로드 완료 건을 재파싱 (개발 중 파서 버전을 올렸을 때 유용).

**Q. 단건이 실패해도 배치가 멈추지 않나요?**
A. 네. 각 rcept_no 처리 후 `commit()` 하고 예외는 잡아서 `parse_error` 컬럼에 저장합니다. 다음 건은 계속 처리.

**Q. KAM 절이 발견되었는데 `kam_count` 가 1인 이유?**
A. 현재 파서는 KAM 절 *존재 여부* 만 0/1로 표현합니다. 실제 항목 개수 카운팅은 미구현 — [audit_extractors 확장 아이디어](./audit_extractors.md#확장-아이디어) 참고.

**Q. `parser_version` 컬럼은 어디서 채워지나요?**
A. `Settings.parser_version` (기본 `"v3"`, `DART_PARSER_VERSION` 환경변수로 오버라이드 가능). 파서 휴리스틱을 크게 바꾸면 버전을 올린 뒤 `--force` 로 재파싱하면 됩니다.

**Q. ZIP 파일이 없는 경우는?**
A. `FileNotFoundError` 가 발생하고 그 메시지가 `parse_error` 컬럼에 저장됩니다. `documents` 단계를 먼저 실행해서 ZIP 을 받아주세요.

**Q. `kam_items` 테이블에 같은 rcept_no 가 여러 행 있을 수 있나요?**
A. 스키마상은 `(rcept_no, ordinal)` UNIQUE 라 가능합니다. 다만 현재 파서는 ordinal=1 한 행만 기록하므로 실질적으로는 1행. 향후 KAM 항목을 N개로 쪼개면 자연스럽게 N행이 됩니다.

**Q. 메모리 사용량은?**
A. 한 건당 평문 텍스트(약 1~3MB)와 정규식 매치 객체 정도. 동시에 한 건만 처리하므로 가벼움.
