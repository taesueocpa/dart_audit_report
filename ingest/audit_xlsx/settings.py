"""Runtime paths and `.env` loading."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


_DOTENV_DONE = False


def _parse_dotenv(text: str) -> dict[str, str]:
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


def _load_dotenv() -> None:
    global _DOTENV_DONE
    if _DOTENV_DONE:
        return
    _DOTENV_DONE = True
    repo_root = Path(__file__).resolve().parents[2]
    candidates = [repo_root / ".env", Path.cwd() / ".env"]
    merged: dict[str, str] = {}
    for path in candidates:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if text.startswith("﻿"):
            text = text.lstrip("﻿")
        merged.update(_parse_dotenv(text))
    for key, value in merged.items():
        if value and not os.environ.get(key, "").strip():
            os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    api_key: str
    repo_root: Path
    market_csv: Path
    data_dir: Path
    raw_dir: Path
    mapping_csv: Path
    output_xlsx: Path

    def require_key(self) -> None:
        if not self.api_key:
            raise RuntimeError(
                "DART_API_KEY가 비어 있습니다. 프로젝트 루트 .env 또는 환경변수로 설정하세요."
            )


def load_settings() -> Settings:
    _load_dotenv()
    repo_root = Path(__file__).resolve().parents[2]
    data_dir = Path(os.environ.get("AUDIT_XLSX_DATA_DIR") or (repo_root / "data"))
    market_csv = Path(
        os.environ.get("AUDIT_XLSX_MARKET_CSV")
        or (repo_root / "market" / "data_2214_20260514.csv")
    )
    output_xlsx = Path(
        os.environ.get("AUDIT_XLSX_OUTPUT") or (data_dir / "audit_reports.xlsx")
    )
    return Settings(
        api_key=os.environ.get("DART_API_KEY", "").strip(),
        repo_root=repo_root,
        market_csv=market_csv,
        data_dir=data_dir,
        raw_dir=data_dir / "raw_audit",
        mapping_csv=data_dir / "stock_to_corp.csv",
        output_xlsx=output_xlsx,
    )
