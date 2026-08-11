from __future__ import annotations

from dataclasses import dataclass
import re


ENGINE_BUILDING = "building"
ENGINE_CHARACTER = "character"
ENGINE_DYNASTY = "dynasty"
ENGINE_SETTLEMENT = "settlement"


@dataclass(frozen=True)
class EnginePreviewStyle:
    """Visual contract of a localization family rendered directly by the engine."""

    kind: str
    background: str


@dataclass(frozen=True)
class EngineFormatContract:
    """Known semantics supplied by the engine rather than a visible script call."""

    argument_kinds: tuple[tuple[int, str], ...] = ()
    argument_labels: tuple[tuple[int, tuple[str, ...]], ...] = ()
    preview_style: EnginePreviewStyle | None = None
    argument_aliases: tuple[tuple[int, int], ...] = ()


_TOOLTIP = EnginePreviewStyle("tooltip", "dark_panel")
_ONSCREEN_HELP = EnginePreviewStyle("onscreen_help", "dark_panel")
_STATUS_PANEL = EnginePreviewStyle("status", "dark_panel")

_HELP_SURFACES_BY_DOMAIN: tuple[tuple[str, str], ...] = (
    ("character", "character_help"),
    ("items", "item_help"),
    ("buildings", "building_help"),
    ("upgrades", "upgrade_help"),
    ("carts", "cart_help"),
    ("offices", "office_help"),
    ("measures", "measure_help"),
    ("settlement", "settlement_help"),
    ("ships", "ship_help"),
    ("skill", "skill_help"),
    ("class", "class_help"),
    ("zodiac", "zodiac_help"),
)

_PAIR_SURFACES_BY_PREFIX: tuple[tuple[str, str], ...] = (
    ("item_", "item_help"),
    ("building_", "building_help"),
    ("upgrade_", "upgrade_help"),
    ("measure_", "measure_help"),
    ("laws_", "text_help"),
)


_EXACT_CONTRACTS: dict[str, EngineFormatContract] = {
    "city_levelchange": EngineFormatContract(((1, ENGINE_SETTLEMENT),)),
    "diplomatic_state_changed": EngineFormatContract(
        argument_labels=(
            (2, ("@LNeutral",)),
            (3, ("@LHostility",)),
        ),
    ),
    "facility: %1l": EngineFormatContract(
        argument_labels=((1, ("_BUILDING_*_NAME_+0",)),),
    ),
    "family_150_attendapprenticeship_missed_body": EngineFormatContract(
        argument_labels=((2, ("_CHARACTERS_1_CLASSES_*_NAME_+0",)),),
    ),
    "general_tooltips_building_kontor": EngineFormatContract(
        ((1, ENGINE_SETTLEMENT),),
        preview_style=_TOOLTIP,
    ),
    "general_tooltips_building_market": EngineFormatContract(
        ((1, ENGINE_SETTLEMENT),),
        preview_style=_TOOLTIP,
    ),
    "interface_npcpanel_markettooltip": EngineFormatContract(
        ((1, ENGINE_SETTLEMENT),),
        preview_style=_TOOLTIP,
    ),
    "office_charge": EngineFormatContract(
        argument_labels=((1, ("_CHARACTERS_3_OFFICES_NAME_*_+0",)),),
    ),
    "characters_3_offices_template": EngineFormatContract(
        argument_labels=((1, ("_CHARACTERS_3_OFFICES_NAME_*_+0",)),),
    ),
    "characters_3_titles_aquire_messages_loose_title_body": EngineFormatContract(
        argument_labels=(
            (1, ("_CHARACTERS_3_TITLES_NAME_+9",)),
            (2, ("_CHARACTERS_3_TITLES_NAME_+7",)),
        ),
    ),
    "onscreenhelp_4_upgrades_impact_measure": EngineFormatContract(
        argument_labels=((1, ("_MEASURE_*_NAME_+0",)),),
        preview_style=_ONSCREEN_HELP,
    ),
    "onscreenhelp_4_upgrades_impact_talent": EngineFormatContract(
        argument_labels=((1, ("_TALENTS_*_NAME_+0",)),),
        preview_style=_ONSCREEN_HELP,
    ),
    "onscreenhelp_4_upgrades_impact_product": EngineFormatContract(
        argument_labels=((1, ("_ITEM_*_NAME_+0",)),),
        preview_style=_ONSCREEN_HELP,
    ),
    "measure_assign_employees_to_service_tavern_body": EngineFormatContract(
        argument_labels=tuple(
            (number, ("_ITEM_*_NAME_+0",))
            for number in range(2, 17, 2)
        ),
    ),
    "measure_assign_employees_to_service_order": EngineFormatContract(
        argument_labels=((1, ("_ITEM_*_NAME_+0",)),),
    ),
    "measure_administrate_diplomacy_request_enemies_body": EngineFormatContract(
        # The shipped format repeats money slot %2 as %2l where the caller's
        # crest is slot 3. Keep source text untouched and recover only in preview.
        argument_aliases=((2, 3),),
    ),
    "privileges_runinquisition_msg_victim_failed_body": EngineFormatContract(
        ((2, ENGINE_SETTLEMENT),),
    ),
    "substsimfulldescoffice": EngineFormatContract(
        (
            (1, ENGINE_CHARACTER),
            (2, ENGINE_SETTLEMENT),
        ),
    ),
}

_VARIANT_CONTRACTS: dict[str, EngineFormatContract] = {
    "general_measures_failures_+18": EngineFormatContract(
        argument_labels=((2, ("_MEASURE_*_NAME_+0",)),),
    ),
    "general_measures_failures_+20": EngineFormatContract(
        argument_labels=((3, ("_MEASURE_*_NAME_+0",)),),
    ),
}

_PREFIX_CONTRACTS: tuple[tuple[str, EngineFormatContract], ...] = (
    ("general_tooltips_", EngineFormatContract(preview_style=_TOOLTIP)),
    ("onscreenhelp_", EngineFormatContract(preview_style=_ONSCREEN_HELP)),
    (
        "general_information_city_level_msg_",
        EngineFormatContract(((2, ENGINE_SETTLEMENT),)),
    ),
    (
        "settlementstate_",
        EngineFormatContract(
            ((1, ENGINE_SETTLEMENT),),
            preview_style=_STATUS_PANEL,
        ),
    ),
)


def engine_format_argument_kind(label: str, number: int) -> str:
    """Return a type guaranteed by an engine-owned localization format."""
    contract = _engine_format_contract(label)
    if contract is not None:
        for argument_number, kind in contract.argument_kinds:
            if argument_number == number:
                return kind
    return ""


def engine_format_argument_labels(label: str, number: int) -> tuple[str, ...]:
    """Return label families guaranteed by an engine-owned localization format."""
    contract = _engine_format_contract(label)
    if contract is not None:
        for argument_number, labels in contract.argument_labels:
            if argument_number == number:
                return labels
    return ()


def engine_format_argument_alias(label: str, number: int) -> int | None:
    """Return a caller slot that a known shipped format refers to incorrectly."""
    contract = _engine_format_contract(label)
    if contract is not None:
        for requested_number, actual_number in contract.argument_aliases:
            if requested_number == number:
                return actual_number
    return None


def engine_format_preview_style(label: str) -> EnginePreviewStyle | None:
    """Return the engine-owned window style when the family defines one."""
    normalized = _format_identity(label)
    if normalized.startswith("onscreenhelp_"):
        return EnginePreviewStyle(_onscreen_help_surface(normalized), "dark_panel")
    contract = _engine_format_contract(label)
    return contract.preview_style if contract is not None else None


def engine_pair_preview_surface(label: str) -> str:
    """Return the registered HUD help panel for an engine-owned NAME/TOOLTIP pair."""
    normalized = _format_identity(label)
    if normalized.startswith("onscreenhelp_"):
        return _onscreen_help_surface(normalized)
    for prefix, surface in _PAIR_SURFACES_BY_PREFIX:
        if normalized.startswith(prefix):
            return surface
    return "text_help"


def _onscreen_help_surface(normalized: str) -> str:
    for domain, surface in _HELP_SURFACES_BY_DOMAIN:
        if re.search(rf"(?:^|_){re.escape(domain)}(?:_|$)", normalized):
            return surface
    return "text_help"


def _engine_format_contract(label: str) -> EngineFormatContract | None:
    variant = _VARIANT_CONTRACTS.get(_format_variant_identity(label))
    if variant is not None:
        return variant
    normalized = _format_identity(label)
    exact = _EXACT_CONTRACTS.get(normalized)
    if exact is not None:
        return exact
    for prefix, contract in _PREFIX_CONTRACTS:
        if normalized.startswith(prefix):
            return contract
    return None


def _format_identity(label: str) -> str:
    return re.sub(r"(?:_\+|\+)[a-z0-9*]+$", "", _format_variant_identity(label))


def _format_variant_identity(label: str) -> str:
    value = label.strip()
    if value.casefold().startswith("@l_"):
        value = value[3:]
    return value.lstrip("_").casefold()
