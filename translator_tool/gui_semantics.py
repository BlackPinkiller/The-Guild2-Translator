from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re

from .script_semantics import script_calls


_ASSET_RE = re.compile(
    rb"[A-Za-z0-9_./\\-]+\.(?:dds|gst|tga)",
    re.IGNORECASE,
)
_STRING_RE = re.compile(r"""^(?:"([^"\\]*(?:\\.[^"\\]*)*)"|'([^'\\]*(?:\\.[^'\\]*)*)')$""")


@dataclass(frozen=True)
class GuiResourceInfo:
    path: Path
    resource_name: str
    assets: tuple[str, ...]


def gui_resource_info(path: Path) -> GuiResourceInfo | None:
    try:
        resolved = path.expanduser().resolve()
        stat = resolved.stat()
    except OSError:
        return None
    return _cached_gui_resource_info(
        str(resolved),
        stat.st_mtime_ns,
        stat.st_size,
    )


@lru_cache(maxsize=128)
def _cached_gui_resource_info(
    path_text: str,
    _modified_ns: int,
    _size: int,
) -> GuiResourceInfo | None:
    path = Path(path_text)
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    assets: list[str] = []
    seen_assets: set[str] = set()
    for match in _ASSET_RE.finditer(raw):
        asset = match.group(0).decode("ascii", errors="ignore").replace("\\", "/")
        normalized = asset.casefold()
        if asset and normalized not in seen_assets:
            seen_assets.add(normalized)
            assets.append(asset)
    return GuiResourceInfo(
        path,
        _gui_resource_name(path),
        tuple(assets),
    )


def resolve_panel_gui_resource(source_path: Path, panel_name: str) -> GuiResourceInfo | None:
    normalized = panel_name.strip()
    if not normalized or any(char in normalized for char in "@[]"):
        return None
    for root in _candidate_game_roots(source_path):
        registered = _registered_panel_path(root, normalized)
        if registered is not None:
            info = gui_resource_info(registered)
            if info is not None:
                return info
        for relative in (
            Path("GUI") / "Hud" / f"{normalized}.gui",
            Path("GUI") / "Menu" / f"{normalized}.gui",
            Path("GUI") / f"{normalized}.gui",
        ):
            info = gui_resource_info(root / relative)
            if info is not None:
                return info
    return None


def _registered_panel_path(root: Path, panel_name: str) -> Path | None:
    registry_path = root / "Scripts" / "Hud" / "GameHud.lua"
    try:
        stat = registry_path.stat()
    except OSError:
        return None
    registry = _cached_panel_registry(
        str(registry_path.resolve()),
        stat.st_mtime_ns,
        stat.st_size,
    )
    resource = registry.get(panel_name.casefold())
    if not resource:
        return None
    return root.joinpath(*resource.replace("\\", "/").split("/"))


@lru_cache(maxsize=16)
def _cached_panel_registry(
    path_text: str,
    _modified_ns: int,
    _size: int,
) -> dict[str, str]:
    path = Path(path_text)
    try:
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
    except OSError:
        return {}
    panels: dict[str, str] = {}
    for call in script_calls(text, path):
        if call.name.casefold() != "addpanel" or len(call.arguments) < 3:
            continue
        panel = _literal(call.arguments[0])
        resource = _literal(call.arguments[2])
        if panel and resource:
            panels[panel.casefold()] = resource
    return panels


def _candidate_game_roots(source_path: Path) -> tuple[Path, ...]:
    try:
        resolved = source_path.expanduser().resolve()
    except OSError:
        resolved = source_path.expanduser()
    roots: list[Path] = []
    for candidate in resolved.parents:
        if (candidate / "GUI").is_dir() and candidate not in roots:
            roots.append(candidate)
    return tuple(roots)


def _gui_resource_name(path: Path) -> str:
    parts = path.parts
    for index, part in enumerate(parts):
        if part.casefold() == "gui":
            return "/".join(parts[index:])
    return path.name


def _literal(expression: str) -> str:
    match = _STRING_RE.fullmatch(expression.strip())
    if match is None:
        return ""
    return (match.group(1) or match.group(2) or "").strip()
