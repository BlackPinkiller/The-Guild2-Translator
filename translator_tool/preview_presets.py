from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import tomllib
from typing import Mapping


VALID_FLOWS = {"row", "column", "stack"}
VALID_ALIGNS = {"start", "center", "end", "stretch"}
VALID_RENDERERS = {"flow", "specialized"}


@dataclass(frozen=True)
class PreviewRegionPreset:
    id: str
    parent: str
    slot: str
    flow: str
    grow: float = 0.0
    ratio: float = 0.0
    basis: int = 0
    min_size: int = 0
    max_size: int = 0
    gap: int = 0
    align: str = "stretch"


@dataclass(frozen=True)
class PreviewLayoutPreset:
    id: str
    surfaces: tuple[str, ...]
    kinds: tuple[str, ...]
    flow: str
    min_width: int
    preferred_width: int
    max_width: int
    min_height: int
    max_height: int
    padding: tuple[int, int, int, int]
    gap: int
    background: str = ""
    frame: str = ""
    title: str = ""
    header_scale: float = 0.88
    body_scale: float = 0.78
    renderer: str = "specialized"
    regions: tuple[PreviewRegionPreset, ...] = ()


@dataclass(frozen=True)
class ResolvedPreviewRegion:
    id: str
    slot: str
    align: str
    x: int
    y: int
    width: int
    height: int


def preview_preset_root() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "preview_presets"


@lru_cache(maxsize=1)
def preview_presets() -> tuple[PreviewLayoutPreset, ...]:
    return tuple(
        _load_preview_preset(path)
        for path in sorted(preview_preset_root().glob("*.toml"))
    )


def preview_preset(
    surface: str,
    kind: str = "",
) -> PreviewLayoutPreset | None:
    normalized_surface = surface.casefold()
    normalized_kind = kind.casefold()
    for preset in preview_presets():
        if normalized_surface and normalized_surface in preset.surfaces:
            return preset
    for preset in preview_presets():
        if normalized_kind and normalized_kind in preset.kinds:
            return preset
    return None


def preview_preset_natural_size(
    preset: PreviewLayoutPreset,
    slot_sizes: Mapping[str, tuple[int, int]],
) -> tuple[int, int]:
    """Return the content-driven size of a preset before its outer bounds apply."""
    children = _children_by_parent(preset)
    active = _active_region_ids(preset, children, slot_sizes)
    content_width, content_height = _natural_children_size(
        "root",
        preset.flow,
        preset.gap,
        preset,
        children,
        active,
        slot_sizes,
    )
    top, right, bottom, left = preset.padding
    return content_width + left + right, content_height + top + bottom


def resolve_preview_regions(
    preset: PreviewLayoutPreset,
    width: int,
    height: int,
    slot_sizes: Mapping[str, tuple[int, int]],
) -> tuple[ResolvedPreviewRegion, ...]:
    """Lay out active preset regions using one small row/column/stack model."""
    children = _children_by_parent(preset)
    active = _active_region_ids(preset, children, slot_sizes)
    top, right, bottom, left = preset.padding
    root_rect = (
        left,
        top,
        max(0, width - left - right),
        max(0, height - top - bottom),
    )
    resolved: list[ResolvedPreviewRegion] = []
    _resolve_children(
        "root",
        preset.flow,
        preset.gap,
        root_rect,
        preset,
        children,
        active,
        slot_sizes,
        resolved,
    )
    return tuple(resolved)


def _load_preview_preset(path: Path) -> PreviewLayoutPreset:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    raw = data.get("preset")
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name}: missing [preset]")
    preset_id = _text(raw, "id")
    flow = _choice(raw, "flow", VALID_FLOWS)
    min_width = _positive_int(raw, "min_width")
    preferred_width = _positive_int(raw, "preferred_width")
    max_width = _positive_int(raw, "max_width")
    min_height = _positive_int(raw, "min_height")
    max_height = _positive_int(raw, "max_height")
    if not min_width <= preferred_width <= max_width:
        raise ValueError(f"{path.name}: width bounds are out of order")
    if min_height > max_height:
        raise ValueError(f"{path.name}: height bounds are out of order")
    padding_value = raw.get("padding", ())
    if (
        not isinstance(padding_value, list)
        or len(padding_value) != 4
        or any(not isinstance(value, int) or value < 0 for value in padding_value)
    ):
        raise ValueError(f"{path.name}: padding must contain four non-negative integers")
    regions = tuple(
        _load_region(path, value)
        for value in data.get("region", ())
        if isinstance(value, dict)
    )
    ids = {region.id for region in regions}
    if len(ids) != len(regions):
        raise ValueError(f"{path.name}: duplicate region id")
    for region in regions:
        if region.parent != "root" and region.parent not in ids:
            raise ValueError(f"{path.name}: unknown parent {region.parent!r}")
        if region.parent == region.id:
            raise ValueError(f"{path.name}: region cannot parent itself")
    return PreviewLayoutPreset(
        id=preset_id,
        surfaces=_text_tuple(raw.get("surfaces", ())),
        kinds=_text_tuple(raw.get("kinds", ())),
        flow=flow,
        min_width=min_width,
        preferred_width=preferred_width,
        max_width=max_width,
        min_height=min_height,
        max_height=max_height,
        padding=tuple(padding_value),
        gap=_non_negative_int(raw, "gap", 0),
        background=str(raw.get("background", "")),
        frame=str(raw.get("frame", "")),
        title=str(raw.get("title", "")),
        header_scale=float(raw.get("header_scale", 0.88)),
        body_scale=float(raw.get("body_scale", 0.78)),
        renderer=_choice(raw, "renderer", VALID_RENDERERS, "specialized"),
        regions=regions,
    )


def _children_by_parent(
    preset: PreviewLayoutPreset,
) -> dict[str, tuple[PreviewRegionPreset, ...]]:
    grouped: dict[str, list[PreviewRegionPreset]] = {}
    for region in preset.regions:
        grouped.setdefault(region.parent, []).append(region)
    return {parent: tuple(values) for parent, values in grouped.items()}


def _active_region_ids(
    preset: PreviewLayoutPreset,
    children: Mapping[str, tuple[PreviewRegionPreset, ...]],
    slot_sizes: Mapping[str, tuple[int, int]],
) -> frozenset[str]:
    active: set[str] = set()

    def visit(region: PreviewRegionPreset) -> bool:
        visible = bool(region.slot and region.slot in slot_sizes)
        children_visible = False
        for child in children.get(region.id, ()):
            children_visible = visit(child) or children_visible
        visible = visible or children_visible
        if visible:
            active.add(region.id)
        return visible

    for region in children.get("root", ()):
        visit(region)
    return frozenset(active)


def _bounded_main_size(region: PreviewRegionPreset, value: int) -> int:
    size = region.basis or value
    size = max(region.min_size, size)
    if region.max_size:
        size = min(region.max_size, size)
    return size


def _natural_region_size(
    region: PreviewRegionPreset,
    preset: PreviewLayoutPreset,
    children: Mapping[str, tuple[PreviewRegionPreset, ...]],
    active: frozenset[str],
    slot_sizes: Mapping[str, tuple[int, int]],
) -> tuple[int, int]:
    own_width, own_height = slot_sizes.get(region.slot, (0, 0))
    child_width, child_height = _natural_children_size(
        region.id,
        region.flow,
        region.gap,
        preset,
        children,
        active,
        slot_sizes,
    )
    return max(own_width, child_width), max(own_height, child_height)


def _natural_children_size(
    parent: str,
    flow: str,
    gap: int,
    preset: PreviewLayoutPreset,
    children: Mapping[str, tuple[PreviewRegionPreset, ...]],
    active: frozenset[str],
    slot_sizes: Mapping[str, tuple[int, int]],
) -> tuple[int, int]:
    visible = tuple(
        region for region in children.get(parent, ()) if region.id in active
    )
    if not visible:
        return 0, 0
    sizes = [
        _natural_region_size(region, preset, children, active, slot_sizes)
        for region in visible
    ]
    if flow == "row":
        widths = [
            _bounded_main_size(region, size[0])
            for region, size in zip(visible, sizes)
        ]
        return sum(widths) + gap * (len(widths) - 1), max(size[1] for size in sizes)
    if flow == "column":
        heights = [
            _bounded_main_size(region, size[1])
            for region, size in zip(visible, sizes)
        ]
        return max(size[0] for size in sizes), sum(heights) + gap * (len(heights) - 1)
    return max(size[0] for size in sizes), max(size[1] for size in sizes)


def _distribute_main_sizes(
    regions: tuple[PreviewRegionPreset, ...],
    natural_sizes: tuple[tuple[int, int], ...],
    available: int,
    flow: str,
    gap: int,
) -> list[int]:
    usable = max(0, available - gap * max(0, len(regions) - 1))
    sizes: list[int] = []
    for region, natural in zip(regions, natural_sizes):
        natural_main = natural[0] if flow == "row" else natural[1]
        value = round(usable * region.ratio) if region.ratio else natural_main
        sizes.append(_bounded_main_size(region, value))
    extra = usable - sum(sizes)
    growers = [index for index, region in enumerate(regions) if region.grow > 0]
    if extra > 0 and growers:
        total_grow = sum(regions[index].grow for index in growers)
        remaining = extra
        for index in growers[:-1]:
            share = round(extra * regions[index].grow / total_grow)
            if regions[index].max_size:
                share = min(share, max(0, regions[index].max_size - sizes[index]))
            sizes[index] += share
            remaining -= share
        last = growers[-1]
        if regions[last].max_size:
            remaining = min(remaining, max(0, regions[last].max_size - sizes[last]))
        sizes[last] += remaining
    elif extra < 0:
        deficit = -extra
        shrinkable = [
            index
            for index, region in enumerate(regions)
            if sizes[index] > region.min_size
        ]
        while deficit > 0 and shrinkable:
            share = max(1, (deficit + len(shrinkable) - 1) // len(shrinkable))
            next_round: list[int] = []
            for index in shrinkable:
                room = sizes[index] - regions[index].min_size
                shrink = min(room, share, deficit)
                sizes[index] -= shrink
                deficit -= shrink
                if sizes[index] > regions[index].min_size:
                    next_round.append(index)
                if deficit <= 0:
                    break
            shrinkable = next_round
    return sizes


def _resolve_children(
    parent: str,
    flow: str,
    gap: int,
    rect: tuple[int, int, int, int],
    preset: PreviewLayoutPreset,
    children: Mapping[str, tuple[PreviewRegionPreset, ...]],
    active: frozenset[str],
    slot_sizes: Mapping[str, tuple[int, int]],
    resolved: list[ResolvedPreviewRegion],
) -> None:
    visible = tuple(
        region for region in children.get(parent, ()) if region.id in active
    )
    if not visible:
        return
    x, y, width, height = rect
    natural_sizes = tuple(
        _natural_region_size(region, preset, children, active, slot_sizes)
        for region in visible
    )
    if flow == "stack":
        child_rects = [(x, y, width, height) for _region in visible]
    else:
        available = width if flow == "row" else height
        main_sizes = _distribute_main_sizes(
            visible,
            natural_sizes,
            available,
            flow,
            gap,
        )
        cursor = x if flow == "row" else y
        child_rects = []
        for main_size in main_sizes:
            if flow == "row":
                child_rects.append((cursor, y, main_size, height))
            else:
                child_rects.append((x, cursor, width, main_size))
            cursor += main_size + gap
    for region, child_rect in zip(visible, child_rects):
        child_x, child_y, child_width, child_height = child_rect
        resolved.append(
            ResolvedPreviewRegion(
                region.id,
                region.slot,
                region.align,
                child_x,
                child_y,
                max(0, child_width),
                max(0, child_height),
            )
        )
        _resolve_children(
            region.id,
            region.flow,
            region.gap,
            child_rect,
            preset,
            children,
            active,
            slot_sizes,
            resolved,
        )


def _load_region(path: Path, raw: dict[str, object]) -> PreviewRegionPreset:
    return PreviewRegionPreset(
        id=_text(raw, "id"),
        parent=str(raw.get("parent", "root")),
        slot=str(raw.get("slot", "")),
        flow=_choice(raw, "flow", VALID_FLOWS),
        grow=max(0.0, float(raw.get("grow", 0.0))),
        ratio=max(0.0, min(1.0, float(raw.get("ratio", 0.0)))),
        basis=_non_negative_int(raw, "basis", 0),
        min_size=_non_negative_int(raw, "min_size", 0),
        max_size=_non_negative_int(raw, "max_size", 0),
        gap=_non_negative_int(raw, "gap", 0),
        align=_choice(raw, "align", VALID_ALIGNS, "stretch"),
    )


def _text(raw: dict[str, object], key: str) -> str:
    value = str(raw.get(key, "")).strip()
    if not value:
        raise ValueError(f"missing {key}")
    return value


def _text_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        text.strip().casefold()
        for item in value
        if (text := str(item)).strip()
    )


def _choice(
    raw: dict[str, object],
    key: str,
    choices: set[str],
    default: str = "",
) -> str:
    value = str(raw.get(key, default)).casefold()
    if value not in choices:
        raise ValueError(f"invalid {key}: {value!r}")
    return value


def _positive_int(raw: dict[str, object], key: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"{key} must be a positive integer")
    return value


def _non_negative_int(
    raw: dict[str, object],
    key: str,
    default: int,
) -> int:
    value = raw.get(key, default)
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"{key} must be a non-negative integer")
    return value
