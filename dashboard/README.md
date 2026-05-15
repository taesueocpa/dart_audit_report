# DART 감사보고서 대시보드 (Streamlit)

`audit_xlsx` 파이프라인이 만든 4,627행 감사보고서 DB
(`data/audit_reports_full_v2.xlsx`) 를 시각화/검색하는 Streamlit 앱.

## 기능

- 📈 **요약**: 감사의견·시장·회계법인 분포 차트, KPI 4개
- 📋 **회사목록**: 검색·정렬·필터, 행 클릭 → 본문 드릴다운, Excel 다운로드
- 🔎 **본문 검색**: KAM/강조사항/기타사항/본문 전체에서 키워드 매칭 + 컨텍스트 발췌

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
├── app.py             # entry — 사이드바 + 3개 탭
├── data_loader.py     # XLSX → DataFrame (캐싱)
├── filters.py         # 사이드바 필터 위젯
├── charts.py          # KPI + Plotly 차트 4종
├── download.py        # 필터 결과 → Excel bytes
├── search.py          # 본문 키워드 검색 + highlight
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
