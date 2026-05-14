# `config.py` — 런타임 설정 / `.env` 로딩

## 개요

`dart_kam` 패키지의 모든 런타임 옵션을 한곳에 모은 모듈. 환경변수 → `.env` 파일 → 기본값 순으로 우선순위가 결정됩니다.

핵심 진입점은 `load_settings()`. 호출 시점에:

1. 프로젝트 루트와 CWD의 `.env` 파일을 머지하여 프로세스 환경에 주입 (이미 비어있지 않은 변수는 보존).
2. `Settings` 데이터클래스를 만들어 반환.

## 구성 요소

| 심볼 | 종류 | 역할 |
|---|---|---|
| `load_settings()` | 함수 | 공식 진입점. `.env` 로드 후 `Settings` 생성 |
| `Settings` | 데이터클래스 | 모든 설정 값을 보관 |
| `Settings.require_key()` | 메서드 | `DART_API_KEY` 누락 시 한국어 안내와 함께 `RuntimeError` |
| `Settings.default_start_date()` / `default_end_date()` | 메서드 | "최근 N년" 기본 기간 계산 |
| `_parse_dotenv(text)` | 내부 | `KEY=VALUE` 줄 단위 파싱 (주석/`export`/따옴표 처리) |
| `_load_dotenv_files()` | 내부 | repo 루트 + CWD `.env` 머지 |
| `_env`, `_env_cast`, `_env_bool`, `_env_csv` | 내부 | 환경변수 → 타입 캐스팅 헬퍼 |

## `Settings` 필드 상세

| 필드 | 환경변수 | 기본값 |
|---|---|---|
| `dart_api_key` | `DART_API_KEY` | `""` |
| `base_url` | `DART_API_BASE` | `https://opendart.fss.or.kr/api` |
| `data_dir` | `DART_KAM_DATA_DIR` | `<repo>/data` |
| `request_sleep_sec` | `DART_REQUEST_SLEEP` | `0.12` |
| `max_retries` | `DART_MAX_RETRIES` | `5` |
| `list_chunk_days` | `DART_LIST_CHUNK_DAYS` | `90` |
| `years_back` | `DART_YEARS_BACK` | `3` |
| `pblntf_ty` | `DART_PBLNTF_TY` | `F` |
| `pblntf_detail_types` | `DART_PBLNTF_DETAIL` | `("F001", "F002", "F003")` |
| `last_reprt_at` | `DART_LAST_REPRT_AT` | `Y` |
| `corp_cls` | `DART_CORP_CLS` | `None` |
| `parser_version` | `DART_PARSER_VERSION` | `v3` |
| `http_log` | `DART_HTTP_LOG` | `False` |

> **연도 정의**: `years_back` 의 기준이 되는 "연도"는 공시 *접수일*(`rcept_dt` 첫 4자리). list 인제스트 이후 `export_dashboard` 단계에서 `filing_year` 컬럼으로 노출.

## 예제

### 기본 사용

```python
from dart_kam.config import load_settings

settings = load_settings()
print(settings.data_dir)
print(settings.pblntf_detail_types)  # ('F001', 'F002', 'F003')
```

### 인증키 체크

```python
settings = load_settings()
settings.require_key()  # 키 누락 시 한국어 안내와 함께 RuntimeError 발생
```

### `.env` 파일 예시

`<repo>/.env`:

```ini
DART_API_KEY=abcdef...
DART_REQUEST_SLEEP=0.2
DART_YEARS_BACK=5
DART_HTTP_LOG=1
# 한정 필터(Y=KOSPI, K=KOSDAQ, E=ETC, N=비상장)
DART_CORP_CLS=Y
```

### 코드에서 임시 오버라이드

```python
import os
os.environ["DART_YEARS_BACK"] = "1"
from dart_kam.config import load_settings
settings = load_settings()
assert settings.years_back == 1
```

## FAQ

**Q. `.env`에 값이 있는데 적용이 안 됩니다.**
A. *이미 설정된* 환경변수는 `.env`가 덮어쓰지 않습니다. 우선순위는 **프로세스 env > repo `.env` > CWD `.env` > 기본값**. PowerShell에서 `$env:DART_API_KEY` 값을 먼저 확인하세요.

**Q. `.env` 의 BOM 때문에 첫 줄이 안 읽힙니다.**
A. `_load_dotenv_files()` 가 UTF-8 BOM(`\ufeff`)을 자동 제거합니다. 그래도 안 되면 노트패드++ 등에서 BOM 없는 UTF-8로 다시 저장하세요.

**Q. `DART_END_DATE` 는 어디서 쓰나요?**
A. `Settings.default_end_date()` 가 읽습니다. `YYYYMMDD` 8자리 숫자만 인식. 테스트나 백필 시 "오늘이 아닌 어떤 시점" 기준으로 회귀 조회할 때 유용합니다.

**Q. `DART_CORP_CLS` 를 비우려면?**
A. 환경변수에서 키 자체를 지우면 됩니다. 빈 문자열도 `None` 으로 취급되어 list.json 호출 시 `corp_cls` 파라미터가 빠집니다 (전체 시장 조회).

**Q. `_env_truthy` 가 없어졌습니다.**
A. `_env_bool` 로 이름이 바뀌었습니다. 같은 기능. 외부에서는 import 하지 마세요 (모듈 private).
