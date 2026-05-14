# `dart_client.py` — OPENDART HTTP 클라이언트

## 개요

OPENDART REST API 호출을 감싸는 얇은 클라이언트. 모든 요청에 자동으로:

- `crtfc_key` 인증 파라미터 주입
- 재시도(`max_retries`회) — 네트워크 예외 + OPENDART status `020`(rate limit)
- 지수 백오프 (네트워크 30초 cap, rate-limit 60초 cap)
- 글로벌 sleep (`request_sleep_sec`, OPENDART 부담 완화)
- 옵션 한 줄 로그 (`http_log=True` 또는 `--verbose-http`)

이전 버전에서 `get_json` 과 `get_bytes` 가 각자 50줄짜리 재시도 루프를 가지고 있던 것을, 단일 `_request_with_retry(..., handle)` 헬퍼로 통합했습니다.

## 구성 요소

| 심볼 | 역할 |
|---|---|
| `DartClient(settings)` | 클라이언트 생성. `with` 컨텍스트 매니저 지원 |
| `DartClient.close()` | 내부 `httpx.Client` 닫기 |
| `DartClient.get_json(path, params)` | JSON 응답을 `dict` 로 |
| `DartClient.get_bytes(path, params)` | raw bytes (ZIP 등) |
| `DartClient._request_with_retry(...)` | 내부 — 재시도/로깅/백오프 공통 루프 |
| `RetrySignal` | 내부 예외 — "재시도 가능한 응답" 신호 |
| `_STATUS_RATE_LIMIT = "020"` | OPENDART rate-limit 상태 코드 |

## 동작 흐름

```
get_json/get_bytes
        │
        ▼
_request_with_retry (max_retries 회 반복)
        │
        ├─ sleep(request_sleep_sec)
        ├─ httpx.get(url, params={crtfc_key=…, **params})
        ├─ raise_for_status()
        ├─ handle(response) ─► (result, log_extra)
        │       │
        │       ├─ 성공 ─► 결과 반환
        │       └─ status == "020" ─► RetrySignal 발생 → 백오프(최대 60초) 후 retry
        │
        └─ 네트워크 예외 ─► 백오프(최대 30초) 후 retry

max_retries 모두 실패 ─► RuntimeError 발생 (last_err을 cause로 체이닝)
```

## 예제

### JSON API

```python
from dart_kam.config import load_settings
from dart_kam.dart_client import DartClient

settings = load_settings()
with DartClient(settings) as client:
    data = client.get_json("list.json", {
        "bgn_de": "20250101",
        "end_de": "20250331",
        "page_no": "1",
        "page_count": "100",
    })
    print(data["status"], data.get("total_count"))
```

### 바이너리 (ZIP)

```python
with DartClient(settings) as client:
    zip_bytes = client.get_bytes("corpCode.xml", {})
    print(f"received {len(zip_bytes):,} bytes")
```

### HTTP 로그 켜기

```python
import os
os.environ["DART_HTTP_LOG"] = "1"
settings = load_settings()
with DartClient(settings) as client:
    client.get_json("list.json", {"bgn_de": "20250101", "end_de": "20250105"})
```

출력 예:

```
[OPENDART] GET list.json http=200 312ms api=000 params={'crtfc_key': '***', 'bgn_de': '20250101', ...}
```

### 타임아웃 오버라이드

```python
# 공시 원본 ZIP은 크므로 timeout 을 늘림
zip_bytes = client.get_bytes(
    "document.xml",
    {"rcept_no": "20260331002433"},
    timeout=300.0,
)
```

## FAQ

**Q. `crtfc_key` 가 로그에 노출되나요?**
A. 아니요. `_mask_params` 가 항상 `***` 로 마스킹합니다.

**Q. 재시도 횟수를 늘리고 싶어요.**
A. `DART_MAX_RETRIES=10` 환경변수로 설정하세요. 다만 OPENDART 정책상 분당 호출량이 초과되면 그 분 안에는 어떤 재시도도 통과하지 않으므로, 보통 `request_sleep_sec` 을 늘리는 게 더 효과적입니다.

**Q. 401/403 같은 인증 에러도 재시도되나요?**
A. 네, 현재 모든 HTTPError를 재시도합니다 (`max_retries` 만큼). 키가 잘못된 경우 빠르게 실패하길 원하면 `Settings.require_key()` 를 호출부에서 먼저 부르세요 (`DartClient._request_with_retry` 도 매 호출마다 이걸 확인합니다).

**Q. 비동기(async) 지원되나요?**
A. 현재는 동기 `httpx.Client` 만 씁니다. OPENDART는 분당 호출량 제약이 강해서 동시성으로 얻는 이득이 크지 않습니다. 필요하면 `httpx.AsyncClient` 로 갈아끼우면 됩니다 (`_request_with_retry` 시그니처만 async로 바꾸면 됨).

**Q. `RetrySignal` 은 직접 throw 해도 되나요?**
A. 사용자 코드에서 직접 던질 일은 없습니다. 내부 `handle` 콜백이 응답을 검사한 후 "재시도하라"고 알리는 용도로만 씁니다. 외부에 노출은 되어 있지만 안정 API가 아닙니다.
