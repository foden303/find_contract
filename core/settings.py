"""Search settings, with Windows per-user DPAPI protection for API keys."""

import base64
import ctypes
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from core.paths import state_dir

DEFAULTS = {
    "search_provider": "ddg",
    "searxng_url": "http://127.0.0.1:8080",
    "search_api_key": None,
}
ENVIRONMENT = {
    "search_provider": "FINDER_SEARCH_PROVIDER",
    "searxng_url": "FINDER_SEARXNG_URL",
    "search_api_key": "FINDER_SEARCH_API_KEY",
}


def _protect(data: bytes, decrypt: bool = False) -> bytes:
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_byte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise OSError("Cannot access the API key protected by this Windows account.")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel32.LocalFree(target.data)


def load_saved() -> dict:
    path = Path(state_dir()) / "settings.json"
    if not path.exists():
        return {}
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(saved, dict):
            raise ValueError("Expected a settings object")
        encrypted = saved.pop("protected_api_key", None)
        if encrypted:
            if os.name != "nt":
                raise ValueError("This API key belongs to a Windows account; enter it again on this system.")
            saved["search_api_key"] = _protect(base64.b64decode(encrypted, validate=True), True).decode("utf-8")
        return {key: value for key, value in saved.items() if key in DEFAULTS}
    except (ValueError, OSError, TypeError) as exc:
        raise ValueError("Cannot read saved settings. Open the data folder and repair settings.json.") from exc


def effective_settings(saved: dict | None = None) -> dict:
    result = DEFAULTS | (load_saved() if saved is None else saved)
    for key, variable in ENVIRONMENT.items():
        if variable in os.environ:
            result[key] = os.environ[variable]
    result["search_provider"] = (result["search_provider"] or "ddg").lower()
    result["search_api_key"] = result["search_api_key"] or None
    return result


def public_settings() -> dict:
    result = effective_settings()
    return {
        "search_provider": result["search_provider"],
        "searxng_url": result["searxng_url"],
        "has_api_key": bool(result["search_api_key"]),
        "env_overrides": [key for key, variable in ENVIRONMENT.items() if variable in os.environ],
    }


def validate_search(settings: dict, *, require_key: bool = True) -> None:
    from core.config import SEARCH_PROVIDERS

    if settings["search_provider"] not in SEARCH_PROVIDERS:
        raise ValueError("Choose DuckDuckGo, SearXNG, Brave or Serper.")
    if require_key and settings["search_provider"] in ("brave", "serper") and not settings.get("search_api_key"):
        raise ValueError("This search provider needs an API key. Enter it in Search settings.")
    if settings["search_provider"] == "searxng":
        url = urlsplit(settings.get("searxng_url") or "")
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("Enter a SearXNG http:// or https:// address without credentials, query or fragment.")
        try:
            url.port
        except ValueError as exc:
            raise ValueError("The SearXNG port is invalid.") from exc


def save_settings(changes: dict) -> dict:
    unknown = changes.keys() - DEFAULTS.keys()
    if unknown:
        raise ValueError("Unknown search setting.")
    saved = load_saved()
    for key, value in changes.items():
        if ENVIRONMENT[key] in os.environ:
            continue
        if value is not None and not isinstance(value, str):
            raise ValueError("Search settings must be text.")
        saved[key] = value.strip() if isinstance(value, str) else value
    validate_search(effective_settings(saved), require_key=False)
    document = dict(saved)
    key = document.get("search_api_key")
    if os.name == "nt" and key:
        document.pop("search_api_key")
        document["protected_api_key"] = base64.b64encode(_protect(key.encode("utf-8"))).decode("ascii")
    path = Path(state_dir()) / "settings.json"
    fd, temporary = tempfile.mkstemp(prefix=".settings-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return public_settings()
