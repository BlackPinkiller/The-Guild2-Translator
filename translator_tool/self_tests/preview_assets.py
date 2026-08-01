from __future__ import annotations

import json
from pathlib import Path

from ..preview_assets import bundled_preview_asset_path, bundled_preview_asset_root


def assert_bundled_preview_assets_are_complete() -> None:
    root = bundled_preview_asset_root()
    manifest_path = root / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    sources: list[str] = []
    for entry in payload.get("assets", ()):
        source = str(entry.get("source", ""))
        if not source or bundled_preview_asset_path(source) is None:
            raise AssertionError(f"bundled preview asset is missing: {source!r}")
        sources.append(source.replace("\\", "/").casefold())
    for family in payload.get("nine_slices", ()):
        prefix = str(family.get("source_prefix", ""))
        for piece in family.get("pieces", ()):
            source = f"{prefix}{piece}"
            if bundled_preview_asset_path(source) is None:
                raise AssertionError(f"bundled preview frame piece is missing: {source}")
            sources.append(source.replace("\\", "/").casefold())
    if len(sources) != len(set(sources)):
        raise AssertionError("bundled preview manifest contains duplicate source names")
    repository_root = Path(__file__).resolve().parents[2]
    for build_script in (
        repository_root / "build_translator_lite.bat",
        repository_root / "build_translator_lite_onefile.bat",
    ):
        text = build_script.read_text(encoding="utf-8")
        if "assets\\preview_ui;assets\\preview_ui" not in text:
            raise AssertionError(
                f"{build_script.name} does not package the bundled preview assets"
            )
