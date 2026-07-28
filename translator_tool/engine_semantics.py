from __future__ import annotations

import re


ENGINE_BUILDING = "building"
ENGINE_CHARACTER = "character"
ENGINE_DYNASTY = "dynasty"
ENGINE_SETTLEMENT = "settlement"


_EXACT_ARGUMENT_KINDS: dict[str, dict[int, str]] = {
    "city_levelchange": {1: ENGINE_SETTLEMENT},
    "general_tooltips_building_kontor": {1: ENGINE_SETTLEMENT},
    "general_tooltips_building_market": {1: ENGINE_SETTLEMENT},
    "interface_npcpanel_markettooltip": {1: ENGINE_SETTLEMENT},
    "substsimfulldescoffice": {
        1: ENGINE_CHARACTER,
        2: ENGINE_SETTLEMENT,
    },
}

_PREFIX_ARGUMENT_KINDS: tuple[tuple[str, dict[int, str]], ...] = (
    ("general_information_city_level_msg_", {2: ENGINE_SETTLEMENT}),
    ("settlementstate_", {1: ENGINE_SETTLEMENT}),
)


def engine_format_argument_kind(label: str, number: int) -> str:
    """Return a type guaranteed by an engine-owned localization format."""
    normalized = _format_identity(label)
    exact = _EXACT_ARGUMENT_KINDS.get(normalized)
    if exact is not None:
        return exact.get(number, "")
    for prefix, arguments in _PREFIX_ARGUMENT_KINDS:
        if normalized.startswith(prefix):
            return arguments.get(number, "")
    return ""


def _format_identity(label: str) -> str:
    value = label.strip()
    if value.casefold().startswith("@l_"):
        value = value[3:]
    value = value.lstrip("_").casefold()
    return re.sub(r"_\+[a-z0-9*]+$", "", value)
