"""Runtime configuration: env vars, defaults, and scope policy."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

_DOTENV_DONE = False


def _parse_dotenv(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k:
            out[k] = v
    return out


def _load_dotenv_files() -> None:
    """Load `.env` from repo root then cwd (later file wins on duplicate keys).

    Does not override non-empty variables already present in the process environment.
    """
    global _DOTENV_DONE
    if _DOTENV_DONE:
        return
    _DOTENV_DONE = True
    repo_root = Path(__file__).resolve().parent.parent.parent
    merged: dict[str, str] = {}
    for path in (repo_root / ".env", Path.cwd() / ".env"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if text.startswith("\ufeff"):
            text = text.lstrip("\ufeff")
        merged.update(_parse_dotenv(text))
    for k, v in merged.items():
        if not v:
            continue
        cur = os.environ.get(k)
        if cur is None or str(cur).strip() == "":
            os.environ[k] = v


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return v.strip()


def _env_truthy(name: str) -> bool:
    v = (_env(name) or "").lower()
    return v in ("1", "true", "yes", "on", "y")


@dataclass
class Settings:
    """OPENDART scope and throttling.

    Year definition: default is **filing date** (`rcept_dt` first 4 chars),
    applied after list ingestion (see `filing_year` in DB export).
    """

    dart_api_key: str = field(default_factory=lambda: _env("DART_API_KEY", "") or "")
    base_url: str = field(
        default_factory=lambda: _env("DART_API_BASE", "https://opendart.fss.or.kr/api") or ""
    )
    data_dir: str = field(
        default_factory=lambda: _env("DART_KAM_DATA_DIR")
        or str(Path(__file__).resolve().parent.parent.parent / "data")
    )
    # Rate limiting: sleep between HTTP calls (seconds)
    request_sleep_sec: float = field(
        default_factory=lambda: float(_env("DART_REQUEST_SLEEP", "0.12") or "0.12")
    )
    max_retries: int = field(default_factory=lambda: int(_env("DART_MAX_RETRIES", "5") or "5"))
    # List API without corp_code: max ~3 months per query — use chunk_days <= 90
    list_chunk_days: int = field(default_factory=lambda: int(_env("DART_LIST_CHUNK_DAYS", "90") or "90"))
    # Default lookback for "recent 3 years" from today (authoritative date from user env in prod)
    years_back: int = field(default_factory=lambda: int(_env("DART_YEARS_BACK", "3") or "3"))
    # Periodic vs external audit: F001 감사보고서, F002 연결감사보고서, F003 결합감사보고서
    pblntf_ty: str = field(default_factory=lambda: _env("DART_PBLNTF_TY", "F") or "F")
    pblntf_detail_types: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            x.strip()
            for x in (_env("DART_PBLNTF_DETAIL", "F001,F002,F003") or "F001,F002,F003").split(",")
            if x.strip()
        )
    )
    last_reprt_at: str = field(default_factory=lambda: _env("DART_LAST_REPRT_AT", "Y") or "Y")
    corp_cls: str | None = field(default_factory=lambda: _env("DART_CORP_CLS"))  # Y,K,N,E or empty=all
    parser_version: str = field(default_factory=lambda: _env("DART_PARSER_VERSION", "v3") or "v3")
    # 각 OPENDART HTTP 요청을 터미널에 한 줄로 표시 (crtfc_key 마스킹). CLI --verbose-http 또는 DART_HTTP_LOG=1
    http_log: bool = field(default_factory=lambda: _env_truthy("DART_HTTP_LOG"))

    def require_key(self) -> None:
        if not self.dart_api_key:
            root = Path(__file__).resolve().parent.parent.parent
            env_file = root / ".env"
            raise RuntimeError(
                "DART_API_KEY가 비어 있습니다. OPENDART API(list, sync-corp, documents 등)에는 인증키가 필요합니다.\n"
                "해결 방법(하나만 하면 됨):\n"
                "  1) 같은 PowerShell 창에서 먼저 실행한 뒤 다시 시도:\n"
                "       $env:DART_API_KEY = '발급받은_키'\n"
                f"  2) 프로젝트 루트에 파일 생성: {env_file}\n"
                "       내용 한 줄: DART_API_KEY=발급받은_키\n"
                "  3) Windows 사용자 환경 변수로 저장했다면 Cursor를 완전히 종료 후 다시 열고,"
                " echo $env:DART_API_KEY 로 값이 보이는지 확인하세요."
            )

    def default_end_date(self) -> date:
        raw = _env("DART_END_DATE")
        if raw and len(raw) == 8:
            return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
        return date.today()

    def default_start_date(self) -> date:
        return self.default_end_date() - timedelta(days=365 * self.years_back)


def load_settings() -> Settings:
    _load_dotenv_files()
    return Settings()
