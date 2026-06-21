from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CACHE_FILE_NAME = "translator_tool_cache.json"


def cache_path(root: Path) -> Path:
    return root / CACHE_FILE_NAME


def load_cache(root: Path) -> dict[str, Any]:
    path = cache_path(root)
    if not path.exists():
        return {"version": 1, "languages": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "languages": {}}
    if not isinstance(raw, dict):
        return {"version": 1, "languages": {}}
    raw.setdefault("version", 1)
    raw.setdefault("languages", {})
    if not isinstance(raw["languages"], dict):
        raw["languages"] = {}
    return raw


def save_cache(root: Path, cache: dict[str, Any]) -> None:
    path = cache_path(root)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def ignored_uids(root: Path, language: str) -> set[str]:
    cache = load_cache(root)
    language_data = cache.get("languages", {}).get(language, {})
    ignored = language_data.get("ignored", []) if isinstance(language_data, dict) else []
    if not isinstance(ignored, list):
        return set()
    return {str(item) for item in ignored}


def set_ignored(root: Path, language: str, uid: str, ignored: bool) -> None:
    set_ignored_many(root, language, (uid,), ignored)


def set_ignored_many(root: Path, language: str, uids: list[str] | tuple[str, ...], ignored: bool) -> None:
    cache = load_cache(root)
    languages = cache.setdefault("languages", {})
    language_data = languages.setdefault(language, {})
    if not isinstance(language_data, dict):
        language_data = {}
        languages[language] = language_data
    current = language_data.get("ignored", [])
    if not isinstance(current, list):
        current = []
    values = {str(item) for item in current}
    if ignored:
        values.update(str(uid) for uid in uids)
    else:
        values.difference_update(str(uid) for uid in uids)
    language_data["ignored"] = sorted(values)
    save_cache(root, cache)
