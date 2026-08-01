from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import tomllib


VALID_FLOWS = {"row", "column", "stack"}
VALID_ALIGNS = {"start", "center", "end", "stretch"}


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
    regions: tuple[PreviewRegionPreset, ...] = ()


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
        regions=regions,
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
