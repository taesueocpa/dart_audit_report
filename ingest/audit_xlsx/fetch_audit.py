"""OPENDART API 호출 + "감사보고서제출" 공시 첨부 본문 다운로드.

세 단계로 구성된다.

1. :func:`fetch_audit_opinion` — ``dart.report(corp, '회계감사', …)`` 로 회사 1건의
   회계감사 구조화 정보 (감사인/감사의견/강조사항/핵심감사사항) 한 행을 받는다.
2. :func:`list_audit_disclosures` — ``list.json`` 으로 기간 내 공시 목록 중
   ``report_nm`` 에 "감사보고서" 키워드가 있는 행만. 자회사 공시는 기본 제외.
3. :func:`list_audit_attachments` + :func:`fetch_attachment_body` —
   ``attach_docs`` 로 감사보고서/연결감사보고서 첨부 목록 + 각 첨부의 main.do
   페이지에서 viewer.do URL 을 파싱해 raw HTML 다운로드.

OPENDART API quirk
~~~~~~~~~~~~~~~~~~~

``corp_code`` 와 ``pblntf_ty='F'`` 를 함께 보내면 0건이 응답된다 (상세분류가
미설정인 공시가 누락). 이 모듈은 ``list.json`` 호출 시 종류 필터를 비우고
응답의 ``report_nm`` 텍스트로 직접 필터링한다.
"""
from __future__ import annotations

import html
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

import requests
from opendartreader import OpenDartReader

from audit_xlsx.extractors import collapse_hangul_spaces

# ---------------------------------------------------------------------------
# HTTP 상수
# ---------------------------------------------------------------------------
#
# 첨부 목록(attach_docs)/뷰어(viewer.do) 는 OPENDART *API* 가 아니라 공시뷰어
# 웹사이트(dart.fss.or.kr)를 스크래핑한다. 이 사이트는 짧은 시간에 동시·연타
# 요청이 들어오면 anti-bot 으로 연결을 강제로 끊는다 (ConnectionReset). API
# 분당 1,000회 한도와 무관하므로, 정상 브라우저처럼 단일 흐름 + 요청 간 최소
# 간격 + 세션 재사용으로 "예의 바르게" 긁어야 차단을 피한다.

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
_VIEWER_BASE = "http://dart.fss.or.kr/report/viewer.do"
_DEFAULT_TIMEOUT = 60.0
_MAIN_DO_TIMEOUT = 30.0
_LIST_PAGE_COUNT = 100
_LIST_TIMEOUT = 30

# 연결 차단(throttle) 시 백오프 재시도. attach_docs 의 "첨부 없음" 예외는
# 재시도하지 않고 즉시 포기 (연결오류만 재시도).
_HTTP_RETRIES = 4
_HTTP_BACKOFF_SEC = 4.0
_ATTACH_RETRIES = 3
_ATTACH_BACKOFF_SEC = 6.0

# dart.fss.or.kr 요청 사이 최소 간격(초) — 모든 워커가 공유하는 전역 rate limit.
_MIN_REQUEST_INTERVAL = 0.4

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": _USER_AGENT})

_THROTTLE_LOCK = threading.Lock()
_last_request_ts = 0.0


def _throttle() -> None:
    """dart.fss.or.kr 요청 전 전역 최소 간격을 강제 (anti-bot 회피)."""
    global _last_request_ts
    with _THROTTLE_LOCK:
        wait = _MIN_REQUEST_INTERVAL - (time.monotonic() - _last_request_ts)
        if wait > 0:
            time.sleep(wait)
        _last_request_ts = time.monotonic()

# ---------------------------------------------------------------------------
# HTML → 평문 변환
# ---------------------------------------------------------------------------

_TAG = re.compile(r"<[^>]+>")
# 단락/표/목록 시작·종료를 줄바꿈으로 변환할 블록 태그.
_BLOCK_TAG = re.compile(
    r"<\s*/?\s*(?:p|br|div|tr|li|h[1-6]|table|thead|tbody)\b[^>]*>", re.IGNORECASE
)
_INLINE_WS = re.compile(r"[ \t\r\f\v]+")


def html_to_text(raw: str) -> str:
    """HTML/XML 태그를 제거해 평문으로 정규화 (줄바꿈 보존).

    블록 태그 → 줄바꿈, 인라인 태그 → 공백, HTML entity 디코드, 줄별
    공백 정리 + 빈 줄 제거.
    """
    if not raw:
        return ""
    s = _BLOCK_TAG.sub("\n", raw)
    s = _TAG.sub(" ", s)
    s = html.unescape(s)
    lines = (_INLINE_WS.sub(" ", line).strip() for line in s.split("\n"))
    return "\n".join(line for line in lines if line)


# ---------------------------------------------------------------------------
# 회계법인 표기 정규화
# ---------------------------------------------------------------------------
#
# OPENDART 응답의 ``adtor`` 필드는 회사마다 표기가 다양하다 (줄바꿈, 공백,
# 부가설명 괄호, 대표이사 정보 첨부 등). 단일 회계법인이 여러 형태로 표시되어
# 통계가 왜곡되는 것을 막기 위해 일관된 형태로 정규화한다.

_FIRM_PARENS = re.compile(r"\s*\([^)]*\)")
_FIRM_HEAD = re.compile(r"^(.{1,30}?회계법인)")  # "...회계법인" 첫 매치
_FIRM_REVERSED = re.compile(r"(회계법인[가-힣A-Za-z\s]{1,15})")  # "회계법인 OOO" 역순
_WS = re.compile(r"\s+")


def normalize_firm_name(s: str) -> str:
    """감사인 표기 정규화 → 일관된 표준형.

    >>> normalize_firm_name('삼정\\n회계법인')
    '삼정회계법인'
    >>> normalize_firm_name('대성회계법인\\n(구, 대성삼경회계법인)')
    '대성회계법인'
    >>> normalize_firm_name('한미회계법인\\n대표이사 정우진')
    '한미회계법인'
    >>> normalize_firm_name('회계법인 리안')
    '회계법인리안'
    """
    if not s:
        return ""
    # 1) 모든 공백 → 단일 공백
    s = _WS.sub(" ", s).strip()
    # 2) 괄호 안 부가설명 제거 — (구, …) (前 …) (PwC) (주1) 등
    s = _FIRM_PARENS.sub("", s).strip()
    # 3) "...회계법인" 첫 매치만 채택 (뒤의 대표이사/주석 등 절단)
    m = _FIRM_HEAD.search(s)
    if m:
        s = m.group(1)
    elif "회계법인" in s:
        m = _FIRM_REVERSED.search(s)
        if m:
            s = m.group(1)
    # 4) 한글 사이 공백 제거 (extractors 의 공통 헬퍼 재사용)
    return collapse_hangul_spaces(s)


# ---------------------------------------------------------------------------
# 1) 회계감사 구조화 4필드
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditOpinion:
    """OPENDART ``accnutAdtorNmNdAdtOpinion`` API 의 당기 행."""

    rcept_no: str          # 사업보고서 접수번호 (참고용)
    corp_code: str
    corp_name: str
    corp_cls: str          # Y/K/N/E (코스피/코스닥/코넥스/기타)
    bsns_year: str         # "당기(2025년 12월 31일)" 등
    stlm_dt: str           # 결산기준일 (YYYY-MM-DD)
    adtor: str             # 감사인(회계법인) — 정규화 적용됨
    adt_opinion: str       # 감사의견 (적정/한정/부적정/의견거절)
    emphs_matter: str      # 강조사항 등 (EOM)
    core_adt_matter: str   # 핵심감사사항 (KAM)


def _clean_field(v: Any) -> str:
    """API 응답 값을 깨끗한 문자열로 — None / 'nan' / 'None' → 빈 문자열."""
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("nan", "none") else s


def _row_to_opinion(row: dict[str, Any]) -> AuditOpinion:
    """report() DataFrame 의 한 행 dict → :class:`AuditOpinion`."""
    return AuditOpinion(
        rcept_no=_clean_field(row.get("rcept_no")),
        corp_code=_clean_field(row.get("corp_code")),
        corp_name=_clean_field(row.get("corp_name")),
        corp_cls=_clean_field(row.get("corp_cls")),
        bsns_year=_clean_field(row.get("bsns_year")),
        stlm_dt=_clean_field(row.get("stlm_dt")),
        adtor=normalize_firm_name(_clean_field(row.get("adtor"))),
        adt_opinion=_clean_field(row.get("adt_opinion")),
        emphs_matter=_clean_field(row.get("emphs_matter")),
        core_adt_matter=_clean_field(row.get("core_adt_matter")),
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
    except Exception:  # noqa: BLE001 — OpenDartReader 가 다양한 예외 던짐
        return None
    if df is None or df.empty:
        return None
    return _row_to_opinion(df.iloc[0].to_dict())


# ---------------------------------------------------------------------------
# 2) "감사보고서제출" 공시 목록
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditDisclosure:
    """list.json 응답 중 감사보고서 관련 공시 1건."""

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
    """기간 내 회사의 공시 중 "감사보고서" 키워드가 들어간 행만.

    OPENDART quirk 회피를 위해 ``kind`` 필터를 보내지 않고 응답 텍스트로
    직접 필터링한다.

    :param exclude_subsidiary: ``True`` (기본) 면 "감사보고서제출(자회사의
        주요경영사항)" 처럼 자회사 보고서 공시는 제외.
    """
    bgn = start.replace("-", "")
    end_de = end.replace("-", "")
    out: list[AuditDisclosure] = []
    page = 1
    while True:
        params = {
            "crtfc_key": api_key,
            "corp_code": corp_code,
            "bgn_de": bgn,
            "end_de": end_de,
            "last_reprt_at": "Y",  # 정정공시 별건은 제외
            "page_no": page,
            "page_count": _LIST_PAGE_COUNT,
        }
        r = requests.get(_LIST_URL, params=params, timeout=_LIST_TIMEOUT)
        try:
            j = r.json()
        except Exception:  # noqa: BLE001
            return out
        if str(j.get("status")) != "000" or "list" not in j:
            return out
        out.extend(_pick_audit_rows(j["list"], exclude_subsidiary=exclude_subsidiary))
        total_page = int(j.get("total_page") or 1)
        if page >= total_page:
            return out
        page += 1


def _pick_audit_rows(
    items: list[dict[str, Any]], *, exclude_subsidiary: bool
) -> list[AuditDisclosure]:
    """list.json 응답의 ``list`` 배열에서 감사보고서 행만 골라 변환."""
    out: list[AuditDisclosure] = []
    for it in items:
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
    return out


# ---------------------------------------------------------------------------
# 3) 첨부 목록 + viewer.do URL
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Attachment:
    """공시 첨부 1건 — 감사보고서 / 연결감사보고서 / 결합감사보고서 등."""

    title: str             # "2026.03.19 감사보고서" 등
    main_url: str          # http://dart.fss.or.kr/dsaf001/main.do?rcpNo=X&dcmNo=Y
    parent_rcept_no: str   # URL 의 rcpNo
    dcm_no: str            # URL 의 dcmNo

    @property
    def report_kind(self) -> str:
        """제목으로 보고서 종류 분류."""
        t = self.title
        if "연결" in t:
            return "연결감사보고서"
        if "결합" in t:
            return "결합감사보고서"
        if "감사의감사" in t:
            return "감사의감사보고서"
        return "감사보고서"


_ATTACH_DCMNO = re.compile(r"dcmNo=(\d+)")
_ATTACH_RCPNO = re.compile(r"rcpNo=(\d+)")
# main.do 페이지 onload viewDoc 호출 — 단일 첨부의 식별자가 박혀 있다.
_VIEWDOC_INIT = re.compile(
    r'viewDoc\(\s*"(\d+)"\s*,\s*"(\d+)"\s*,\s*"(\d*)"\s*,\s*"(\d*)"\s*,\s*"(\d*)"\s*,\s*"([\w\.]+)"'
)
# main.do 페이지 multi-page tree — 첨부가 여러 sub 문서로 나뉜 경우.
_MULTI_PAGE = re.compile(
    r"\s+node[12]?\['text'\][ =]+\"(.*?)\";"
    r"\s+node[12]?\['id'\][ =]+\"(\d+)\";"
    r"\s+node[12]?\['rcpNo'\][ =]+\"(\d+)\";"
    r"\s+node[12]?\['dcmNo'\][ =]+\"(\d+)\";"
    r"\s+node[12]?\['eleId'\][ =]+\"(\d+)\";"
    r"\s+node[12]?\['offset'\][ =]+\"(\d+)\";"
    r"\s+node[12]?\['length'\][ =]+\"(\d+)\";"
    r"\s+node[12]?\['dtd'\][ =]+\"(.*?)\";"
)
# 외부감사보고서가 *아닌* 첨부 제외 — 감사위원회/감사의감사 등.
_EXCLUDE_ATTACH_TITLE = re.compile(r"감사위원회|감사의\s*감사")


def _attach_docs_with_retry(dart: OpenDartReader, parent_rcept_no: str, match: str):
    """``dart.attach_docs`` 호출 — 연결오류(throttle)만 백오프 재시도.

    attach_docs 는 dart.fss.or.kr 스크래핑이라, 연결차단 시 ``RequestException``
    을 던진다(→ 재시도). 반면 첨부가 실제로 없으면 일반 ``Exception``("첨부문서
    를 포함하고 있지 않습니다")을 던지므로 재시도하지 않고 즉시 ``None``.
    """
    for attempt in range(_ATTACH_RETRIES):
        _throttle()
        try:
            return dart.attach_docs(parent_rcept_no, match=match)
        except requests.RequestException:  # 연결차단 = 일시 throttle → 재시도
            if attempt == _ATTACH_RETRIES - 1:
                return None
            time.sleep(_ATTACH_BACKOFF_SEC * (attempt + 1))
        except Exception:  # noqa: BLE001 — 첨부 없음 등 = 즉시 포기
            return None
    return None


def list_audit_attachments(
    dart: OpenDartReader, parent_rcept_no: str
) -> list[Attachment]:
    """공시의 첨부 중 외부감사보고서 (감사/연결감사/결합감사) 만."""
    df = _attach_docs_with_retry(dart, parent_rcept_no, "감사보고서")
    if df is None or df.empty:
        return []
    out: list[Attachment] = []
    for _, row in df.iterrows():
        title = str(row.get("title") or "").strip()
        url = str(row.get("url") or "").strip()
        if not title or not url or "감사보고서" not in title:
            continue
        if _EXCLUDE_ATTACH_TITLE.search(title):
            continue
        rcp = _ATTACH_RCPNO.search(url)
        dcm = _ATTACH_DCMNO.search(url)
        if not rcp or not dcm:
            continue
        out.append(
            Attachment(title=title, main_url=url, parent_rcept_no=rcp.group(1), dcm_no=dcm.group(1))
        )
    return out


# ---------------------------------------------------------------------------
# 첨부 본문 다운로드
# ---------------------------------------------------------------------------


def _viewer_url(rcp: str, dcm: str, ele: str, off: str, length: str, dtd: str) -> str:
    return (
        f"{_VIEWER_BASE}?rcpNo={rcp}&dcmNo={dcm}"
        f"&eleId={ele}&offset={off}&length={length}&dtd={dtd}"
    )


def _http_get(url: str, *, timeout: float = _DEFAULT_TIMEOUT) -> str:
    """raw text GET (세션 재사용 + 전역 rate limit).

    throttle(ConnectionReset)/타임아웃 시 백오프 재시도. 모든 재시도 소진 후
    에도 실패하면 빈 문자열.
    """
    for attempt in range(_HTTP_RETRIES):
        _throttle()
        try:
            r = _SESSION.get(url, timeout=timeout)
            return r.text
        except requests.RequestException:
            if attempt == _HTTP_RETRIES - 1:
                return ""
            time.sleep(_HTTP_BACKOFF_SEC * (attempt + 1))
    return ""


def fetch_attachment_body(main_url: str) -> str:
    """첨부 main.do → viewer.do → raw HTML 본문 (합본).

    1차: main.do 페이지에 multi-page tree (sub_docs) 가 있으면 모든 sub
    viewer 를 합쳐서 반환 (예: "표지 / 감사보고서 / 첨부재무제표 / ...").
    2차: 단일 첨부면 onload viewDoc 인자로 viewer 한 개 GET.
    """
    main_html = _http_get(main_url, timeout=_MAIN_DO_TIMEOUT)
    if not main_html:
        return ""

    # 1차: multi-page tree
    nodes = _MULTI_PAGE.findall(main_html)
    if nodes:
        parts = [_http_get(_viewer_url(rcp, dcm, ele, off, length, dtd))
                 for _title, _id, rcp, dcm, ele, off, length, dtd in nodes]
        joined = "\n\n".join(p for p in parts if p)
        if joined:
            return joined

    # 2차: 단일 viewDoc
    m = _VIEWDOC_INIT.search(main_html)
    if m is None:
        return ""
    return _http_get(_viewer_url(*m.groups()))
