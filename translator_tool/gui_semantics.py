from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
import struct

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
    root_size: tuple[int, int] = ()
    content_rect: tuple[int, int, int, int] = ()
    nodes: tuple[GuiNodeGeometry, ...] = ()


@dataclass(frozen=True)
class GuiNodeGeometry:
    name: str
    x: int | None
    y: int
    width: int
    height: int
    horizontal_alignment: int | None


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
    nodes = _gui_node_geometry(raw)
    root = max(
        nodes,
        key=lambda node: node.width * node.height,
        default=None,
    )
    content = max(
        (
            node
            for node in nodes
            if re.search(r"(?:entry|body|text|label)", node.name, re.IGNORECASE)
            and node.width > 0
            and node.height > 0
        ),
        key=lambda node: node.width * node.height,
        default=None,
    )
    root_size = (root.width, root.height) if root is not None else ()
    content_rect: tuple[int, int, int, int] = ()
    if root is not None and content is not None:
        x = content.x
        if x is None:
            x = (
                max(0, (root.width - content.width) // 2)
                if content.horizontal_alignment == 4
                else 0
            )
        content_rect = (x, content.y, content.width, content.height)
    return GuiResourceInfo(
        path,
        _gui_resource_name(path),
        tuple(assets),
        root_size,
        content_rect,
        nodes,
    )


def gui_node_geometry(
    info: GuiResourceInfo,
    *names: str,
) -> GuiNodeGeometry | None:
    requested = {name.casefold() for name in names}
    return next(
        (node for node in info.nodes if node.name.casefold() in requested),
        None,
    )


def _gui_node_geometry(raw: bytes) -> tuple[GuiNodeGeometry, ...]:
    property_ids = {
        name: _gui_property_id(raw, name)
        for name in (
            "NODE_NAME",
            "ABS_X",
            "ABS_Y",
            "ABS_WIDTH",
            "ABS_HEIGHT",
            "HALIGN",
        )
    }
    node_id = property_ids["NODE_NAME"]
    if not node_id:
        return ()
    nodes: list[GuiNodeGeometry] = []
    previous_end = 0
    position = 0
    while True:
        position = raw.find(node_id, position)
        if position < 0:
            break
        value = _gui_string_property(raw, position)
        if value is None:
            position += 1
            continue
        name, node_end = value
        segment_start = previous_end
        nodes.append(
            GuiNodeGeometry(
                name=name,
                x=_gui_integer_property(
                    raw,
                    property_ids["ABS_X"],
                    segment_start,
                    position,
                ),
                y=_gui_integer_property(
                    raw,
                    property_ids["ABS_Y"],
                    segment_start,
                    position,
                )
                or 0,
                width=_gui_integer_property(
                    raw,
                    property_ids["ABS_WIDTH"],
                    segment_start,
                    position,
                )
                or 0,
                height=_gui_integer_property(
                    raw,
                    property_ids["ABS_HEIGHT"],
                    segment_start,
                    position,
                )
                or 0,
                horizontal_alignment=_gui_integer_property(
                    raw,
                    property_ids["HALIGN"],
                    segment_start,
                    position,
                ),
            )
        )
        previous_end = node_end
        position = node_end
    return tuple(nodes)


def _gui_property_id(raw: bytes, name: str) -> bytes:
    marker = name.encode("ascii") + b"\0"
    position = raw.find(marker)
    identifier_start = position + len(marker) + 4
    if position < 0 or identifier_start + 4 > len(raw):
        return b""
    return raw[identifier_start : identifier_start + 4]


def _gui_integer_property(
    raw: bytes,
    identifier: bytes,
    start: int,
    end: int,
) -> int | None:
    if not identifier:
        return None
    position = raw.rfind(identifier, start, end)
    if position < 0 or position + 9 > len(raw) or raw[position + 4] != 1:
        return None
    return struct.unpack_from("<i", raw, position + 5)[0]


def _gui_string_property(raw: bytes, position: int) -> tuple[str, int] | None:
    if position + 9 > len(raw) or raw[position + 4] != 2:
        return None
    length = struct.unpack_from("<I", raw, position + 5)[0]
    value_start = position + 9
    value_end = value_start + length
    if not 0 < length <= 256 or value_end > len(raw):
        return None
    value = raw[value_start:value_end].rstrip(b"\0")
    try:
        text = value.decode("ascii")
    except UnicodeDecodeError:
        return None
    return (text, value_end) if text else None


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
