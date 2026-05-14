"""Download original disclosure ZIP per ``rcept_no`` (document.json / document.xml).

OPENDART 의 ``document.xml`` 엔드포인트는 응답을 다음 *여러* 형태로 줄 수 있다.
이 모듈은 그 다양성을 한 곳에서 흡수해 디스크에 ZIP 파일로 저장한다.

응답 페이로드 디스패치 (우선순위):

1. **ZIP 바이트** — prefix 가 ``PK`` 인 경우 그대로 저장 (가장 흔함).
2. **JSON in XML 응답** — 본문이 ``{`` / ``[`` 로 시작하면 JSON 파싱 후
   ``document`` 키의 base64를 디코드.
3. **XML 내 ``<document>`` base64** — 위 2가지 모두 실패 시 XML 트리에서
   ``document`` 태그를 찾아 base64 디코드.
4. **document.json 폴백** — 위 모두 실패 시 ``document.json`` 엔드포인트를
   별도로 호출해 base64 페이로드를 받는다.

위 어느 단계에서든 검증된 ZIP 바이트가 얻어지면 즉시 디스크에 기록 후 반환.
"""

from __future__ import annotations

import base64
import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from dart_kam.config import Settings
from dart_kam.dart_client import DartClient
from dart_kam.paths import raw_zip_path
from dart_kam.progress_util import BatchProgress
from dart_kam.repository import (
    mark_document_downloaded,
    mark_document_failed,
    select_filings_for_download,
)


_ZIP_MAGIC = b"PK"
_BOM_UTF8 = b"\xef\xbb\xbf"
_OPENDART_OK = "000"
# 오류 메시지에 첨부할 raw payload 프리뷰 길이.
_HEAD_PREVIEW_LEN = 400
# 다운로드는 페이지가 크므로 일반 호출보다 timeout 을 늘려 잡는다.
_DOCUMENT_TIMEOUT_SEC = 300.0


# --------------------------------------------------------------------------- payload helpers

def _xml_local_tag(tag: str) -> str:
    """XML namespace 접두사를 제거한 로컬 태그명."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _document_b64_from_xml(root: ET.Element) -> str | None:
    """XML 트리에서 ``document`` (대소문자 무관) 태그의 텍스트 반환."""
    for el in root.iter():
        if _xml_local_tag(el.tag).lower() == "document" and el.text:
            stripped = el.text.strip()
            if stripped:
                return stripped
    return None


def _document_b64_from_json(payload: dict[str, object]) -> str | None:
    """JSON dict 에서 ``document`` 키(또는 ``Document``)의 base64 문자열."""
    for key in ("document", "Document"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _validate_zip(data: bytes) -> None:
    """ZIP 매직 매칭. 실패 시 진단 가능한 ``RuntimeError``."""
    if len(data) < 4 or data[:2] != _ZIP_MAGIC:
        raise RuntimeError(
            f"payload is not a ZIP (starts with {data[:8]!r}, len={len(data)})"
        )


def _write_zip_bytes(path: Path, data: bytes) -> None:
    _validate_zip(data)
    path.write_bytes(data)


def _write_zip_from_b64(path: Path, b64: str) -> None:
    try:
        data = base64.b64decode(b64, validate=False)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"base64 decode failed: {e}") from e
    _write_zip_bytes(path, data)


# --------------------------------------------------------------------------- payload dispatchers

def _try_extract_zip_or_b64_from_json(raw: bytes, path: Path) -> bool:
    """raw 가 JSON 으로 보이면 파싱하고 ``document`` base64 를 추출해 저장 시도.

    :returns: 저장 성공 시 ``True``. JSON 이 아니거나 실패하면 ``False``.
    :raises RuntimeError: OPENDART status 가 ``000`` 이 아니거나 base64 디코드 실패.
    """
    stripped = raw.lstrip()
    if stripped[:1] not in (b"{", b"["):
        return False
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        preview = raw[: min(_HEAD_PREVIEW_LEN, len(raw))]
        raise RuntimeError(
            f"document.xml: ZIP가 아니고 JSON 파싱에도 실패했습니다. Raw prefix: {preview!r}"
        ) from e
    if not isinstance(obj, dict):
        return False
    status = str(obj.get("status", "")).strip()
    if status != _OPENDART_OK:
        raise RuntimeError(
            f"document.xml OPENDART status={status} message={obj.get('message', obj)}"
        )
    b64 = _document_b64_from_json(obj)
    if not b64:
        return False
    _write_zip_from_b64(path, b64)
    return True


def _try_extract_b64_from_xml_tree(raw: bytes, path: Path) -> bool:
    """raw 를 XML 로 파싱하고 ``<document>`` base64 를 저장 시도."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return False
    b64 = _document_b64_from_xml(root)
    if not b64:
        return False
    _write_zip_from_b64(path, b64)
    return True


def _fetch_via_document_json(
    client: DartClient,
    rcept_no: str,
    path: Path,
    xml_prefix: bytes,
) -> Path:
    """폴백: ``document.json`` 엔드포인트에서 base64 페이로드를 받아 ZIP 으로 저장."""
    payload = client.get_json(
        "document.json", {"rcept_no": rcept_no}, timeout=_DOCUMENT_TIMEOUT_SEC
    )
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"document.json returned non-object: {type(payload).__name__}"
        )
    status = str(payload.get("status", "")).strip()
    if status != _OPENDART_OK:
        msg = payload.get("message", payload)
        raise RuntimeError(
            f"OPENDART document.json status={status} message={msg} "
            f"(document.xml ZIP/XML 실패, prefix={xml_prefix!r})"
        )
    b64 = _document_b64_from_json(payload)
    if not b64:
        raise RuntimeError(
            "Missing base64 in document.json and document.xml was not a ZIP. "
            f"JSON keys: {list(payload.keys())} xml_prefix={xml_prefix!r}"
        )
    _write_zip_from_b64(path, b64)
    return path


# --------------------------------------------------------------------------- public API

def _validate_rcept_no(rcept_no: str) -> str:
    rno = rcept_no.strip()
    if len(rno) != 14 or not rno.isdigit():
        raise RuntimeError(f"invalid rcept_no (expected 14 digits): {rcept_no!r}")
    return rno


def fetch_document_zip(client: DartClient, settings: Settings, rcept_no: str) -> Path:
    """공시 원본 ZIP 한 건을 디스크에 저장하고 그 경로를 반환.

    응답 페이로드의 *4가지 변형*을 우선순위대로 처리한다 (모듈 docstring 참조).
    """
    rno = _validate_rcept_no(rcept_no)
    path = raw_zip_path(settings, rno)
    path.parent.mkdir(parents=True, exist_ok=True)

    raw = client.get_bytes("document.xml", {"rcept_no": rno}, timeout=_DOCUMENT_TIMEOUT_SEC)
    if raw.startswith(_BOM_UTF8):
        raw = raw[len(_BOM_UTF8):]

    # 1) Direct ZIP.
    if len(raw) >= 2 and raw[:2] == _ZIP_MAGIC:
        _write_zip_bytes(path, raw)
        return path

    # 2) JSON 응답 (XML 엔드포인트가 JSON 으로 응답할 수 있음).
    if _try_extract_zip_or_b64_from_json(raw, path):
        return path

    # 3) XML 트리 안의 <document> base64.
    if _try_extract_b64_from_xml_tree(raw, path):
        return path

    # 4) document.json 폴백.
    return _fetch_via_document_json(
        client,
        rno,
        path,
        xml_prefix=raw[: min(_HEAD_PREVIEW_LEN, len(raw))],
    )


# --------------------------------------------------------------------------- batch ingest

def _select_targets(
    conn: sqlite3.Connection,
    *,
    rcept_nos: Sequence[str] | None,
    skip_downloaded: bool,
    limit: int | None,
) -> list[str]:
    if rcept_nos is None:
        return select_filings_for_download(
            conn, skip_downloaded=skip_downloaded, limit=limit
        )
    rows = [s.strip() for s in rcept_nos if s and s.strip()]
    if limit is not None:
        rows = rows[: max(0, limit)]
    return rows


def ingest_documents(
    client: DartClient,
    settings: Settings,
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
    skip_downloaded: bool = True,
    verbose: bool = False,
    rcept_nos: Sequence[str] | None = None,
    progress: bool = True,
) -> tuple[int, int]:
    """대상 공시들의 원본 ZIP을 일괄 다운로드하고 결과를 ``document_fetch`` 에 기록.

    :param rcept_nos: 명시되면 그 접수번호들만 처리 (``skip_downloaded`` 무시).
    :param verbose: 종료 시 실패 메시지 최대 15건을 stdout 출력.
    :returns: ``(성공, 실패)``.
    """
    rows = _select_targets(
        conn, rcept_nos=rcept_nos, skip_downloaded=skip_downloaded, limit=limit
    )
    bp = BatchProgress(label="문서(ZIP) 다운로드", total=len(rows), enabled=progress)
    bp.start()
    if not rows:
        return 0, 0

    now = datetime.now(timezone.utc).isoformat()
    ok = 0
    bad = 0
    failures: list[str] = []
    for idx, rcept_no in enumerate(rows, start=1):
        bp.tick(idx, detail=f"rcept_no={rcept_no} 다운로드 시도")
        try:
            zip_path = fetch_document_zip(client, settings, rcept_no)
            mark_document_downloaded(
                conn,
                rcept_no=rcept_no,
                zip_path=str(zip_path),
                updated_at=now,
            )
            ok += 1
            bp.done(idx, ok=ok, bad=bad, note=f"저장: {zip_path.name}")
        except Exception as e:  # noqa: BLE001 — 단건 실패는 다음 건으로 계속
            bad += 1
            err = str(e)
            failures.append(f"{rcept_no}: {err}")
            bp.fail(idx, ok=ok, bad=bad, error=f"{rcept_no}: {err}")
            mark_document_failed(conn, rcept_no=rcept_no, error=err, updated_at=now)
        conn.commit()

    if verbose and failures:
        print("--- failures (up to 15) ---")
        for line in failures[:15]:
            print(line)

    bp.finish(ok=ok, bad=bad)
    return ok, bad
