from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re

from .code_index import (
    CodeFileSpec,
    CodeReference,
    LABEL_RE,
    analyze_code_file,
    dynamic_label_patterns,
    normalize_label,
)
from .engine_semantics import engine_format_preview_style, engine_pair_preview_surface
from .gui_semantics import GuiResourceInfo, gui_resource_info, resolve_panel_gui_resource
from .script_semantics import CallContract, call_contract


PARCHMENT_TEXT = (55, 38, 24, 255)
DARK_PANEL_TEXT = (245, 239, 216, 255)


@dataclass(frozen=True)
class PreviewWindowButton:
    identifier: str
    label: str = ""
    text: str = ""
    icon_asset: str = ""


@dataclass(frozen=True)
class PreviewSurfacePresentation:
    kind: str
    background: str
    layout: str
    gui_resource: str = ""
    background_asset: str = ""
    frame_asset: str = ""
    title_asset: str = ""
    icon_asset: str = ""


@dataclass(frozen=True)
class PreviewWindowContext:
    kind: str
    background: str
    default_color: tuple[int, int, int, int]
    header_label: str = ""
    body_label: str = ""
    buttons: tuple[PreviewWindowButton, ...] = ()
    argument_labels: tuple[str, ...] = ()
    call_name: str = ""
    surface: str = ""
    panel: str = ""
    category: str = ""
    speaker: str = ""
    layout: str = ""
    gui_resource: str = ""
    background_asset: str = ""
    frame_asset: str = ""
    title_asset: str = ""
    icon_asset: str = ""

    @property
    def labels(self) -> tuple[str, ...]:
        values: list[str] = []
        for label in (self.header_label, self.body_label):
            if label and label not in values:
                values.append(label)
        for button in self.buttons:
            if button.label and button.label not in values:
                values.append(button.label)
        for label in self.argument_labels:
            if label and label not in values:
                values.append(label)
        return tuple(values)


BUTTON_RE = re.compile(r"@B\[(?P<body>[^\]]*)\]", re.IGNORECASE | re.DOTALL)
STRING_LITERAL_RE = re.compile(r"""(?:"([^"\\]*(?:\\.[^"\\]*)*)"|'([^'\\]*(?:\\.[^'\\]*)*)')""")
RESOLVED_LABEL_RE = re.compile(r"@L_[A-Za-z0-9_+*]+", re.IGNORECASE)
BUTTON_ASSET_RE = re.compile(r"[A-Za-z0-9_./\\-]+\.tga", re.IGNORECASE)


def window_context_for_reference(reference: CodeReference, current_label: str = "") -> PreviewWindowContext | None:
    call_name = (reference.call_name or "").casefold()
    if not call_name:
        if reference.role == "gui_resource":
            return _gui_resource_window_context(reference, current_label)
        return None
    contract = call_contract(call_name)
    surface = _surface_for_call(call_name, contract)
    if not surface:
        return None
    arguments = tuple(str(argument) for argument in reference.arguments)
    argument_expressions = _argument_expressions(reference, arguments)
    panel = _contract_argument_hint(
        argument_expressions,
        contract.panel_argument if contract else None,
    )
    category = _contract_argument_hint(
        argument_expressions,
        contract.category_argument if contract else None,
    ) or _feedback_category(call_name)
    speaker = _contract_argument_hint(
        argument_expressions,
        contract.speaker_argument if contract else None,
    )
    buttons = _buttons_from_arguments(argument_expressions)
    labels_by_arg = _labels_by_argument(argument_expressions)
    button_label_set = {button.label for button in buttons if button.label}
    header_label, body_label = _header_body_labels(call_name, labels_by_arg, button_label_set, current_label)
    related_references = related_window_references(reference)
    if surface == "questbook":
        for related in related_references:
            related_arguments = tuple(str(argument) for argument in related.arguments)
            related_expressions = _argument_expressions(related, related_arguments)
            related_labels = _labels_by_argument(related_expressions)
            related_header, related_body = _header_body_labels(
                (related.call_name or "").casefold(),
                related_labels,
                set(),
                "",
            )
            if related_header and not header_label:
                header_label = related_header
            if related_body and not body_label:
                body_label = related_body
            offset = max((index for index, _values in labels_by_arg), default=-1) + 1
            labels_by_arg.extend(
                (offset + index, values)
                for index, values in related_labels
            )
    referenced_label = _context_label(current_label or reference.label)
    if referenced_label:
        if reference.role == "header":
            header_label = referenced_label
        elif reference.role == "body":
            body_label = referenced_label
        elif reference.role == "template" and not body_label:
            body_label = referenced_label
        elif reference.role == "button" and not any(
            _equivalent_label(button.label, referenced_label) for button in buttons
        ):
            buttons = (*buttons, PreviewWindowButton(identifier="", label=referenced_label))
    argument_labels = _runtime_argument_labels(labels_by_arg, (header_label, body_label), button_label_set)
    if (
        referenced_label
        and reference.role == "runtime_label"
        and not any(_equivalent_label(label, referenced_label) for label in argument_labels)
    ):
        argument_labels = (*argument_labels, referenced_label)
    if not header_label and not body_label and not buttons:
        return None
    presentation = _presentation_for_surface(surface, panel=panel, category=category)
    if panel:
        gui_info = resolve_panel_gui_resource(reference.path, panel)
        if gui_info is not None:
            presentation = _merge_gui_presentation(presentation, gui_info)
    return PreviewWindowContext(
        kind=presentation.kind,
        background=presentation.background,
        default_color=(
            DARK_PANEL_TEXT
            if presentation.background in {"dark_panel", "overlay", "transparent"}
            else PARCHMENT_TEXT
        ),
        header_label=header_label,
        body_label=body_label,
        buttons=buttons,
        argument_labels=argument_labels,
        call_name=call_name,
        surface=surface,
        panel=panel,
        category=category,
        speaker=speaker,
        layout=presentation.layout,
        gui_resource=presentation.gui_resource,
        background_asset=presentation.background_asset,
        frame_asset=presentation.frame_asset,
        title_asset=presentation.title_asset,
        icon_asset=presentation.icon_asset,
    )


def related_window_references(reference: CodeReference) -> tuple[CodeReference, ...]:
    """Return the other state-setting call that completes a questbook preview."""
    call_name = (reference.call_name or "").casefold()
    counterpart = {
        "setmainquesttitle": "setmainquestdescription",
        "setmainquestdescription": "setmainquesttitle",
    }.get(call_name)
    if counterpart is None or not reference.arguments:
        return ()
    key = reference.arguments[0].strip().casefold()
    if not key:
        return ()
    try:
        resolved = reference.path.expanduser().resolve()
        stat = resolved.stat()
    except OSError:
        return ()
    candidates = _cached_file_references(
        str(resolved),
        stat.st_mtime_ns,
        stat.st_size,
        reference.source,
    )
    matching = tuple(
        candidate
        for candidate in candidates
        if (candidate.call_name or "").casefold() == counterpart
        and candidate.arguments
        and candidate.arguments[0].strip().casefold() == key
        and candidate.role in {"header", "body"}
    )
    if not matching:
        return ()
    return (
        min(
            matching,
            key=lambda candidate: (
                abs(candidate.line - reference.line),
                -candidate.confidence,
                candidate.line,
            ),
        ),
    )


@lru_cache(maxsize=64)
def _cached_file_references(
    path_text: str,
    _modified_ns: int,
    _size: int,
    source: str,
) -> tuple[CodeReference, ...]:
    analysis = analyze_code_file(CodeFileSpec(Path(path_text), source))
    mappings = (
        analysis.index.project_references,
        analysis.index.vanilla_references,
    )
    values: list[CodeReference] = []
    seen: set[CodeReference] = set()
    for mapping in mappings:
        for references in mapping.values():
            for reference in references:
                if reference not in seen:
                    seen.add(reference)
                    values.append(reference)
    return tuple(values)


def _gui_resource_window_context(
    reference: CodeReference,
    current_label: str,
) -> PreviewWindowContext | None:
    info = gui_resource_info(reference.path)
    if info is None:
        return None
    label = _context_label(current_label or reference.label)
    if not label:
        return None
    presentation = _presentation_from_gui_resource(info)
    return PreviewWindowContext(
        kind=presentation.kind,
        background=presentation.background,
        default_color=_default_color_for_presentation(presentation),
        body_label=label,
        call_name="gui_resource",
        surface="gui_embedded",
        layout=presentation.layout,
        gui_resource=presentation.gui_resource,
        background_asset=presentation.background_asset,
        frame_asset=presentation.frame_asset,
        title_asset=presentation.title_asset,
        icon_asset=presentation.icon_asset,
    )


def best_window_context(references: tuple[CodeReference, ...], current_label: str = "") -> PreviewWindowContext | None:
    normalized = _context_label(current_label) if current_label else ""
    for reference in references:
        context = window_context_for_reference(reference, normalized)
        if context is not None and (not normalized or _context_has_label(context, normalized)):
            return context
    if normalized:
        return None
    for reference in references:
        context = window_context_for_reference(reference, normalized)
        if context is not None:
            return context
    return None


def engine_window_context(label: str) -> PreviewWindowContext | None:
    """Build a window context for a format rendered by native engine code."""
    style = engine_format_preview_style(label)
    if style is None:
        return None
    normalized = _context_label(label)
    role = _engine_label_role(normalized)
    return surface_window_context(
        style.kind,
        header_label=normalized if role == "header" else "",
        body_label=normalized if role != "header" else "",
        call_name=f"engine:{style.kind}",
    )


def surface_window_context(
    surface: str,
    *,
    header_label: str = "",
    body_label: str = "",
    call_name: str = "",
) -> PreviewWindowContext:
    presentation = _presentation_for_surface(surface)
    return PreviewWindowContext(
        kind=presentation.kind,
        background=presentation.background,
        default_color=(
            DARK_PANEL_TEXT
            if presentation.background in {"dark_panel", "overlay", "transparent"}
            else PARCHMENT_TEXT
        ),
        header_label=header_label,
        body_label=body_label,
        call_name=call_name,
        surface=surface,
        layout=presentation.layout,
        gui_resource=presentation.gui_resource,
        background_asset=presentation.background_asset,
        frame_asset=presentation.frame_asset,
        title_asset=presentation.title_asset,
        icon_asset=presentation.icon_asset,
    )


def _default_color_for_presentation(
    presentation: PreviewSurfacePresentation,
) -> tuple[int, int, int, int]:
    return (
        DARK_PANEL_TEXT
        if presentation.background in {"dark_panel", "overlay", "transparent"}
        else PARCHMENT_TEXT
    )


def _merge_gui_presentation(
    semantic: PreviewSurfacePresentation,
    info: GuiResourceInfo,
) -> PreviewSurfacePresentation:
    resource = _presentation_from_gui_resource(info)
    has_visual_profile = bool(
        resource.background_asset
        or resource.frame_asset
        or resource.title_asset
    )
    return PreviewSurfacePresentation(
        semantic.kind,
        resource.background if has_visual_profile else semantic.background,
        resource.layout if has_visual_profile else semantic.layout,
        info.resource_name,
        resource.background_asset or semantic.background_asset,
        resource.frame_asset or semantic.frame_asset,
        resource.title_asset or semantic.title_asset,
        resource.icon_asset or semantic.icon_asset,
    )


def _presentation_from_gui_resource(
    info: GuiResourceInfo,
) -> PreviewSurfacePresentation:
    assets = tuple(asset.replace("\\", "/") for asset in info.assets)
    background_asset = _first_gui_asset(
        assets,
        (
            lambda value: value.endswith("/mbback1.tga"),
            lambda value: value.endswith("/mbback0.tga"),
            lambda value: value.endswith("/bg_buch.tga"),
            lambda value: "/onscreenhelp/" in value and value.endswith("/bg.tga"),
            lambda value: value.endswith("/panelbackground_01.tga"),
            _looks_like_background_asset,
        ),
    )
    frame_asset = _first_gui_asset(
        assets,
        (
            lambda value: value.endswith("/border_gold_02.tga"),
            lambda value: value.endswith("/border_wood.tga"),
        ),
    )
    title_asset = _first_gui_asset(
        assets,
        (lambda value: value.endswith("/header_red.tga"),),
    )
    normalized_background = background_asset.casefold()
    if normalized_background.endswith("/mbback1.tga"):
        background, layout = "parchment", "document"
    elif normalized_background.endswith("/mbback0.tga"):
        background, layout = "parchment", "parchment"
    elif normalized_background.endswith("/bg_buch.tga"):
        background, layout = "parchment", "book"
    elif "/onscreenhelp/" in normalized_background:
        background, layout = "dark_panel", "help"
    elif background_asset or frame_asset or title_asset:
        background, layout = "dark_panel", "panel"
    else:
        background, layout = "overlay", "overlay"
    return PreviewSurfacePresentation(
        "gui",
        background,
        layout,
        info.resource_name,
        background_asset,
        frame_asset,
        title_asset,
    )


def _first_gui_asset(
    assets: tuple[str, ...],
    predicates: tuple[Callable[[str], bool], ...],
) -> str:
    for predicate in predicates:
        for asset in assets:
            if predicate(asset.casefold()):
                return asset
    return ""


def _looks_like_background_asset(value: str) -> bool:
    name = value.rsplit("/", 1)[-1]
    return (
        "background" in name
        or name.startswith("background")
        or name.startswith("bg_")
        or name == "bg.tga"
    )


def _engine_label_role(label: str) -> str:
    identity = re.sub(r"_[+][a-z0-9*]+$", "", label, flags=re.IGNORECASE)
    if re.search(r"(?:^|_)(?:head|header|headline|name)$", identity, re.IGNORECASE):
        return "header"
    return "body"


def _surface_for_call(call_name: str, contract: CallContract | None = None) -> str:
    resolved = contract if contract is not None else call_contract(call_name)
    surface = str(getattr(resolved, "surface", "") or "")
    if surface:
        return surface
    if call_name.startswith("feedback_message"):
        return "news"
    return ""


@lru_cache(maxsize=64)
def _presentation_for_surface(
    surface: str,
    *,
    panel: str = "",
    category: str = "",
) -> PreviewSurfacePresentation:
    if surface == "news" and panel.casefold() == "panel_nobility_title_deed":
        return PreviewSurfacePresentation(
            "document",
            "parchment",
            "document",
            "engine:panel_nobility_title_deed",
            "Hud/messagebox/mbback1.tga",
        )
    help_resources = {
        "character_help": "GUI/Hud/Helppanels/characters.gui",
        "item_help": "GUI/Hud/Helppanels/items.gui",
        "building_help": "GUI/Hud/Helppanels/buildings.gui",
        "upgrade_help": "GUI/Hud/Helppanels/upgrades.gui",
        "cart_help": "GUI/Hud/Helppanels/carts.gui",
        "office_help": "GUI/Hud/Helppanels/offices.gui",
        "measure_help": "GUI/Hud/Helppanels/measures.gui",
        "settlement_help": "GUI/Hud/Helppanels/settlement.gui",
        "ship_help": "GUI/Hud/Helppanels/ship.gui",
        "skill_help": "GUI/Hud/Helppanels/skill.gui",
        "class_help": "GUI/Hud/Helppanels/class.gui",
        "zodiac_help": "GUI/Hud/Helppanels/zodiac.gui",
        "text_help": "GUI/Hud/Helppanels/text.gui",
        "onscreen_help": "GUI/Hud/Helppanels/text.gui",
    }
    help_resource = help_resources.get(surface)
    if help_resource:
        return PreviewSurfacePresentation(
            "onscreen_help",
            "dark_panel",
            "help",
            help_resource,
            "Hud/sheets/OnscreenHelp/bg.tga",
            "Hud/borders/Border_Gold_02.tga",
            "Hud/NoCompression/header_red.tga",
        )
    profiles = {
        "messagebox": PreviewSurfacePresentation(
            "message",
            "parchment",
            "parchment",
            "GUI/Hud/panel_messagebox.gui",
            "Hud/messagebox/mbback0.tga",
        ),
        "questbox": PreviewSurfacePresentation(
            "quest",
            "parchment",
            "parchment",
            "GUI/Hud/questboxpanel.gui",
            "Hud/messagebox/mbback0.tga",
        ),
        "news": PreviewSurfacePresentation(
            "news",
            "parchment",
            "parchment",
            "GUI/Hud/questboxpanel.gui",
            "Hud/messagebox/mbback0.tga",
            icon_asset=_news_icon_asset(category),
        ),
        "dialog": PreviewSurfacePresentation(
            "short",
            "dark_panel",
            "panel",
            "GUI/Hud/SayPanel.gui",
            "Hud/NoCompression/Priority3/PanelBackground_01.tga",
            "Hud/borders/Border_Gold_02.tga",
        ),
        "quick_message": PreviewSurfacePresentation(
            "short",
            "transparent",
            "overlay",
            "GUI/Hud/panel_quickmessage.gui",
        ),
        "measure_message": PreviewSurfacePresentation(
            "short",
            "transparent",
            "overlay",
            "GUI/Hud/panel_measuremessage.gui",
        ),
        "system_message": PreviewSurfacePresentation(
            "short",
            "transparent",
            "overlay",
            "GUI/Hud/panel_systemmessage.gui",
        ),
        "tutorial": PreviewSurfacePresentation(
            "tutorial",
            "dark_panel",
            "panel",
            "GUI/Hud/panel_tutorial.gui",
            "Hud/NoCompression/Priority3/PanelBackground_01.tga",
            "Hud/borders/Border_Gold_02.tga",
        ),
        "quest_intro": PreviewSurfacePresentation(
            "quest_intro",
            "transparent",
            "panel",
            "GUI/Hud/panel_questintro.gui",
            "Hud/NoCompression/Priority3/PanelBackground_01.tga",
            "Hud/borders/border_wood.tga",
        ),
        "questbook": PreviewSurfacePresentation(
            "questbook",
            "parchment",
            "book",
            "GUI/Hud/panel_questbooksheet.gui",
            "Hud/sheets/evidences/bg_buch.tga",
        ),
        "measure_choice": PreviewSurfacePresentation(
            "measure_choice",
            "dark_panel",
            "panel",
            "GUI/Hud/panel_measurechoice.gui",
            "Hud/NoCompression/Priority3/PanelBackground_01.tga",
            "Hud/borders/Border_Gold_02.tga",
        ),
        "measure_help": PreviewSurfacePresentation(
            "onscreen_help",
            "dark_panel",
            "help",
            "GUI/Hud/Helppanels/measures.gui",
            "Hud/sheets/OnscreenHelp/bg.tga",
            "Hud/borders/Border_Gold_02.tga",
            "Hud/NoCompression/header_red.tga",
        ),
        "tooltip": PreviewSurfacePresentation(
            "tooltip",
            "transparent",
            "tooltip",
            "GUI/styles/tooltip.gst",
        ),
        "status": PreviewSurfacePresentation(
            "status",
            "dark_panel",
            "panel",
            background_asset="Hud/NoCompression/Priority3/PanelBackground_01.tga",
        ),
        "overhead": PreviewSurfacePresentation(
            "overhead",
            "transparent",
            "overhead",
            gui_resource="GUI/styles/overheadsymbollabel.gst",
        ),
        "datebook": PreviewSurfacePresentation(
            "datebook",
            "parchment",
            "book",
            "GUI/Hud/panel_datebooksheet.gui",
            "Hud/sheets/evidences/bg_buch.tga",
        ),
        "city_schedule": PreviewSurfacePresentation(
            "city_schedule",
            "dark_panel",
            "panel",
            "GUI/Hud/panel_cityschedule.gui",
            "Hud/NoCompression/Priority3/PanelBackground_01.tga",
            "Hud/borders/Border_Gold_02.tga",
        ),
        "gui_embedded": PreviewSurfacePresentation(
            "gui_embedded",
            "dark_panel",
            "panel",
        ),
        "important_persons": PreviewSurfacePresentation(
            "important_persons",
            "parchment",
            "book",
            "GUI/Hud/panel_importantpersons.gui",
            "Hud/sheets/evidences/bg_buch.tga",
            "Hud/borders/Border_Gold_02.tga",
        ),
        "pamphlet": PreviewSurfacePresentation(
            "pamphlet",
            "dark_panel",
            "panel",
            "GUI/Hud/panel_pamphletsheet.gui",
            "Hud/NoCompression/Priority3/PanelBackground_01.tga",
            "Hud/borders/Border_Gold_02.tga",
        ),
    }
    return profiles.get(
        surface,
        PreviewSurfacePresentation("message", "parchment", "parchment"),
    )


def _feedback_category(call_name: str) -> str:
    return {
        "feedback_messageworkshop": "building",
        "feedback_messagecharacter": "intrigue",
        "feedback_messageothercharacters": "intrigue",
        "feedback_messageproduction": "production",
        "feedback_messageeconomie": "economie",
        "feedback_messagemilitary": "military",
        "feedback_messagepolitics": "politics",
        "feedback_messageschedule": "schedule",
        "feedback_messagemission": "mission",
        "feedback_messageoffice": "politics",
        "feedback_messagedefault": "default",
    }.get(call_name, "")


def _news_icon_asset(category: str) -> str:
    normalized = re.sub(r"[^a-z0-9_]+", "", category.casefold())
    return f"Hud/news/{normalized or 'default'}.tga"


def _argument_expressions(
    reference: CodeReference,
    arguments: tuple[str, ...],
) -> tuple[tuple[str, ...], ...]:
    resolved = reference.resolved_arguments
    return tuple(
        tuple(dict.fromkeys((*(resolved[index] if index < len(resolved) else ()), argument)))
        for index, argument in enumerate(arguments)
    )


def _contract_argument_hint(
    arguments: tuple[tuple[str, ...], ...],
    argument_index: int | None,
) -> str:
    if argument_index is None or argument_index >= len(arguments):
        return ""
    expressions = arguments[argument_index]
    for expression in reversed(expressions):
        value = expression.strip()
        match = STRING_LITERAL_RE.fullmatch(value)
        if match is not None:
            return (match.group(1) or match.group(2) or "").strip()
    return expressions[-1].strip() if expressions else ""


def _labels_by_argument(
    arguments: tuple[tuple[str, ...], ...],
) -> list[tuple[int, tuple[str, ...]]]:
    labels: list[tuple[int, tuple[str, ...]]] = []
    for index, expressions in enumerate(arguments):
        found: list[str] = []
        for argument in expressions:
            dynamic = dynamic_label_patterns(argument)
            candidates = dynamic or tuple(
                normalize_label(match.group(0)) for match in LABEL_RE.finditer(argument)
            )
            for label in candidates:
                if label not in found:
                    found.append(label)
        if found:
            labels.append((index, tuple(found)))
    return labels


def _context_label(label: str) -> str:
    return normalize_label(label).lstrip("_")


def _context_has_label(context: PreviewWindowContext, label: str) -> bool:
    return any(_equivalent_label(candidate, label) for candidate in context.labels)


def context_has_label(context: PreviewWindowContext, label: str) -> bool:
    return _context_has_label(context, _context_label(label))


def _buttons_from_arguments(
    arguments: tuple[tuple[str, ...], ...],
) -> tuple[PreviewWindowButton, ...]:
    buttons: list[PreviewWindowButton] = []
    for expressions in arguments:
        for argument in expressions:
            buttons.extend(_buttons_from_expression(argument))
    unique: list[PreviewWindowButton] = []
    seen: set[tuple[str, str, str, str]] = set()
    for button in buttons:
        key = (button.identifier, button.label, button.text, button.icon_asset)
        if key not in seen:
            seen.add(key)
            unique.append(button)
    return tuple(unique)


def _buttons_from_expression(expression: str) -> tuple[PreviewWindowButton, ...]:
    buttons: list[PreviewWindowButton] = []
    for part in _concat_parts(expression):
        buttons.extend(_direct_buttons_from_text(part))
    return tuple(buttons)


def _direct_buttons_from_text(text: str) -> tuple[PreviewWindowButton, ...]:
    buttons: list[PreviewWindowButton] = []
    for match in BUTTON_RE.finditer(text):
        body = match.group("body")
        if ".." in body:
            continue
        parts = _split_button_parts(body)
        identifier = parts[0].strip() if parts else ""
        label = ""
        text_value = ""
        icon_asset = ""
        for part in parts[1:]:
            asset_match = BUTTON_ASSET_RE.search(part)
            if asset_match is not None:
                icon_asset = asset_match.group(0).replace("\\", "/")
            label_match = RESOLVED_LABEL_RE.search(part) or LABEL_RE.search(part)
            if label_match is not None:
                label = normalize_label(label_match.group(0))
                continue
            literal = _literal_text(part)
            if literal and not icon_asset:
                text_value = literal
        buttons.append(
            PreviewWindowButton(
                identifier=identifier,
                label=label,
                text=text_value,
                icon_asset=icon_asset,
            )
        )
    return tuple(buttons)


def _concat_parts(expression: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in expression.split("..") if part.strip())


def _split_button_parts(value: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    escaped = False
    for index, char in enumerate(value):
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
            continue
        if char in "([":
            depth += 1
            continue
        if char in ")]" and depth:
            depth -= 1
            continue
        if char == "," and depth == 0:
            parts.append(value[start:index].strip())
            start = index + 1
    parts.append(value[start:].strip())
    return parts


def _literal_text(value: str) -> str:
    match = STRING_LITERAL_RE.search(value.strip())
    if match is None:
        return ""
    return (match.group(1) or match.group(2) or "").strip()


def _header_body_labels(
    call_name: str,
    labels_by_arg: list[tuple[int, tuple[str, ...]]],
    button_labels: set[str],
    current_label: str,
) -> tuple[str, str]:
    candidates: list[tuple[int, str]] = []
    for argument_index, labels in labels_by_arg:
        for label in labels:
            if label not in button_labels:
                candidates.append((argument_index, label))
    if not candidates:
        return "", ""
    contract = call_contract(call_name)
    if contract is not None:
        role_by_index = dict(contract.label_roles)
        header_candidates = [
            (index, label)
            for index, label in candidates
            if role_by_index.get(index) == "header"
        ]
        body_candidates = [
            (index, label)
            for index, label in candidates
            if role_by_index.get(index) == "body"
        ]
        header_values = (
            _specialize_candidates(header_candidates, current_label, minimum_argument_index=0)
            if header_candidates
            else []
        )
        body_values = (
            _specialize_candidates(body_candidates, current_label, minimum_argument_index=0)
            if body_candidates
            else []
        )
        if header_values or body_values:
            header = _nearest_or_first_label(header_values, current_label) if header_values else ""
            body = _nearest_or_first_label(body_values, current_label) if body_values else ""
            return header, body
    if call_name.startswith("feedback_message"):
        return _labels_from_first_two(_specialize_candidates(candidates, current_label, minimum_argument_index=1))
    if call_name == "msgsayinteraction":
        return _labels_from_first_two(_specialize_candidates(candidates, current_label, minimum_argument_index=4))
    if call_name in {"msgquick", "msgsay", "msgsaynowait", "msgmeasure"}:
        return "", _nearest_or_first_label(_specialize_candidates(candidates, current_label, minimum_argument_index=0), current_label)
    if call_name in {"msgnews", "msgnewsnowait"}:
        return _labels_from_first_two(_specialize_candidates(candidates, current_label, minimum_argument_index=5))
    if call_name in {"msgbox", "msgboxnowait", "msgquest", "showtutorialboxnowait"}:
        return _labels_from_first_two(_specialize_candidates(candidates, current_label, minimum_argument_index=2))
    return _labels_from_first_two(_specialize_candidates(candidates, current_label, minimum_argument_index=0))


def _labels_from_first_two(candidates: list[tuple[int, str]]) -> tuple[str, str]:
    unique: list[str] = []
    seen_arguments: set[int] = set()
    for argument_index, label in sorted(candidates, key=lambda item: item[0]):
        if argument_index in seen_arguments:
            continue
        seen_arguments.add(argument_index)
        if label not in unique:
            unique.append(label)
        if len(unique) >= 2:
            break
    if len(unique) == 1:
        if _looks_like_head(unique[0]):
            return unique[0], ""
        return "", unique[0]
    return unique[0], unique[1]


def _specialize_candidates(
    candidates: list[tuple[int, str]],
    current_label: str,
    *,
    minimum_argument_index: int,
) -> list[tuple[int, str]]:
    suffix = _numeric_suffix(current_label)
    narrowed: list[tuple[int, str]] = []
    for argument_index, label in candidates:
        if argument_index < minimum_argument_index:
            continue
        if suffix and label.endswith("_+*"):
            label = f"{label[:-3]}{suffix}"
        narrowed.append((argument_index, label))
    return narrowed or candidates


def _runtime_argument_labels(
    labels_by_arg: list[tuple[int, tuple[str, ...]]],
    window_labels: tuple[str, str],
    button_labels: set[str],
) -> tuple[str, ...]:
    last_window_label_index = -1
    for argument_index, labels in labels_by_arg:
        for label in labels:
            if any(_equivalent_label(label, window_label) for window_label in window_labels if window_label):
                last_window_label_index = max(last_window_label_index, argument_index)
    if last_window_label_index < 0:
        return ()
    values: list[str] = []
    for argument_index, labels in labels_by_arg:
        if argument_index <= last_window_label_index:
            continue
        for label in labels:
            if label in button_labels:
                continue
            if any(_equivalent_label(label, window_label) for window_label in window_labels if window_label):
                continue
            if label not in values:
                values.append(label)
    return tuple(values)


def _equivalent_label(left: str, right: str) -> bool:
    if not left or not right:
        return False
    left_value = left.lstrip("_")
    right_value = right.lstrip("_")
    if left_value == right_value:
        return True
    if _wildcard_label_matches(left_value, right_value):
        return True
    if _wildcard_label_matches(right_value, left_value):
        return True
    if left_value.endswith("_+*") and right_value.startswith(left_value[:-1]):
        return True
    if right_value.endswith("_+*") and left_value.startswith(right_value[:-1]):
        return True
    return False


def _wildcard_label_matches(pattern: str, label: str) -> bool:
    if "*" not in pattern:
        return False
    regex = "^" + re.escape(pattern).replace("\\*", "[a-z0-9_]+") + "$"
    return re.match(regex, label) is not None


def _numeric_suffix(label: str) -> str:
    match = re.search(r"_\+\d+$", label)
    return match.group(0) if match is not None else ""


def _nearest_or_first_label(candidates: list[tuple[int, str]], current_label: str) -> str:
    if current_label:
        for _, label in candidates:
            if label == current_label:
                return label
    return candidates[0][1]


def _looks_like_head(label: str) -> bool:
    return "_head" in label or label.endswith("head")
