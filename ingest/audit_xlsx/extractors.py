"""감사보고서 본문에서 정보를 뽑는 순수 정규식 추출기 모음.

모든 함수는 부수효과 없고 입력 텍스트만 받아 결과를 반환한다.
실제 사용 위치 (어디서 import 되는지):

* :func:`extract_standalone_audit_report_body` — parse_audit, reparse_from_cache
* :func:`extract_kam_full_block`              — parse_audit, reparse_from_cache
* :func:`extract_audit_body_html`             — reparse_from_cache
* :func:`extract_cpa_partner`                 — parse_audit
* :func:`extract_other_matters`               — parse_audit

설계 원칙
~~~~~~~~~

표준 감사보고서의 절 순서는 ``감사의견 → 감사의견근거 → [핵심감사사항] →
[강조사항] → [기타사항] → 재무제표에 대한 경영진과 지배기구의 책임 →
독립된 감사인의 책임`` 이다. 각 절 추출은 *시작 헤더* + *다음 절 헤더* 의
정규식 매칭으로 슬라이스한다.

본문은 줄바꿈을 보존한다 (가독성). 줄 안의 공백만 단일화.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# 공통 헬퍼
# ---------------------------------------------------------------------------

_INLINE_WS = re.compile(r"[ \t\r\f\v]+")
_HANGUL_GAP = re.compile(r"([가-힣])\s([가-힣])")


def _normalize_lines(text: str) -> str:
    """줄 단위로 인라인 공백만 단일화 + 빈 줄 제거 (줄바꿈 보존)."""
    lines = (_INLINE_WS.sub(" ", line).strip() for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


def collapse_hangul_spaces(s: str) -> str:
    """'동 현 회 계 법 인' 처럼 한글 사이 단일 공백을 제거. 영문/숫자 공백은 보존."""
    s = _INLINE_WS.sub(" ", s).strip()
    prev = None
    while s != prev:
        prev, s = s, _HANGUL_GAP.sub(r"\1\2", s)
    return s


# ---------------------------------------------------------------------------
# 1) 감사보고서 본문 슬라이스 (평문)
# ---------------------------------------------------------------------------
#
# 시작 = "독립된 감사인의 감사보고서 [회사명] 주주 및 이사회 귀중" 표준 헤더.
# 회사명 가변 길이라 [\s\S]{1,300}? 로 lazy 매칭. 목차에 단독 등장하는
# "독립된 감사인의 감사보고서" 는 회사명+주주귀중이 안 따라오므로 자동 제외.
#
# 끝 = 결구 또는 (첨부)재무제표 중 더 빠른 위치.
#  - 결구는 본문에 포함 (`match.end()`)
#  - (첨부) 헤더는 직전까지 (`match.start()`)

_BODY_HEAD = re.compile(
    r"독립된\s*감사인의\s*감사보고서[\s\S]{1,300}?주주\s*(?:및|,)\s*이사회\s*귀중"
)
_BODY_END_CLAUSE = re.compile(r"이\s*감사보고서가\s*수정될\s*수도\s*있습니다\.?")
_BODY_END_ATTACH = re.compile(r"\(\s*첨\s*부\s*\)\s*재\s*무\s*제\s*표")
_BODY_MAX_LEN = 2_000_000
_BODY_MIN_LEN = 40


def extract_standalone_audit_report_body(text: str) -> str | None:
    """감사보고서 본문 슬라이스 (평문, 줄바꿈 보존).

    Returns ``None`` 일 때:
      - 시작 헤더 매치 실패 (본문 형식이 비표준)
      - 슬라이스 결과가 ``_BODY_MIN_LEN`` 미만
    """
    head = _BODY_HEAD.search(text)
    if head is None:
        return None
    start = head.start()
    window = text[start : start + _BODY_MAX_LEN]

    clause = _BODY_END_CLAUSE.search(window)
    attach = _BODY_END_ATTACH.search(window)
    if clause and (not attach or clause.start() < attach.start()):
        end_pos = clause.end()
    elif attach:
        end_pos = attach.start()
    else:
        end_pos = len(window)

    block = _normalize_lines(window[:end_pos])
    return block if len(block) >= _BODY_MIN_LEN else None


# ---------------------------------------------------------------------------
# 2) 핵심감사사항 (KAM) 전체 문단 (평문)
# ---------------------------------------------------------------------------
#
# 시작 = "핵심감사사항" 또는 "Key Audit Matters" 헤더.
# 끝   = 다음 절 후보 중 가장 빠른 위치 (강조사항/기타사항/책임 단락).
#
# negative lookahead 로 "기타사항들/외/을 …" 같은 본문 내 일반 표현이
# 절 헤더로 잘못 잡히는 false positive 차단.

_KAM_HEADER = re.compile(
    r"(?<![가-힣])(?:핵심\s*감사\s*사항|Key\s*Audit\s*Matters)(?![가-힣]|들)", re.I
)
# 절 헤더 false positive 차단용 — '강조사항 외에는' / '기타사항들' 등 제외.
_PARTICLE_BLOCK = r"(?!\s*(?:외|을|은|는|이|가|에|에서|의|들))"

_KAM_END_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"(?<![가-힣])강\s*조\s*사\s*항(?![가-힣]){_PARTICLE_BLOCK}"),
    re.compile(rf"(?<![가-힣])기\s*타\s*사\s*항(?![가-힣]){_PARTICLE_BLOCK}"),
    re.compile(r"(?:연결)?재무제표에\s*대한\s*경영(?:자|진)과?\s*(?:및\s*)?지배기구의\s*책임"),
    re.compile(r"(?:연결)?재무제표\s*감사에\s*대한\s*감사인의\s*책임"),
    re.compile(r"독립된\s*감사인의\s*책임"),
)
_KAM_OUT_LIMIT = 200_000
_KAM_MIN_LEN = 30


def extract_kam_full_block(text: str) -> str | None:
    """'핵심감사사항' 헤더 ~ 다음 표준 절 직전 전체 문단. 줄바꿈 보존.

    KAM 항목별 분리는 하지 않고 한 덩어리로 반환.
    """
    head = _KAM_HEADER.search(text)
    if head is None:
        return None
    start = head.start()
    end = len(text)
    for pat in _KAM_END_PATTERNS:
        m = pat.search(text, pos=head.end())
        if m:
            end = min(end, m.start())
    block = _normalize_lines(text[start:end])
    return block[:_KAM_OUT_LIMIT] if len(block) >= _KAM_MIN_LEN else None


# ---------------------------------------------------------------------------
# 3) 감사보고서 본문 HTML 슬라이스 (태그 보존)
# ---------------------------------------------------------------------------
#
# Streamlit ``st.html`` 로 표·문단·스타일 그대로 렌더링하기 위한 슬라이스.
# 평문화하지 않고 raw HTML 그대로 검색. 글자 사이에 태그가 끼어들 수 있으므로
# 글자 사이 ``\s*`` 허용.
#
# 시작 = 두 번째 "독립된 감사인의 감사보고서" (첫 번째는 보통 목차 항목).
# 끝   = 결구 (closing 태그까지 확장) 또는 (첨부)재무제표 직전.

_HTML_HEAD = re.compile(r"독\s*립\s*된\s*감\s*사\s*인\s*의\s*감\s*사\s*보\s*고\s*서", re.I)
_HTML_END_CLAUSE = re.compile(
    r"이\s*감\s*사\s*보\s*고\s*서\s*가?\s*수\s*정\s*될\s*수\s*도\s*있\s*습\s*니\s*다\s*\.?"
)
_HTML_END_ATTACH = re.compile(r"\(\s*첨\s*부\s*\)\s*재\s*무\s*제\s*표")
_HTML_BODY_MIN_LEN = 200
_HTML_BODY_MAX_LEN = 200_000
_HTML_CLOSE_TAG_LOOKAHEAD = 300
_HTML_CLOSE_TAGS: tuple[str, ...] = ("P", "TABLE", "DIV")


def _extend_to_closing_tag(html: str, pos: int) -> int:
    """``pos`` 직후 가까이 있는 closing tag 까지 끝 위치를 확장 (HTML 무결성).

    찾은 close tag 가 ``_HTML_CLOSE_TAG_LOOKAHEAD`` 자 이내이면 그 뒤까지.
    """
    for tag in _HTML_CLOSE_TAGS:
        close_idx = html.find(f"</{tag}>", pos)
        if close_idx != -1 and close_idx - pos < _HTML_CLOSE_TAG_LOOKAHEAD:
            return close_idx + len(f"</{tag}>")
    return pos


def extract_audit_body_html(raw_html: str) -> str | None:
    """raw HTML 에서 감사보고서 본문 부분만 태그 보존 슬라이스.

    Returns ``None`` 일 때:
      - 시작 마커 매치 실패
      - 슬라이스 결과가 ``_HTML_BODY_MIN_LEN`` 미만
    """
    if not raw_html:
        return None
    heads = list(_HTML_HEAD.finditer(raw_html))
    if not heads:
        return None
    head = heads[1] if len(heads) >= 2 else heads[0]
    start = head.start()

    clause = _HTML_END_CLAUSE.search(raw_html, pos=head.end())
    attach = _HTML_END_ATTACH.search(raw_html, pos=head.end())
    if clause and (not attach or clause.start() < attach.start()):
        end = _extend_to_closing_tag(raw_html, clause.end())
    elif attach:
        end = attach.start()
    else:
        end = len(raw_html)

    block = raw_html[start:end]
    if len(block) < _HTML_BODY_MIN_LEN:
        return None
    return block[:_HTML_BODY_MAX_LEN]


# ---------------------------------------------------------------------------
# 4) 업무수행 공인회계사 이름
# ---------------------------------------------------------------------------
#
# 표준 패턴 5종을 우선순위 순으로 시도. 본문 말미 12 KB 에서 먼저 검색
# (서명란 위치). 못 찾으면 본문 전체 fallback.

_CPA_NAME_RE = r"[가-힣A-Za-z·\s]{2,30}?"
_CPA_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "업무수행이사는 공인회계사 OOO 입니다"
    re.compile(rf"업무수행\s*이사는?\s*공인회계사\s+({_CPA_NAME_RE})\s*(?:입니다|임\.)"),
    # "업무수행이사는 OOO 입니다"
    re.compile(rf"업무수행\s*이사는?\s+({_CPA_NAME_RE})\s*(?:입니다|임\.)"),
    # 보고서 말미: "공인회계사 OOO 입니다"
    re.compile(rf"공인회계사\s+({_CPA_NAME_RE})\s*(?:입니다|임\.|이고|으로\s*갑)"),
    # "업무수행 공인회계사 : OOO"
    re.compile(
        rf"업무수행\s*공인회계사\s*[:：\s]*({_CPA_NAME_RE})"
        r"(?=\s*(?:\(|공인회계사\)|등록|$|\n))"
    ),
    # "등록 공인회계사 : OOO"
    re.compile(r"등록\s*공인회계사\s*[:：\s]*([가-힣A-Za-z·\s]{2,30})"),
)
_CPA_TAIL_LEN = 12_000
_CPA_MIN_LEN = 2
_CPA_MAX_LEN = 30


def _clean_cpa_name(raw: str) -> str | None:
    """매치된 이름 후보를 정규화. '회계법인'/'공인회계사' 포함 시 무효."""
    s = collapse_hangul_spaces(raw)
    if not (_CPA_MIN_LEN <= len(s) <= _CPA_MAX_LEN):
        return None
    if "회계법인" in s or "공인회계사" in s:
        return None
    return s


def _try_cpa_patterns(text: str) -> str | None:
    for pattern in _CPA_PATTERNS:
        m = pattern.search(text)
        if m:
            name = _clean_cpa_name(m.group(1))
            if name:
                return name
    return None


def extract_cpa_partner(text: str) -> str | None:
    """업무수행 공인회계사 이름. 말미 12KB 우선 → 본문 전체 fallback."""
    return _try_cpa_patterns(text[-_CPA_TAIL_LEN:]) or _try_cpa_patterns(text)


# ---------------------------------------------------------------------------
# 5) 기타사항 (Other Matters) 절 본문
# ---------------------------------------------------------------------------
#
# 헤더 매치 + stop 패턴 사이를 슬라이스. EOM 끝 마커는 stop 에 안 둔다 —
# '강조사항 외에는' 같은 본문 일반 표현이 헤더로 잘못 잡혀 본문이 잘리는
# 케이스 방지.

_OTHER_HEADER = re.compile(
    rf"(?<![가-힣])기\s*타\s*사\s*항(?![가-힣]){_PARTICLE_BLOCK}"
    r"|기\s*타\s*의\s*감사\s*사항"
    r"|Other\s+Matter",
    re.I,
)
_OTHER_STOPS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:연결)?재무제표에\s*대한\s*경영(?:자|진)과?\s*(?:및\s*)?지배기구의\s*책임"),
    re.compile(r"(?:연결)?재무제표\s*감사에\s*대한\s*감사인의\s*책임"),
    re.compile(r"독립된\s*감사인의\s*책임"),
    re.compile(r"핵심\s*감사사항|핵심감사사항|Key\s*Audit", re.I),
)
_OTHER_OUT_LIMIT = 30_000
_OTHER_MIN_LEN = 25


def _section_end(text: str, start: int, stops: tuple[re.Pattern[str], ...]) -> int:
    """``start`` 이후로 가장 먼저 매칭되는 stop 위치 (없으면 ``len(text)``)."""
    end = len(text)
    for pattern in stops:
        m = pattern.search(text, pos=start)
        if m:
            end = min(end, m.start())
    return end


def extract_other_matters(text: str) -> str | None:
    """'기타사항' 절 본문. 단일 공백 정규화 (줄바꿈은 보존 안 함)."""
    header = _OTHER_HEADER.search(text)
    if header is None:
        return None
    end = _section_end(text, header.end(), _OTHER_STOPS)
    body = re.sub(r"\s+", " ", text[header.end() : end]).strip()
    return body[:_OTHER_OUT_LIMIT] if len(body) >= _OTHER_MIN_LEN else None


# ---------------------------------------------------------------------------
# 6) 공식 document.xml API 기반 「내부통제에 관한 사항」
# ---------------------------------------------------------------------------
#
# OPENDART 공시서류원본파일(document.xml) ZIP 의 *사업보고서 본문* XML 에는
# 「내부통제에 관한 사항」 절이 있다 — 경영진의 내부회계관리제도 효과성 평가
# 결과(평가 결론·중요한 취약점·시정조치계획)와 감사인의 감사(검토)의견 요약표.
#
# 같은 문구가 목차에도 등장하므로, 표제 직후 점선/대시 런이 보이면 목차 항목으로
# 보고 건너뛴다. 끝 마커는 다음 로마숫자 대제목.

_IC_SUMMARY_HEAD = re.compile(r"내부통제에\s*관한\s*사항")
# 끝 마커: 다음 대제목 — ASCII (IV.) 와 유니코드 로마숫자 (Ⅵ.) 모두.
_IC_SUMMARY_END = re.compile(r"^\s*(?:[IVX]+|[Ⅰ-Ⅻ])\s*\.\s*\S", re.MULTILINE)
# 목차(TOC) 잔해 감지 — 표제 직후 점선/대시 런 (예: "내부통제에 관한 사항 ---- 287").
_TOC_DASH_RUN = re.compile(r"[-.…·]{4,}")
_IC_SUMMARY_OUT_LIMIT = 50_000
_IC_SUMMARY_MIN_LEN = 100


def extract_internal_control_summary(flat: str) -> str | None:
    """사업보고서 본문 평문에서 「내부통제에 관한 사항」 절.

    경영진의 내부회계관리제도 효과성 평가 결과·감사인의 감사(검토)의견 요약
    표가 들어 있다. 같은 문구가 목차에도 등장하므로 표제 직후 80자 내에
    점선/대시 런이 보이면 목차 항목으로 보고 다음 매치로 넘어간다.
    끝 마커는 다음 로마숫자 대제목.
    """
    for m in _IC_SUMMARY_HEAD.finditer(flat):
        if _TOC_DASH_RUN.search(flat[m.end(): m.end() + 80]):
            continue  # 목차 항목
        m2 = _IC_SUMMARY_END.search(flat, m.end())
        end = m2.start() if m2 else min(len(flat), m.end() + 20_000)
        out = flat[m.start():end].strip()
        if len(out) >= _IC_SUMMARY_MIN_LEN:
            return out[:_IC_SUMMARY_OUT_LIMIT]
    return None
