"""Heuristic extraction of audit opinion and KAM from DART disclosure XML inside ZIP."""

from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime, timezone

from lxml import etree

from dart_kam.config import Settings
from dart_kam.paths import raw_zip_path
from dart_kam.progress_util import progress_print


_WS = re.compile(r"\s+")

# 단일 공시 XML: 목차 등에 첫 번째 제목이 나올 수 있어, **두 번째** 「독립된 감사인의 감사보고서」부터
# 「현재로 유효한 것입니다.」까지를 감사보고서 본문으로 저장
_AUDIT_REPORT_TITLE = re.compile(r"독립된\s*감사인의\s*감사보고서")
_AUDIT_REPORT_BODY_END = re.compile(r"현재로\s*유효한\s*것입니다\.?")
_MAX_AUDIT_REPORT_BODY = 2_000_000


def extract_standalone_audit_report_body(text: str) -> str | None:
    """두 번째 '독립된 감사인의 감사보고서'부터 '현재로 유효한 것입니다.'까지. 없으면 None."""
    matches = list(_AUDIT_REPORT_TITLE.finditer(text))
    if len(matches) < 2:
        return None
    start = matches[1].start()
    m1 = _AUDIT_REPORT_BODY_END.search(text, pos=start)
    if not m1:
        return None
    end = m1.end()
    block = _WS.sub(" ", text[start:end]).strip()
    if len(block) < 40:
        return None
    return block[:_MAX_AUDIT_REPORT_BODY]


def _flatten_xml_text(xml_bytes: bytes) -> str:
    parser = etree.XMLParser(huge_tree=True, recover=True)
    root = etree.fromstring(xml_bytes, parser=parser)
    texts: list[str] = []
    for el in root.iter():
        if el.text:
            texts.append(el.text)
        if el.tail:
            texts.append(el.tail)
    blob = _WS.sub(" ", " ".join(texts)).strip()
    return blob


def _pick_main_xml(zf: zipfile.ZipFile) -> bytes:
    names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
    if not names:
        raise ValueError("ZIP contains no XML files")
    best_name = None
    best_score = -1.0
    for n in names:
        raw = zf.read(n)
        try:
            text = raw.decode("utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            text = ""
        score = float(len(raw))
        for kw in ("핵심감사사항", "핵심 감사사항", "Key Audit Matters", "감사의견", "독립된 감사인"):
            if kw in text:
                score += 1_000_000.0
        if score > best_score:
            best_score = score
            best_name = n
    assert best_name is not None
    return zf.read(best_name)


_OPINION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("부적정의견", re.compile(r"부적정\s*의견")),
    ("의견거절", re.compile(r"의견\s*거절|거절\s*의견")),
    ("한정의견", re.compile(r"한정\s*의견")),
    ("감사범위제한", re.compile(r"감사의\s*범위가\s*한|감사\s*범위\s*제한")),
    ("적정의견", re.compile(r"적정\s*의견|공정하게\s*표시|재무제표는.*적정")),
]

_NON_CLEAN = frozenset({"한정의견", "부적정의견", "의견거절", "감사범위제한"})


def classify_opinion(text: str) -> tuple[str | None, str | None]:
    """Return (label, short_snippet)."""
    idxs: list[tuple[int, str, re.Pattern[str]]] = []
    for label, pat in _OPINION_PATTERNS:
        m = pat.search(text)
        if m:
            idxs.append((m.start(), label, pat))
    if not idxs:
        return None, None
    idxs.sort(key=lambda x: x[0])
    _, label, pat = idxs[0]
    m = pat.search(text)
    if not m:
        return label, None
    start = max(0, m.start() - 80)
    end = min(len(text), m.end() + 160)
    return label, text[start:end].strip()


def extract_opinion_modification_reason(text: str, opinion_label: str | None) -> str | None:
    """한정·비적정 등 비적정적 의견에 대한 근거·사유 구간(휴리스틱)."""
    if not opinion_label or opinion_label not in _NON_CLEAN:
        return None
    for lab, pat in _OPINION_PATTERNS:
        if lab != opinion_label:
            continue
        m = pat.search(text)
        if not m:
            return None
        window = text[m.start() : m.start() + 2800]
        stop = re.search(
            r"(?:핵심\s*감사사항|핵심감사사항|강조\s*사항|기타\s*사항|독립된\s*감사인의\s*책임)",
            window[280:],
        )
        if stop:
            window = window[: 280 + stop.start()]
        s = _WS.sub(" ", window).strip()
        return s[:2800] if s else None
    return None


def detect_accounting_standard(text: str) -> str | None:
    """문서에 '한국채택국제회계기준' 또는 '일반기업회계기준'이 보이면 해당 문자열로 저장."""
    kifrs = re.search(r"한국채택국제회계기준|한국\s*채택\s*국제\s*회계기준", text)
    kgaap = re.search(r"일반기업회계기준", text)
    if kifrs and (not kgaap or kifrs.start() <= kgaap.start()):
        return "한국채택국제회계기준"
    if kgaap:
        return "일반기업회계기준"
    return None


def extract_auditor_firm(text: str) -> str | None:
    """'~~회계법인' 또는 '회계법인~~' 형태의 감사인(회계법인) 명칭."""
    head = text[: min(len(text), 400_000)]
    m = re.search(r"([가-힣A-Za-z0-9·\(\)\[\]\s]{1,48}회계법인)", head)
    if m:
        s = _WS.sub(" ", m.group(1).strip())
        if len(s) >= 5:
            return s[:160]
    m2 = re.search(r"(회계법인[가-힣A-Za-z0-9·\s]{0,32}?)(?=\s*(?:의|은|는|으로|에서|\.|,|\)|$))", head)
    if m2:
        s = _WS.sub(" ", m2.group(1).strip())
        if len(s) >= 4:
            return s[:160]
    return None


def extract_cpa_partner(text: str) -> str | None:
    """말미 '공인회계사 ~~~입니다' 형식 우선."""
    tail = text[-8000:]
    m = re.search(
        r"공인회계사\s+([가-힣A-Za-z·\s]{2,45}?)\s*(?:입니다|임\.|이고|으로\s*갑)",
        tail,
    )
    if m:
        s = _WS.sub(" ", m.group(1).strip())
        if 2 <= len(s) <= 100 and "회계법인" not in s:
            return s
    for pat in (
        r"업무수행\s*공인회계사\s*[:：\s]*([가-힣A-Za-z·\s]{2,50}?)(?=\s*(?:\(|공인회계사\)|등록|$|\n))",
        r"등록\s*공인회계사\s*[:：\s]*([가-힣A-Za-z·\s]{2,45})",
    ):
        m2 = re.search(pat, text)
        if m2:
            s = _WS.sub(" ", m2.group(1).strip())
            if 2 <= len(s) <= 120 and "회계법인" not in s:
                return s
    return None


_KAM_HEADER = re.compile(
    r"(핵심\s*감사사항|핵심감사사항|Key\s*Audit\s*Matters)",
    re.IGNORECASE,
)


def _next_section_start(text: str, start: int, patterns: list[re.Pattern[str]]) -> int:
    end = len(text)
    for p in patterns:
        m = p.search(text, pos=start)
        if m:
            end = min(end, m.start())
    return end


_EOM_HEADER = re.compile(r"강조\s*사항|Emphasis\s+of\s+Matter", re.IGNORECASE)
_OTHER_HEADER = re.compile(r"기타\s*사항|Other\s+Matter|기타\s*의\s*감사\s*사항", re.IGNORECASE)


def extract_emphasis_of_matter(text: str) -> tuple[bool, str | None]:
    h = _EOM_HEADER.search(text)
    if not h:
        return False, None
    start = h.end()
    stops = [
        re.compile(r"기타\s*사항|기타\s*의\s*감사", re.I),
        re.compile(r"핵심\s*감사|핵심감사사항|Key\s*Audit", re.I),
        re.compile(r"의견\s*거절|한정\s*의견|부적정\s*의견", re.I),
    ]
    end = _next_section_start(text, start, stops)
    body = _WS.sub(" ", text[start:end]).strip()
    if len(body) < 25:
        return False, None
    return True, body[:8000]


def extract_other_matters(text: str) -> tuple[bool, str | None]:
    h = _OTHER_HEADER.search(text)
    if not h:
        return False, None
    start = h.end()
    stops = [
        re.compile(r"강조\s*사항|Emphasis\s+of\s+Matter", re.I),
        re.compile(r"핵심\s*감사|핵심감사사항|Key\s*Audit", re.I),
    ]
    end = _next_section_start(text, start, stops)
    body = _WS.sub(" ", text[start:end]).strip()
    if len(body) < 25:
        return False, None
    return True, body[:8000]


def extract_kam_section_full(text: str) -> str | None:
    """'핵심감사사항' 제목부터 다음 주요 절까지 본문 전체(정규화 없이)."""
    m = _KAM_HEADER.search(text)
    if not m:
        return None
    start = m.start()
    stop_pat = re.compile(
        r"(감사가\s*재무제표\s*감사를\s*수행|기타\s*필수적\s*감사|기타\s*경영자의\s*책임|"
        r"재무제표에\s*대한\s*경영자의\s*책임|독립된\s*감사인의\s*책임|강조\s*사항|기타\s*사항)"
    )
    sm = stop_pat.search(text, pos=m.end())
    end = sm.start() if sm else len(text)
    block = _WS.sub(" ", text[start:end]).strip()
    if len(block) < 12:
        return None
    return block[:500_000]


def load_filing_flat_text(settings: Settings, rcept_no: str) -> str:
    """다운로드된 공시 ZIP에서 주요 XML을 골라 태그 제거 평문 한 덩어리로 반환."""
    path = raw_zip_path(settings, rcept_no.strip())
    if not path.exists():
        raise FileNotFoundError(str(path))
    zf = zipfile.ZipFile(io.BytesIO(path.read_bytes()))
    xml_bytes = _pick_main_xml(zf)
    return _flatten_xml_text(xml_bytes)


def parse_filing_zip(settings: Settings, rcept_no: str) -> dict[str, object]:
    flat = load_filing_flat_text(settings, rcept_no)
    audit_body = extract_standalone_audit_report_body(flat)
    work = audit_body if audit_body else flat
    label, snip = classify_opinion(work)
    mod_reason = extract_opinion_modification_reason(work, label)
    acct_std = detect_accounting_standard(work)
    firm = extract_auditor_firm(work)
    cpa = extract_cpa_partner(work)
    emph_ok, emph_body = extract_emphasis_of_matter(work)
    other_ok, other_body = extract_other_matters(work)
    kam_full = extract_kam_section_full(work)
    kam_has = bool(kam_full and len(kam_full.strip()) > 0)
    return {
        "opinion_label": label,
        "opinion_raw_snippet": snip,
        "opinion_modification_reason": mod_reason,
        "accounting_standard": acct_std,
        "auditor_firm": firm,
        "auditor_name": firm,
        "cpa_partner_name": cpa,
        "emphasis_of_matter_present": 1 if emph_ok else 0,
        "emphasis_of_matter_content": emph_body,
        "other_matters_present": 1 if other_ok else 0,
        "other_matters_content": other_body,
        "kam_section_full": kam_full,
        "kam_count": 1 if kam_has else 0,
        "audit_report_body": audit_body,
    }


def ingest_parse_results(
    settings: Settings,
    conn,
    *,
    limit: int | None = None,
    force: bool = False,
    progress: bool = True,
) -> tuple[int, int]:
    """Parse filings that are downloaded but not yet parsed (or failed). Returns (ok, fail).

    ``force``: True면 이미 성공 파싱된 접수번호도 다시 파싱.
    """
    now = datetime.now(timezone.utc).isoformat()
    if force:
        where_parse = "WHERE 1=1"
    else:
        where_parse = "WHERE p.rcept_no IS NULL OR p.parse_error IS NOT NULL"
    q = f"""
    SELECT f.rcept_no
    FROM filings f
    JOIN document_fetch d ON d.rcept_no = f.rcept_no AND d.status = 'downloaded'
    LEFT JOIN parse_results p ON p.rcept_no = f.rcept_no
    {where_parse}
    ORDER BY f.rcept_dt DESC
    """
    cur = conn.execute(q)
    rcepts = [r[0] for r in cur.fetchall()]
    if limit is not None:
        rcepts = rcepts[: max(0, limit)]

    total = len(rcepts)
    progress_print(
        f"ZIP 파싱 — 대상 총 {total}건"
        + ("" if force else " (다운로드 완료·미파싱 또는 실패 재시도)"),
        enabled=progress,
    )
    if total == 0:
        progress_print("파싱할 접수번호가 없습니다.", enabled=progress)
        return 0, 0

    ok = 0
    bad = 0
    for idx, rcept_no in enumerate(rcepts, start=1):
        remain = total - idx
        progress_print(
            f"총 {total}건 중 {idx}번째 (남음 {remain}건) — rcept_no={rcept_no} 파싱 중…",
            enabled=progress,
        )
        try:
            res = parse_filing_zip(settings, rcept_no)
            conn.execute("DELETE FROM kam_items WHERE rcept_no = ?", (rcept_no,))
            kam_full = res.get("kam_section_full")
            if isinstance(kam_full, str) and len(kam_full.strip()) > 0:
                snippet = kam_full.strip()[:800]
                conn.execute(
                    """
                    INSERT INTO kam_items(
                      rcept_no, ordinal, title, body_snippet, kam_content, selection_reason
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (rcept_no, 1, "핵심감사사항", snippet, kam_full.strip(), None),
                )
            conn.execute(
                """
                INSERT INTO parse_results(
                  rcept_no, parser_version, opinion_label, opinion_raw_snippet,
                  opinion_modification_reason, accounting_standard,
                  auditor_firm, auditor_name, cpa_partner_name, kam_count,
                  emphasis_of_matter_present, emphasis_of_matter_content,
                  other_matters_present, other_matters_content,
                  kam_section_full, audit_report_body,
                  parsed_at, parse_error
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(rcept_no) DO UPDATE SET
                  parser_version=excluded.parser_version,
                  opinion_label=excluded.opinion_label,
                  opinion_raw_snippet=excluded.opinion_raw_snippet,
                  opinion_modification_reason=excluded.opinion_modification_reason,
                  accounting_standard=excluded.accounting_standard,
                  auditor_firm=excluded.auditor_firm,
                  auditor_name=excluded.auditor_name,
                  cpa_partner_name=excluded.cpa_partner_name,
                  kam_count=excluded.kam_count,
                  emphasis_of_matter_present=excluded.emphasis_of_matter_present,
                  emphasis_of_matter_content=excluded.emphasis_of_matter_content,
                  other_matters_present=excluded.other_matters_present,
                  other_matters_content=excluded.other_matters_content,
                  kam_section_full=excluded.kam_section_full,
                  audit_report_body=excluded.audit_report_body,
                  parsed_at=excluded.parsed_at,
                  parse_error=excluded.parse_error
                """,
                (
                    rcept_no,
                    settings.parser_version,
                    res.get("opinion_label"),
                    res.get("opinion_raw_snippet"),
                    res.get("opinion_modification_reason"),
                    res.get("accounting_standard"),
                    res.get("auditor_firm"),
                    res.get("auditor_name"),
                    res.get("cpa_partner_name"),
                    int(res.get("kam_count") or 0),  # type: ignore[arg-type]
                    int(res.get("emphasis_of_matter_present") or 0),  # type: ignore[arg-type]
                    res.get("emphasis_of_matter_content"),
                    int(res.get("other_matters_present") or 0),  # type: ignore[arg-type]
                    res.get("other_matters_content"),
                    res.get("kam_section_full"),
                    res.get("audit_report_body"),
                    now,
                    None,
                ),
            )
            ok += 1
            progress_print(
                f"파싱 완료 {idx}/{total} — 성공 누적 {ok}건, 실패 {bad}건 — 의견={res.get('opinion_label')!s} · KAM {res.get('kam_count')}건",
                enabled=progress,
            )
        except Exception as e:  # noqa: BLE001
            bad += 1
            progress_print(
                f"파싱 실패 {idx}/{total} — 성공 누적 {ok}건, 실패 누적 {bad}건 — {rcept_no}: {str(e)[:200]}",
                enabled=progress,
            )
            conn.execute(
                """
                INSERT INTO parse_results(
                  rcept_no, parser_version, opinion_label, opinion_raw_snippet,
                  opinion_modification_reason, accounting_standard,
                  auditor_firm, auditor_name, cpa_partner_name, kam_count,
                  emphasis_of_matter_present, emphasis_of_matter_content,
                  other_matters_present, other_matters_content,
                  kam_section_full, audit_report_body,
                  parsed_at, parse_error
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(rcept_no) DO UPDATE SET
                  parser_version=excluded.parser_version,
                  opinion_label=NULL,
                  opinion_raw_snippet=NULL,
                  opinion_modification_reason=NULL,
                  accounting_standard=NULL,
                  auditor_firm=NULL,
                  auditor_name=NULL,
                  cpa_partner_name=NULL,
                  kam_count=0,
                  emphasis_of_matter_present=0,
                  emphasis_of_matter_content=NULL,
                  other_matters_present=0,
                  other_matters_content=NULL,
                  kam_section_full=NULL,
                  audit_report_body=NULL,
                  parsed_at=excluded.parsed_at,
                  parse_error=excluded.parse_error
                """,
                (
                    rcept_no,
                    settings.parser_version,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    0,
                    0,
                    None,
                    0,
                    None,
                    None,
                    None,
                    now,
                    str(e),
                ),
            )
        conn.commit()
    progress_print(f"파싱 단계 종료 — 성공 {ok}건, 실패 {bad}건 (대상 {total}건)", enabled=progress)
    return ok, bad
