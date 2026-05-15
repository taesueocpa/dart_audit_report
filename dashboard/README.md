# DART 감사보고서 대시보드 (Streamlit)

`audit_xlsx` 파이프라인이 만든 감사보고서 DB
(`data/audit_reports_full_v2.xlsx`) 를 필터링·검색·조회하는 Streamlit 앱.

## 기능

단일 페이지 — 사이드바 필터 + 메인 표 + 행 클릭 시 본문 전체 조회.

- **사이드바 필터**:
  - 시장구분 / 감사의견 / 보고서 종류 / 감사인(회계법인) — 멀티셀렉트
  - 회사명/종목코드 — 부분일치 텍스트
  - **본문 키워드** — KAM·강조사항·기타사항·본문 전체 통합 검색
- **메인 표**: 회사명·시장·의견·감사인·본문 길이 등. 컬럼 정렬·검색 빌트인
- **본문 조회**: 표 행 클릭 → 메타 + KAM/강조/기타/본문 전체 expander
- **Excel 다운로드**: 현재 필터 결과를 XLSX로 (본문 포함/제외 토글)

## 로컬 실행

```bash
cd dashboard
pip install -r requirements.txt
streamlit run app.py
# 브라우저: http://localhost:8501
```

## 데이터 갱신

1. `audit_xlsx` 파이프라인 재실행 → `data/audit_reports_full_v2.xlsx` 생성
2. `cp data/audit_reports_full_v2.xlsx dashboard/data/`
3. git commit + push → Streamlit Cloud 자동 재배포

## Streamlit Cloud 배포

1. https://share.streamlit.io 에서 GitHub 연결
2. **Repository**: `taesueocpa/dart_audit_report`
3. **Branch**: `main`
4. **Main file path**: `dashboard/app.py`
5. (자동) `dashboard/requirements.txt` 감지

## 파일 구성

```
dashboard/
├── app.py             # entry — 사이드바 + 메인 표 + 본문 expander
├── data_loader.py     # XLSX → DataFrame (캐싱)
├── filters.py         # 사이드바 5종 필터 + 본문 키워드 통합 검색
├── download.py        # 필터 결과 → Excel bytes
├── data/
│   └── audit_reports_full_v2.xlsx
├── requirements.txt
├── .streamlit/
│   └── config.toml
└── README.md
```

## 면책

추출 결과(감사의견·KAM·CPA 이름·본문 슬라이스 등)는 OPENDART 원본 + 정규식 휴리스틱
혼합이므로 참고용입니다. 법적·회계적 판단을 대체하지 않습니다.
