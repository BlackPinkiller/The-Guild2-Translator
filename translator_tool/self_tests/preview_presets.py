from __future__ import annotations

from ..preview_presets import (
    preview_preset,
    preview_preset_natural_size,
    preview_presets,
    resolve_preview_regions,
)


def assert_preview_presets_are_complete_and_unambiguous() -> None:
    presets = preview_presets()
    if len(presets) != 7:
        raise AssertionError(f"expected seven focused preview styles, got {len(presets)}")
    owners: dict[str, str] = {}
    for preset in presets:
        for surface in preset.surfaces:
            previous = owners.setdefault(surface, preset.id)
            if previous != preset.id:
                raise AssertionError(
                    f"surface {surface!r} belongs to both {previous!r} and {preset.id!r}"
                )
        if not preset.regions:
            raise AssertionError(f"preview preset has no content regions: {preset.id}")
    expected = {
        "dialog": "framed_panel",
        "news": "parchment_message",
        "system_message": "compact_overlay",
        "item_help": "help_panel",
        "measure_choice": "choice_panel",
        "questbook": "split_book",
        "pamphlet": "document_page",
    }
    for surface, preset_id in expected.items():
        preset = preview_preset(surface)
        if preset is None or preset.id != preset_id:
            raise AssertionError(
                f"surface {surface!r} did not select {preset_id!r}: {preset!r}"
            )
    compact = preview_preset("system_message")
    if compact is None or compact.renderer != "flow":
        raise AssertionError("compact messages should opt into the generic flow renderer")
    short_slots = {"body": (72, 20)}
    long_slots = {"body": (280, 92)}
    short_natural = preview_preset_natural_size(compact, short_slots)
    long_natural = preview_preset_natural_size(compact, long_slots)
    if short_natural[1] >= long_natural[1]:
        raise AssertionError(
            f"preset natural height did not follow its content: {short_natural!r}, {long_natural!r}"
        )
    regions = resolve_preview_regions(compact, 260, 80, short_slots)
    body = next((region for region in regions if region.slot == "body"), None)
    if body is None or body.x != 16 or body.width != 228 or body.height != 60:
        raise AssertionError(f"compact preset padding/growth was not resolved: {body!r}")
    message = preview_preset("news")
    if message is None:
        raise AssertionError("news preset was not found")
    message_regions = resolve_preview_regions(
        message,
        440,
        180,
        {"header": (80, 24), "icon": (52, 52), "body": (220, 56)},
    )
    message_slots = {region.slot: region for region in message_regions if region.slot}
    if set(message_slots) != {"header", "icon", "body"}:
        raise AssertionError(
            f"a visible sibling slot disappeared from the preset tree: {message_slots!r}"
        )
    if message_slots["icon"].x >= message_slots["body"].x:
        raise AssertionError(
            f"message icon should reserve space before its body: {message_slots!r}"
        )
    if message_slots["icon"].width != 52:
        raise AssertionError(
            f"an active message icon should retain its material width: {message_slots!r}"
        )
    help_panel = preview_preset("text_help")
    document_page = preview_preset("quest_intro")
    if (
        help_panel is None
        or help_panel.renderer != "flow"
        or document_page is None
        or document_page.renderer != "flow"
    ):
        raise AssertionError("help and document presets should use the shared flow renderer")
    help_without_sidebar = resolve_preview_regions(
        help_panel,
        520,
        240,
        {"header": (120, 24), "body": (420, 120)},
    )
    help_slots = {
        region.slot: region for region in help_without_sidebar if region.slot
    }
    if "sidebar" in help_slots or help_slots["body"].width != 480:
        raise AssertionError(
            f"ordinary help should use the full content width: {help_slots!r}"
        )
    help_with_sidebar = resolve_preview_regions(
        help_panel,
        520,
        240,
        {"header": (120, 24), "body": (260, 120), "sidebar": (160, 132)},
    )
    help_slots = {region.slot: region for region in help_with_sidebar if region.slot}
    if help_slots["body"].x >= help_slots["sidebar"].x:
        raise AssertionError(
            f"optional help sidebar should follow the main body: {help_slots!r}"
        )
