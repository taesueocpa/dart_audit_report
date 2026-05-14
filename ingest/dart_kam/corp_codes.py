"""Download and parse OPENDART corporation code ZIP (corpCode.xml)."""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

from dart_kam.config import Settings
from dart_kam.dart_client import DartClient
from dart_kam.paths import corp_zip_path
from dart_kam.progress_util import progress_print


def download_corp_zip(client: DartClient, settings: Settings) -> bytes:
    # Binary ZIP from OPENDART
    return client.get_bytes("corpCode.xml", {})


def refresh_corp_codes(client: DartClient, settings: Settings, conn, *, progress: bool = True) -> int:
    progress_print("corpCode.xml 다운로드 중…", enabled=progress)
    raw = download_corp_zip(client, settings)
    progress_print(f"corpCode.xml 다운로드 완료 — 수신 {len(raw):,}바이트", enabled=progress)
    path = corp_zip_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)

    root: ET.Element
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
        inner_name = next((n for n in zf.namelist() if n.upper().endswith("CORPCODE.XML")), None)
        if not inner_name:
            raise ValueError("corpCode.zip does not contain CORPCODE.xml")
        inner = zf.read(inner_name)
        root = ET.fromstring(inner)
    except zipfile.BadZipFile:
        # Some environments return the XML payload directly.
        root = ET.fromstring(raw)
    rows = 0
    now = datetime.now(timezone.utc).isoformat()
    for row in root.findall(".//list"):
        corp_code = (row.findtext("corp_code") or "").strip()
        if not corp_code:
            continue
        corp_name = (row.findtext("corp_name") or "").strip()
        stock_code = (row.findtext("stock_code") or "").strip()
        corp_cls = None  # corpCode file does not always include market class; filings will.
        conn.execute(
            """
            INSERT INTO companies(corp_code, corp_name, stock_code, corp_cls, updated_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(corp_code) DO UPDATE SET
              corp_name=excluded.corp_name,
              stock_code=excluded.stock_code,
              updated_at=excluded.updated_at
            """,
            (corp_code, corp_name, stock_code, corp_cls, now),
        )
        rows += 1
        if progress and rows > 0 and rows % 5000 == 0:
            progress_print(f"companies 테이블 반영 중… {rows:,}건 처리됨", enabled=progress)
    conn.commit()
    progress_print(f"companies 테이블 반영 완료 — 총 {rows:,}건", enabled=progress)
    return rows
