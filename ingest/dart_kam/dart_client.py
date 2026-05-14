"""HTTP client for OPENDART with retries and gentle rate limiting.

OPENDART REST 호출을 감싸는 얇은 클라이언트.

- 모든 요청은 ``crtfc_key`` 를 자동 주입한다.
- API 응답 코드 ``020`` (rate limit) 은 지수 백오프로 재시도한다.
- 네트워크 예외도 ``max_retries`` 만큼 재시도한다.
- ``settings.http_log`` 가 ``True`` 면 호출마다 ``[OPENDART] GET ...`` 한 줄을 출력한다
  (``crtfc_key`` 는 ``***`` 로 마스킹).
- :meth:`DartClient.get_json` / :meth:`DartClient.get_bytes` 는 동일한 재시도 루프를
  공유한다 (구 버전에 있던 중복 코드 제거).
"""

from __future__ import annotations

import json
import time
from contextlib import AbstractContextManager
from typing import Any, Callable, TypeVar

import httpx

from dart_kam.config import Settings

T = TypeVar("T")

# 재시도 시 대기 상한(초). 지수 백오프가 폭주하지 않도록 캡.
_BACKOFF_RATE_LIMIT_MAX_SEC = 60.0  # OPENDART status="020" (분당 호출량 초과)
_BACKOFF_NETWORK_MAX_SEC = 30.0     # 네트워크 예외

# OPENDART 응답 ``status`` 의 의미가 명확한 코드만 상수화.
_STATUS_RATE_LIMIT = "020"


class DartClient(AbstractContextManager["DartClient"]):
    """OPENDART HTTP 클라이언트.

    ``with DartClient(settings) as c: ...`` 형태로 사용할 수 있도록
    컨텍스트 매니저를 구현했다 (close 누락 방지).
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = httpx.Client(timeout=120.0)

    def close(self) -> None:
        self._client.close()

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ helpers

    def _sleep_between_calls(self) -> None:
        """OPENDART 친화적인 글로벌 sleep (API 부담 완화)."""
        time.sleep(self.settings.request_sleep_sec)

    @staticmethod
    def _mask_params(params: dict[str, Any]) -> dict[str, Any]:
        """로그 출력용으로 ``crtfc_key`` 를 마스킹한 dict 사본을 만든다."""
        return {k: ("***" if k == "crtfc_key" else v) for k, v in params.items()}

    def _log(
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
        tail = f" {extra}" if extra else ""
        masked = self._mask_params(merged)
        print(
            f"[OPENDART] GET {path} http={http_status} {elapsed_ms:.0f}ms{tail} params={masked}",
            flush=True,
        )

    def _build_url(self, path: str) -> str:
        return f"{self.settings.base_url.rstrip('/')}/{path.lstrip('/')}"

    @staticmethod
    def _peek_api_status(payload: bytes) -> str | None:
        """응답이 JSON으로 보이면 ``status`` 필드만 가볍게 추출 (실패시 None)."""
        if not payload or payload[:1] not in (b"{", b"["):
            return None
        try:
            obj = json.loads(payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        if isinstance(obj, dict):
            return str(obj.get("status", ""))
        return None

    # ------------------------------------------------------------------ core

    def _request_with_retry(
        self,
        path: str,
        params: dict[str, Any],
        *,
        timeout: float | None,
        handle: Callable[[httpx.Response], tuple[T, str]],
    ) -> T:
        """공통 재시도 루프. ``handle`` 콜백이 (결과값, 로그용 extra) 튜플을 반환한다.

        ``handle`` 안에서 ``status=='020'`` 등으로 재시도를 강제하려면
        :class:`RetrySignal` 예외를 던지면 된다.
        """
        self.settings.require_key()
        url = self._build_url(path)
        merged = {"crtfc_key": self.settings.dart_api_key, **params}
        effective_timeout = 120.0 if timeout is None else timeout

        last_err: Exception | None = None
        for attempt in range(self.settings.max_retries):
            self._sleep_between_calls()
            t0 = time.perf_counter()
            try:
                response = self._client.get(url, params=merged, timeout=effective_timeout)
                response.raise_for_status()
                result, extra = handle(response)
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                self._log(
                    path,
                    merged,
                    http_status=response.status_code,
                    elapsed_ms=elapsed_ms,
                    extra=extra,
                )
                return result
            except RetrySignal as sig:
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                self._log(
                    path,
                    merged,
                    http_status=getattr(sig.response, "status_code", 0) or 0,
                    elapsed_ms=elapsed_ms,
                    extra=sig.log_extra,
                )
                time.sleep(min(_BACKOFF_RATE_LIMIT_MAX_SEC, 2.0 ** attempt))
                last_err = sig
            except Exception as e:  # noqa: BLE001 — 모든 HTTP/네트워크 예외 재시도
                last_err = e
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                status = getattr(getattr(e, "response", None), "status_code", 0) or 0
                self._log(
                    path,
                    merged,
                    http_status=status,
                    elapsed_ms=elapsed_ms,
                    extra=f"ERROR {type(e).__name__}",
                )
                time.sleep(min(_BACKOFF_NETWORK_MAX_SEC, 1.5 ** attempt))

        raise RuntimeError(f"OPENDART request failed after retries: {url}") from last_err

    # ------------------------------------------------------------------ public

    def get_json(
        self,
        path: str,
        params: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """``application/json`` 응답을 파싱해서 ``dict`` 로 반환.

        OPENDART status ``020`` (rate limit) 응답은 자동으로 백오프 후 재시도한다.
        """

        def handle(response: httpx.Response) -> tuple[dict[str, Any], str]:
            data = response.json()
            api_status = str(data.get("status", "")) if isinstance(data, dict) else ""
            if api_status == _STATUS_RATE_LIMIT:
                raise RetrySignal(response=response, log_extra=f"api={api_status}")
            return data, f"api={api_status}"

        return self._request_with_retry(path, params, timeout=timeout, handle=handle)

    def get_bytes(
        self,
        path: str,
        params: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> bytes:
        """ZIP/바이너리 응답 raw bytes 를 반환.

        응답 prefix 가 JSON 으로 보이면 ``status`` 만 가볍게 추출해 로그에 남긴다
        (페이로드 자체는 파싱하지 않음).
        """

        def handle(response: httpx.Response) -> tuple[bytes, str]:
            raw = response.content
            extra = f"bytes={len(raw)}"
            api_status = self._peek_api_status(raw)
            if api_status is not None:
                extra += f" api={api_status}"
            return raw, extra

        return self._request_with_retry(path, params, timeout=timeout, handle=handle)


class RetrySignal(Exception):
    """내부용 — 재시도 가능한 OPENDART 응답(예: status='020')임을 알리는 신호."""

    def __init__(self, *, response: httpx.Response, log_extra: str) -> None:
        super().__init__(log_extra)
        self.response = response
        self.log_extra = log_extra
