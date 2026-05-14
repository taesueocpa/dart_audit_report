"""Download and parse OPENDART corporation code ZIP (corpCode.xml).

OPENDART ``corpCode.xml`` 응답은 ZIP 안에 ``CORPCODE.xml`` 한 개가 들어있는 형태가
표준이지만, 일부 환경에서는 XML 본문이 그대로 내려오기도 한다. 두 경우 모두 처리한다.
"""

from __future__ import annotations

import io
import sqlite3
import zipfile
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

from dart_kam.config import Settings
from dart_kam.dart_client import DartClient
from dart_kam.paths import corp_zip_path
from dart_kam.progress_util import progress_print
from dart_kam.repository import upsert_company


_PROGRESS_BATCH_SIZE = 5000  # N건마다 진행 메시지 한 줄.


def download_corp_zip(client: DartClient, settings: Settings) -> bytes:  # noqa: ARG001
    """``corpCode.xml`` 응답(보통 ZIP 바이너리) raw bytes."""
    return client.get_bytes("corpCode.xml", {})


def _parse_corp_xml(raw: bytes) -> ET.Element:
    """raw 응답이 ZIP 이면 안에서 CORPCODE.xml 추출, 아니면 XML 본문 그대로 파싱."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            inner_name = next(
                (n for n in zf.namelist() if n.upper().endswith("CORPCODE.XML")),
                None,
            )
            if not inner_name:
                raise ValueError("corpCode.zip does not contain CORPCODE.xml")
            return ET.fromstring(zf.read(inner_name))
    except zipfile.BadZipFile:
        return ET.fromstring(raw)


def refresh_corp_codes(
    client: DartClient,
    settings: Settings,
    conn: sqlite3.Connection,
    *,
    progress: bool = True,
) -> int:
    """corpCode.xml 을 다운로드하고 ``companies`` 테이블에 업서트. 처리 건수 반환."""
    progress_print("corpCode.xml 다운로드 중", enabled=progress)
    raw = download_corp_zip(client, settings)
    progress_print(
        f"corpCode.xml 다운로드 완료 - 수신 {len(raw):,}바이트", enabled=progress
    )

    zip_path = corp_zip_path(settings)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    zip_path.write_bytes(raw)

    root = _parse_corp_xml(raw)
    now = datetime.now(timezone.utc).isoformat()
    rows = 0
    for entry in root.findall(".//list"):
        corp_code = (entry.findtext("corp_code") or "").strip()
        if not corp_code:
            continue
        upsert_company(
            conn,
            corp_code=corp_code,
            corp_name=(entry.findtext("corp_name") or "").strip(),
            stock_code=(entry.findtext("stock_code") or "").strip(),
            # corpCode.xml 자체에는 corp_cls(시장구분) 가 없다 — list.json 단계에서 채워짐.
            corp_cls=None,
            updated_at=now,
        )
        rows += 1
        if progress and rows and rows % _PROGRESS_BATCH_SIZE == 0:
            progress_print(
                f"companies 테이블 반영 중 - {rows:,}건 처리됨", enabled=progress
            )

    conn.commit()
    progress_print(f"companies 테이블 반영 완료 - 총 {rows:,}건", enabled=progress)
    return rows
