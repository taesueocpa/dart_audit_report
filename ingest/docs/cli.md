# `cli.py` — 명령행 진입점 (`dart-kam` / `python -m dart_kam`)

## 개요

`argparse` 기반 CLI. 7개 서브커맨드로 파이프라인 각 단계를 독립 실행합니다.

이전 버전에서는 각 `_cmd_*` 함수마다 4줄짜리 보일러플레이트(`load_settings → require_key → connect → DartClient → close`)가 반복되었습니다. 그리고 `conn.close()` 가 빠져 있어 매 실행마다 SQLite 파일 핸들이 누수되는 잠재 이슈가 있었습니다. 이번 리팩토링에서 `with_client` / `_db_session` 컨텍스트 매니저로 통합했습니다.

## 서브커맨드

| 이름 | 인자 | 인증키 필요 |
|---|---|---|
| `init-db` | — | × |
| `sync-corp` | `--verbose-http`, `-q` | ○ |
| `list` | `--bgn-de YYYYMMDD`, `--end-de YYYYMMDD`, `--verbose-http`, `-q` | ○ |
| `documents` | `--limit N`, `--all`, `--verbose`/`-v`, `--verbose-http`, `-q` | ○ |
| `parse` | `--limit N`, `--force`, `-q` | × |
| `ae00024` | `--limit N`, `--verbose-http`, `-q` | ○ |
| `export-dashboard` | `--out PATH` | × |

## 공통 옵션

- `--verbose-http` — OPENDART 호출마다 한 줄 로그 (`crtfc_key` 마스킹). 또는 `DART_HTTP_LOG=1`.
- `-q` / `--quiet` — 진행 메시지 숨김. 마지막 요약만 출력.

## 구성 요소

| 심볼 | 역할 |
|---|---|
| `main(argv=None)` | 공식 진입점. `argv` 미지정 시 `sys.argv` 사용 |
| `_build_parser()` | 모든 서브커맨드를 등록한 `ArgumentParser` |
| `_with_client(args, *, require_key)` | `settings + conn + client` 컨텍스트 |
| `_db_session(*, require_key)` | `settings + conn` (HTTP 클라이언트 불필요한 명령용) |
| `_open_conn(settings)` | DB 연결 + 마이그레이션 + 메타 키 기록 |
| `_parse_yyyymmdd(s)` | `YYYYMMDD` → `date` 변환 |
| `_COMMANDS` | (이름, 도움말, setup_fn, handler) 튜플 모음 |
| `_cmd_init_db`, `_cmd_sync_corp`, ... `_cmd_export` | 각 서브커맨드 핸들러 |

## 동작 흐름 (예: `documents`)

```
python -m dart_kam documents --limit 100 --verbose-http
        │
        ▼
argparse → args.cmd == 'documents', args.func == _cmd_documents
        │
        ▼
_cmd_documents(args)
        │
        └─ with _with_client(args):
                _apply_verbose_http(args)         # DART_HTTP_LOG=1 설정
                _db_session(require_key=True)     # settings + conn 컨텍스트
                  ├─ load_settings()
                  ├─ settings.require_key()
                  ├─ _open_conn(settings)
                  │     ├─ connect(db_path)
                  │     ├─ init_db(conn)
                  │     └─ set_meta(...) × 3
                  └─ yield (settings, conn)
                DartClient(settings):
                  └─ yield (settings, conn, client)
                  │
                  └─ ingest_documents(client, settings, conn, limit=100, ...)
                  │
                  └─ DartClient 자동 close
                conn 자동 close
        │
        ▼
print("downloaded:", ok, "failed:", bad)
return 0
```

## 예제

### 셋업

```powershell
$env:DART_API_KEY = "발급받은_키"
python -m dart_kam init-db
```

### 풀 파이프라인 (한 번에)

```powershell
python -m dart_kam sync-corp
python -m dart_kam list
python -m dart_kam documents --limit 200
python -m dart_kam parse
python -m dart_kam ae00024 --limit 100
python -m dart_kam export-dashboard
```

### 조용히 자동화

```powershell
python -m dart_kam list -q
python -m dart_kam documents -q --limit 50
python -m dart_kam parse -q
python -m dart_kam export-dashboard
```

### Python 코드에서 CLI 호출

```python
from dart_kam.cli import main

exit_code = main(["documents", "--limit", "10", "--verbose-http"])
print(exit_code)
```

### 도움말

```powershell
python -m dart_kam --help
python -m dart_kam parse --help
```

## FAQ

**Q. `python -m dart_kam` 과 `python -m dart_kam.cli` 차이는?**
A. 동일합니다. `__main__.py` 가 `cli.main()` 을 호출합니다. 첫 번째 형태가 좀 더 짧고 권장.

**Q. `dart-kam` 명령어가 안 됩니다.**
A. `pip install -e ingest/` 로 패키지 설치 후 사용 가능합니다. 설치 없이도 `python -m dart_kam` 은 동작.

**Q. `documents` 가 실패해도 종료 코드가 0 인 이유?**
A. 의도된 동작입니다. 부분 실패가 있더라도 다음 파이프라인 단계(parse/export)가 자동으로 계속 진행될 수 있게 하기 위함. 실패 건수는 stdout 마지막 줄에 표시됩니다. *모든* 단계가 실패하면 stderr 에 예외가 찍히고 비정상 종료됩니다.

**Q. 서브커맨드를 추가하고 싶어요.**
A. 다음 3단계:
1. `_cmd_xxx(args: argparse.Namespace) -> int` 핸들러 작성
2. `_setup_xxx(p: ArgumentParser) -> None` 으로 인자 등록 (필요한 경우)
3. `_COMMANDS` 튜플에 한 줄 추가

자동으로 `main()` 에 노출됩니다.

**Q. 환경변수가 안 먹힙니다.**
A. 명령어 *실행 전* 같은 shell 세션에서 설정해야 합니다. PowerShell:
```powershell
$env:DART_API_KEY = "..."
python -m dart_kam list  # 이 시점에 환경변수 로드
```

**Q. `--bgn-de` 형식이 까다롭습니다.**
A. `YYYYMMDD` 8자리 숫자 고정입니다. 슬래시/하이픈은 불허. 잘못된 형식이면 argparse 에러로 즉시 실패.
