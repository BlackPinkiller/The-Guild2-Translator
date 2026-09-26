from __future__ import annotations

from datetime import datetime
import html
import re
import threading

from ..git_history import GitCommit, TranslationLogEntry
from ..history_render import inline_diff_html
from ..history_search import build_history_search_index, matches_terms, query_terms


def assert_history_search_prepares_complete_cancellable_results() -> None:
    commits = (
        GitCommit("b" * 40, "bbbbbbb", datetime(2026, 1, 2), "New save"),
        GitCommit("a" * 40, "aaaaaaa", datetime(2026, 1, 1), "Initial save"),
    )
    old = TranslationLogEntry("新增", "Text.dbt", "1", "Greeting", "Text", "HELLO", "您好")
    new = TranslationLogEntry("更新", "Text.dbt", "1", "Greeting", "Text", "HELLO", "你好", "您好")
    events = [(commits[0], new), (commits[1], old)]
    cancel = threading.Event()
    index = build_history_search_index(commits, events, cancel)
    assert index is not None
    if index.events_by_key[new.change_key] != events or index.change_count != 2:
        raise AssertionError("prepared history search changed timeline ordering")
    if not matches_terms(index.entry_blobs[new.change_key], query_terms(" Hello 您好 你好 INITIAL\nbbbbbbb")):
        raise AssertionError("prepared entry search lost a revision, source, or commit metadata")
    if matches_terms(index.commit_blobs[commits[1].full_hash], query_terms("你好")):
        raise AssertionError("commit search included text from a different revision")
    if not matches_terms(index.commit_blobs[commits[0].full_hash], query_terms("hello 你好")):
        raise AssertionError("commit search lost translated or source text")
    cancel.set()
    if build_history_search_index(commits, events, cancel) is not None:
        raise AssertionError("cancelled history search still published a snapshot")


def assert_history_diff_preserves_text_and_escapes_markup() -> None:
    if inline_diff_html("<same>&", "<same>&") != "&lt;same&gt;&amp;":
        raise AssertionError("unchanged diff text was not safely escaped")
    for before, after, expected in (
        ("ABC", "ABxC", 'AB<span class="diff-add">x</span>C'),
        ("ABxC", "ABC", 'AB<span class="diff-del">x</span>C'),
        ("<old>", "<new>", '&lt;<span class="diff-del">old</span><span class="diff-add">new</span>&gt;'),
    ):
        if inline_diff_html(before, after) != expected:
            raise AssertionError("trimmed history diff misplaced the changed text")
    before, after = "AB" * 3000, "BA" * 3000
    rendered = inline_diff_html(before, after)
    if html.unescape(re.sub(r"<[^>]*>", "", rendered)) != before + after:
        raise AssertionError("bounded long diff truncated replacement text")
    if 'diff-empty' not in inline_diff_html("", ""):
        raise AssertionError("empty history diff lost its empty-value indicator")
