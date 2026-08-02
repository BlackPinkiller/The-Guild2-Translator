from __future__ import annotations

from dataclasses import dataclass
import filecmp
import hashlib
import re
from pathlib import Path

from .cache import cache_path, set_source_review_many
from .file_utils import atomic_write_many
from .format_io import dbt_row_values, load_dbt, load_plain_text, matching_source_field, translatable_fields


DEFAULT_TRANSLATION_LANGUAGE = "#chinese"
VANILLA_PROJECT_NAME = "Vanilla"
TRANSLATION_TYPE_RE = re.compile(r"^\s*type\s*=\s*translation\s*$", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class SourceProjectSpec:
    name: str
    kind: str
    source_root: Path
    project_root: Path
    added: bool
    mod_root: Path | None = None


@dataclass(frozen=True)
class SourceSyncResult:
    project_root: Path
    synced_source_files: tuple[str, ...]
    added_source_files: tuple[str, ...]
    modified_source_files: tuple[str, ...]
    removed_source_files: tuple[str, ...]
    added_entries: int
    modified_entries: int
    removed_entries: int
    invalidated_units: int


@dataclass(frozen=True)
class SourceSyncFileChange:
    rel_path: str
    kind: str
    added_entries: int = 0
    modified_entries: int = 0
    removed_entries: int = 0


@dataclass(frozen=True)
class SourceReviewBatch:
    language: str
    uids: tuple[str, ...]


@dataclass(frozen=True)
class SourceSyncPlan:
    source_root: Path
    project_root: Path
    changes: tuple[SourceSyncFileChange, ...]
    review_batches: tuple[SourceReviewBatch, ...]
    basis_hash: str

    @property
    def added_files(self) -> tuple[str, ...]:
        return tuple(change.rel_path for change in self.changes if change.kind == "added")

    @property
    def modified_files(self) -> tuple[str, ...]:
        return tuple(change.rel_path for change in self.changes if change.kind == "modified")

    @property
    def removed_files(self) -> tuple[str, ...]:
        return tuple(change.rel_path for change in self.changes if change.kind == "removed")

    @property
    def added_entries(self) -> int:
        return sum(change.added_entries for change in self.changes)

    @property
    def modified_entries(self) -> int:
        return sum(change.modified_entries for change in self.changes)

    @property
    def removed_entries(self) -> int:
        return sum(change.removed_entries for change in self.changes)

    @property
    def invalidated_units(self) -> int:
        return sum(len(batch.uids) for batch in self.review_batches)

    @property
    def has_changes(self) -> bool:
        return bool(self.changes)


class SourceSyncPlanStaleError(RuntimeError):
    pass


def managed_project_root(app_root: Path, name: str) -> Path:
    return app_root / "sources" / name


def managed_vanilla_project_root(app_root: Path) -> Path:
    return managed_project_root(app_root, VANILLA_PROJECT_NAME)


def local_project_roots(app_root: Path) -> list[Path]:
    sources_root = app_root / "sources"
    if not sources_root.is_dir():
        return []
    roots: list[Path] = []
    for candidate in sorted(sources_root.iterdir(), key=lambda path: path.name.casefold()):
        if not candidate.is_dir():
            continue
        languages_root = candidate / "languages"
        if languages_root.is_dir() and has_vanilla_source_entries(languages_root):
            roots.append(candidate)
    return roots


def discover_game_source_projects(game_root: Path, app_root: Path) -> list[SourceProjectSpec]:
    game_root = game_root.expanduser().resolve()
    projects: list[SourceProjectSpec] = []
    vanilla_root = game_languages_root(game_root)
    if vanilla_root.is_dir() and has_vanilla_source_entries(vanilla_root):
        project_root = managed_vanilla_project_root(app_root)
        projects.append(
            SourceProjectSpec(
                name=VANILLA_PROJECT_NAME,
                kind="vanilla",
                source_root=vanilla_root,
                project_root=project_root,
                added=_project_has_sources(project_root),
            )
        )

    mods_root = game_root / "mods"
    if mods_root.is_dir():
        for mod_root in sorted((path for path in mods_root.iterdir() if path.is_dir()), key=lambda path: path.name.casefold()):
            source_root = mod_root / "DB" / "Languages"
            if not source_root.is_dir() or not has_vanilla_source_entries(source_root):
                continue
            if _modinfo_declares_translation(mod_root / "modinfo.txt"):
                continue
            project_root = managed_project_root(app_root, mod_root.name)
            projects.append(
                SourceProjectSpec(
                    name=mod_root.name,
                    kind="mod",
                    source_root=source_root,
                    project_root=project_root,
                    added=_project_has_sources(project_root),
                    mod_root=mod_root,
                )
            )
    return projects


def game_languages_root(game_root: Path) -> Path:
    return game_root / "DB" / "Languages"


def has_vanilla_source_entries(languages_root: Path) -> bool:
    try:
        return any(not item.name.startswith("#") for item in languages_root.iterdir())
    except OSError:
        return False


def ensure_translation_dir(project_root: Path, language: str) -> Path:
    path = project_root / "languages" / language
    path.mkdir(parents=True, exist_ok=True)
    return path


def sync_source_project(source_root: Path, project_root: Path) -> SourceSyncResult:
    return apply_source_sync_plan(plan_source_project_sync(source_root, project_root))


def plan_source_project_sync(source_root: Path, project_root: Path) -> SourceSyncPlan:
    source_root = source_root.expanduser().resolve()
    project_root = project_root.expanduser().resolve()
    _validate_source_root(source_root)

    target_languages_root = project_root / "languages"
    source_files = _collect_source_files(source_root)
    existing_files = _collect_source_files(target_languages_root)
    changes: list[SourceSyncFileChange] = []
    review_uids: dict[str, set[str]] = {}

    for rel_path, source_file in source_files.items():
        previous_file = existing_files.get(rel_path)
        if previous_file is not None and filecmp.cmp(source_file, previous_file, shallow=False):
            continue
        kind = "added" if previous_file is None else "modified"
        added, modified, removed = _entry_change_counts(rel_path, previous_file, source_file)
        changes.append(SourceSyncFileChange(rel_path.as_posix(), kind, added, modified, removed))
        if previous_file is not None:
            _collect_translations_for_source_change(
                project_root,
                rel_path,
                previous_file,
                source_file,
                review_uids,
            )

    for rel_path, previous_file in existing_files.items():
        if rel_path in source_files:
            continue
        added, modified, removed = _entry_change_counts(rel_path, previous_file, None)
        changes.append(SourceSyncFileChange(rel_path.as_posix(), "removed", added, modified, removed))

    changes.sort(key=lambda change: (change.rel_path.casefold(), change.kind))
    batches = tuple(
        SourceReviewBatch(language, tuple(sorted(uids)))
        for language, uids in sorted(review_uids.items(), key=lambda item: item[0].casefold())
        if uids
    )
    return SourceSyncPlan(
        source_root=source_root,
        project_root=project_root,
        changes=tuple(changes),
        review_batches=batches,
        basis_hash=_sync_basis_hash(source_files, existing_files),
    )


def apply_source_sync_plan(plan: SourceSyncPlan) -> SourceSyncResult:
    current_plan = plan_source_project_sync(plan.source_root, plan.project_root)
    if current_plan.basis_hash != plan.basis_hash or current_plan.changes != plan.changes:
        raise SourceSyncPlanStaleError

    target_languages_root = plan.project_root / "languages"
    target_languages_root.mkdir(parents=True, exist_ok=True)
    source_files = _collect_source_files(plan.source_root)
    existing_files = _collect_source_files(target_languages_root)
    writes: dict[Path, bytes] = {}
    deletions: set[Path] = set()
    workflow_cache_path = cache_path(plan.project_root)
    workflow_cache_before = workflow_cache_path.read_bytes() if workflow_cache_path.exists() else None

    try:
        for batch in plan.review_batches:
            set_source_review_many(plan.project_root, batch.language, batch.uids, True)
        for change in plan.changes:
            rel_path = Path(change.rel_path)
            if change.kind == "removed":
                previous_file = existing_files.get(rel_path)
                if previous_file is not None:
                    deletions.add(previous_file)
                continue
            writes[target_languages_root / rel_path] = source_files[rel_path].read_bytes()

        atomic_write_many(writes, deletions)
    except Exception:
        if workflow_cache_before is None:
            atomic_write_many({}, (workflow_cache_path,))
        else:
            atomic_write_many({workflow_cache_path: workflow_cache_before})
        raise
    for previous_file in deletions:
        _prune_empty_directories(previous_file.parent, target_languages_root)

    added_files = plan.added_files
    modified_files = plan.modified_files
    return SourceSyncResult(
        project_root=plan.project_root,
        synced_source_files=added_files + modified_files,
        added_source_files=added_files,
        modified_source_files=modified_files,
        removed_source_files=plan.removed_files,
        added_entries=plan.added_entries,
        modified_entries=plan.modified_entries,
        removed_entries=plan.removed_entries,
        invalidated_units=plan.invalidated_units,
    )


def sync_vanilla_sources(game_root: Path, project_root: Path) -> Path:
    source_root = game_languages_root(game_root.expanduser().resolve())
    project_root = project_root.expanduser().resolve()
    sync_source_project(source_root, project_root)
    return project_root


def _validate_source_root(source_root: Path) -> None:
    if not source_root.is_dir():
        raise FileNotFoundError(f"languages directory not found: {source_root}")
    if not has_vanilla_source_entries(source_root):
        raise ValueError(f"no source entries found under: {source_root}")


def _project_has_sources(project_root: Path) -> bool:
    languages_root = project_root / "languages"
    return languages_root.is_dir() and has_vanilla_source_entries(languages_root)


def _modinfo_declares_translation(modinfo_path: Path) -> bool:
    try:
        content = modinfo_path.read_bytes().decode("utf-8", errors="ignore")
    except OSError:
        return False
    return bool(TRANSLATION_TYPE_RE.search(content))


def _collect_source_files(root: Path) -> dict[Path, Path]:
    files: dict[Path, Path] = {}
    if not root.is_dir():
        return files
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if not path.is_file():
            continue
        rel_path = path.relative_to(root)
        if _should_skip_managed_path(rel_path):
            continue
        files[rel_path] = path
    return files


def _should_skip_managed_path(rel_path: Path) -> bool:
    parts = rel_path.parts
    if not parts:
        return True
    if parts[0].startswith("#"):
        return True
    return any(part.startswith(".") for part in parts)


def _translation_roots(project_root: Path) -> list[Path]:
    languages_root = project_root / "languages"
    if not languages_root.is_dir():
        return []
    return [
        path
        for path in sorted(languages_root.iterdir(), key=lambda item: item.name.casefold())
        if path.is_dir() and path.name.startswith("#")
    ]


def _collect_translations_for_source_change(
    project_root: Path,
    rel_path: Path,
    previous_source: Path,
    next_source: Path | None,
    review_uids: dict[str, set[str]],
) -> None:
    suffix = rel_path.suffix.lower()
    if suffix == ".dbt":
        _collect_dbt_translations_for_review(
            project_root, rel_path, previous_source, next_source, review_uids
        )
    elif suffix == ".txt":
        _collect_plain_text_translations_for_review(
            project_root, rel_path, previous_source, next_source, review_uids
        )


def _collect_plain_text_translations_for_review(
    project_root: Path,
    rel_path: Path,
    previous_source: Path,
    next_source: Path | None,
    review_uids: dict[str, set[str]],
) -> None:
    if next_source is None:
        return
    previous_text = load_plain_text(previous_source).text
    next_text = load_plain_text(next_source).text
    if previous_text == next_text:
        return
    for language_root in _translation_roots(project_root):
        target_path = language_root / rel_path
        if not target_path.is_file():
            continue
        if not load_plain_text(target_path).text:
            continue
        review_uids.setdefault(language_root.name, set()).add(f"text:{rel_path.as_posix()}")


def _collect_dbt_translations_for_review(
    project_root: Path,
    rel_path: Path,
    previous_source: Path,
    next_source: Path | None,
    review_uids_by_language: dict[str, set[str]],
) -> None:
    if next_source is None:
        return
    previous_doc = load_dbt(previous_source)
    next_doc = load_dbt(next_source)
    previous_index = previous_doc.row_index
    next_index = next_doc.row_index

    for language_root in _translation_roots(project_root):
        target_path = language_root / rel_path
        if not target_path.is_file():
            continue
        target_doc = load_dbt(target_path)
        target_fields = translatable_fields(rel_path.name, target_doc.string_columns)
        planned_uids: list[str] = []
        for key, target_row in target_doc.row_index.items():
            previous_row = previous_index.get(key)
            next_row = next_index.get(key)
            if previous_row is None:
                continue
            if next_row is None:
                continue
            for target_field in target_fields:
                previous_field = matching_source_field(target_field, previous_doc.string_columns)
                next_field = matching_source_field(target_field, next_doc.string_columns)
                if previous_row.get(previous_field) == next_row.get(next_field):
                    continue
                if target_row.get(target_field):
                    planned_uids.append(f"dbt:{rel_path.name}:{key[0]}:{key[1]}:{target_field}")
        if planned_uids:
            review_uids_by_language.setdefault(language_root.name, set()).update(planned_uids)


def _entry_change_counts(
    rel_path: Path,
    previous_source: Path | None,
    next_source: Path | None,
) -> tuple[int, int, int]:
    suffix = rel_path.suffix.lower()
    if suffix == ".dbt":
        previous_index = load_dbt(previous_source).row_index if previous_source is not None else {}
        next_index = load_dbt(next_source).row_index if next_source is not None else {}
        previous_keys = set(previous_index)
        next_keys = set(next_index)
        modified = sum(
            dbt_row_values(previous_index[key]) != dbt_row_values(next_index[key])
            for key in previous_keys & next_keys
        )
        return len(next_keys - previous_keys), modified, len(previous_keys - next_keys)
    if suffix == ".txt":
        if previous_source is None:
            return (1, 0, 0)
        if next_source is None:
            return (0, 0, 1)
        previous_text = load_plain_text(previous_source).text
        next_text = load_plain_text(next_source).text
        return (0, int(previous_text != next_text), 0)
    return (0, 0, 0)


def _sync_basis_hash(source_files: dict[Path, Path], existing_files: dict[Path, Path]) -> str:
    digest = hashlib.sha256()
    for scope, files in ((b"source", source_files), (b"managed", existing_files)):
        for rel_path, path in sorted(files.items(), key=lambda item: item[0].as_posix().casefold()):
            digest.update(scope)
            digest.update(b"\0")
            digest.update(rel_path.as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def _prune_empty_directories(path: Path, stop_at: Path) -> None:
    current = path
    while current != stop_at and current.exists():
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent
