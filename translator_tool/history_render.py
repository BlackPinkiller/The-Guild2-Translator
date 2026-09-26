from __future__ import annotations

from difflib import SequenceMatcher
import html
from pathlib import Path
from typing import Iterable

from .git_history import GitCommit, TranslationLogEntry
from .i18n import history_kind_text, translate
from .theme import history_colors


def history_text(text: str) -> str:
    return html.escape(text.replace("\r", ""))


def inline_diff_html(before: str, after: str) -> str:
    # Long repetitive Guides can make an exact character diff quadratic. Trim
    # unchanged ends first; bound the remaining comparison and preserve all text
    # in a coarse replacement when a detailed match would exceed the budget.
    prefix = 0
    common = min(len(before), len(after))
    while prefix < common and before[prefix] == after[prefix]:
        prefix += 1
    suffix = 0
    while suffix < common - prefix and before[-suffix - 1] == after[-suffix - 1]:
        suffix += 1
    leading = history_text(before[:prefix])
    trailing = history_text(after[len(after) - suffix:]) if suffix else ""
    before = before[prefix:len(before) - suffix if suffix else len(before)]
    after = after[prefix:len(after) - suffix if suffix else len(after)]
    if len(before) * len(after) > 1_000_000:
        operations = [("replace", 0, len(before), 0, len(after))]
    else:
        operations = SequenceMatcher(None, before, after, autojunk=False).get_opcodes()
    parts: list[str] = []
    for tag, i1, i2, j1, j2 in operations:
        left = history_text(before[i1:i2])
        right = history_text(after[j1:j2])
        if tag == "equal":
            parts.append(right)
        elif tag == "delete":
            if left:
                parts.append(f'<span class="diff-del">{left}</span>')
        elif tag == "insert":
            if right:
                parts.append(f'<span class="diff-add">{right}</span>')
        else:
            if left:
                parts.append(f'<span class="diff-del">{left}</span>')
            if right:
                parts.append(f'<span class="diff-add">{right}</span>')
    return leading + "".join(parts) + trailing or f'<span class="diff-empty">{html.escape(translate("history.empty_value"))}</span>'


def entry_title(entry: TranslationLogEntry) -> str:
    title = entry.label if entry.label and entry.label != entry.file_rel else ""
    if not title:
        title = f"ID {entry.record_id}" if entry.record_id else Path(entry.file_rel).name
    hidden_fields = {"body", "text", "translation", "translated", "translator"}
    if entry.field_name and entry.field_name.lower() not in hidden_fields:
        title = f"{title} · {entry.field_name}"
    return title


def entry_meta(entry: TranslationLogEntry) -> str:
    parts = [entry.file_rel]
    if entry.record_id:
        parts.append(f"ID {entry.record_id}")
    return " · ".join(parts)


def entry_search_blob(entry: TranslationLogEntry) -> str:
    return "\n".join(
        (
            entry.file_rel,
            entry.record_id,
            entry.label,
            entry.field_name,
            entry.source_text,
            entry.before_text,
            entry.translated_text,
        )
    ).casefold()


def commit_search_blob(commit: GitCommit, entries: Iterable[TranslationLogEntry] = ()) -> str:
    values = [commit.full_hash, commit.short_hash, commit.subject, commit.display]
    values.extend(entry_search_blob(entry) for entry in entries)
    return "\n".join(values).casefold()


def render_entry_timeline_html(
    events: list[tuple[GitCommit, TranslationLogEntry]], *, theme: str = "modern",
) -> str:
    colors = history_colors(theme)
    if not events:
        return _state_html(translate("history.entry_timeline.empty_title"), translate("history.entry_timeline.empty_detail"), theme=theme)
    entry = events[0][1]
    event_html: list[str] = []
    for commit, change in events:
        after = "" if change.kind == "删除" else change.translated_text
        kind = html.escape(history_kind_text(change.kind))
        subject = html.escape(commit.display.split(" · ", 2)[-1])
        event_html.append(
            f"""
            <table width="100%" cellspacing="0" cellpadding="12" bgcolor="{colors['panel']}">
              <tr><td>
                <p class="timeline-commit">{html.escape(commit.short_hash)} · {commit.timestamp:%Y-%m-%d %H:%M} · {kind}</p>
                <p class="timeline-meta">{subject}</p>
                <p class="timeline-diff">{inline_diff_html(change.before_text, after)}</p>
              </td></tr>
            </table><br>
            """
        )
    # QTextDocument supports tables and paragraph spacing, but ignores several
    # CSS card properties (including section padding). Use its supported HTML.
    return f"""
    <html>
      <head><style>{_history_style(theme)}</style></head>
      <body>
        <p class="timeline-title">{html.escape(translate("history.entry_timeline.title", title=entry_title(entry), count=len(events)))}</p>
        <p class="timeline-meta">{html.escape(entry_meta(entry))}</p>
        <p class="timeline-source">{html.escape(translate("history.entry.source", text=entry.source_text))}</p>
        {''.join(event_html)}
      </body>
    </html>
    """


def _state_html(title: str, detail: str, *, theme: str = "modern") -> str:
    return f"""
    <html><head><style>{_history_style(theme)}</style></head>
    <body><p class="timeline-title">{html.escape(title)}</p>
      <p class="timeline-meta">{html.escape(detail)}</p></body></html>
    """


def _history_style(theme: str) -> str:
    colors = history_colors(theme)
    return f"""
body {{ background: {colors['base']}; color: {colors['text']}; font-family: 'Microsoft YaHei UI', 'Segoe UI'; margin: 0; }}
p {{ margin-top: 0; margin-bottom: 8px; }}
.timeline-title {{ font-size: 17px; font-weight: 600; margin-bottom: 8px; }}
.timeline-meta, .timeline-source {{ color: {colors['muted']}; white-space: pre-wrap; }}
.timeline-source {{ margin-top: 8px; margin-bottom: 18px; }}
.timeline-commit {{ font-weight: 600; }}
.timeline-diff {{ white-space: pre-wrap; line-height: 140%; margin-top: 12px; }}
.diff-del {{ background: {colors['danger_bg']}; color: {colors['danger_text']}; text-decoration: line-through; }}
.diff-add {{ background: {colors['diff_add_bg']}; color: {colors['diff_add_text']}; }}
.diff-empty {{ color: {colors['empty']}; font-style: italic; }}
"""
