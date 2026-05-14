"""Runtime configuration: env vars, defaults, and scope policy.

OPENDART 인제스트 파이프라인이 사용하는 모든 런타임 설정 값을 한곳에 모은다.

- :func:`load_settings` 가 단일 진입점. 호출 즉시 ``.env`` 파일을 로드하고
  :class:`Settings` 데이터클래스를 만들어 반환한다.
- 우선순위: **프로세스 환경변수 > 프로젝트 루트 ``.env`` > CWD ``.env`` > 기본값**.
  (이미 값이 설정된 환경변수는 ``.env`` 가 덮어쓰지 않는다.)
- ``DART_API_KEY`` 는 필수. :meth:`Settings.require_key` 에서 한국어 안내 메시지와
  함께 ``RuntimeError`` 를 던진다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")

_DOTENV_DONE = False


def _parse_dotenv(text: str) -> dict[str, str]:
    """``KEY=VALUE`` 형태의 라인을 dict 로 파싱. 주석/공백/``export`` 접두사 허용."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        out[key] = value.strip().strip('"').strip("'")
    return out


def _load_dotenv_files() -> None:
    """``.env`` 를 (repo 루트 → CWD) 순서로 머지해서 프로세스 환경에 주입.

    이미 비어있지 않은 환경변수는 보존(덮어쓰지 않음).
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

    for key, value in merged.items():
        if not value:
            continue
        current = os.environ.get(key)
        if current is None or current.strip() == "":
            os.environ[key] = value


def _env(name: str, default: str | None = None) -> str | None:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip()


def _env_cast(name: str, default: T, cast: Callable[[str], T]) -> T:
    """문자열 환경변수를 임의 타입으로 캐스팅. 비어 있거나 캐스팅 실패 시 ``default``."""
    raw = _env(name)
    if raw is None:
        return default
    try:
        return cast(raw)
    except (TypeError, ValueError):
        return default


def _env_bool(name: str) -> bool:
    return (_env(name) or "").lower() in ("1", "true", "yes", "on", "y")


def _env_csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = _env(name)
    if raw is None:
        return default
    parts = tuple(x.strip() for x in raw.split(",") if x.strip())
    return parts or default


@dataclass
class Settings:
    """OPENDART 인제스트 범위·스로틀링·인증 설정.

    Year definition: 기본값은 **공시 접수일**(``rcept_dt`` 첫 4글자) 기준 연도.
    list 인제스트 단계 이후 DB export 시 ``filing_year`` 컬럼으로 노출된다.

    Periodic vs external audit (``pblntf_detail_types``):
        - ``F001`` 감사보고서
        - ``F002`` 연결감사보고서
        - ``F003`` 결합감사보고서
    """

    dart_api_key: str = field(default_factory=lambda: _env("DART_API_KEY", "") or "")
    base_url: str = field(
        default_factory=lambda: _env("DART_API_BASE", "https://opendart.fss.or.kr/api") or ""
    )
    data_dir: str = field(
        default_factory=lambda: _env("DART_KAM_DATA_DIR")
        or str(Path(__file__).resolve().parent.parent.parent / "data")
    )

    # Rate limiting.
    request_sleep_sec: float = field(
        default_factory=lambda: _env_cast("DART_REQUEST_SLEEP", 0.12, float)
    )
    max_retries: int = field(
        default_factory=lambda: _env_cast("DART_MAX_RETRIES", 5, int)
    )

    # list.json 은 corp_code 없이 호출하면 최대 3개월 — chunk_days ≤ 90.
    list_chunk_days: int = field(
        default_factory=lambda: _env_cast("DART_LIST_CHUNK_DAYS", 90, int)
    )
    # 기본 조회 회귀 연수: "최근 N년".
    years_back: int = field(
        default_factory=lambda: _env_cast("DART_YEARS_BACK", 3, int)
    )

    pblntf_ty: str = field(default_factory=lambda: _env("DART_PBLNTF_TY", "F") or "F")
    pblntf_detail_types: tuple[str, ...] = field(
        default_factory=lambda: _env_csv("DART_PBLNTF_DETAIL", ("F001", "F002", "F003"))
    )
    last_reprt_at: str = field(
        default_factory=lambda: _env("DART_LAST_REPRT_AT", "Y") or "Y"
    )
    corp_cls: str | None = field(default_factory=lambda: _env("DART_CORP_CLS"))
    parser_version: str = field(
        default_factory=lambda: _env("DART_PARSER_VERSION", "v3") or "v3"
    )

    # 각 OPENDART HTTP 요청을 터미널에 한 줄로 표시 (crtfc_key 마스킹).
    # CLI ``--verbose-http`` 또는 환경변수 ``DART_HTTP_LOG=1``.
    http_log: bool = field(default_factory=lambda: _env_bool("DART_HTTP_LOG"))

    def require_key(self) -> None:
        """``DART_API_KEY`` 누락 시 한국어 안내 메시지와 함께 :class:`RuntimeError` 발생."""
        if self.dart_api_key:
            return
        env_file = Path(__file__).resolve().parent.parent.parent / ".env"
        raise RuntimeError(
            "DART_API_KEY가 비어 있습니다. OPENDART API(list, sync-corp, documents 등)에는 "
            "인증키가 필요합니다.\n"
            "해결 방법(하나만 하면 됨):\n"
            "  1) 같은 PowerShell 창에서 먼저 실행한 뒤 다시 시도:\n"
            "       $env:DART_API_KEY = '발급받은_키'\n"
            f"  2) 프로젝트 루트에 파일 생성: {env_file}\n"
            "       내용 한 줄: DART_API_KEY=발급받은_키\n"
            "  3) Windows 사용자 환경 변수로 저장했다면 Cursor를 완전히 종료 후 다시 열고,"
            " echo $env:DART_API_KEY 로 값이 보이는지 확인하세요."
        )

    def default_end_date(self) -> date:
        """``DART_END_DATE=YYYYMMDD`` 가 있으면 그 날짜, 없으면 오늘."""
        raw = _env("DART_END_DATE")
        if raw and len(raw) == 8 and raw.isdigit():
            return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
        return date.today()

    def default_start_date(self) -> date:
        return self.default_end_date() - timedelta(days=365 * self.years_back)


def load_settings() -> Settings:
    """``.env`` 로드 후 :class:`Settings` 인스턴스를 반환하는 공식 진입점."""
    _load_dotenv_files()
    return Settings()
