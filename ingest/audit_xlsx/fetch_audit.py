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
_USER_AGENT = "Mozilla/5.0"
_LIST_URL = "https://opendart.fss.or.kr/api/list.json"


def html_to_text(raw: str) -> str:
    """OPENDART 응답의 XML/HTML 태그를 제거해 평문 1줄로 정규화."""
    if not raw:
        return ""
    s = _TAG_RE.sub(" ", raw)
    s = html.unescape(s)
    return _WS_RE.sub(" ", s).strip()


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
        adtor=_s(row.get("adtor")),
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


def fetch_attachment_body(main_url: str) -> str:
    """첨부 main.do URL → viewer.do URL → raw HTML 본문.

    main.do 페이지의 onload `viewDoc(...)` 인자에 박힌 (rcpNo,dcmNo,eleId,offset,length,dtd)
    를 파싱해 viewer.do URL 을 구성하고 GET 한다.
    """
    try:
        r1 = requests.get(main_url, headers={"User-Agent": _USER_AGENT}, timeout=30)
    except requests.RequestException:
        return ""
    m = _VIEWDOC_INIT_RE.search(r1.text)
    if not m:
        return ""
    rcp, dcm, ele, off, length, dtd = m.groups()
    viewer = (
        f"http://dart.fss.or.kr/report/viewer.do?rcpNo={rcp}&dcmNo={dcm}"
        f"&eleId={ele}&offset={off}&length={length}&dtd={dtd}"
    )
    try:
        r2 = requests.get(viewer, headers={"User-Agent": _USER_AGENT}, timeout=60)
    except requests.RequestException:
        return ""
    return r2.text
