from __future__ import annotations

from ..preview_presets import preview_preset, preview_presets


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
