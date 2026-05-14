# `corp_codes.py` — 법인코드 동기화

## 개요

OPENDART `corpCode.xml` 엔드포인트에서 전체 법인코드 목록을 받아 `companies` 테이블에 업서트합니다.

응답이 ZIP 안에 `CORPCODE.xml` 한 개로 오는 표준 케이스와, XML 본문이 그대로 내려오는 예외 케이스 모두를 처리합니다.

## 구성 요소

| 심볼 | 역할 |
|---|---|
| `download_corp_zip(client, settings)` | `corpCode.xml` raw 응답 bytes |
| `refresh_corp_codes(client, settings, conn, *, progress=True)` | 전체 파이프라인 (다운로드 → 파싱 → 업서트) |
| `_parse_corp_xml(raw)` | 내부 — ZIP 또는 XML 본문 둘 다 처리 |
| `_PROGRESS_BATCH_SIZE = 5000` | N건 단위로 진행 메시지 출력 |

## 동작 흐름

```
client.get_bytes("corpCode.xml") ─► raw bytes
                  │
                  ▼
        디스크에 corpCode.zip 백업
                  │
                  ▼
        _parse_corp_xml: ZIP인지 시도 → 실패 시 직접 XML 파싱
                  │
                  ▼
        <list> 요소 순회 → upsert_company(...)
                  │
                  └─ 5,000건마다 진행 메시지
```

## 예제

### CLI

```powershell
$env:DART_API_KEY = "..."
python -m dart_kam sync-corp
# 또는
python -m dart_kam sync-corp --verbose-http --quiet
```

### 라이브러리

```python
from dart_kam.config import load_settings
from dart_kam.corp_codes import refresh_corp_codes
from dart_kam.dart_client import DartClient
from dart_kam.db import connect, init_db
from dart_kam.paths import db_path

settings = load_settings()
settings.require_key()
conn = connect(db_path(settings))
init_db(conn)
with DartClient(settings) as client:
    n = refresh_corp_codes(client, settings, conn, progress=True)
print(f"upserted {n:,} companies")
conn.close()
```

### 결과 확인

```sql
SELECT corp_code, corp_name, stock_code, updated_at
FROM companies
WHERE stock_code != ''
LIMIT 10;
```

## FAQ

**Q. `corp_cls` (시장구분)가 항상 NULL 입니다.**
A. `corpCode.xml` 응답에는 `corp_cls` 가 없습니다. 이 컬럼은 `list_service.ingest_filings` 가 list.json 응답에서 채웁니다. 따라서 `companies.corp_cls` 는 `list` 단계 이후에 일부 법인에 한해 의미를 가집니다.

**Q. `stock_code` 가 빈 문자열인 법인이 많습니다.**
A. 비상장 법인은 `stock_code` 가 비어있는 게 정상입니다. 상장사만 보려면 `WHERE stock_code != ''` 로 필터링하세요.

**Q. 얼마나 자주 동기화해야 하나요?**
A. OPENDART는 신규 상장/상호 변경 시 업데이트되므로, 일주일~한 달에 한 번 정도면 충분합니다. 캐시 백업(`data/corpCode.zip`)도 함께 갱신됩니다.

**Q. 이미 다운로드된 `corpCode.zip` 을 재사용할 수 있나요?**
A. 현재 함수는 항상 API를 새로 호출합니다. 오프라인 모드가 필요하면 별도 함수가 필요합니다.

**Q. 파싱 도중 메모리가 많이 듭니다.**
A. 약 십만 행 규모이므로 `ElementTree.fromstring` 으로 메모리에 전부 로드합니다. 메모리 부족 시 `iterparse` 로 바꾸면 됩니다 (현재 코드 기준 충분히 빠르므로 미적용).
