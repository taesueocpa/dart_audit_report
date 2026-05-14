"""Pure-text extractors for OPENDART audit reports (no I/O, no DB).

DART 공시 XML을 평문으로 합친 문자열에서 감사보고서의 주요 정보를 정규식 기반
휴리스틱으로 뽑아내는 **순수 함수**들. 외부 의존성(파일/네트워크/DB)이 없으므로
단위 테스트가 쉽다.

추출 항목 요약:

- 감사의견 라벨 + 짧은 스니펫 (:func:`classify_opinion`)
- 비적정의견(한정/부적정/거절)의 사유 구간 (:func:`extract_opinion_modification_reason`)
- 적용 회계기준 (:func:`detect_accounting_standard`)
- 감사인(회계법인) 명칭 (:func:`extract_auditor_firm`)
- 업무수행 공인회계사 이름 (:func:`extract_cpa_partner`)
- 강조사항·기타사항 본문 (:func:`extract_emphasis_of_matter`, :func:`extract_other_matters`)
- 핵심감사사항(KAM) 절 전체 (:func:`extract_kam_section_full`)
- "독립된 감사인의 감사보고서" 본문 슬라이스 (:func:`extract_standalone_audit_report_body`)

상위 함수 :func:`analyze_audit_text` 가 위 모든 추출을 한 번에 실행해서
`parse_results` 컬럼 dict 를 만든다.
"""

from __future__ import annotations

import re
from typing import Any


_WS = re.compile(r"\s+")


# --------------------------------------------------------------------- standalone audit body

# 단일 공시 XML 안에 「독립된 감사인의 감사보고서」가 목차/본문에 각각 나오므로
# **두 번째** 등장부터 「현재로 유효한 것입니다.」까지를 감사보고서 본문으로 잘라낸다.
_AUDIT_REPORT_TITLE = re.compile(r"독립된\s*감사인의\s*감사보고서")
_AUDIT_REPORT_BODY_END = re.compile(r"현재로\s*유효한\s*것입니다\.?")
_MAX_AUDIT_REPORT_BODY = 2_000_000
_MIN_AUDIT_REPORT_BODY = 40


def extract_standalone_audit_report_body(text: str) -> str | None:
    """두 번째 '독립된 감사인의 감사보고서' ~ '현재로 유효한 것입니다.' 슬라이스.

    조건 미충족(타이틀 1회 이하, 종료 마커 없음, 본문 길이 < 40) 시 ``None``.
    """
    titles = list(_AUDIT_REPORT_TITLE.finditer(text))
    if len(titles) < 2:
        return None
    start = titles[1].start()
    end_match = _AUDIT_REPORT_BODY_END.search(text, pos=start)
    if not end_match:
        return None
    block = _WS.sub(" ", text[start : end_match.end()]).strip()
    if len(block) < _MIN_AUDIT_REPORT_BODY:
        return None
    return block[:_MAX_AUDIT_REPORT_BODY]


# --------------------------------------------------------------------- opinion classification

# 첫 매치 우선 (가장 빠르게 나타나는 라벨이 결정). 정렬 순서가 의미를 가짐 →
# "부적정"이 "적정"보다 먼저 와야 한다 (부적정의 부분문자열 매칭 방지).
_OPINION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("부적정의견", re.compile(r"부적정\s*의견")),
    ("의견거절", re.compile(r"의견\s*거절|거절\s*의견")),
    ("한정의견", re.compile(r"한정\s*의견")),
    ("감사범위제한", re.compile(r"감사의\s*범위가\s*한|감사\s*범위\s*제한")),
    ("적정의견", re.compile(r"적정\s*의견|공정하게\s*표시|재무제표는.*적정")),
)

# 비적정적 의견(=수정 사유 구간 추출 대상).
NON_CLEAN_OPINIONS = frozenset({"한정의견", "부적정의견", "의견거절", "감사범위제한"})

_OPINION_SNIPPET_LEFT = 80
_OPINION_SNIPPET_RIGHT = 160


def classify_opinion(text: str) -> tuple[str | None, str | None]:
    """문서에 등장한 의견 라벨 중 **가장 먼저 나타나는** 것을 채택.

    :returns: ``(label, snippet)`` — label/snippet 둘 다 ``None`` 가능.
    """
    earliest: tuple[int, str, re.Pattern[str]] | None = None
    for label, pattern in _OPINION_PATTERNS:
        m = pattern.search(text)
        if m is None:
            continue
        if earliest is None or m.start() < earliest[0]:
            earliest = (m.start(), label, pattern)
    if earliest is None:
        return None, None
    pos, label, pattern = earliest
    m = pattern.search(text, pos=pos)
    if m is None:
        return label, None
    left = max(0, m.start() - _OPINION_SNIPPET_LEFT)
    right = min(len(text), m.end() + _OPINION_SNIPPET_RIGHT)
    return label, text[left:right].strip()


# --------------------------------------------------------------------- opinion modification reason

_MOD_REASON_WINDOW = 2800  # 사유 구간 최대 길이
_MOD_REASON_PREFIX = 280   # 사유 구간 앞쪽 — stop pattern 검색 시작 offset
_MOD_REASON_STOP = re.compile(
    r"(?:핵심\s*감사사항|핵심감사사항|강조\s*사항|기타\s*사항|독립된\s*감사인의\s*책임)"
)


def extract_opinion_modification_reason(
    text: str, opinion_label: str | None
) -> str | None:
    """한정/부적정/거절/감사범위제한 의견의 사유 구간(휴리스틱)."""
    if not opinion_label or opinion_label not in NON_CLEAN_OPINIONS:
        return None
    for label, pattern in _OPINION_PATTERNS:
        if label != opinion_label:
            continue
        m = pattern.search(text)
        if m is None:
            return None
        window = text[m.start() : m.start() + _MOD_REASON_WINDOW]
        stop = _MOD_REASON_STOP.search(window[_MOD_REASON_PREFIX:])
        if stop:
            window = window[: _MOD_REASON_PREFIX + stop.start()]
        out = _WS.sub(" ", window).strip()
        return out[:_MOD_REASON_WINDOW] if out else None
    return None


# --------------------------------------------------------------------- accounting standard

_KIFRS_RE = re.compile(r"한국채택국제회계기준|한국\s*채택\s*국제\s*회계기준")
_KGAAP_RE = re.compile(r"일반기업회계기준")


def detect_accounting_standard(text: str) -> str | None:
    """문서에서 회계기준 표기를 찾는다. 둘 다 있으면 먼저 등장한 쪽."""
    kifrs = _KIFRS_RE.search(text)
    kgaap = _KGAAP_RE.search(text)
    if kifrs and (not kgaap or kifrs.start() <= kgaap.start()):
        return "한국채택국제회계기준"
    if kgaap:
        return "일반기업회계기준"
    return None


# --------------------------------------------------------------------- auditor firm / CPA partner

_AUDITOR_FIRM_HEAD_LIMIT = 400_000
_AUDITOR_FIRM_OUT_LIMIT = 160

# 1차: "XXX 회계법인" 형태 (가장 흔함).
_AUDITOR_FIRM_RE = re.compile(r"([가-힣A-Za-z0-9·\(\)\[\]\s]{1,48}회계법인)")
# 2차: "회계법인 XXX" 형태 (역순).
_AUDITOR_FIRM_REVERSED_RE = re.compile(
    r"(회계법인[가-힣A-Za-z0-9·\s]{0,32}?)(?=\s*(?:의|은|는|으로|에서|\.|,|\)|$))"
)


def extract_auditor_firm(text: str) -> str | None:
    """감사인(회계법인) 명칭. 본문 앞 400KB 범위에서만 찾는다."""
    head = text[:_AUDITOR_FIRM_HEAD_LIMIT]
    for regex, min_len in ((_AUDITOR_FIRM_RE, 5), (_AUDITOR_FIRM_REVERSED_RE, 4)):
        m = regex.search(head)
        if not m:
            continue
        cleaned = _WS.sub(" ", m.group(1).strip())
        if len(cleaned) >= min_len:
            return cleaned[:_AUDITOR_FIRM_OUT_LIMIT]
    return None


_CPA_TAIL_LEN = 8000
# 1차: 보고서 말미 "공인회계사 XXX 입니다" 형태.
_CPA_TAIL_RE = re.compile(
    r"공인회계사\s+([가-힣A-Za-z·\s]{2,45}?)\s*(?:입니다|임\.|이고|으로\s*갑)"
)
# 2차: "업무수행 공인회계사 : XXX" / "등록 공인회계사 : XXX" 형태.
_CPA_OTHER_RES: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"업무수행\s*공인회계사\s*[:：\s]*([가-힣A-Za-z·\s]{2,50}?)"
        r"(?=\s*(?:\(|공인회계사\)|등록|$|\n))"
    ),
    re.compile(r"등록\s*공인회계사\s*[:：\s]*([가-힣A-Za-z·\s]{2,45})"),
)


def _clean_cpa_name(raw: str, *, max_len: int) -> str | None:
    s = _WS.sub(" ", raw.strip())
    if 2 <= len(s) <= max_len and "회계법인" not in s:
        return s
    return None


def extract_cpa_partner(text: str) -> str | None:
    """업무수행 공인회계사 (등록 공인회계사) 성명."""
    tail = text[-_CPA_TAIL_LEN:]
    m = _CPA_TAIL_RE.search(tail)
    if m:
        name = _clean_cpa_name(m.group(1), max_len=100)
        if name:
            return name
    for regex in _CPA_OTHER_RES:
        m = regex.search(text)
        if m:
            name = _clean_cpa_name(m.group(1), max_len=120)
            if name:
                return name
    return None


# --------------------------------------------------------------------- EOM / Other matters / KAM

_EOM_HEADER = re.compile(r"강조\s*사항|Emphasis\s+of\s+Matter", re.IGNORECASE)
_OTHER_HEADER = re.compile(r"기타\s*사항|Other\s+Matter|기타\s*의\s*감사\s*사항", re.IGNORECASE)
_KAM_HEADER = re.compile(r"(핵심\s*감사사항|핵심감사사항|Key\s*Audit\s*Matters)", re.IGNORECASE)

_SECTION_MIN_LEN = 25
_SECTION_OUT_LIMIT = 8000
_KAM_OUT_LIMIT = 500_000


def _next_section_start(text: str, start: int, patterns: tuple[re.Pattern[str], ...]) -> int:
    """``start`` 이후로 가장 먼저 나오는 패턴 매치 위치 (없으면 ``len(text)``)."""
    end = len(text)
    for p in patterns:
        m = p.search(text, pos=start)
        if m:
            end = min(end, m.start())
    return end


def _extract_section(
    text: str,
    header: re.Pattern[str],
    stops: tuple[re.Pattern[str], ...],
) -> tuple[bool, str | None]:
    """공통 — 헤더 매치 후 다음 절까지 본문을 잘라낸다."""
    h = header.search(text)
    if not h:
        return False, None
    end = _next_section_start(text, h.end(), stops)
    body = _WS.sub(" ", text[h.end() : end]).strip()
    if len(body) < _SECTION_MIN_LEN:
        return False, None
    return True, body[:_SECTION_OUT_LIMIT]


_EOM_STOPS: tuple[re.Pattern[str], ...] = (
    re.compile(r"기타\s*사항|기타\s*의\s*감사", re.I),
    re.compile(r"핵심\s*감사|핵심감사사항|Key\s*Audit", re.I),
    re.compile(r"의견\s*거절|한정\s*의견|부적정\s*의견", re.I),
)
_OTHER_STOPS: tuple[re.Pattern[str], ...] = (
    re.compile(r"강조\s*사항|Emphasis\s+of\s+Matter", re.I),
    re.compile(r"핵심\s*감사|핵심감사사항|Key\s*Audit", re.I),
)
_KAM_STOP = re.compile(
    r"(감사가\s*재무제표\s*감사를\s*수행|기타\s*필수적\s*감사|기타\s*경영자의\s*책임|"
    r"재무제표에\s*대한\s*경영자의\s*책임|독립된\s*감사인의\s*책임|강조\s*사항|기타\s*사항)"
)


def extract_emphasis_of_matter(text: str) -> tuple[bool, str | None]:
    return _extract_section(text, _EOM_HEADER, _EOM_STOPS)


def extract_other_matters(text: str) -> tuple[bool, str | None]:
    return _extract_section(text, _OTHER_HEADER, _OTHER_STOPS)


def extract_kam_section_full(text: str) -> str | None:
    """'핵심감사사항' 헤더부터 다음 주요 절 직전까지 본문 전체 (정규화 후)."""
    m = _KAM_HEADER.search(text)
    if not m:
        return None
    stop = _KAM_STOP.search(text, pos=m.end())
    end = stop.start() if stop else len(text)
    block = _WS.sub(" ", text[m.start() : end]).strip()
    if len(block) < 12:
        return None
    return block[:_KAM_OUT_LIMIT]


# --------------------------------------------------------------------- aggregate

def analyze_audit_text(flat_text: str) -> dict[str, Any]:
    """평문 텍스트 → ``parse_results`` 컬럼 dict.

    내부 동작:

    1. 가능하면 '독립된 감사인의 감사보고서' 본문 슬라이스(``audit_body``)만으로 분석.
       (목차/연결재무제표 본문 등 노이즈를 줄이기 위함)
    2. 슬라이스가 없으면 전체 텍스트 사용.
    3. 각 추출 함수를 호출해 결과 dict 를 구성.

    :returns: :func:`dart_kam.repository.upsert_parse_result` 가 그대로 사용하는 dict.
    """
    audit_body = extract_standalone_audit_report_body(flat_text)
    work = audit_body if audit_body else flat_text

    label, snippet = classify_opinion(work)
    mod_reason = extract_opinion_modification_reason(work, label)
    firm = extract_auditor_firm(work)
    eom_present, eom_body = extract_emphasis_of_matter(work)
    other_present, other_body = extract_other_matters(work)
    kam_full = extract_kam_section_full(work)

    kam_has = bool(kam_full and kam_full.strip())
    return {
        "opinion_label": label,
        "opinion_raw_snippet": snippet,
        "opinion_modification_reason": mod_reason,
        "accounting_standard": detect_accounting_standard(work),
        "auditor_firm": firm,
        # `auditor_name` 은 현재 firm 과 동일 값을 저장 (legacy 컬럼).
        "auditor_name": firm,
        "cpa_partner_name": extract_cpa_partner(work),
        "emphasis_of_matter_present": 1 if eom_present else 0,
        "emphasis_of_matter_content": eom_body,
        "other_matters_present": 1 if other_present else 0,
        "other_matters_content": other_body,
        "kam_section_full": kam_full,
        # 현재 파서는 KAM 절 존재 여부(0/1)만 표현. 추후 실제 개수 추출 시 정수 카운트로 확장.
        "kam_count": 1 if kam_has else 0,
        "audit_report_body": audit_body,
    }
