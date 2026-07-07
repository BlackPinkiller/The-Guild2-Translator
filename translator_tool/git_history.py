from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import argparse
import re
import subprocess
import threading
import time
from typing import Iterable

from .codec_adapter import CodecError, Guild2Codec, load_codec_for_language
from .format_io import (
    DbtDocument,
    load_dbt_bytes,
    load_plain_text_bytes,
    matching_source_field,
    row_key,
    translatable_fields,
)
from .i18n import translate
from .project import MISSING_WORK_STATUSES, TranslationUnit
from .settings import AppSettings


class GitError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitCommit:
    full_hash: str
    short_hash: str
    timestamp: datetime
    subject: str

    @property
    def display(self) -> str:
        return f"{self.short_hash} · {self.timestamp:%Y-%m-%d %H:%M} · {_display_subject(self.subject)}"


@dataclass(frozen=True)
class TranslationLogEntry:
    kind: str
    file_rel: str
    record_id: str
    label: str
    field_name: str
    source_text: str
    translated_text: str
    previous_text: str | None = None

    @property
    def heading(self) -> str:
        """The entry identity, intentionally excluding the enclosing file name."""
        identity = ""
        if self.record_id:
            identity = f"#{self.record_id}"
        if self.label:
            identity = f"{identity} · {self.label}" if identity else self.label
        if self.field_name:
            identity = f"{identity} · {self.field_name}" if identity else self.field_name
        return f"[{self.kind}] {identity or self.file_rel}"

    @property
    def change_key(self) -> tuple[str, str, str, str]:
        return (self.file_rel, self.record_id, self.label, self.field_name)

    @property
    def before_text(self) -> str:
        return self.source_text if self.previous_text is None else self.previous_text

    @property
    def display_before_text(self) -> str:
        return self.source_text if self.kind == "新增" else self.before_text

    def merged_with(self, newer: "TranslationLogEntry") -> "TranslationLogEntry":
        if self.change_key != newer.change_key:
            raise ValueError("cannot merge unrelated history entries")
        previous_text = self.previous_text
        if newer.kind == "删除" and previous_text not in {None, "", self.source_text}:
            kind = "删除"
        else:
            kind = "新增" if previous_text in {None, "", self.source_text} else "更新"
        return TranslationLogEntry(
            kind,
            newer.file_rel,
            newer.record_id,
            newer.label,
            newer.field_name,
            newer.source_text,
            newer.translated_text,
            previous_text,
        )


class LanguageGit:
    """A narrow Git facade: only the language repository is ever auto-committed."""

    # A zero-byte lock left by a killed Git process is safe to remove after a
    # brief grace period. Never touch a non-empty or freshly-created lock:
    # those may still belong to a live Git operation.
    STALE_INDEX_LOCK_SECONDS = 5

    def __init__(
        self,
        project_root: Path,
        language: str = "#chinese",
        codec_root: Path | None = None,
        *,
        enable_codec: bool = True,
    ) -> None:
        self.project_root = project_root.resolve()
        self.repo = self.project_root / "languages"
        self.language = language
        self.enable_codec = enable_codec
        self.codec = (
            load_codec_for_language(codec_root if codec_root is not None else self.project_root, language)
            if enable_codec
            else None
        )
        self._cache_lock = threading.Lock()
        self._commit_list_cache: tuple[GitCommit, ...] | None = None
        self._entry_cache: dict[str, tuple[TranslationLogEntry, ...]] = {}
        self._combined_cache: dict[tuple[str, ...], tuple[TranslationLogEntry, ...]] = {}

    def ensure_repository(self, settings: AppSettings) -> bool:
        """Create the initial language baseline. Returns true when it was created."""
        if self._is_repository():
            self._ensure_identity(settings)
            return False
        self._run("init", "-b", "main")
        self._ensure_identity(settings)
        self._run("add", "--all")
        if self._has_staged_changes():
            self._run("commit", "-m", "chore: import language baseline")
        self._invalidate_history_cache()
        return True

    def commit_saved(
        self,
        changed_files: Iterable[Path],
        saved_units: Iterable[TranslationUnit],
        deleted_units: Iterable[TranslationUnit] = (),
    ) -> GitCommit | None:
        relative_paths = [str(path.resolve().relative_to(self.repo)).replace("\\", "/") for path in changed_files]
        if not relative_paths:
            return None
        self._run("add", "--", *relative_paths)
        if not self._has_staged_changes(relative_paths):
            return None
        units = tuple(saved_units)
        deleted = len(tuple(deleted_units))
        added = sum(1 for unit in units if unit.status in MISSING_WORK_STATUSES)
        updated = len(units) - added
        files = ", ".join(sorted({Path(path).name for path in relative_paths}))
        portions: list[str] = []
        if added:
            portions.append(f"add {added}")
        if updated:
            portions.append(f"update {updated}")
        if deleted:
            portions.append(f"delete {deleted}")
        if not portions:
            portions.append("sync")
        subject = f"translation: {', '.join(portions)} ({files})"
        self._run("commit", "--only", "-m", subject, "--", *relative_paths)
        self._invalidate_history_cache()
        return self.list_commits(1)[0]

    def has_pending_changes(self) -> bool:
        # The source language and other target languages can legitimately have
        # local changes.  This UI only owns the active target-language folder.
        return bool(self._pending_target_paths())

    def commit_pending(self) -> GitCommit | None:
        paths = self._pending_target_paths()
        if not paths:
            return None
        self._run("add", "--", *paths)
        if not self._has_staged_changes(paths):
            return None
        self._run("commit", "--only", "-m", "translation: commit pending language changes", "--", *paths)
        self._invalidate_history_cache()
        return self.list_commits(1)[0]

    def list_commits(self, limit: int = 100) -> list[GitCommit]:
        with self._cache_lock:
            cached = self._commit_list_cache
        if cached is not None and limit <= 100:
            return list(cached[:limit])
        result = self._run("log", f"-n{limit}", "--format=%H%x1f%h%x1f%ct%x1f%s", "--", self._language_pathspec())
        commits: list[GitCommit] = []
        for line in result.stdout.splitlines():
            parts = line.split("\x1f", 3)
            if len(parts) != 4:
                continue
            commits.append(GitCommit(parts[0], parts[1], datetime.fromtimestamp(int(parts[2])), parts[3]))
        if limit == 100:
            with self._cache_lock:
                self._commit_list_cache = tuple(commits)
        return commits

    def entries_for_commit(self, commit: str) -> list[TranslationLogEntry]:
        with self._cache_lock:
            cached = self._entry_cache.get(commit)
        if cached is not None:
            return list(cached)
        parent = self._parent_of(commit)
        if parent is None:
            return []
        changed = self._run("diff-tree", "--no-commit-id", "--name-only", "-r", commit).stdout.splitlines()
        prefix = self._language_pathspec()
        entries: list[TranslationLogEntry] = []
        for target_rel in changed:
            if not target_rel.startswith(prefix):
                continue
            file_rel = target_rel[len(prefix) :]
            after = self._show_bytes(commit, target_rel)
            before = self._show_bytes(parent, target_rel)
            source = self._show_bytes(commit, file_rel)
            if source is None:
                continue
            if target_rel.lower().endswith(".dbt"):
                if after is None:
                    continue
                entries.extend(self._dbt_entries(file_rel, source, before, after))
            elif target_rel.lower().endswith(".txt"):
                entries.extend(self._text_entries(file_rel, source, before, after))
        packed = tuple(entries)
        with self._cache_lock:
            self._entry_cache.setdefault(commit, packed)
            cached = self._entry_cache[commit]
        return list(cached)

    def entries_for_commits(self, commits_oldest_first: Iterable[str]) -> list[TranslationLogEntry]:
        """Return translation changes for the selected commits in commit order."""
        commit_list = tuple(commits_oldest_first)
        if not commit_list:
            return []
        with self._cache_lock:
            cached = self._combined_cache.get(commit_list)
        if cached is not None:
            return list(cached)
        entry_groups = tuple(tuple(self.entries_for_commit(commit)) for commit in commit_list)
        combined = tuple(entry for group in entry_groups for entry in group)
        with self._cache_lock:
            self._combined_cache.setdefault(commit_list, combined)
            cached = self._combined_cache[commit_list]
        return list(cached)

    def _dbt_entries(
        self, file_rel: str, source_raw: bytes, before_raw: bytes | None, after_raw: bytes
    ) -> list[TranslationLogEntry]:
        file_name = Path(file_rel).name
        source_doc = load_dbt_bytes(Path(file_name), source_raw)
        after_doc = load_dbt_bytes(Path(file_name), after_raw)
        before_doc = load_dbt_bytes(Path(file_name), before_raw) if before_raw is not None else None
        source_rows = source_doc.row_index
        after_rows = after_doc.row_index
        before_rows = before_doc.row_index if before_doc is not None else {}
        fields = translatable_fields(file_name, after_doc.string_columns)
        entries: list[TranslationLogEntry] = []
        for row in after_doc.rows:
            key = row_key(file_name, row)
            source_row = source_rows.get(key)
            before_row = before_rows.get(key)
            for field_name in fields:
                after_value = row.get(field_name)
                before_value = before_row.get(field_name) if before_row is not None else None
                if before_value == after_value:
                    continue
                source_field = matching_source_field(field_name, source_doc.string_columns)
                source_text = source_row.get(source_field) if source_row is not None else ""
                previous_text = self._decode(before_value) if before_value is not None else None
                kind = "新增" if previous_text in {None, "", source_text} else "更新"
                entries.append(
                    TranslationLogEntry(
                        kind,
                        file_rel,
                        str(key[0]),
                        key[1],
                        field_name,
                        source_text,
                        self._decode(after_value),
                        previous_text,
                    )
                )
        for key, before_row in before_rows.items():
            if key in after_rows:
                continue
            source_row = source_rows.get(key)
            for field_name in fields:
                before_value = before_row.get(field_name)
                if before_value is None:
                    continue
                source_field = matching_source_field(field_name, source_doc.string_columns)
                source_text = source_row.get(source_field) if source_row is not None else ""
                entries.append(
                    TranslationLogEntry(
                        "删除",
                        file_rel,
                        str(key[0]),
                        key[1],
                        field_name,
                        source_text,
                        "",
                        self._decode(before_value),
                    )
                )
        return entries

    def _text_entries(
        self, file_rel: str, source_raw: bytes, before_raw: bytes | None, after_raw: bytes | None
    ) -> list[TranslationLogEntry]:
        source = load_plain_text_bytes(Path(file_rel), source_raw).text
        after = load_plain_text_bytes(Path(file_rel), after_raw).text if after_raw is not None else None
        before = load_plain_text_bytes(Path(file_rel), before_raw).text if before_raw is not None else None
        if before == after:
            return []
        if after is None:
            previous_text = before if before is not None else ""
            return [TranslationLogEntry("删除", file_rel, "", file_rel, "body", source, "", previous_text)]
        previous_text = before if before is not None else None
        kind = "新增" if previous_text in {None, "", source} else "更新"
        return [TranslationLogEntry(kind, file_rel, "", file_rel, "body", source, after, previous_text)]

    def _decode(self, value: str) -> str:
        if self.codec is None:
            return value
        try:
            return self.codec.decode(value)
        except CodecError:
            return value

    def _parent_of(self, commit: str) -> str | None:
        result = self._run("rev-parse", f"{commit}^", check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    def _show_bytes(self, commit: str, path: str) -> bytes | None:
        result = self._run("show", f"{commit}:{path}", text=False, check=False)
        return result.stdout if result.returncode == 0 else None

    def _ensure_identity(self, settings: AppSettings) -> None:
        name = self._run("config", "--get", "user.name", check=False).stdout.strip()
        email = self._run("config", "--get", "user.email", check=False).stdout.strip()
        if not name:
            self._run("config", "user.name", settings.git_author_name or "The Guild 2 Translator")
        if not email:
            self._run("config", "user.email", settings.git_author_email or "translator@local")

    def _has_staged_changes(self, paths: Iterable[str] | None = None) -> bool:
        args = ["diff", "--cached", "--quiet"]
        if paths:
            args.extend(("--", *paths))
        return self._run(*args, check=False).returncode != 0

    def _pending_target_paths(self) -> list[str]:
        prefix = self._language_pathspec()
        raw = self._run("status", "--porcelain", "-z").stdout
        paths: list[str] = []
        for record in raw.split("\0"):
            if len(record) < 4:
                continue
            path = record[3:]
            if path.startswith(prefix):
                paths.append(path)
        return sorted(set(paths))

    def _is_repository(self) -> bool:
        result = self._run("rev-parse", "--show-toplevel", check=False)
        if result.returncode != 0:
            return False
        try:
            return Path(result.stdout.strip()).resolve() == self.repo.resolve()
        except OSError:
            return False

    def _run(self, *args: str, text: bool = True, check: bool = True) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
        try:
            result = subprocess.run(
                ["git", "-C", str(self.repo), *args],
                **self._subprocess_kwargs(text=text),
            )
        except FileNotFoundError as exc:
            raise GitError(translate("git.error.not_found")) from exc
        if check and result.returncode != 0:
            stderr = result.stderr.decode("utf-8", "replace") if isinstance(result.stderr, bytes) else result.stderr
            if "index.lock" in stderr and self._clear_stale_index_lock():
                # Retry exactly once. A real concurrent Git operation will
                # keep or recreate its own lock and report its own error.
                result = subprocess.run(
                    ["git", "-C", str(self.repo), *args],
                    **self._subprocess_kwargs(text=text),
                )
                if result.returncode == 0:
                    return result
                stderr = result.stderr.decode("utf-8", "replace") if isinstance(result.stderr, bytes) else result.stderr
            raise GitError(stderr.strip() or translate("git.error.command_failed"))
        return result

    @staticmethod
    def _subprocess_kwargs(*, text: bool) -> dict[str, object]:
        kwargs: dict[str, object] = {
            "capture_output": True,
            "text": text,
            "encoding": "utf-8" if text else None,
            "errors": "replace" if text else None,
            "check": False,
        }
        if hasattr(subprocess, "CREATE_NO_WINDOW") and hasattr(subprocess, "STARTUPINFO"):
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            kwargs["startupinfo"] = startupinfo
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        return kwargs

    def _clear_stale_index_lock(self) -> bool:
        """Remove only an old, empty repository index lock left after a crash."""
        lock_path = self.repo / ".git" / "index.lock"
        try:
            stat = lock_path.stat()
        except OSError:
            return False
        if stat.st_size != 0 or time.time() - stat.st_mtime < self.STALE_INDEX_LOCK_SECONDS:
            return False
        try:
            lock_path.unlink()
        except OSError:
            return False
        return True

    def _language_pathspec(self) -> str:
        return self.language.rstrip("/") + "/"

    def _invalidate_history_cache(self) -> None:
        with self._cache_lock:
            self._commit_list_cache = None
            self._combined_cache.clear()


def combine_entries(entry_groups: Iterable[Iterable[TranslationLogEntry]]) -> list[TranslationLogEntry]:
    """Merge commit entry lists while keeping one final result per translation field."""
    combined: dict[tuple[str, str, str, str], TranslationLogEntry] = {}
    for entries in entry_groups:
        for entry in entries:
            current = combined.get(entry.change_key)
            combined[entry.change_key] = entry if current is None else current.merged_with(entry)
    return [entry for entry in combined.values() if entry.translated_text != entry.before_text]


def _display_subject(subject: str) -> str:
    if subject == "translation: commit pending language changes":
        return translate("history.subject.pending")
    if subject == "chore: import language baseline":
        return translate("history.subject.baseline")
    match = re.match(r"^translation:\s*(.+?)\s*\((.+)\)$", subject)
    if not match:
        return subject
    counts_raw, files = match.groups()
    parts: list[str] = []
    for chunk in [item.strip() for item in counts_raw.split(",") if item.strip()]:
        count_match = re.match(r"^(add|update|delete)\s+(\d+)$", chunk)
        if not count_match:
            parts.append(chunk)
            continue
        kind, count = count_match.groups()
        if kind == "add":
            parts.append(translate("history.change.add", count=count))
        elif kind == "update":
            parts.append(translate("history.change.update", count=count))
        else:
            parts.append(translate("history.change.delete", count=count))
    summary = " · ".join(parts) if parts else translate("history.subject.summary_default")
    return f"{summary} · {files}"


def format_entries(entries: Iterable[TranslationLogEntry]) -> str:
    grouped: dict[str, list[TranslationLogEntry]] = {}
    for entry in entries:
        grouped.setdefault(entry.file_rel, []).append(entry)

    parts: list[str] = []
    for file_rel, file_entries in grouped.items():
        lines = [file_rel]
        for entry in file_entries:
            source = entry.source_text.replace("\r", "").replace("\n", " ↵ ")
            before = entry.display_before_text.replace("\r", "").replace("\n", " ↵ ")
            translated = (
                translate("history.formatted_entry.deleted")
                if entry.kind == "删除"
                else entry.translated_text.replace("\r", "").replace("\n", " ↵ ")
            )
            lines.extend((f"  {entry.heading}", f"  {before} → {translated}"))
            if entry.kind == "更新" and entry.before_text != entry.source_text:
                lines.append(translate("history.formatted_entry.source", source=source))
        parts.append("\n".join(lines))
    return "\n\n".join(parts) or translate("history.formatted_entry.no_changes")


def main() -> int:
    parser = argparse.ArgumentParser(description="Show clean original-to-translation Git history.")
    parser.add_argument("--commit", default="HEAD", help="Git commit, default: HEAD")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    print(format_entries(LanguageGit(root).entries_for_commit(args.commit)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
