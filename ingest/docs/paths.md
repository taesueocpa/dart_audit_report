# `paths.py` — 데이터 경로 헬퍼

## 개요

모든 파일 저장 위치(DB, corpCode ZIP, 공시 원본 ZIP)를 한 함수씩으로 정의. 모든 경로는 `Settings.data_dir`(기본 `<repo>/data`) 하위.

`Settings` 가 바뀌면 경로도 따라 바뀌므로, 테스트나 멀티 데이터셋 분리 시 `DART_KAM_DATA_DIR` 환경변수만 갈아끼우면 됩니다.

## 구성 요소

| 함수 | 반환 경로 | 비고 |
|---|---|---|
| `data_root(settings)` | `<data_dir>` (resolve된 절대경로) | 부모 디렉터리 |
| `db_path(settings)` | `<data_dir>/dart_kam.sqlite3` | SQLite 단일 파일 |
| `corp_zip_path(settings)` | `<data_dir>/corpCode.zip` | corpCode.xml 다운로드 결과 (백업용) |
| `raw_zip_path(settings, rcept_no)` | `<data_dir>/raw_zips/<rcept_no>.zip` | 공시 원본 ZIP |

## 예제

```python
from dart_kam.config import load_settings
from dart_kam.paths import db_path, raw_zip_path

settings = load_settings()
print(db_path(settings))
# → C:\...\Dart_KAM_dashboard\data\dart_kam.sqlite3

print(raw_zip_path(settings, "20260331002433"))
# → C:\...\Dart_KAM_dashboard\data\raw_zips\20260331002433.zip
```

## FAQ

**Q. 디렉터리를 미리 만들지 않아도 되나요?**
A. 부모 디렉터리 생성은 *경로를 사용하는 쪽*이 책임집니다 (`db.connect`, `document_service` 등). `paths.py` 함수들은 단순히 `Path` 객체만 반환합니다.

**Q. `raw_zips/` 하위에 너무 많은 파일이 쌓이면?**
A. 현재 구조는 평면(flat)입니다. 수십만 건 규모로 커지면 `raw_zips/{YYYY}/{YYYYMMDD}/...` 같은 계층화가 필요할 수 있습니다. 이 경우 `raw_zip_path` 만 수정하면 전체 파이프라인이 따라옵니다.

**Q. 데이터 디렉터리를 OneDrive 밖으로 옮기고 싶어요.**
A. `$env:DART_KAM_DATA_DIR = "D:\dart_kam_data"` 처럼 환경변수만 설정하면 됩니다. 기존 파일은 직접 옮겨주세요.
