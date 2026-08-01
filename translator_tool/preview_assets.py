from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path


def bundled_preview_asset_root() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "preview_ui"


@lru_cache(maxsize=1)
def _bundled_preview_asset_index() -> dict[str, Path]:
    root = bundled_preview_asset_root()
    try:
        payload = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return {}
    index: dict[str, Path] = {}
    for entry in payload.get("assets", ()):
        source = str(entry.get("source", "")).replace("\\", "/").casefold()
        relative = str(entry.get("file", "")).replace("\\", "/")
        if source and relative:
            index[source] = root.joinpath(*relative.split("/"))
    for family in payload.get("nine_slices", ()):
        source = str(family.get("source_prefix", "")).replace("\\", "/")
        directory = str(family.get("directory", "")).replace("\\", "/")
        if not source or not directory:
            continue
        pieces = family.get("pieces", range(9))
        for piece in pieces:
            if not isinstance(piece, int) or not 0 <= piece <= 8:
                continue
            index[f"{source}{piece}".casefold()] = root.joinpath(
                *directory.split("/"),
                f"{piece}.png",
            )
    return index


def bundled_preview_asset_path(name: str) -> Path | None:
    path = _bundled_preview_asset_index().get(
        name.replace("\\", "/").casefold()
    )
    if path is None or not path.is_file():
        return None
    return path
