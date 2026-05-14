# `audit_text.py` — ZIP → 평문 텍스트

## 개요

저장된 공시 원본 ZIP에서 "본문에 가장 가까운" XML 한 개를 골라 태그를 모두 제거한 평문 한 덩어리로 변환합니다.

DART 가 내려주는 ZIP은 여러 XML(주재무제표·연결재무제표·보고서 본문 등)을 포함하므로, 단순히 첫 번째 파일을 읽으면 안 됩니다. *키워드 스코어링*으로 본문 후보를 골라냅니다.

## 본문 선정 로직

각 XML 파일에 대해 다음 점수를 매겨 최댓값을 선택:

```
score = 파일크기(byte) + (포함된 키워드 수 × 1,000,000)
```

키워드(`_BODY_KEYWORDS`):

- "핵심감사사항"
- "핵심 감사사항"
- "Key Audit Matters"
- "감사의견"
- "독립된 감사인"

대부분의 경우 키워드 보너스가 압도적이라 본문 XML 이 확실히 선택됩니다.

## 구성 요소

| 심볼 | 역할 |
|---|---|
| `load_filing_flat_text(settings, rcept_no)` | 공개 진입점. 단일 rcept_no → 평문 문자열 |
| `pick_main_xml(zf)` | ZIP 안에서 본문 XML 선택 |
| `_flatten_xml_text(xml_bytes)` | XML → 평문 (모든 `text`/`tail` 합치기) |
| `_score_xml_candidate(raw)` | 본문 후보 점수 계산 |
| `_BODY_KEYWORDS` / `_KEYWORD_BONUS` | 스코어 보너스 키워드와 가중치 |

## 예제

### 기본 사용

```python
from dart_kam.config import load_settings
from dart_kam.audit_text import load_filing_flat_text

settings = load_settings()
flat = load_filing_flat_text(settings, "20260331002433")
print(f"length: {len(flat):,}")
print(flat[:500])
```

### ZIP 내 본문 직접 선택 (저수준)

```python
import io, zipfile
from dart_kam.audit_text import pick_main_xml
from dart_kam.paths import raw_zip_path

path = raw_zip_path(settings, "20260331002433")
with zipfile.ZipFile(io.BytesIO(path.read_bytes())) as zf:
    xml_bytes = pick_main_xml(zf)
    print(f"main XML: {len(xml_bytes):,} bytes")
```

## FAQ

**Q. 본문이 아니라 표지 XML 이 선택됩니다.**
A. 표지에 우연히 키워드가 많이 들어간 경우입니다. `_BODY_KEYWORDS` 를 더 구체적인 본문 신호("재무제표에 대한 경영자의 책임" 등)로 추가하면 변별력이 올라갑니다.

**Q. lxml 의 `huge_tree=True` 가 필요한 이유는?**
A. DART XML 은 단일 요소 깊이가 수천에 달하는 경우가 있어, 기본 안전 제한을 풀어야 합니다. `recover=True` 도 함께 켜서 깨진 XML도 가능한 한 파싱합니다.

**Q. 메모리 사용량이 큰가요?**
A. ZIP 한 개당 일반적으로 1~5MB이며, 평문 변환 후 약 1~3MB의 문자열이 생성됩니다. 동시에 메모리에 올라가는 양은 한 건 분량이므로 큰 문제 없음.

**Q. text/tail 만 합치면 누락되는 정보가 있지 않나요?**
A. DART XML은 본문이 거의 모두 element text 로 들어 있어, attribute 값을 합치지 않아도 감사보고서 분석에는 충분합니다. 만약 attribute에 의미 있는 데이터가 있다면 `_flatten_xml_text` 에서 `element.attrib` 도 합치도록 확장하세요.

**Q. ZIP 안에 XML 이 없으면?**
A. `pick_main_xml` 이 `ValueError("ZIP contains no XML files")` 를 던집니다. `parse_audit.ingest_parse_results` 가 이 예외를 잡아 `parse_error` 컬럼에 기록합니다.
