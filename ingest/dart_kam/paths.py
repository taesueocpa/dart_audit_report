"""Paths under DART_KAM_DATA_DIR."""

from __future__ import annotations

from pathlib import Path

from dart_kam.config import Settings


def data_root(settings: Settings) -> Path:
    return Path(settings.data_dir).resolve()


def db_path(settings: Settings) -> Path:
    return data_root(settings) / "dart_kam.sqlite3"


def corp_zip_path(settings: Settings) -> Path:
    return data_root(settings) / "corpCode.zip"


def raw_zip_path(settings: Settings, rcept_no: str) -> Path:
    return data_root(settings) / "raw_zips" / f"{rcept_no}.zip"
