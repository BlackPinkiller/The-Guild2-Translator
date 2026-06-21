from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import argparse
import subprocess
from typing import Iterable

from .codec_adapter import CodecError, Guild2Codec, default_codec_path
from .format_io import (
    DbtDocument,
    load_dbt_bytes,
    load_plain_text_bytes,
    matching_source_field,
    row_key,
    translatable_fields,
)
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
        return f"{self.short_hash} · {self.timestamp:%Y-%m-%d %H:%M} · {self.subject}"


@dataclass(frozen=True)
class TranslationLogEntry:
    kind: str
    file_rel: str
    record_id: str
    label: str
    field_name: str
    source_text: str
    translated_text: str

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


class LanguageGit:
    """A narrow Git facade: only the language repository is ever auto-committed."""

    def __init__(self, project_root: Path, language: str = "#chinese") -> None:
        self.project_root = project_root.resolve()
        self.repo = self.project_root / "languages"
        self.language = language
        self.codec = Guild2Codec.load(default_codec_path(self.project_root))

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
        return True

    def commit_saved(self, changed_files: Iterable[Path], saved_units: Iterable[TranslationUnit]) -> GitCommit | None:
        relative_paths = [str(path.resolve().relative_to(self.repo)).replace("\\", "/") for path in changed_files]
        if not relative_paths:
            return None
        self._run("add", "--", *relative_paths)
        if not self._has_staged_changes(relative_paths):
            return None
        units = tuple(saved_units)
        added = sum(1 for unit in units if unit.status in MISSING_WORK_STATUSES)
        updated = len(units) - added
        files = ", ".join(sorted({Path(path).name for path in relative_paths}))
        portions: list[str] = []
        if added:
            portions.append(f"add {added}")
        if updated:
            portions.append(f"update {updated}")
        subject = f"translation: {', '.join(portions)} ({files})"
        self._run("commit", "--only", "-m", subject, "--", *relative_paths)
        return self.list_commits(1)[0]

    def has_pending_changes(self) -> bool:
        return bool(self._run("status", "--porcelain").stdout.strip())

    def commit_pending(self) -> GitCommit | None:
        paths = self._pending_target_paths()
        if not paths:
            return None
        self._run("add", "--", *paths)
        if not self._has_staged_changes(paths):
            return None
        self._run("commit", "--only", "-m", "translation: commit pending language changes", "--", *paths)
        return self.list_commits(1)[0]

    def list_commits(self, limit: int = 100) -> list[GitCommit]:
        result = self._run("log", f"-n{limit}", "--format=%H%x1f%h%x1f%ct%x1f%s")
        commits: list[GitCommit] = []
        for line in result.stdout.splitlines():
            parts = line.split("\x1f", 3)
            if len(parts) != 4:
                continue
            commits.append(GitCommit(parts[0], parts[1], datetime.fromtimestamp(int(parts[2])), parts[3]))
        return commits

    def entries_for_commit(self, commit: str) -> list[TranslationLogEntry]:
        parent = self._parent_of(commit)
        if parent is None:
            return []
        changed = self._run("diff-tree", "--no-commit-id", "--name-only", "-r", commit).stdout.splitlines()
        prefix = self.language.rstrip("/") + "/"
        entries: list[TranslationLogEntry] = []
        for target_rel in changed:
            if not target_rel.startswith(prefix):
                continue
            file_rel = target_rel[len(prefix) :]
            after = self._show_bytes(commit, target_rel)
            before = self._show_bytes(parent, target_rel)
            source = self._show_bytes(commit, file_rel)
            if after is None or source is None:
                continue
            if target_rel.lower().endswith(".dbt"):
                entries.extend(self._dbt_entries(file_rel, source, before, after))
            elif target_rel.lower().endswith(".txt"):
                entries.extend(self._text_entries(file_rel, source, before, after))
        return entries

    def entries_for_commits(self, commits_oldest_first: Iterable[str]) -> list[TranslationLogEntry]:
        """Return the net translation changes across several commits.

        The caller supplies commits from oldest to newest.  If one entry was
        edited more than once in the selected range, its last translation is
        retained so the log describes the combined result rather than showing
        several noisy intermediate revisions.
        """
        return combine_entries(self.entries_for_commit(commit) for commit in commits_oldest_first)

    def _dbt_entries(
        self, file_rel: str, source_raw: bytes, before_raw: bytes | None, after_raw: bytes
    ) -> list[TranslationLogEntry]:
        file_name = Path(file_rel).name
        source_doc = load_dbt_bytes(Path(file_name), source_raw)
        after_doc = load_dbt_bytes(Path(file_name), after_raw)
        before_doc = load_dbt_bytes(Path(file_name), before_raw) if before_raw is not None else None
        source_rows = source_doc.row_index
        before_rows = before_doc.row_index if before_doc is not None else {}
        fields = translatable_fields(file_name, after_doc.string_columns)
        entries: list[TranslationLogEntry] = []
        for row in after_doc.rows:
            key = row_key(file_name, row)
            source_row = source_rows.get(key)
            if source_row is None:
                continue
            before_row = before_rows.get(key)
            for field_name in fields:
                after_value = row.get(field_name)
                before_value = before_row.get(field_name) if before_row is not None else None
                if before_value == after_value:
                    continue
                source_field = matching_source_field(field_name, source_doc.string_columns)
                source_text = source_row.get(source_field)
                kind = "新增" if before_value is None or before_value in {"", source_text} else "更新"
                entries.append(
                    TranslationLogEntry(
                        kind,
                        file_rel,
                        str(key[0]),
                        key[1],
                        field_name,
                        source_text,
                        self._decode(after_value),
                    )
                )
        return entries

    def _text_entries(
        self, file_rel: str, source_raw: bytes, before_raw: bytes | None, after_raw: bytes
    ) -> list[TranslationLogEntry]:
        source = load_plain_text_bytes(Path(file_rel), source_raw).text
        after = load_plain_text_bytes(Path(file_rel), after_raw).text
        before = load_plain_text_bytes(Path(file_rel), before_raw).text if before_raw is not None else None
        if before == after:
            return []
        kind = "新增" if before is None or before in {"", source} else "更新"
        return [TranslationLogEntry(kind, file_rel, "", file_rel, "body", source, self._decode(after))]

    def _decode(self, value: str) -> str:
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
        prefix = self.language.rstrip("/") + "/"
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
                capture_output=True,
                text=text,
                encoding="utf-8" if text else None,
                errors="replace" if text else None,
                check=False,
            )
        except FileNotFoundError as exc:
            raise GitError("找不到 Git；请安装 Git 并重新打开翻译器。") from exc
        if check and result.returncode != 0:
            stderr = result.stderr.decode("utf-8", "replace") if isinstance(result.stderr, bytes) else result.stderr
            raise GitError(stderr.strip() or "Git 命令执行失败。")
        return result


def combine_entries(entry_groups: Iterable[Iterable[TranslationLogEntry]]) -> list[TranslationLogEntry]:
    """Merge commit entry lists while keeping one final result per translation field."""
    combined: dict[tuple[str, str, str, str], TranslationLogEntry] = {}
    for entries in entry_groups:
        for entry in entries:
            key = (entry.file_rel, entry.record_id, entry.label, entry.field_name)
            combined[key] = entry
    return list(combined.values())


def format_entries(entries: Iterable[TranslationLogEntry]) -> str:
    grouped: dict[str, list[TranslationLogEntry]] = {}
    for entry in entries:
        grouped.setdefault(entry.file_rel, []).append(entry)

    parts: list[str] = []
    for file_rel, file_entries in grouped.items():
        lines = [file_rel]
        for entry in file_entries:
            source = entry.source_text.replace("\r", "").replace("\n", " ↵ ")
            translated = entry.translated_text.replace("\r", "").replace("\n", " ↵ ")
            lines.extend((f"  {entry.heading}", f"  {source} → {translated}"))
        parts.append("\n".join(lines))
    return "\n\n".join(parts) or "此提交没有译文条目变化。"


def main() -> int:
    parser = argparse.ArgumentParser(description="Show clean original-to-translation Git history.")
    parser.add_argument("--commit", default="HEAD", help="Git commit, default: HEAD")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    print(format_entries(LanguageGit(root).entries_for_commit(args.commit)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
