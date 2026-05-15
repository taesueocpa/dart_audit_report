"""OPENDART 회계감사 구조화 API + "감사보고서제출" 공시의 첨부 본문 다운로드.

흐름:
1. ``dart.report(corp_code, '회계감사', bsns_year, reprt_code)`` →
   감사인/감사의견/강조사항/핵심감사사항 (회사당 1건).
2. ``list_audit_disclosures(corp_code, ...)`` → "감사보고서" 키워드가 들어간
   외부감사 공시들 (예: "감사보고서제출", "감사보고서 (2025.12)") 의 rcept_no 목록.
3. 각 공시의 ``attach_docs(rcept_no, match='감사보고서')`` 로 첨부 목록 (감사보고서 /
   연결감사보고서 / 결합감사보고서 등) 을 받고,
4. 첨부 main.do URL → viewDoc 인자 파싱 → viewer.do URL → 본문 HTML.

OPENDART API 한 quirk: ``corp_code`` 와 ``pblntf_ty='F'`` 를 함께 보내면 0건이 옴.
그래서 list.json 호출 시 ``pblntf_ty`` / ``pblntf_detail_ty`` 를 보내지 않고, 결과의
``report_nm`` 에서 "감사보고서" 키워드로 직접 필터링한다.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any

import requests
from opendartreader import OpenDartReader


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# 단락/표/목록 시작·종료를 줄바꿈으로 변환할 블록 태그.
_BLOCK_TAG_RE = re.compile(
    r"<\s*/?\s*(?:p|br|div|tr|li|h[1-6]|table|thead|tbody)\b[^>]*>", re.IGNORECASE
)
_INLINE_WS_RE = re.compile(r"[ \t\r\f\v]+")
_USER_AGENT = "Mozilla/5.0"
_LIST_URL = "https://opendart.fss.or.kr/api/list.json"


def html_to_text(raw: str) -> str:
    """OPENDART 응답의 XML/HTML 태그를 제거해 평문으로 정규화 (줄바꿈 보존).

    블록 태그(<p>, <br>, <div>, <tr>, <li>, <h*>, <table>)는 줄바꿈으로 치환하고,
    나머지 인라인 태그는 공백으로 치환. HTML entity 디코드 후 줄별로
    공백 정리 + 빈 줄 제거.
    """
    if not raw:
        return ""
    s = _BLOCK_TAG_RE.sub("\n", raw)
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    lines = (_INLINE_WS_RE.sub(" ", line).strip() for line in s.split("\n"))
    return "\n".join(line for line in lines if line)


# --- 1) 회계감사 구조화 4필드 (당기 행) ---------------------------------------------
@dataclass(frozen=True)
class AuditOpinion:
    rcept_no: str  # 사업보고서 접수번호 (참고용)
    corp_code: str
    corp_name: str
    corp_cls: str
    bsns_year: str
    stlm_dt: str
    adtor: str  # 감사인(회계법인)
    adt_opinion: str  # 감사의견
    emphs_matter: str  # 강조사항 등
    core_adt_matter: str  # 핵심감사사항


_FIRM_HANGUL_GAP_RE = re.compile(r"([가-힣])\s+([가-힣])")
_FIRM_PARENS_RE = re.compile(r"\s*\([^)]*\)")
_FIRM_TAIL_RE = re.compile(r"^(.{1,30}?회계법인)")
_FIRM_REVERSED_RE = re.compile(r"(회계법인[가-힣A-Za-z\s]{1,15})")


def normalize_firm_name(s: str) -> str:
    """감사인 표기 정규화: 줄바꿈/공백/괄호 부가설명 제거 + 한글 사이 공백 합침.

    예: '삼정\\n회계법인' → '삼정회계법인'
        '삼일 회계법인' → '삼일회계법인'
        '대성회계법인\\n(구, 대성삼경회계법인)' → '대성회계법인'
        '한미회계법인\\n대표이사 정우진' → '한미회계법인'
        '회계법인 리안' → '회계법인리안'
    """
    if not s:
        return ""
    s = _WS_RE.sub(" ", s).strip()
    # 1) 괄호 안 부가설명 제거 — (구, …) (前 …) (PwC) (주1) 등
    s = _FIRM_PARENS_RE.sub("", s).strip()
    # 2) "...회계법인" 첫 매치만 채택 (뒤의 대표이사/주석 등 절단)
    m = _FIRM_TAIL_RE.search(s)
    if m:
        s = m.group(1)
    elif "회계법인" in s:
        m = _FIRM_REVERSED_RE.search(s)
        if m:
            s = m.group(1)
    # 3) 한글 글자 사이 단일 공백 제거 (반복)
    while True:
        new = _FIRM_HANGUL_GAP_RE.sub(r"\1\2", s)
        if new == s:
            break
        s = new
    return s.strip()


def _row_to_opinion(row: dict[str, Any]) -> AuditOpinion:
    def _s(v: Any) -> str:
        if v is None:
            return ""
        s = str(v).strip()
        return "" if s.lower() in ("nan", "none") else s
    return AuditOpinion(
        rcept_no=_s(row.get("rcept_no")),
        corp_code=_s(row.get("corp_code")),
        corp_name=_s(row.get("corp_name")),
        corp_cls=_s(row.get("corp_cls")),
        bsns_year=_s(row.get("bsns_year")),
        stlm_dt=_s(row.get("stlm_dt")),
        adtor=normalize_firm_name(_s(row.get("adtor"))),
        adt_opinion=_s(row.get("adt_opinion")),
        emphs_matter=_s(row.get("emphs_matter")),
        core_adt_matter=_s(row.get("core_adt_matter")),
    )


def fetch_audit_opinion(
    dart: OpenDartReader,
    *,
    corp_code: str,
    bsns_year: str = "2025",
    reprt_code: str = "11011",
) -> AuditOpinion | None:
    """회사 1건의 회계감사 구조화 정보. 데이터 없으면 ``None``."""
    try:
        df = dart.report(corp_code, "회계감사", bsns_year, reprt_code)
    except Exception:  # noqa: BLE001
        return None
    if df is None or df.empty:
        return None
    return _row_to_opinion(df.iloc[0].to_dict())


# --- 2) "감사보고서제출" 공시 목록 -----------------------------------------------
@dataclass(frozen=True)
class AuditDisclosure:
    rcept_no: str
    rcept_dt: str
    report_nm: str


def list_audit_disclosures(
    api_key: str,
    *,
    corp_code: str,
    start: str,
    end: str,
    exclude_subsidiary: bool = True,
) -> list[AuditDisclosure]:
    """회사의 ``start~end`` 기간 공시 중 "감사보고서" 키워드가 들어간 행만.

    OPENDART list.json 을 직접 호출 (kind 필터 없음 — corp_code 와 함께 쓰면
    상세분류가 미설정인 데이터를 놓치는 quirk 회피).

    :param exclude_subsidiary: ``True`` (기본) 면 "감사보고서제출(자회사의 주요경영사항)"
        같이 자회사 보고서 공시는 제외 — 매핑 회사 본인 것만 남긴다.
    """
    out: list[AuditDisclosure] = []
    bgn = start.replace("-", "")
    end_de = end.replace("-", "")
    page = 1
    while True:
        r = requests.get(
            _LIST_URL,
            params={
                "crtfc_key": api_key,
                "corp_code": corp_code,
                "bgn_de": bgn,
                "end_de": end_de,
                "last_reprt_at": "Y",  # 최종보고서만 (별도 정정공시 제외)
                "page_no": page,
                "page_count": 100,
            },
            timeout=30,
        )
        try:
            j = r.json()
        except Exception:  # noqa: BLE001
            return out
        if str(j.get("status")) != "000" or "list" not in j:
            return out
        for it in j["list"]:
            nm = str(it.get("report_nm") or "")
            if "감사보고서" not in nm:
                continue
            if exclude_subsidiary and ("자회사" in nm or "종속회사" in nm):
                continue
            out.append(
                AuditDisclosure(
                    rcept_no=str(it.get("rcept_no") or "").strip(),
                    rcept_dt=str(it.get("rcept_dt") or "").strip(),
                    report_nm=nm.strip(),
                )
            )
        total_page = int(j.get("total_page") or 1)
        if page >= total_page:
            return out
        page += 1


# --- 3) 첨부 목록 + viewer URL ----------------------------------------------------
@dataclass(frozen=True)
class Attachment:
    title: str
    main_url: str  # http://dart.fss.or.kr/dsaf001/main.do?rcpNo=X&dcmNo=Y
    parent_rcept_no: str
    dcm_no: str

    @property
    def report_kind(self) -> str:
        t = self.title
        if "연결" in t:
            return "연결감사보고서"
        if "결합" in t:
            return "결합감사보고서"
        if "감사의감사" in t:
            return "감사의감사보고서"
        return "감사보고서"


_ATTACH_DCMNO_RE = re.compile(r"dcmNo=(\d+)")
_ATTACH_RCPNO_RE = re.compile(r"rcpNo=(\d+)")
# main.do 페이지의 onload viewDoc 호출 — 단일 첨부의 첨부 식별자가 박혀 있다.
_VIEWDOC_INIT_RE = re.compile(
    r'viewDoc\(\s*"(\d+)"\s*,\s*"(\d+)"\s*,\s*"(\d*)"\s*,\s*"(\d*)"\s*,\s*"(\d*)"\s*,\s*"([\w\.]+)"'
)
# main.do 페이지의 multi-page tree (첨부가 여러 sub 문서로 나뉜 경우)
_MULTI_PAGE_RE = re.compile(
    r"\s+node[12]?\['text'\][ =]+\"(.*?)\";"
    r"\s+node[12]?\['id'\][ =]+\"(\d+)\";"
    r"\s+node[12]?\['rcpNo'\][ =]+\"(\d+)\";"
    r"\s+node[12]?\['dcmNo'\][ =]+\"(\d+)\";"
    r"\s+node[12]?\['eleId'\][ =]+\"(\d+)\";"
    r"\s+node[12]?\['offset'\][ =]+\"(\d+)\";"
    r"\s+node[12]?\['length'\][ =]+\"(\d+)\";"
    r"\s+node[12]?\['dtd'\][ =]+\"(.*?)\";"
)
# 첨부 제목에서 외부감사보고서가 *아닌* 것들을 거른다.
_EXCLUDE_ATTACH_TITLE_RE = re.compile(r"감사위원회|감사의\s*감사")


def list_audit_attachments(
    dart: OpenDartReader, parent_rcept_no: str
) -> list[Attachment]:
    """공시의 첨부 중 제목에 '감사보고서' 가 들어간 항목들."""
    try:
        df = dart.attach_docs(parent_rcept_no, match="감사보고서")
    except Exception:  # noqa: BLE001
        return []
    if df is None or df.empty:
        return []
    out: list[Attachment] = []
    for _, row in df.iterrows():
        title = str(row.get("title") or "").strip()
        url = str(row.get("url") or "").strip()
        if not title or not url or "감사보고서" not in title:
            continue
        # 외부감사보고서가 아닌 첨부 제거: 감사위원회 감사보고서, 감사의 감사보고서.
        if _EXCLUDE_ATTACH_TITLE_RE.search(title):
            continue
        rcp_m = _ATTACH_RCPNO_RE.search(url)
        dcm_m = _ATTACH_DCMNO_RE.search(url)
        if not rcp_m or not dcm_m:
            continue
        out.append(
            Attachment(
                title=title,
                main_url=url,
                parent_rcept_no=rcp_m.group(1),
                dcm_no=dcm_m.group(1),
            )
        )
    return out


def _viewer_url(rcp: str, dcm: str, ele: str, off: str, length: str, dtd: str) -> str:
    return (
        f"http://dart.fss.or.kr/report/viewer.do?rcpNo={rcp}&dcmNo={dcm}"
        f"&eleId={ele}&offset={off}&length={length}&dtd={dtd}"
    )


def _http_get(url: str, *, timeout: float = 60.0) -> str:
    try:
        r = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=timeout)
        return r.text
    except requests.RequestException:
        return ""


def fetch_attachment_body(main_url: str) -> str:
    """첨부 main.do URL → viewer.do URL → raw HTML 본문.

    1차: main.do 페이지에 multi-page tree (sub_docs) 가 있으면 모든 sub viewer 를
    합쳐서 반환 (예: "표지 / 감사보고서 / 첨부재무제표 / 주석 / ...").
    2차: tree 가 없는 단일 첨부면 onload viewDoc 인자로 viewer 한 개 GET.
    """
    main_html = _http_get(main_url, timeout=30)
    if not main_html:
        return ""

    # 1차: multi-page tree
    nodes = _MULTI_PAGE_RE.findall(main_html)
    if nodes:
        parts: list[str] = []
        for _title, _id, rcp, dcm, ele, off, length, dtd in nodes:
            parts.append(_http_get(_viewer_url(rcp, dcm, ele, off, length, dtd)))
        joined = "\n\n".join(p for p in parts if p)
        if joined:
            return joined

    # 2차: 단일 viewDoc
    m = _VIEWDOC_INIT_RE.search(main_html)
    if not m:
        return ""
    return _http_get(_viewer_url(*m.groups()))
