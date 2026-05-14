# `document_service.py` — 공시 원본 ZIP 다운로드

## 개요

각 공시 한 건의 원본 ZIP을 `data/raw_zips/<rcept_no>.zip` 에 저장하고, 결과(성공/실패)를 `document_fetch` 테이블에 기록합니다.

OPENDART `document.xml` 엔드포인트의 응답이 4가지 형태로 다양해서, 이 모듈이 그 다양성을 흡수합니다.

## 응답 페이로드 4가지 변형 (우선순위)

1. **ZIP 바이트** — prefix가 `PK` 인 경우, 그대로 저장 (가장 흔함).
2. **JSON 응답** — XML 엔드포인트인데 JSON으로 응답 (`{` / `[` 시작). JSON 파싱 후 `document` 키의 base64 디코드.
3. **XML 내 `<document>` base64** — 위 2가지 실패 시 XML 트리 순회.
4. **`document.json` 폴백** — 위 모두 실패 시 별도 엔드포인트 호출.

위 단계 중 *어느 한 곳에서 유효한 ZIP 바이트*를 얻으면 즉시 디스크에 기록 후 반환합니다.

## 구성 요소

| 심볼 | 역할 |
|---|---|
| `fetch_document_zip(client, settings, rcept_no)` | 단건 다운로드 (페이로드 4가지 자동 판별) |
| `ingest_documents(client, settings, conn, *, limit, skip_downloaded, verbose, rcept_nos, progress)` | 배치 다운로드 + `document_fetch` 기록 |
| `_try_extract_zip_or_b64_from_json(raw, path)` | 변형 #2 처리 |
| `_try_extract_b64_from_xml_tree(raw, path)` | 변형 #3 처리 |
| `_fetch_via_document_json(client, rno, path, xml_prefix)` | 변형 #4 처리 |
| `_validate_rcept_no(rcept_no)` | 14자리 숫자 검증 |
| `_validate_zip(data)` / `_write_zip_bytes(path, data)` | ZIP magic 검증 + 디스크 쓰기 |
| `_write_zip_from_b64(path, b64)` | base64 디코드 후 ZIP 저장 |
| `_select_targets(...)` | rcept_nos 인자 vs DB 조회 분기 |

## 예제

### CLI — 최근 100건만 다운로드

```powershell
python -m dart_kam documents --limit 100
```

### CLI — 이미 다운로드된 것도 강제 재다운로드

```powershell
python -m dart_kam documents --all --limit 50 --verbose
```

### CLI — 특정 접수번호 지정 (현재 미지원, 라이브러리만)

```python
from dart_kam.document_service import ingest_documents

with DartClient(settings) as client:
    ok, bad = ingest_documents(
        client, settings, conn,
        rcept_nos=["20260331002433", "20260331003230"],
    )
```

### 단건 다운로드 (배치 우회)

```python
from dart_kam.document_service import fetch_document_zip

with DartClient(settings) as client:
    zip_path = fetch_document_zip(client, settings, "20260331002433")
    print(zip_path)
    # → C:\...\data\raw_zips\20260331002433.zip
```

## FAQ

**Q. `--limit 100` 인데 50건만 받고 종료됩니다.**
A. `skip_downloaded=True` (기본)이므로 이미 다운로드된 건은 카운트에 들어가지 않습니다. 강제로 100건을 다시 받으려면 `--all` 을 추가하세요.

**Q. 종료 코드가 항상 0 이에요. 실패가 있어도?**
A. 의도된 동작입니다. 부분 실패가 있더라도 다음 파이프라인 단계(parse/export)가 계속 진행될 수 있게 0을 반환합니다. 실패 건수는 stdout 마지막 줄에 `failed: N` 으로 표시됩니다.

**Q. ZIP magic 검증이 실패한다는 에러가 나옵니다.**
A. OPENDART가 일시적으로 HTML 에러 페이지를 응답한 경우입니다. 자동으로 재시도되며, 모든 변형(4단계)이 다 실패해야 최종 실패로 기록됩니다. 해당 `rcept_no` 의 raw 응답 prefix가 `document_fetch.error` 컬럼에 남으니 확인할 수 있습니다.

**Q. base64 디코드 실패 사례는 어떻게 디버깅하나요?**
A. `--verbose` 플래그를 켜면 종료 시 첫 15건 실패 메시지를 stdout에 추가 출력합니다. 또한 `--verbose-http` 로 OPENDART 응답 prefix를 확인할 수 있습니다.

**Q. 큰 ZIP 다운로드 시 timeout 이 부족하지 않나요?**
A. document API는 일반 호출보다 timeout을 300초로 늘려 잡습니다 (`_DOCUMENT_TIMEOUT_SEC`). 그래도 모자라면 코드 상수를 조정하세요.

**Q. `raw_zips/` 가 너무 커집니다. 삭제해도 되나요?**
A. 파싱이 끝난 ZIP은 안전하게 삭제 가능합니다. 단 `document_fetch.zip_path` 와 일관성이 깨지므로, 삭제 시 `UPDATE document_fetch SET zip_path=NULL` 도 함께 하세요. 그 후 재파싱이 필요하면 `--all` 로 다시 받으세요.
