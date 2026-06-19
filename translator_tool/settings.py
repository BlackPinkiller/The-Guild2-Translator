from __future__ import annotations

from dataclasses import asdict, dataclass
import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
from typing import Any


APP_DIR_NAME = "TheGuild2Translator"
SETTINGS_FILE_NAME = "settings.json"


@dataclass
class AppSettings:
    provider: str = "google"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    openai_api_key_protected: str = ""
    google_endpoint: str = "https://translate.googleapis.com/translate_a/single"
    source_language: str = "en"
    target_language: str = "zh-CN"
    git_author_name: str = "The Guild 2 Translator"
    git_author_email: str = "translator@local"


def settings_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / APP_DIR_NAME


def settings_path() -> Path:
    return settings_dir() / SETTINGS_FILE_NAME


def load_settings() -> AppSettings:
    path = settings_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AppSettings()
    if not isinstance(raw, dict):
        return AppSettings()
    allowed = {field: raw[field] for field in AppSettings.__dataclass_fields__ if isinstance(raw.get(field), str)}
    return AppSettings(**allowed)


def save_settings(settings: AppSettings) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def protect_secret(value: str) -> str:
    if not value:
        return ""
    raw = value.encode("utf-8")
    if os.name != "nt":
        return "plain:" + base64.b64encode(raw).decode("ascii")
    return "dpapi:" + base64.b64encode(_dpapi_protect(raw)).decode("ascii")


def reveal_secret(value: str) -> str:
    if not value:
        return ""
    try:
        if value.startswith("dpapi:"):
            return _dpapi_unprotect(base64.b64decode(value[6:])).decode("utf-8")
        if value.startswith("plain:"):
            return base64.b64decode(value[6:]).decode("utf-8")
    except (OSError, ValueError, UnicodeDecodeError):
        return ""
    # Compatibility with an early manually created settings file.
    return value


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _dpapi_protect(data: bytes) -> bytes:
    in_blob, _buffer = _blob(data)
    out_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptProtectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        raise OSError("Windows DPAPI could not protect the API key")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    in_blob, _buffer = _blob(data)
    out_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        raise OSError("Windows DPAPI could not read the API key")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)
