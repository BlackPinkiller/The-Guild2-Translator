from __future__ import annotations

from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
import hashlib
import sqlite3
from typing import Iterator

from .git_history import TranslationLogEntry


SCHEMA_VERSION = 1
MAX_INDEXED_COMMITS = 5_000
MAX_INDEXED_CHANGES = 250_000
MAX_INDEXED_TEXT_BYTES = 128 * 1024 * 1024


class HistoryIndexCapacityError(RuntimeError):
    pass


class HistoryIndexStore:
    """Disposable, bounded search index derived from the language Git history."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def for_repository(
        cls,
        repo: Path,
        language: str,
        *,
        codec_fingerprint: str,
    ) -> "HistoryIndexStore":
        cache_key = f"{language}\0codec={codec_fingerprint}\0schema={SCHEMA_VERSION}"
        language_key = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()[:16]
        return cls(repo / ".git" / "translator-tool" / f"history-{language_key}.sqlite3")

    def indexed_hashes(self, commits: Iterable[str]) -> set[str]:
        hashes = tuple(commits)
        if not hashes or not self.path.exists():
            return set()
        with self._database() as database:
            return {
                row[0]
                for chunk in _chunks(hashes, 500)
                for row in database.execute(
                    f"SELECT commit_hash FROM indexed_commits WHERE commit_hash IN ({','.join('?' for _ in chunk)})",
                    chunk,
                )
            }

    def entries_for_commits(self, commits_newest_first: Iterable[str]) -> dict[str, list[TranslationLogEntry]]:
        hashes = tuple(commits_newest_first)
        entries: dict[str, list[TranslationLogEntry]] = {commit: [] for commit in hashes}
        if not hashes or not self.path.exists():
            return entries
        with self._database() as database:
            for chunk in _chunks(hashes, 200):
                rows = database.execute(
                    f"""
                    SELECT commit_hash, kind, file_rel, record_id, label, field_name,
                           source_text, translated_text, previous_text
                    FROM history_changes
                    WHERE commit_hash IN ({','.join('?' for _ in chunk)})
                    ORDER BY commit_hash, entry_order
                    """,
                    chunk,
                )
                for row in rows:
                    entries[row[0]].append(TranslationLogEntry(*row[1:]))
        return entries

    def store_commit(self, commit: str, entries: Iterable[TranslationLogEntry]) -> None:
        with self.writer() as writer:
            writer.store_commit(commit, entries)

    @contextmanager
    def writer(self) -> Iterator["_HistoryIndexWriter"]:
        """Reuse one bounded transaction while indexing a sequence of commits."""
        with self._database() as database:
            yield _HistoryIndexWriter(database)

    def retain_commits(self, commits: Iterable[str]) -> None:
        retained = tuple(commits)[:MAX_INDEXED_COMMITS]
        with self._database() as database:
            database.execute("CREATE TEMP TABLE retained_commits(commit_hash TEXT PRIMARY KEY)")
            database.executemany(
                "INSERT INTO retained_commits(commit_hash) VALUES (?)",
                ((value,) for value in retained),
            )
            stale_count = database.execute(
                """
                SELECT COUNT(*) FROM indexed_commits
                WHERE commit_hash NOT IN (SELECT commit_hash FROM retained_commits)
                """
            ).fetchone()[0]
            if not stale_count:
                return
            database.execute(
                "DELETE FROM indexed_commits WHERE commit_hash NOT IN (SELECT commit_hash FROM retained_commits)"
            )
            commit_count = database.execute("SELECT COUNT(*) FROM indexed_commits").fetchone()[0]
            change_count = database.execute("SELECT COUNT(*) FROM history_changes").fetchone()[0]
            text_bytes = sum(
                _row_text_bytes(row)
                for row in database.execute(
                    """
                    SELECT kind, file_rel, record_id, label, field_name,
                           source_text, translated_text, previous_text
                    FROM history_changes
                    """
                )
            )
            database.execute(
                "UPDATE index_totals SET commit_count = ?, change_count = ?, text_bytes = ? WHERE singleton = 1",
                (commit_count, change_count, text_bytes),
            )

    @contextmanager
    def _database(self) -> Iterator[sqlite3.Connection]:
        database = self._connect()
        try:
            with database:
                yield database
        finally:
            database.close()

    def _connect(self) -> sqlite3.Connection:
        try:
            return self._connect_once()
        except sqlite3.DatabaseError as exc:
            if not _is_recoverable_index_error(exc):
                raise
            self.path.unlink(missing_ok=True)
            return self._connect_once()

    def _connect_once(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        database = sqlite3.connect(self.path, timeout=5)
        try:
            database.execute("PRAGMA foreign_keys = ON")
            version = database.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, SCHEMA_VERSION}:
                raise sqlite3.DatabaseError(f"unsupported history index version: {version}")
            database.executescript(
                """
                CREATE TABLE IF NOT EXISTS indexed_commits(
                    commit_hash TEXT PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS history_changes(
                    commit_hash TEXT NOT NULL REFERENCES indexed_commits(commit_hash) ON DELETE CASCADE,
                    entry_order INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    file_rel TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    field_name TEXT NOT NULL,
                    source_text TEXT NOT NULL,
                    translated_text TEXT NOT NULL,
                    previous_text TEXT,
                    PRIMARY KEY(commit_hash, entry_order)
                );
                CREATE TABLE IF NOT EXISTS index_totals(
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    commit_count INTEGER NOT NULL,
                    change_count INTEGER NOT NULL,
                    text_bytes INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO index_totals(singleton, commit_count, change_count, text_bytes)
                VALUES (1, 0, 0, 0);
                """
            )
            if version == 0:
                database.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            return database
        except Exception:
            database.close()
            raise


class _HistoryIndexWriter:
    def __init__(self, database: sqlite3.Connection) -> None:
        self.database = database

    def store_commit(self, commit: str, entries: Iterable[TranslationLogEntry]) -> None:
        packed = tuple(entries)
        payload_bytes = sum(_entry_text_bytes(entry) for entry in packed)
        database = self.database
        if database.execute(
            "SELECT 1 FROM indexed_commits WHERE commit_hash = ?", (commit,)
        ).fetchone() is not None:
            return
        current_commits, current_changes, current_bytes = database.execute(
            "SELECT commit_count, change_count, text_bytes FROM index_totals WHERE singleton = 1"
        ).fetchone()
        if current_commits >= MAX_INDEXED_COMMITS:
            raise HistoryIndexCapacityError("commit limit reached")
        if current_changes + len(packed) > MAX_INDEXED_CHANGES:
            raise HistoryIndexCapacityError("change limit reached")
        if current_bytes + payload_bytes > MAX_INDEXED_TEXT_BYTES:
            raise HistoryIndexCapacityError("text limit reached")
        database.execute("INSERT INTO indexed_commits(commit_hash) VALUES (?)", (commit,))
        database.executemany(
            """
            INSERT INTO history_changes(
                commit_hash, entry_order, kind, file_rel, record_id, label, field_name,
                source_text, translated_text, previous_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    commit,
                    order,
                    entry.kind,
                    entry.file_rel,
                    entry.record_id,
                    entry.label,
                    entry.field_name,
                    entry.source_text,
                    entry.translated_text,
                    entry.previous_text,
                )
                for order, entry in enumerate(packed)
            ),
        )
        database.execute(
            """
            UPDATE index_totals
            SET commit_count = commit_count + 1,
                change_count = change_count + ?,
                text_bytes = text_bytes + ?
            WHERE singleton = 1
            """,
            (len(packed), payload_bytes),
        )


def _is_recoverable_index_error(error: sqlite3.DatabaseError) -> bool:
    message = str(error).casefold()
    return any(
        marker in message
        for marker in (
            "file is not a database",
            "database disk image is malformed",
            "unsupported history index version",
        )
    )


def _chunks(values: tuple[str, ...], size: int) -> Iterable[tuple[str, ...]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _entry_text_bytes(entry: TranslationLogEntry) -> int:
    return _row_text_bytes(
        (
            entry.kind,
            entry.file_rel,
            entry.record_id,
            entry.label,
            entry.field_name,
            entry.source_text,
            entry.translated_text,
            entry.previous_text,
        )
    )


def _row_text_bytes(values: Iterable[str | None]) -> int:
    return sum(len((value or "").encode("utf-8")) for value in values)
