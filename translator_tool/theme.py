"""Shared, Qt-independent colors for widgets and rendered history documents."""
from __future__ import annotations


INTERFACE_COLORS = {
    "modern": {
        "window": "#eef0f5", "base": "#ffffff", "panel": "#f6f7fb", "raised": "#e9edf6",
        "text": "#26314c", "muted": "#63708a", "disabled": "#919bae", "border": "#dce1ec",
        "divider": "#eef1f7",
        "accent": "#405fc1", "accent_hover": "#334eaa", "accent_text": "#ffffff",
        "selection": "#e7edff", "selection_text": "#263e80",
        "success_bg": "#e5f2ea", "success_text": "#286343",
        "warning_bg": "#fcf0d7", "warning_text": "#805710",
        "danger_bg": "#fbe8e7", "danger_text": "#a33838",
        "info_bg": "#e8edf8", "info_text": "#405f97",
    },
    "dark": {
        "window": "#171b24", "base": "#202530", "panel": "#252b37", "raised": "#30394b",
        "text": "#e4e8f1", "muted": "#a4afc2", "disabled": "#758096", "border": "#3a4457",
        "divider": "#2b3241",
        "accent": "#97afff", "accent_hover": "#b2c3ff", "accent_text": "#172344",
        "selection": "#334467", "selection_text": "#f0f3ff",
        "success_bg": "#213c31", "success_text": "#a0d8b6",
        "warning_bg": "#433722", "warning_text": "#ebc784",
        "danger_bg": "#442b32", "danger_text": "#f3b0b2",
        "info_bg": "#29384e", "info_text": "#b0c8f0",
    },

}


def history_colors(theme: str = "modern") -> dict[str, str]:
    colors = INTERFACE_COLORS.get(theme, INTERFACE_COLORS["modern"])
    return {
        **colors,
        "error_bg": colors["danger_bg"], "error_border": colors["danger_text"],
        "header": colors["raised"], "entry": colors["panel"], "diff": colors["base"],
        "add_bg": colors["success_bg"], "add_text": colors["success_text"],
        "diff_add_bg": colors["success_bg"], "diff_add_text": colors["success_text"],
        "empty": colors["muted"],
    }
