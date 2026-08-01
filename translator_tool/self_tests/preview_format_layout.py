from __future__ import annotations

from ..preview import PreviewService, _aligned_line_left, _next_tab_width


def assert_preview_format_layout_controls_are_semantic() -> None:
    document = PreviewService().render(
        "$Lleft$N$Zcenter$N$Rright$N$T tabbed",
        unit_key="layout-controls",
        label="_LAYOUT_CONTROLS_+0",
        file_rel="Text.dbt",
        kind="dbt",
        target=False,
    )
    controls = [atom.layout for atom in document.atoms if atom.layout]
    if controls != ["left", "center", "right", "tab"]:
        raise AssertionError(f"format layout controls lost their semantics: {controls!r}")
    if "$L" in document.display_text or "$T" in document.display_text:
        raise AssertionError("format layout controls leaked into preview text")
    if _aligned_line_left("left", 10, 210, 40) != 10:
        raise AssertionError("left-aligned preview line did not use the content edge")
    if _aligned_line_left("center", 10, 210, 40) != 90:
        raise AssertionError("centered preview line did not use the content center")
    if _aligned_line_left("right", 10, 210, 40) != 170:
        raise AssertionError("right-aligned preview line did not use the content edge")
    if _next_tab_width(0, 48) != 48 or _next_tab_width(49, 48) != 47:
        raise AssertionError("preview tabs did not advance to the next stable tab stop")
