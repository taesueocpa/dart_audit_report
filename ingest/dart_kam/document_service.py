"""Download original disclosure ZIP per rcept_no (document.json / document.xml)."""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from dart_kam.config import Settings
from dart_kam.dart_client import DartClient
from dart_kam.paths import raw_zip_path
from dart_kam.progress_util import progress_print


def _xml_local_tag(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _document_b64_from_xml(root: ET.Element) -> str | None:
    for el in root.iter():
        if _xml_local_tag(el.tag).lower() == "document" and el.text:
            t = el.text.strip()
            if t:
                return t
    return None


def _document_b64_from_json(payload: dict[str, object]) -> str | None:
    for key in ("document", "Document"):
        v = payload.get(key)
        if isinstance(v, str) and len(v.strip()) > 0:
            return v.strip()
    return None


def _write_zip_bytes(path: Path, data: bytes) -> None:
    if len(data) < 4 or data[:2] != b"PK":
        raise RuntimeError(
            f"payload is not a ZIP (starts with {data[:8]!r}, len={len(data)})"
        )
    path.write_bytes(data)


def _write_zip_from_b64(path: Path, b64: str) -> None:
    try:
        data = base64.b64decode(b64, validate=False)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"base64 decode failed: {e}") from e
    _write_zip_bytes(path, data)


def fetch_document_zip(client: DartClient, settings: Settings, rcept_no: str) -> Path:
    """Fetch 공시서류 원본 ZIP.

    OPENDART 가이드: ``document.xml`` 응답은 **Zip 바이너리**가 일반적이다.
    오류·예외 시 JSON/XML·``document.json``(base64) 경로를 순차 시도한다.
    """
    path = raw_zip_path(settings, rcept_no.strip())
    path.parent.mkdir(parents=True, exist_ok=True)
    rno = rcept_no.strip()
    if len(rno) != 14 or not rno.isdigit():
        raise RuntimeError(f"invalid rcept_no (expected 14 digits): {rcept_no!r}")

    raw = client.get_bytes("document.xml", {"rcept_no": rno}, timeout=300.0)
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    if len(raw) >= 2 and raw[:2] == b"PK":
        _write_zip_bytes(path, raw)
        return path

    strip = raw.lstrip()
    if strip[:1] in (b"{", b"["):
        try:
            jo = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            head = raw[: min(400, len(raw))]
            raise RuntimeError(
                "document.xml: ZIP가 아니고 JSON 파싱에도 실패했습니다. "
                f"Raw prefix: {head!r}"
            ) from e
        if isinstance(jo, dict):
            st = str(jo.get("status", "")).strip()
            if st != "000":
                raise RuntimeError(
                    f"document.xml OPENDART status={st} message={jo.get('message', jo)}"
                )
            b64 = _document_b64_from_json(jo)
            if b64:
                _write_zip_from_b64(path, b64)
                return path

    b64_xml: str | None = None
    try:
        root = ET.fromstring(raw)
        b64_xml = _document_b64_from_xml(root)
    except ET.ParseError:
        pass
    if b64_xml:
        _write_zip_from_b64(path, b64_xml)
        return path

    head_preview = raw[: min(400, len(raw))]
    payload = client.get_json("document.json", {"rcept_no": rno}, timeout=300.0)
    if not isinstance(payload, dict):
        raise RuntimeError(f"document.json returned non-object: {type(payload).__name__}")
    st = str(payload.get("status", "")).strip()
    if st != "000":
        msg = payload.get("message", payload)
        raise RuntimeError(
            f"OPENDART document.json status={st} message={msg} "
            f"(document.xml ZIP/XML 실패, prefix={head_preview!r})"
        )
    b64j = _document_b64_from_json(payload)
    if not b64j:
        raise RuntimeError(
            "Missing base64 in document.json and document.xml was not a ZIP. "
            f"JSON keys: {list(payload.keys())} xml_prefix={head_preview!r}"
        )
    _write_zip_from_b64(path, b64j)
    return path


def _select_rcept_nos_for_download(
    conn,
    *,
    skip_downloaded: bool,
    limit: int | None,
) -> list[str]:
    if skip_downloaded:
        cur = conn.execute(
            """
            SELECT f.rcept_no FROM filings f
            LEFT JOIN document_fetch d ON d.rcept_no = f.rcept_no
            WHERE d.rcept_no IS NULL OR d.status != 'downloaded'
            ORDER BY f.rcept_dt DESC
            """
        )
    else:
        cur = conn.execute("SELECT f.rcept_no FROM filings f ORDER BY f.rcept_dt DESC")
    rows = [str(r[0]) for r in cur.fetchall()]
    if limit is not None:
        rows = rows[: max(0, limit)]
    return rows


def ingest_documents(
    client: DartClient,
    settings: Settings,
    conn,
    *,
    limit: int | None = None,
    skip_downloaded: bool = True,
    verbose: bool = False,
    rcept_nos: Sequence[str] | None = None,
    progress: bool = True,
) -> tuple[int, int]:
    """Return (downloaded_count, failed_count).

    If ``rcept_nos`` is set, only those 접수번호 are processed (``skip_downloaded`` ignored for selection).
    """
    now = datetime.now(timezone.utc).isoformat()
    if rcept_nos is not None:
        rows = [str(x).strip() for x in rcept_nos if str(x).strip()]
        if limit is not None:
            rows = rows[: max(0, limit)]
    else:
        rows = _select_rcept_nos_for_download(conn, skip_downloaded=skip_downloaded, limit=limit)

    total = len(rows)
    progress_print(f"문서(ZIP) 다운로드 — 대상 총 {total}건", enabled=progress)
    if total == 0:
        progress_print("다운로드할 접수번호가 없습니다.", enabled=progress)
        return 0, 0

    ok = 0
    bad = 0
    failures: list[str] = []
    for idx, rcept_no in enumerate(rows, start=1):
        remain = total - idx
        progress_print(
            f"총 {total}건 중 {idx}번째 처리 중 (남음 {remain}건) — rcept_no={rcept_no} 다운로드 시도…",
            enabled=progress,
        )
        try:
            zip_path = fetch_document_zip(client, settings, rcept_no)
            conn.execute(
                """
                INSERT INTO document_fetch(rcept_no, status, zip_path, error, updated_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(rcept_no) DO UPDATE SET
                  status=excluded.status,
                  zip_path=excluded.zip_path,
                  error=excluded.error,
                  updated_at=excluded.updated_at
                """,
                (rcept_no, "downloaded", str(zip_path), None, now),
            )
            ok += 1
            progress_print(
                f"다운로드 완료 {idx}/{total} — 성공 누적 {ok}건, 실패 {bad}건 — 저장: {zip_path.name}",
                enabled=progress,
            )
        except Exception as e:  # noqa: BLE001
            bad += 1
            err = str(e)
            failures.append(f"{rcept_no}: {err}")
            progress_print(
                f"다운로드 실패 {idx}/{total} — 성공 누적 {ok}건, 실패 누적 {bad}건 — {rcept_no}: {err[:200]}",
                enabled=progress,
            )
            conn.execute(
                """
                INSERT INTO document_fetch(rcept_no, status, zip_path, error, updated_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(rcept_no) DO UPDATE SET
                  status=excluded.status,
                  zip_path=excluded.zip_path,
                  error=excluded.error,
                  updated_at=excluded.updated_at
                """,
                (rcept_no, "failed", None, err, now),
            )
        conn.commit()
    if verbose and failures:
        print("--- failures (up to 15) ---")
        for line in failures[:15]:
            print(line)
    progress_print(f"문서 다운로드 단계 종료 — 성공 {ok}건, 실패 {bad}건 (대상 {total}건)", enabled=progress)
    return ok, bad
