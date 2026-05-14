"""HTTP client for OPENDART with retries and gentle rate limiting."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from dart_kam.config import Settings


class DartClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = httpx.Client(timeout=120.0)

    def close(self) -> None:
        self._client.close()

    def _sleep(self) -> None:
        time.sleep(self.settings.request_sleep_sec)

    @staticmethod
    def _safe_params(params: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in params.items():
            if k == "crtfc_key":
                out[k] = "***"
            else:
                out[k] = v
        return out

    def _log_line(
        self,
        path: str,
        merged: dict[str, Any],
        *,
        http_status: int,
        elapsed_ms: float,
        extra: str = "",
    ) -> None:
        if not self.settings.http_log:
            return
        safe = self._safe_params(merged)
        tail = f" {extra}" if extra else ""
        print(f"[OPENDART] GET {path} http={http_status} {elapsed_ms:.0f}ms{tail} params={safe}", flush=True)

    def get_json(self, path: str, params: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        self.settings.require_key()
        url = f"{self.settings.base_url.rstrip('/')}/{path.lstrip('/')}"
        merged = {"crtfc_key": self.settings.dart_api_key, **params}
        last_err: Exception | None = None
        eff_timeout = timeout if timeout is not None else 120.0
        for attempt in range(self.settings.max_retries):
            self._sleep()
            t0 = time.perf_counter()
            try:
                r = self._client.get(url, params=merged, timeout=eff_timeout)
                r.raise_for_status()
                data = r.json()
                elapsed = (time.perf_counter() - t0) * 1000.0
                status = str(data.get("status", ""))
                self._log_line(path, merged, http_status=r.status_code, elapsed_ms=elapsed, extra=f"api={status}")
                if status == "020":
                    # rate limit — back off
                    time.sleep(min(60.0, 2.0 ** attempt))
                    continue
                return data
            except Exception as e:  # noqa: BLE001
                last_err = e
                elapsed = (time.perf_counter() - t0) * 1000.0
                if self.settings.http_log:
                    self._log_line(
                        path,
                        merged,
                        http_status=getattr(getattr(e, "response", None), "status_code", 0) or 0,
                        elapsed_ms=elapsed,
                        extra=f"ERROR {type(e).__name__}",
                    )
                time.sleep(min(30.0, 1.5 ** attempt))
        raise RuntimeError(f"OPENDART request failed after retries: {url}") from last_err

    def get_bytes(self, path: str, params: dict[str, Any], *, timeout: float | None = None) -> bytes:
        self.settings.require_key()
        url = f"{self.settings.base_url.rstrip('/')}/{path.lstrip('/')}"
        merged = {"crtfc_key": self.settings.dart_api_key, **params}
        last_err: Exception | None = None
        eff_timeout = timeout if timeout is not None else 120.0
        for attempt in range(self.settings.max_retries):
            self._sleep()
            t0 = time.perf_counter()
            try:
                r = self._client.get(url, params=merged, timeout=eff_timeout)
                r.raise_for_status()
                raw = r.content
                elapsed = (time.perf_counter() - t0) * 1000.0
                extra = f"bytes={len(raw)}"
                if raw[:1] in (b"{", b"["):
                    try:
                        jo = json.loads(raw.decode("utf-8"))
                        extra += f" api={jo.get('status')}"
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        pass
                self._log_line(path, merged, http_status=r.status_code, elapsed_ms=elapsed, extra=extra)
                return raw
            except Exception as e:  # noqa: BLE001
                last_err = e
                elapsed = (time.perf_counter() - t0) * 1000.0
                if self.settings.http_log:
                    self._log_line(
                        path,
                        merged,
                        http_status=getattr(getattr(e, "response", None), "status_code", 0) or 0,
                        elapsed_ms=elapsed,
                        extra=f"ERROR {type(e).__name__}",
                    )
                time.sleep(min(30.0, 1.5 ** attempt))
        raise RuntimeError(f"OPENDART request failed after retries: {url}") from last_err
