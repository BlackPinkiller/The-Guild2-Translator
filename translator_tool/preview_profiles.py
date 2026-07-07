from __future__ import annotations

from dataclasses import dataclass

from .validation import FORMAT_GUIDE, guide_plain_double_quotes


@dataclass(frozen=True)
class PreviewBlocker:
    message_key: str


@dataclass(frozen=True)
class PreviewProfile:
    dialect: str
    final_style: bool = False
    line_height_percent: int = 100

    def blocker(self, text: str) -> PreviewBlocker | None:
        if self.dialect == FORMAT_GUIDE and guide_plain_double_quotes(text):
            return PreviewBlocker("preview.error.guide_quote")
        return None

    def guide_token_text(self, token: str) -> str | None:
        if self.dialect != FORMAT_GUIDE or not token.startswith("<"):
            return None
        lowered = token.casefold()
        if lowered.startswith("<separator"):
            return "\n────────\n"
        if lowered == "</header>":
            return "  "
        if lowered == "</text>":
            return "\n"
        if lowered in {"</list>", "</table>"}:
            return ""
        if lowered == "</row>":
            return "\n"
        if lowered == "</cell>":
            return " "
        if lowered == "<item>":
            return "• "
        if lowered == "</item>":
            return " "
        return ""


def preview_profile(dialect: str) -> PreviewProfile:
    if dialect == FORMAT_GUIDE:
        return PreviewProfile(dialect=dialect, final_style=True, line_height_percent=72)
    return PreviewProfile(dialect=dialect)
