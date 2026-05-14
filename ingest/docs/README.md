# `dart_kam` 모듈 문서

OPENDART 공시 데이터를 가져와 감사보고서·핵심감사사항(KAM)을 파싱하고, Next.js 대시보드용 단일 JSON으로 내보내는 Python 파이프라인 패키지.

## 개요

`dart_kam` 패키지는 6단계 파이프라인으로 구성됩니다.

```
sync-corp ──▶ list ──▶ documents ──▶ parse ──▶ ae00024 ──▶ export-dashboard
            (filings)   (ZIP 저장)   (DB 적재)  (구조화 API)  (summary.json)
```

각 단계는 CLI 서브커맨드로 독립 실행 가능하며, SQLite 단일 파일(`data/dart_kam.sqlite3`)을 공유 저장소로 사용합니다.

## 빠른 시작

```powershell
# 0) 인증키 설정 (한 번만)
$env:DART_API_KEY = "발급받은_키"

# 1) DB 초기화
python -m dart_kam init-db

# 2) 법인코드 동기화
python -m dart_kam sync-corp

# 3) 최근 N년 공시목록 수집 (기본 3년)
python -m dart_kam list

# 4) 원본 ZIP 다운로드 (최근 100건)
python -m dart_kam documents --limit 100

# 5) 다운로드된 ZIP 파싱
python -m dart_kam parse

# 6) 대시보드용 JSON 내보내기
python -m dart_kam export-dashboard
```

## 모듈 맵

| 계층 | 파일 | 한 줄 설명 | 문서 |
|---|---|---|---|
| **설정** | `config.py` | `Settings`/`load_settings`, `.env` 로딩 | [config.md](./config.md) |
| **설정** | `paths.py` | DB·ZIP·corpCode 경로 헬퍼 | [paths.md](./paths.md) |
| **인프라** | `progress_util.py` | `[진행] …` 출력, `BatchProgress` | [progress_util.md](./progress_util.md) |
| **인프라** | `dart_client.py` | OPENDART HTTP 클라이언트 (재시도·로그) | [dart_client.md](./dart_client.md) |
| **저장** | `db.py` | SQLite 스키마·마이그레이션 | [db.md](./db.md) |
| **저장** | `repository.py` | 모든 UPSERT SQL 집중 | [repository.md](./repository.md) |
| **수집** | `corp_codes.py` | corpCode.xml → `companies` | [corp_codes.md](./corp_codes.md) |
| **수집** | `list_service.py` | list.json → `filings` (분할/페이지네이션) | [list_service.md](./list_service.md) |
| **수집** | `document_service.py` | document.xml/json → ZIP 파일 저장 | [document_service.md](./document_service.md) |
| **수집** | `ae00024.py` | accnutAdtorNmNdAdtOpinion 캐시 | [ae00024.md](./ae00024.md) |
| **파싱** | `audit_text.py` | ZIP → 평문 텍스트 | [audit_text.md](./audit_text.md) |
| **파싱** | `audit_extractors.py` | 순수 텍스트 추출기 (의견/KAM/EOM/…) | [audit_extractors.md](./audit_extractors.md) |
| **파싱** | `parse_audit.py` | 파이프라인 + DB 저장 | [parse_audit.md](./parse_audit.md) |
| **출력** | `export_dashboard.py` | summary.json 생성 | [export_dashboard.md](./export_dashboard.md) |
| **진입** | `cli.py` | argparse 기반 CLI | [cli.md](./cli.md) |

## 데이터 흐름

```
OPENDART REST API
    │
    ├─ corpCode.xml ──────▶ corp_codes.refresh_corp_codes  ──▶ companies
    │
    ├─ list.json ─────────▶ list_service.ingest_filings    ──▶ filings
    │
    ├─ document.xml/json ─▶ document_service.ingest_       ──▶ raw_zips/*.zip
    │                       documents                          + document_fetch
    │
    └─ accnutAdtorNm…     ─▶ ae00024.cache_ae00024_for_    ──▶ ae00024_cache
                            filings

ZIP files
    │
    └─▶ audit_text.load_filing_flat_text
            │
            └─▶ audit_extractors.analyze_audit_text
                    │
                    └─▶ parse_audit.ingest_parse_results
                            └─▶ parse_results + kam_items

SQLite (data/dart_kam.sqlite3)
    │
    └─▶ export_dashboard.export_dashboard
            └─▶ dashboard/public/data/summary.json
                    │
                    └─▶ Next.js 대시보드
```

## 의존성 및 환경

- Python 3.11+
- `httpx >= 0.27`, `lxml >= 5.0`
- SQLite 3 (Python 표준 라이브러리)
- OneDrive 같은 동기화 폴더에서도 잠금 경합 없이 동작하도록 `busy_timeout=60000ms` 설정

## 환경변수

| 이름 | 기본값 | 설명 |
|---|---|---|
| `DART_API_KEY` | (필수) | OPENDART 인증키 |
| `DART_API_BASE` | `https://opendart.fss.or.kr/api` | API 베이스 URL |
| `DART_KAM_DATA_DIR` | `<repo>/data` | DB·ZIP 저장 디렉터리 |
| `DART_REQUEST_SLEEP` | `0.12` | HTTP 호출 간 sleep (초) |
| `DART_MAX_RETRIES` | `5` | 재시도 횟수 |
| `DART_LIST_CHUNK_DAYS` | `90` | list.json 분할 기간 (≤ 90) |
| `DART_YEARS_BACK` | `3` | 기본 회귀 연수 |
| `DART_PBLNTF_TY` | `F` | 공시유형(F=정기공시) |
| `DART_PBLNTF_DETAIL` | `F001,F002,F003` | 상세공시유형 CSV |
| `DART_LAST_REPRT_AT` | `Y` | 최종보고서만(Y/N) |
| `DART_CORP_CLS` | `(미설정)` | 법인구분 필터(Y/K/N/E) |
| `DART_PARSER_VERSION` | `v3` | 파서 버전 태그 (DB 컬럼에 기록) |
| `DART_HTTP_LOG` | `(미설정)` | `1` 이면 HTTP 호출 한 줄 로그 |
| `DART_END_DATE` | `today` | 기본 종료일 (`YYYYMMDD`) |

## FAQ

**Q. 어떤 단계부터 다시 실행해야 하나요?**
A. SQLite는 모두 멱등(upsert)입니다. 어떤 단계든 다시 돌려도 안전합니다. `documents`는 기본적으로 이미 다운로드된 건을 스킵합니다(`--all`로 강제 재다운로드).

**Q. `parse_results.kam_count` 가 0/1 만 나옵니다. 왜인가요?**
A. 현재 파서는 KAM 절 *존재 여부*만 0/1로 표현합니다. 실제 항목 개수는 미구현. 확장 지점은 [audit_extractors.md](./audit_extractors.md#확장-아이디어) 참고.

**Q. Windows에서 한글이 깨집니다.**
A. PowerShell에서 `$env:PYTHONUTF8='1'` 을 먼저 실행하세요. `progress_util` 이 em-dash/ellipsis 를 ASCII로 치환하지만, 한글 자체는 콘솔 코드페이지(cp949)에서 깨질 수 있습니다.

**Q. OPENDART rate-limit (status 020) 에 걸리면요?**
A. `dart_client` 가 자동으로 지수 백오프(최대 60초)로 재시도합니다. `--verbose-http` 로 호출 로그를 확인할 수 있습니다.

**Q. SQLite 파일이 OneDrive에 있어서 잠금 경합이 무서워요.**
A. `db.connect()` 가 `busy_timeout=60000ms` + `journal_mode=WAL` 로 동작합니다. 그래도 동시 쓰기 충돌 가능성이 있다면 로컬 디스크 경로를 `DART_KAM_DATA_DIR` 로 지정하세요.

**Q. CLI를 호출하지 않고 라이브러리로 쓸 수 있나요?**
A. 네. 예시:

```python
from dart_kam.config import load_settings
from dart_kam.db import connect, init_db
from dart_kam.paths import db_path
from dart_kam.parse_audit import parse_filing_zip

settings = load_settings()
conn = connect(db_path(settings))
init_db(conn)
result = parse_filing_zip(settings, "20260331002433")
print(result["opinion_label"], result["kam_count"])
```

## 리팩토링 노트 (이전 버전과의 차이)

- `parse_audit.py` 가 **469줄 → 165줄**로 축소. 텍스트 추출 함수는 `audit_extractors.py` (순수 함수), ZIP 로딩은 `audit_text.py` 로 분리.
- 모든 UPSERT SQL이 `repository.py` 한 곳으로 집중. 성공/실패 분기마다 동일 SQL을 반복하던 80+줄 중복 제거.
- `dart_client.get_json` / `get_bytes` 가 단일 `_request_with_retry` 루프 공유.
- `cli.py` 가 `with_client` 컨텍스트 매니저로 보일러플레이트 제거. 이전 버전에서 누락되었던 `conn.close()` 자동 호출.
- 기존 외부 import 호환성: `from dart_kam.parse_audit import classify_opinion` 등은 그대로 동작 (re-export).
