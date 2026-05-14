# `db.py` — SQLite 스키마와 연결 헬퍼

## 개요

`dart_kam` 파이프라인 전체가 단일 SQLite 파일(`data/dart_kam.sqlite3`)을 공유 저장소로 씁니다. 이 모듈은:

- 스키마 정의(`SCHEMA` 상수, `CREATE TABLE IF NOT EXISTS`)
- 추가 컬럼 마이그레이션(`ALTER TABLE … ADD COLUMN`)
- 연결 헬퍼(잠금 경합에 강한 설정)
- `meta` 테이블 헬퍼

구체적인 INSERT/UPSERT SQL은 [`repository.py`](./repository.md)로 분리되어 있습니다.

## 테이블 목록

| 테이블 | 키 | 용도 |
|---|---|---|
| `meta` | `key` PK | 임의 키-값 메타 정보 (예: `scope.default_years_back`) |
| `companies` | `corp_code` PK | corpCode.xml 결과. 법인 코드 → 이름 매핑 |
| `filings` | `rcept_no` PK | list.json 결과. 공시 한 건 |
| `document_fetch` | `rcept_no` PK | 공시 원본 ZIP 다운로드 상태 |
| `parse_results` | `rcept_no` PK | 파싱 결과 17개 컬럼 (의견·KAM·EOM·…) |
| `kam_items` | `(rcept_no, ordinal)` UNIQUE | KAM 절 본문 (현재는 1행/공시) |
| `ae00024_cache` | `(corp_code, bsns_year, reprt_code)` UNIQUE | 구조화 API 응답 캐시 |

### 인덱스

- `idx_filings_corp` on `filings(corp_code)`
- `idx_filings_dt` on `filings(rcept_dt)`
- `idx_kam_rcept` on `kam_items(rcept_no)`

## 구성 요소

| 심볼 | 역할 |
|---|---|
| `SCHEMA` | `CREATE TABLE` 문 모음 (멱등) |
| `connect(db_path, *, timeout_sec=60.0)` | SQLite 연결. WAL + busy_timeout 60s |
| `init_db(conn)` | 스키마 + 마이그레이션 멱등 적용 |
| `ensure_parse_schema_migrations(conn)` | `parse_results` / `kam_items` 신규 컬럼 추가 |
| `set_meta(conn, key, value)` | `meta` 테이블 upsert |
| `_PARSE_RESULTS_NEW_COLUMNS` / `_KAM_ITEMS_NEW_COLUMNS` | 마이그레이션 대상 컬럼 목록 |
| `_add_missing_columns(conn, table, columns)` | `ALTER TABLE` 헬퍼 |

## 마이그레이션 정책

SQLite는 `ALTER TABLE` 가 매우 제한적이므로, 새 컬럼은 모두 nullable 로 추가합니다. 컬럼 *이름 변경*이나 *타입 변경*이 필요하면 별도 마이그레이션 코드를 추가해야 합니다 (현재는 추가만 지원).

신규 컬럼을 더하려면:

1. `db.py` 의 `SCHEMA` 안 `CREATE TABLE …` 정의에 컬럼 추가.
2. `_PARSE_RESULTS_NEW_COLUMNS` (또는 해당 테이블의 NEW_COLUMNS 튜플)에 `(이름, 타입선언)` 추가.
3. `init_db()` 호출만으로 기존 DB도 자동 반영.

## 예제

### 직접 연결

```python
from dart_kam.config import load_settings
from dart_kam.db import connect, init_db
from dart_kam.paths import db_path

settings = load_settings()
conn = connect(db_path(settings))
init_db(conn)
try:
    n = conn.execute("SELECT COUNT(*) FROM filings").fetchone()[0]
    print(f"filings: {n:,}")
finally:
    conn.close()
```

### 메타 정보 기록

```python
from dart_kam.db import set_meta

set_meta(conn, "last_run.timestamp", "2026-05-14T03:00:00Z")
set_meta(conn, "last_run.command", "documents --limit 100")
```

### 마이그레이션 확인

```python
cur = conn.execute("PRAGMA table_info(parse_results)")
print([row[1] for row in cur.fetchall()])
# ['rcept_no', 'parser_version', ..., 'kam_section_full', 'audit_report_body']
```

## FAQ

**Q. OneDrive 폴더에서 락이 자주 걸립니다.**
A. `connect()` 가 `journal_mode=WAL` + `busy_timeout=60000` 으로 자동 설정합니다. 그래도 동시 쓰기가 많다면 `DART_KAM_DATA_DIR` 를 로컬 디스크로 옮기세요.

**Q. WAL 모드가 켜져서 `.sqlite3-wal` / `.sqlite3-shm` 파일이 생깁니다.**
A. 정상입니다. WAL 모드는 동시 읽기/쓰기 성능에 유리합니다. 파일을 옮길 때는 세 파일 모두 함께 옮기거나, `conn.close()` 후 wal/shm 파일은 자동 정리됩니다.

**Q. 스키마를 깔끔히 새로 만들고 싶어요.**
A. `data/dart_kam.sqlite3` 파일 자체를 삭제 후 `python -m dart_kam init-db` 를 다시 실행하세요. WAL/shm 사이드카도 함께 지우세요.

**Q. SQL injection 위험은 없나요?**
A. `_add_missing_columns` 와 `_table_columns` 는 *모듈 내부에서만* 호출되며 컬럼 이름이 코드 상수로 고정되어 있습니다. 외부 입력을 절대 받지 마세요. 모든 사용자 입력은 `?` placeholder 로 처리됩니다 (`repository.py` 참고).

**Q. 트랜잭션 처리는 어떻게 하나요?**
A. 각 `repository.upsert_*` 함수는 `commit()` 을 하지 않습니다. 호출 측에서 적절한 시점에 `conn.commit()` 을 부르세요. 보통 각 서비스 (`*_service.py`, `parse_audit.py`) 가 단건 처리 후 commit 합니다.
