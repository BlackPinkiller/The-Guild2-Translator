from __future__ import annotations

from dataclasses import dataclass
import threading

from .git_history import GitCommit, TranslationLogEntry
from .history_render import commit_search_blob, entry_meta, entry_search_blob, entry_title


EntryKey = tuple[str, str, str, str]


@dataclass
class HistorySearchIndex:
    """One search snapshot, bounded by HistoryIndexStore's commit/text limits."""

    events_by_key: dict[EntryKey, list[tuple[GitCommit, TranslationLogEntry]]]
    entry_keys: list[EntryKey]
    entry_blobs: dict[EntryKey, str]
    commit_blobs: dict[str, str]
    change_count: int


def build_history_search_index(
    commits: tuple[GitCommit, ...],
    events: list[tuple[GitCommit, TranslationLogEntry]],
    cancel_event: threading.Event,
) -> HistorySearchIndex | None:
    """Prepare text and grouping in the index worker, before touching widgets."""
    metadata = {commit.full_hash: commit_search_blob(commit) for commit in commits}
    by_commit: dict[str, list[str]] = {key: [value] for key, value in metadata.items()}
    by_key: dict[EntryKey, list[tuple[GitCommit, TranslationLogEntry]]] = {}
    key_blobs: dict[EntryKey, list[str]] = {}
    for commit, entry in events:
        if cancel_event.is_set():
            return None
        key = entry.change_key
        blob = entry_search_blob(entry)
        by_key.setdefault(key, []).append((commit, entry))
        key_blobs.setdefault(key, []).extend((blob, metadata[commit.full_hash]))
        by_commit[commit.full_hash].append(blob)
    keys = sorted(by_key, key=lambda key: (
        entry_title(by_key[key][0][1]).casefold(), entry_meta(by_key[key][0][1]).casefold(),
    ))
    entry_blobs: dict[EntryKey, str] = {}
    for key in keys:
        if cancel_event.is_set():
            return None
        entry_blobs[key] = "\n".join(key_blobs.pop(key))
    commit_blobs: dict[str, str] = {}
    for key, blobs in by_commit.items():
        if cancel_event.is_set():
            return None
        commit_blobs[key] = "\n".join(blobs)
    return HistorySearchIndex(by_key, keys, entry_blobs, commit_blobs, len(events))


def query_terms(query: str) -> tuple[str, ...]:
    return tuple(query.casefold().split())


def matches_terms(blob: str, terms: tuple[str, ...]) -> bool:
    return all(term in blob for term in terms)
