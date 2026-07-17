from __future__ import annotations

import json
from typing import Iterable

from .project import TranslationUnit


ENTRY_CLIPBOARD_MIME = "application/x-guild2-translator-entries+json"
MAX_ENTRY_CLIPBOARD_BYTES = 64 * 1024 * 1024
MAX_ENTRY_CLIPBOARD_COUNT = 100_000


def encode_entries(units: Iterable[TranslationUnit]) -> tuple[bytes, str]:
    payload = [
        {
            "key": entry_key(unit),
            "source": unit.source_text,
            "translation": unit.current_text,
        }
        for unit in units
    ]
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    plain_text = "\n".join(
        f"{item['key']}\t{item['source']}\t{item['translation']}"
        for item in payload
    )
    return raw, plain_text


def decode_translations(raw: bytes) -> list[str] | None:
    if not raw or len(raw) > MAX_ENTRY_CLIPBOARD_BYTES:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, list) or not payload or len(payload) > MAX_ENTRY_CLIPBOARD_COUNT:
        return None
    translations: list[str] = []
    for item in payload:
        if not isinstance(item, dict) or not isinstance(item.get("translation"), str):
            return None
        translations.append(item["translation"])
    return translations


def entry_key(unit: TranslationUnit) -> str:
    return unit.label or unit.record_id or unit.file_rel
