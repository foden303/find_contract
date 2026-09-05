"""Writable user data is independent of the source or frozen application."""

import os
import sys
from pathlib import Path


def _directory(path: str | Path) -> str:
    resolved = Path(path).expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    return str(resolved)


def state_dir() -> str:
    override = os.getenv("FINDER_HOME")
    if override:
        return _directory(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return _directory(base / "B2BContactFinder")


def data_dir() -> str:
    return _directory(os.getenv("FINDER_CACHE_DIR") or Path(state_dir()) / "data")


def upload_dir() -> str:
    return _directory(os.getenv("FINDER_UPLOAD_DIR") or Path(state_dir()) / "uploads")


def log_dir() -> str:
    return _directory(Path(state_dir()) / "logs")
