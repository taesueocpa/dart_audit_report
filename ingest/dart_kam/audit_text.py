"""Read OPENDART disclosure ZIPs and flatten their XML into plain text.

DART 가 내려주는 공시 원본 ZIP은 여러 개의 XML(주재무제표, 연결재무제표, 보고서 본문 등)
을 포함한다. 감사보고서·KAM 추출을 위해서는 그 중 "가장 본문 같은" XML 하나를 골라
태그를 제거한 평문으로 변환해야 한다.

이 모듈은 그 단계만 담당한다 (실제 정보 추출은 :mod:`dart_kam.audit_extractors`).
"""

from __future__ import annotations

import io
import zipfile
from typing import Final

from lxml import etree

from dart_kam.config import Settings
from dart_kam.paths import raw_zip_path


# 본문 후보 XML을 가산점 방식으로 스코어링할 때 쓰는 키워드.
# 이 중 하나라도 본문에 포함되면 점수가 1,000,000 씩 가산되어 압도적으로 선택된다.
_BODY_KEYWORDS: Final[tuple[str, ...]] = (
    "핵심감사사항",
    "핵심 감사사항",
    "Key Audit Matters",
    "감사의견",
    "독립된 감사인",
)
_KEYWORD_BONUS: Final[float] = 1_000_000.0


def _flatten_xml_text(xml_bytes: bytes) -> str:
    """XML 바이트 → 모든 요소의 ``text``/``tail`` 을 공백 1개로 합친 평문."""
    parser = etree.XMLParser(huge_tree=True, recover=True)
    root = etree.fromstring(xml_bytes, parser=parser)
    parts: list[str] = []
    for element in root.iter():
        if element.text:
            parts.append(element.text)
        if element.tail:
            parts.append(element.tail)
    return " ".join(s for s in (p.strip() for p in parts) if s)


def _score_xml_candidate(raw: bytes) -> float:
    """파일 크기 + 키워드 보너스로 본문 가능성을 점수화."""
    try:
        text = raw.decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001
        text = ""
    score = float(len(raw))
    for kw in _BODY_KEYWORDS:
        if kw in text:
            score += _KEYWORD_BONUS
    return score


def pick_main_xml(zf: zipfile.ZipFile) -> bytes:
    """ZIP 안에서 본문에 가장 가까운 XML 파일을 골라 그 바이트를 반환.

    :raises ValueError: ZIP 안에 ``.xml`` 이 하나도 없을 때.
    """
    xml_names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
    if not xml_names:
        raise ValueError("ZIP contains no XML files")

    best_name: str | None = None
    best_score = -1.0
    for name in xml_names:
        raw = zf.read(name)
        score = _score_xml_candidate(raw)
        if score > best_score:
            best_score = score
            best_name = name

    assert best_name is not None  # xml_names 가 비어있지 않다고 위에서 보장됨.
    return zf.read(best_name)


def load_filing_flat_text(settings: Settings, rcept_no: str) -> str:
    """저장된 공시 ZIP → 본문 XML 한 개를 평문으로 펼친 단일 문자열.

    :param rcept_no: 14자리 접수번호 (공백 허용).
    :raises FileNotFoundError: 해당 ``rcept_no`` ZIP 이 디스크에 없을 때.
    """
    path = raw_zip_path(settings, rcept_no.strip())
    if not path.exists():
        raise FileNotFoundError(str(path))
    with zipfile.ZipFile(io.BytesIO(path.read_bytes())) as zf:
        xml_bytes = pick_main_xml(zf)
    return _flatten_xml_text(xml_bytes)
