from __future__ import annotations

from dataclasses import dataclass
import html
import re
from typing import Callable


_TOC_LINE_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:\[(?P<kind>Category|Page|SubCategory|AutoPages)\])"
    r"(?P<value>.*)$",
    re.IGNORECASE,
)
_GUIDE_TOKEN_RE = re.compile(
    r"</?(?:header|text|separator|list|item|table|row|cell)\b[^>\r\n]*>"
    r"|\{/?[A-Za-z_]+(?::[A-Za-z0-9_:-]+)*\}"
    r"|\[(?:type=\"(?:bullet|numbered)\"|font=\"[^\"\]\r\n]+\"|columns=\d+|[rgb]=\d{1,3}|link=\"[A-Za-z0-9_:-]+\")\]",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GuideTocEntry:
    kind: str
    depth: int
    title: str
    page_id: str = ""
    directive: str = ""


def guide_page_id(file_rel: str) -> str:
    normalized = file_rel.replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1]
    return name[:-4] if name.casefold().endswith(".txt") else name


def parse_guide_toc(text: str) -> tuple[GuideTocEntry, ...]:
    entries: list[GuideTocEntry] = []
    for line in text.splitlines():
        match = _TOC_LINE_RE.match(line)
        if match is None:
            continue
        indent = match.group("indent").replace("\t", "  ")
        depth = max(0, len(indent) // 2)
        kind = match.group("kind").casefold()
        value = match.group("value").strip()
        if not value:
            continue
        if kind in {"page", "subcategory"}:
            page_id, separator, title = value.partition("|")
            page_id = page_id.strip()
            title = title.strip() if separator else page_id
            if page_id:
                entries.append(GuideTocEntry(kind, depth, title, page_id=page_id))
        elif kind == "autopages":
            entries.append(
                GuideTocEntry(kind, depth, value.replace("_", " "), directive=value)
            )
        else:
            entries.append(GuideTocEntry(kind, depth, value))
    return tuple(entries)


DynamicGuideResolver = Callable[[str, bool], tuple[str, ...]]


def render_guide_html(
    text: str,
    *,
    target: bool,
    dynamic_resolver: DynamicGuideResolver | None = None,
) -> str:
    """Compile Guide markup to safe interactive HTML without changing stored text."""

    output: list[str] = []
    position = 0
    list_tag = ""
    list_pending = False
    link_open = False
    tip_open = False
    item_depth = 0
    cell_depth = 0
    text_tags: list[str] = []
    color = {"r": 55, "g": 38, "b": 24}
    color_span_open = False

    def close_link() -> None:
        nonlocal link_open
        if link_open:
            output.append("</a>")
            link_open = False

    def ensure_list() -> None:
        nonlocal list_pending, list_tag
        if list_pending:
            list_tag = "ul"
            output.append("<ul>")
            list_pending = False

    def append_plain(value: str) -> None:
        if not value:
            return
        if value.strip():
            ensure_list()
        output.append(html.escape(value))

    for match in _GUIDE_TOKEN_RE.finditer(text):
        append_plain(text[position : match.start()])
        token = match.group(0)
        lowered = token.casefold()
        position = match.end()

        if token.startswith("<"):
            closing = lowered.startswith("</")
            if lowered.startswith("<header"):
                output.append('<h2 class="guide-header">')
            elif lowered == "</header>":
                output.append("</h2><hr>")
            elif lowered.startswith("<text"):
                text_tag = "span" if item_depth or cell_depth else "p"
                text_tags.append(text_tag)
                output.append(f"<{text_tag}>")
            elif lowered == "</text>":
                close_link()
                if tip_open:
                    output.append("</a>")
                    tip_open = False
                output.append(f"</{text_tags.pop() if text_tags else 'p'}>")
            elif lowered.startswith("<separator"):
                output.append("<hr>")
            elif lowered == "<list>":
                list_pending = True
            elif lowered == "</list>":
                ensure_list()
                output.append(f"</{list_tag or 'ul'}>")
                list_tag = ""
            elif lowered == "<item>":
                ensure_list()
                output.append("<li>")
                item_depth += 1
            elif lowered == "</item>":
                close_link()
                output.append("</li>")
                item_depth = max(0, item_depth - 1)
            elif lowered.startswith("<table"):
                output.append('<table class="guide-table">')
            elif lowered == "</table>":
                output.append("</table>")
            elif lowered == "<row>":
                output.append("<tr>")
            elif lowered == "</row>":
                output.append("</tr>")
            elif lowered.startswith("<cell"):
                output.append("<td>")
                cell_depth += 1
            elif lowered == "</cell>":
                output.append("</td>")
                cell_depth = max(0, cell_depth - 1)
            elif closing:
                output.append("")
            continue

        if token.startswith("["):
            type_match = re.fullmatch(r'\[type="(bullet|numbered)"\]', token, re.IGNORECASE)
            if type_match is not None and list_pending:
                list_tag = "ol" if type_match.group(1).casefold() == "numbered" else "ul"
                output.append(f"<{list_tag}>")
                list_pending = False
                continue
            link_match = re.fullmatch(r'\[link="([A-Za-z0-9_:-]+)"\]', token, re.IGNORECASE)
            if link_match is not None:
                close_link()
                page_id = html.escape(link_match.group(1), quote=True)
                output.append(f'<a class="guide-link" href="guide:{page_id}">')
                link_open = True
                continue
            rgb_match = re.fullmatch(r"\[([rgb])=(\d{1,3})\]", token, re.IGNORECASE)
            if rgb_match is not None:
                color[rgb_match.group(1).casefold()] = min(int(rgb_match.group(2)), 255)
                if color_span_open:
                    output.append("</span>")
                output.append(
                    '<span style="color: rgb({r}, {g}, {b})">'.format(**color)
                )
                color_span_open = True
            continue

        inner = token[1:-1]
        lowered_inner = inner.casefold()
        if lowered_inner.startswith("tip:"):
            tip_id = inner.split(":", 1)[1]
            escaped = html.escape(tip_id, quote=True)
            output.append(f'<a class="guide-tip" href="tip:{escaped}" title="{escaped}">')
            tip_open = True
        elif lowered_inner == "/tip":
            if tip_open:
                output.append("</a>")
                tip_open = False
        elif lowered_inner.startswith("key:"):
            output.append(f"<kbd>{html.escape(inner.split(':', 1)[1].replace('_', ' '))}</kbd>")
        elif lowered_inner.startswith(("autolist:", "bullet_autolist:")):
            directive = inner.split(":", 1)[1]
            values = dynamic_resolver(directive, target) if dynamic_resolver is not None else ()
            if values:
                output.append('<ul class="guide-dynamic">')
                output.extend(f"<li>{html.escape(value)}</li>" for value in values)
                output.append("</ul>")
            else:
                output.append(
                    '<span class="guide-unresolved">{'
                    + html.escape(inner)
                    + "}</span>"
                )

    append_plain(text[position:])
    close_link()
    if tip_open:
        output.append("</a>")
    if list_pending:
        ensure_list()
        output.append("</ul>")
    if color_span_open:
        output.append("</span>")
    return "".join(output)
