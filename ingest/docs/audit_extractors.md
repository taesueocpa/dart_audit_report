# `audit_extractors.py` — 감사보고서 텍스트 추출기 (순수 함수)

## 개요

평문화된 감사보고서 텍스트에서 핵심 정보를 정규식 기반 휴리스틱으로 뽑아내는 **순수 함수** 모음. 외부 의존성(파일/네트워크/DB)이 없어서 단위 테스트가 쉽습니다.

이전 버전에서는 이 함수들이 `parse_audit.py` 한 파일에 DB 쓰기 코드와 섞여 있었습니다.

## 추출 항목

| 함수 | 설명 | 반환 |
|---|---|---|
| `extract_standalone_audit_report_body(text)` | "독립된 감사인의 감사보고서" 본문 슬라이스 | `str \| None` |
| `classify_opinion(text)` | 감사의견 라벨 + 짧은 스니펫 | `(label, snippet)` 둘 다 옵션 |
| `extract_opinion_modification_reason(text, label)` | 비적정 의견의 사유 구간 | `str \| None` |
| `detect_accounting_standard(text)` | 회계기준 (한국채택국제회계기준/일반기업회계기준) | `str \| None` |
| `extract_auditor_firm(text)` | 감사인(회계법인) 명칭 | `str \| None` |
| `extract_cpa_partner(text)` | 업무수행 공인회계사 성명 | `str \| None` |
| `extract_emphasis_of_matter(text)` | 강조사항 본문 | `(present_bool, body \| None)` |
| `extract_other_matters(text)` | 기타사항 본문 | `(present_bool, body \| None)` |
| `extract_kam_section_full(text)` | 핵심감사사항 절 전체 본문 | `str \| None` |
| `analyze_audit_text(flat_text)` | 위 모두 한 번에 실행 → `parse_results` 컬럼 dict | `dict[str, Any]` |

## 의견 라벨 정의

| 라벨 | 정규식 (요약) |
|---|---|
| `부적정의견` | `부적정\s*의견` |
| `의견거절` | `의견\s*거절|거절\s*의견` |
| `한정의견` | `한정\s*의견` |
| `감사범위제한` | `감사의\s*범위가\s*한|감사\s*범위\s*제한` |
| `적정의견` | `적정\s*의견|공정하게\s*표시|재무제표는.*적정` |

**선택 정책**: 본문에 등장하는 라벨들 중 *가장 먼저 나오는* 것을 채택. 적정의견의 대안 패턴("공정하게 표시")이 본문 후반에 있을 수 있어 의도된 동작.

`NON_CLEAN_OPINIONS = {"한정의견", "부적정의견", "의견거절", "감사범위제한"}` — 이들만 사유 구간 추출 대상.

## 동작 방식 — 슬라이스 우선

`analyze_audit_text` 는 가능하면 "독립된 감사인의 감사보고서" 본문 슬라이스만으로 분석합니다. 슬라이스가 없으면 전체 평문으로 fallback.

이렇게 하면 목차/연결재무제표 본문 같은 노이즈에서 의견 라벨이 잘못 잡히는 사고를 줄일 수 있습니다.

## 예제

### 기본 사용

```python
from dart_kam.audit_extractors import analyze_audit_text, classify_opinion

with open("audit_slice.txt", encoding="utf-8") as f:
    text = f.read()

result = analyze_audit_text(text)
print(result["opinion_label"])       # "적정의견"
print(result["auditor_firm"])        # "삼일회계법인"
print(result["accounting_standard"]) # "한국채택국제회계기준"
print(result["kam_count"])           # 0 or 1 (현재는 boolean-ish)
```

### 의견만 확인

```python
label, snippet = classify_opinion(text)
if label and label != "적정의견":
    print(f"⚠ 비적정 의견 발견: {label}")
    print(snippet)
```

### 비적정 사유 추출

```python
from dart_kam.audit_extractors import (
    classify_opinion,
    extract_opinion_modification_reason,
)

label, _ = classify_opinion(text)
reason = extract_opinion_modification_reason(text, label)
if reason:
    print(reason[:500])
```

### KAM 절 본문 추출 후 후처리

```python
from dart_kam.audit_extractors import extract_kam_section_full

kam = extract_kam_section_full(text)
if kam:
    # 예: 첫 문장만 요약
    first_sentence = kam.split(".")[0]
    print(first_sentence)
```

## 확장 아이디어

| 목표 | 변경 위치 |
|---|---|
| 실제 KAM **개수** 카운팅 (현재 0/1만) | `extract_kam_section_full` 결과를 다시 분리해 `kam_count = N`, `kam_items` 도 N행 |
| 감사인 명칭 정확도 향상 | `_AUDITOR_FIRM_RE` 의 prefix 길이 한도(48자) 조정 |
| 강조사항/기타사항 본문 길이 제한 완화 | `_SECTION_OUT_LIMIT = 8000` 상수 수정 |
| 새로운 의견 라벨 (예: "조건부 의견") 추가 | `_OPINION_PATTERNS` 튜플에 한 항목 추가 |

## FAQ

**Q. 휴리스틱이 틀리는 경우가 있나요?**
A. 네, 특히 *대상 회사* 가 적정의견을 받았다고 본문에서 언급하는 경우(예: "전기 재무제표는 다른 감사인이 감사하였으며 적정의견을 표명하였습니다") 때문에 의견이 잘못 잡힐 수 있습니다. `extract_standalone_audit_report_body` 로 본문 슬라이스만 분석하는 방식이 1차 완화책. 추가로 `auditor_firm` / `cpa_partner_name` 등 다중 신호로 교차검증할 수 있습니다.

**Q. 영어 보고서(KAM 영문판)는 지원되나요?**
A. 부분적으로 — `_KAM_HEADER` / `_EOM_HEADER` 가 영문 매칭을 포함합니다. 의견 라벨은 한국어 전용입니다.

**Q. `auditor_name` 과 `auditor_firm` 이 같은 값이에요.**
A. 의도적입니다. `auditor_name` 은 레거시 컬럼으로, 현재는 firm 과 동일 값을 저장합니다. 향후 "회계법인명" vs "감사 책임자 이름" 으로 분리되면 의미가 갈릴 예정.

**Q. 본문 슬라이스 시작 지점은 *두 번째* 출현인 이유는?**
A. 공시 XML 의 첫 번째 "독립된 감사인의 감사보고서" 는 보통 목차/표지에 등장합니다. 두 번째가 실제 본문 시작입니다.

**Q. 정규식 직접 export 안 되나요?**
A. `_OPINION_PATTERNS`, `_KAM_HEADER` 등은 모듈 private. 의도적입니다 — 휴리스틱은 자주 바뀌므로 외부 의존을 막습니다. 필요하면 추출 함수 자체를 호출하세요.

**Q. 단위 테스트는 어디 있나요?**
A. 현재 별도 테스트 디렉터리는 없지만, 이 모듈은 모두 순수 함수이므로 `pytest` 한 줄로 검증할 수 있습니다. 합성 입력 예시는 [README 본문](./README.md#리팩토링-노트-이전-버전과의-차이) 참고.
