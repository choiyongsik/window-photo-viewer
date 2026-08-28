"""Locating bundled, non-code files (the app icon) both from a source checkout and
from a PyInstaller build, where data files live under sys._MEIPASS (`_internal/`)."""
from __future__ import annotations

import sys
from pathlib import Path


def _base_dir() -> Path:
    frozen_base = getattr(sys, "_MEIPASS", None)
    if frozen_base:
        return Path(frozen_base)
    return Path(__file__).resolve().parents[1]


def app_icon_path() -> Path:
    return _base_dir() / "assets" / "icon.ico"
