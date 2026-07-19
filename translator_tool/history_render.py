from __future__ import annotations

from difflib import SequenceMatcher
import html
from pathlib import Path
from typing import Iterable

from .git_history import GitCommit, TranslationLogEntry
from .i18n import history_kind_text, translate


def history_text(text: str) -> str:
    return html.escape(text.replace("\r", ""))


def inline_diff_html(before: str, after: str) -> str:
    parts: list[str] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
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
    return "".join(parts) or f'<span class="diff-empty">{html.escape(translate("history.empty_value"))}</span>'


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


def render_entry_timeline_html(events: list[tuple[GitCommit, TranslationLogEntry]]) -> str:
    if not events:
        return _state_html(translate("history.entry_timeline.empty_title"), translate("history.entry_timeline.empty_detail"))
    entry = events[0][1]
    event_html: list[str] = []
    for commit, change in events:
        after = "" if change.kind == "删除" else change.translated_text
        event_html.append(
            f"""
            <section class="timeline-event">
              <div class="timeline-event__commit">{html.escape(commit.short_hash)} · {commit.timestamp:%Y-%m-%d %H:%M}</div>
              <div class="timeline-event__subject">{html.escape(commit.display)}</div>
              <div class="timeline-event__kind">{html.escape(history_kind_text(change.kind))}</div>
              <div class="timeline-event__diff">{inline_diff_html(change.before_text, after)}</div>
            </section>
            """
        )
    return f"""
    <html>
      <head><style>{_HISTORY_STYLE}</style></head>
      <body class="history-root">
        <section class="timeline-summary">
          <div class="timeline-summary__title">{html.escape(translate("history.entry_timeline.title", title=entry_title(entry), count=len(events)))}</div>
          <div class="timeline-summary__meta">{html.escape(entry_meta(entry))}</div>
          <div class="timeline-summary__source">{html.escape(translate("history.entry.source", text=entry.source_text))}</div>
        </section>
        {''.join(event_html)}
      </body>
    </html>
    """


def _state_html(title: str, detail: str) -> str:
    return f"""
    <html><head><style>{_HISTORY_STYLE}</style></head>
    <body class="history-root"><section class="timeline-summary">
      <div class="timeline-summary__title">{html.escape(title)}</div>
      <div class="timeline-summary__meta">{html.escape(detail)}</div>
    </section></body></html>
    """


_HISTORY_STYLE = """
body.history-root { background: #fbf1c7; color: #3c3836; font-family: 'Segoe UI', 'Microsoft YaHei UI'; margin: 0; }
.timeline-summary, .timeline-event { background: #f2e5bc; border: 2px solid #bdae93; border-radius: 10px; padding: 12px 14px; margin-bottom: 12px; }
.timeline-summary__title { font-size: 16px; font-weight: 900; }
.timeline-summary__meta, .timeline-summary__source, .timeline-event__subject { color: #665c54; margin-top: 5px; white-space: pre-wrap; }
.timeline-event__commit { font-weight: 900; }
.timeline-event__kind { display: inline-block; margin: 7px 0 5px; border-radius: 999px; background: #d5c4a1; padding: 1px 8px; font-weight: 900; }
.timeline-event__diff { background: #fbf1c7; border: 1px solid #d5c4a1; border-radius: 6px; padding: 7px 9px; white-space: pre-wrap; word-break: break-word; line-height: 1.5; }
.diff-del { background: #f5d6d6; color: #9d0006; text-decoration: line-through; }
.diff-add { background: #c6a15b; color: #1d2021; }
.diff-empty { color: #928374; font-style: italic; }
"""
