"""감사보고서 평문에서 주요 정보를 정규식으로 뽑는 순수 함수 모음.

dart_kam.audit_extractors 의 추출 로직을 그대로 이식. 단,
:func:`extract_standalone_audit_report_body` 는 종료 마커를 *본문 슬라이스 내
마지막 매치*로 사용하도록 보강 (이전엔 첫 매치를 끝으로 잡아 KAM/회계법인이 잘렸음).
"""
from __future__ import annotations

import re
from typing import Any

_WS = re.compile(r"\s+")

# --- 본문 슬라이스 -------------------------------------------------------------
# 본문 시작: "독립된 감사인의 감사보고서 [회사이름] 주주 및 이사회 귀중" 표준 헤더.
# 회사명 길이 가변(보통 50자 이내)이므로 300자 안에 "주주 및 이사회 귀중"이 따라오면
# 그 위치를 본문 시작으로 본다. 목차에 등장하는 단독 "독립된 감사인의 감사보고서" 는
# 뒤에 회사명/주주귀중이 따라오지 않아 자연스럽게 제외.
_AUDIT_REPORT_HEAD = re.compile(
    r"독립된\s*감사인의\s*감사보고서[\s\S]{1,300}?주주\s*(?:및|,)\s*이사회\s*귀중"
)
# 본문 끝 후보:
#  1) 결구 — 표준 감사보고서 마지막 문장 (가장 신뢰할 수 있음). 매치 끝까지 본문에 포함.
#  2) "(첨부)재 무 제 표" 헤더 — 사업보고서 첨부형. 매치 *시작* 직전까지.
# 둘 중 더 빠른 위치를 본문 끝으로 사용.
_END_BY_CLAUSE = re.compile(r"이\s*감사보고서가\s*수정될\s*수도\s*있습니다\.?")
_END_BY_ATTACH = re.compile(r"\(\s*첨\s*부\s*\)\s*재\s*무\s*제\s*표")
_MAX_AUDIT_REPORT_BODY = 2_000_000
_MIN_AUDIT_REPORT_BODY = 40


def extract_standalone_audit_report_body(text: str) -> str | None:
    """'독립된 감사인의 감사보고서 [회사명] 주주 및 이사회 귀중' ~ 결구 또는 첨부 직전."""
    head = _AUDIT_REPORT_HEAD.search(text)
    if head is None:
        return None
    start = head.start()
    window = text[start : start + _MAX_AUDIT_REPORT_BODY]

    clause = _END_BY_CLAUSE.search(window)
    attach = _END_BY_ATTACH.search(window)

    if clause and (not attach or clause.start() < attach.start()):
        end_pos = clause.end()  # 결구 자체는 본문에 포함
    elif attach:
        end_pos = attach.start()  # (첨부) 헤더 직전까지
    else:
        end_pos = len(window)

    block = _WS.sub(" ", window[:end_pos]).strip()
    if len(block) < _MIN_AUDIT_REPORT_BODY:
        return None
    return block


# --- 감사의견 라벨 -------------------------------------------------------------
_OPINION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("부적정의견", re.compile(r"부적정\s*의견")),
    ("의견거절", re.compile(r"의견\s*거절|거절\s*의견")),
    ("한정의견", re.compile(r"한정\s*의견")),
    ("감사범위제한", re.compile(r"감사의\s*범위가\s*한|감사\s*범위\s*제한")),
    ("적정의견", re.compile(r"적정\s*의견|공정하게\s*표시|재무제표는.*적정")),
)
NON_CLEAN_OPINIONS = frozenset({"한정의견", "부적정의견", "의견거절", "감사범위제한"})


def classify_opinion(text: str) -> str | None:
    """문서에서 가장 먼저 등장한 의견 라벨."""
    earliest: tuple[int, str] | None = None
    for label, pattern in _OPINION_PATTERNS:
        m = pattern.search(text)
        if m is None:
            continue
        if earliest is None or m.start() < earliest[0]:
            earliest = (m.start(), label)
    return earliest[1] if earliest else None


# --- 감사인 / 공인회계사 ----------------------------------------------------------
# 1차: 공백 없는 표준형 — "동현회계법인", "삼일회계법인" 등.
_AUDITOR_FIRM_RE = re.compile(r"([가-힣A-Za-z0-9·]{1,15}회계법인)")
# 2차: 표 셀이 한 글자씩 분리된 형태 — "동 현 회 계 법 인" / "삼 일 회 계 법 인".
_AUDITOR_FIRM_SPACED_RE = re.compile(
    r"((?:[가-힣]\s+){1,12}회\s*계\s*법\s*인)"
)
_AUDITOR_FIRM_OUT_LIMIT = 160


def _normalize_korean_spacing(s: str) -> str:
    """'동 현 회 계 법 인' 처럼 한글 사이에 단일 공백이 있는 경우 공백 제거.

    영문/숫자/구두점 사이 공백은 보존 (예: 'KPMG 삼정' 은 그대로).
    """
    s = _WS.sub(" ", s).strip()
    while True:
        new = re.sub(r"([가-힣])\s([가-힣])", r"\1\2", s)
        if new == s:
            return s
        s = new


def extract_auditor_firm(text: str) -> str | None:
    head = text[:400_000]
    candidates: list[str] = []
    for m in _AUDITOR_FIRM_RE.finditer(head):
        candidates.append(m.group(1))
    for m in _AUDITOR_FIRM_SPACED_RE.finditer(head):
        candidates.append(m.group(1))
    for raw in candidates:
        cleaned = _normalize_korean_spacing(raw)
        # "회계법인" 단독이거나 너무 짧은 매치는 제외.
        if cleaned == "회계법인" or len(cleaned) < 5:
            continue
        return cleaned[:_AUDITOR_FIRM_OUT_LIMIT]
    return None


# 공인회계사·업무수행이사 이름 추출 패턴 (우선순위 순).
_CPA_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "업무수행이사는 공인회계사 OOO 입니다"
    re.compile(
        r"업무수행\s*이사는?\s*공인회계사\s+([가-힣A-Za-z·\s]{2,30}?)\s*(?:입니다|임\.)"
    ),
    # "업무수행이사는 OOO 입니다"
    re.compile(r"업무수행\s*이사는?\s+([가-힣A-Za-z·\s]{2,30}?)\s*(?:입니다|임\.)"),
    # 보고서 말미: "공인회계사 OOO 입니다"
    re.compile(
        r"공인회계사\s+([가-힣A-Za-z·\s]{2,30}?)\s*(?:입니다|임\.|이고|으로\s*갑)"
    ),
    # "업무수행 공인회계사 : OOO"
    re.compile(
        r"업무수행\s*공인회계사\s*[:：\s]*([가-힣A-Za-z·\s]{2,30}?)"
        r"(?=\s*(?:\(|공인회계사\)|등록|$|\n))"
    ),
    # "등록 공인회계사 : OOO"
    re.compile(r"등록\s*공인회계사\s*[:：\s]*([가-힣A-Za-z·\s]{2,30})"),
)


def _clean_cpa(raw: str, *, max_len: int = 30) -> str | None:
    s = _normalize_korean_spacing(raw)
    if not (2 <= len(s) <= max_len):
        return None
    if "회계법인" in s or "공인회계사" in s:
        return None
    return s


def extract_cpa_partner(text: str) -> str | None:
    # 본문 말미 8000자에서 우선 검색 (보고자 서명란 위치).
    tail = text[-12000:]
    for regex in _CPA_PATTERNS:
        m = regex.search(tail)
        if m:
            n = _clean_cpa(m.group(1))
            if n:
                return n
    # 못 찾으면 본문 전체 fallback.
    for regex in _CPA_PATTERNS:
        m = regex.search(text)
        if m:
            n = _clean_cpa(m.group(1))
            if n:
                return n
    return None


# --- 회계기준 ------------------------------------------------------------------
_KIFRS_RE = re.compile(r"한국채택국제회계기준|한국\s*채택\s*국제\s*회계기준")
_KGAAP_RE = re.compile(r"일반기업회계기준")


def detect_accounting_standard(text: str) -> str | None:
    kifrs = _KIFRS_RE.search(text)
    kgaap = _KGAAP_RE.search(text)
    if kifrs and (not kgaap or kifrs.start() <= kgaap.start()):
        return "한국채택국제회계기준"
    if kgaap:
        return "일반기업회계기준"
    return None


# --- 강조사항 / 기타사항 / 핵심감사사항 --------------------------------------------
# EOM·기타사항 헤더는 단어 단독으로 절을 시작해야 한다. 본문에 등장하는
# "강조사항 외에는", "기타사항들" 등은 절 헤더가 아니므로 negative-lookahead 로 차단.
_EOM_HEADER = re.compile(
    r"(?<![가-힣])강\s*조\s*사\s*항(?![가-힣])(?!\s*(?:외|을|은|는|이|가|에|에서|의|들))"
    r"|Emphasis\s+of\s+Matter",
    re.I,
)
_OTHER_HEADER = re.compile(
    r"(?<![가-힣])기\s*타\s*사\s*항(?![가-힣])(?!\s*(?:외|을|은|는|이|가|에|에서|의|들))"
    r"|기\s*타\s*의\s*감사\s*사항"
    r"|Other\s+Matter",
    re.I,
)
_KAM_HEADER = re.compile(r"(핵심\s*감사사항|핵심감사사항|Key\s*Audit\s*Matters)", re.I)

# 각 절의 표준 후속 절(=stop)들. 긴 본문이 잘 잘리도록 명확한 헤더만 사용.
_RESPONSIBILITY_STOPS = (
    re.compile(
        r"(?:연결)?재무제표에\s*대한\s*경영(?:자|진)과?\s*(?:및\s*)?지배기구의\s*책임"
    ),
    re.compile(r"(?:연결)?재무제표\s*감사에\s*대한\s*감사인의\s*책임"),
    re.compile(r"독립된\s*감사인의\s*책임"),
)

_SECTION_MIN_LEN = 25
_SECTION_OUT_LIMIT = 30_000
_KAM_OUT_LIMIT = 500_000


def _next_section_start(text: str, start: int, patterns: tuple[re.Pattern[str], ...]) -> int:
    end = len(text)
    for p in patterns:
        m = p.search(text, pos=start)
        if m:
            end = min(end, m.start())
    return end


def _extract_section(text: str, header: re.Pattern[str], stops: tuple[re.Pattern[str], ...]) -> str | None:
    h = header.search(text)
    if not h:
        return None
    end = _next_section_start(text, h.end(), stops)
    body = _WS.sub(" ", text[h.end() : end]).strip()
    if len(body) < _SECTION_MIN_LEN:
        return None
    return body[:_SECTION_OUT_LIMIT]


# EOM 다음에 올 수 있는 절: 기타사항, 핵심감사사항, 책임 단락.
_EOM_STOPS = _RESPONSIBILITY_STOPS + (
    _OTHER_HEADER,
    re.compile(r"핵심\s*감사사항|핵심감사사항|Key\s*Audit", re.I),
)
# 기타사항 다음에 올 수 있는 절: 책임 단락 (본문에 '강조사항 외에는' 같은
# 거짓 매치가 있어도 자르지 않도록 강조사항을 stop 에서 제외).
_OTHER_STOPS = _RESPONSIBILITY_STOPS + (
    re.compile(r"핵심\s*감사사항|핵심감사사항|Key\s*Audit", re.I),
)
_KAM_STOP = re.compile(
    r"(감사가\s*재무제표\s*감사를\s*수행|기타\s*필수적\s*감사|"
    r"(?:연결)?재무제표에\s*대한\s*경영(?:자|진)과?\s*(?:및\s*)?지배기구의\s*책임|"
    r"독립된\s*감사인의\s*책임|"
    r"(?<![가-힣])강\s*조\s*사\s*항(?![가-힣])(?!\s*(?:외|을|은|는|이|가|에|에서|의|들))|"
    r"(?<![가-힣])기\s*타\s*사\s*항(?![가-힣])(?!\s*(?:외|을|은|는|이|가|에|에서|의|들)))"
)


def extract_emphasis_of_matter(text: str) -> str | None:
    return _extract_section(text, _EOM_HEADER, _EOM_STOPS)


def extract_other_matters(text: str) -> str | None:
    return _extract_section(text, _OTHER_HEADER, _OTHER_STOPS)


def extract_kam_section(text: str) -> str | None:
    m = _KAM_HEADER.search(text)
    if not m:
        return None
    stop = _KAM_STOP.search(text, pos=m.end())
    end = stop.start() if stop else len(text)
    block = _WS.sub(" ", text[m.start() : end]).strip()
    if len(block) < 12:
        return None
    return block[:_KAM_OUT_LIMIT]


# --- 통합 ---------------------------------------------------------------------
def analyze_audit_text(flat_text: str) -> dict[str, Any]:
    """평문 → 결과 dict (XLSX 행으로 직접 들어가는 컬럼들)."""
    body = extract_standalone_audit_report_body(flat_text)
    work = body if body else flat_text
    return {
        "opinion_label": classify_opinion(work),
        "accounting_standard": detect_accounting_standard(work),
        "auditor_firm": extract_auditor_firm(work),
        "cpa_partner_name": extract_cpa_partner(work),
        "kam_section": extract_kam_section(work),
        "emphasis_of_matter": extract_emphasis_of_matter(work),
        "other_matters": extract_other_matters(work),
        "audit_report_body": body,
    }
