# `progress_util.py` — 한국어 진행 메시지

## 개요

배치 단계(다운로드/파싱/조회)의 진행 상황을 터미널에 한국어로 표시하는 유틸. Windows cp949 콘솔에서 자주 인코딩 에러를 내던 em-dash(`—`)와 ellipsis(`…`)를 ASCII로 자동 치환합니다.

이전 버전에서 4개 서비스가 각자 비슷한 진행 메시지 포맷 문자열을 직접 만들던 것을 `BatchProgress` 한 클래스로 통합했습니다.

## 구성 요소

| 심볼 | 종류 | 역할 |
|---|---|---|
| `PROGRESS_PREFIX = "[진행]"` | 상수 | 모든 진행 메시지 접두사 |
| `progress_print(msg, *, enabled)` | 함수 | `[진행] msg` 한 줄을 즉시 flush 출력 |
| `BatchProgress` | 데이터클래스 | "총 N건 중 i번째 …" 패턴을 일관되게 처리 |
| `_to_console_safe(msg)` | 내부 | em-dash/ellipsis 치환 |
| `_SAFE_CHAR_MAP` | 상수 | (원문자, 치환문자) 매핑 테이블 |

### `BatchProgress` 메서드

| 메서드 | 출력 예 |
|---|---|
| `.start(extra="...")` | `[진행] ZIP 파싱 - 대상 총 13건 (다운로드 완료...)` |
| `.tick(idx, detail="...")` | `[진행] 총 13건 중 1번째 처리 중 (남음 12건) - rcept_no=...` |
| `.done(idx, ok, bad, note="...")` | `[진행] ZIP 파싱 완료 1/13 - 성공 누적 1건, 실패 0건 - 의견=적정의견` |
| `.fail(idx, ok, bad, error="...")` | `[진행] ZIP 파싱 실패 3/13 - 성공 누적 2건, 실패 누적 1건 - 20260...` |
| `.finish(ok, bad)` | `[진행] ZIP 파싱 단계 종료 - 성공 11건, 실패 2건 (대상 13건)` |

## 예제

### 단순 한 줄 출력

```python
from dart_kam.progress_util import progress_print

progress_print("작업 시작")
progress_print("디버그 정보", enabled=False)  # 무시됨
```

### 배치 진행 표시

```python
from dart_kam.progress_util import BatchProgress

items = [1, 2, 3, 4, 5]
bp = BatchProgress(label="데이터 처리", total=len(items), enabled=True)
bp.start()

ok = bad = 0
for idx, item in enumerate(items, start=1):
    bp.tick(idx, detail=f"item={item}")
    try:
        # ... 처리 ...
        ok += 1
        bp.done(idx, ok=ok, bad=bad, note=f"value={item*2}")
    except Exception as e:
        bad += 1
        bp.fail(idx, ok=ok, bad=bad, error=str(e))

bp.finish(ok=ok, bad=bad)
```

### `--quiet` 플래그 연계

```python
bp = BatchProgress(label="조용 모드", total=10, enabled=not args.quiet)
```

## FAQ

**Q. 콘솔에 한글이 깨집니다.**
A. PowerShell에서 `chcp 65001` 또는 `$env:PYTHONUTF8='1'` 을 먼저 실행하세요. `progress_util` 은 em-dash/ellipsis 만 치환하며, 한글은 콘솔 코드페이지 자체가 UTF-8이어야 정상 표시됩니다.

**Q. 다른 특수문자도 치환하고 싶어요.**
A. `_SAFE_CHAR_MAP` 상수에 `(원문자, 치환문자)` 튜플을 추가하세요. 단, 이 상수는 모듈 private입니다.

**Q. 메시지를 파일에도 기록하고 싶어요.**
A. 현재 `progress_print` 는 stdout 만 출력합니다. 파일 로깅이 필요하면 `logging.basicConfig(...)` 후 `progress_print` 대신 logger를 쓰는 것이 깔끔합니다. 또는 `python -m dart_kam ... 2>&1 | Tee-Object -FilePath log.txt` 로 외부에서 캡처해도 됩니다.

**Q. `BatchProgress.start()` 가 빈 배치에서 두 줄을 출력해요.**
A. 의도된 동작입니다 — "대상 총 0건" + "대상이 없습니다." 두 줄로 사용자에게 명확히 알립니다.
