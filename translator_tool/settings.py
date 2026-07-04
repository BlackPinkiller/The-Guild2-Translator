from __future__ import annotations

from dataclasses import asdict, dataclass, field
import base64
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any


APP_DIR_NAME = "TheGuild2Translator"
SETTINGS_FILE_NAME = "settings.json"
RECENT_PROJECT_LIMIT = 8


@dataclass
class AppSettings:
    ui_language: str = "en"
    provider: str = "google"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    openai_api_key_protected: str = ""
    google_endpoint: str = "https://translate.googleapis.com/translate_a/single"
    source_language: str = "en"
    target_language: str = "zh-CN"
    git_author_name: str = "The Guild 2 Translator"
    git_author_email: str = "translator@local"
    auto_space_before_color_tokens_on_save: bool = False
    editor_zoom_steps: int = 0
    last_project_root: str = ""
    last_game_root: str = ""
    recent_project_roots: list[str] = field(default_factory=list)


def settings_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    root = Path(base) / APP_DIR_NAME
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        bucket = hashlib.sha1(str(exe_dir).encode("utf-8")).hexdigest()[:12]
        return root / "bundled" / bucket
    return root / "dev"


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
    values: dict[str, Any] = {}
    for name in AppSettings.__dataclass_fields__:
        value = raw.get(name)
        if isinstance(value, str):
            values[name] = value
        elif isinstance(value, int) and not isinstance(value, bool):
            values[name] = value
        elif isinstance(value, bool):
            values[name] = value
        elif name == "recent_project_roots" and isinstance(value, list):
            values[name] = [item for item in value if isinstance(item, str)][:RECENT_PROJECT_LIMIT]
    return AppSettings(**values)


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
