from __future__ import annotations

from dataclasses import dataclass
import filecmp
import re
import shutil
from pathlib import Path

from .cache import set_source_review_many
from .format_io import load_dbt, load_plain_text, matching_source_field, translatable_fields


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
    removed_source_files: tuple[str, ...]
    invalidated_units: int


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
    source_root = source_root.expanduser().resolve()
    project_root = project_root.expanduser().resolve()
    _validate_source_root(source_root)

    target_languages_root = project_root / "languages"
    target_languages_root.mkdir(parents=True, exist_ok=True)

    source_files = _collect_source_files(source_root)
    existing_files = _collect_source_files(target_languages_root)
    synced_source_files: list[str] = []
    removed_source_files: list[str] = []
    invalidated_units = 0

    for rel_path, source_file in source_files.items():
        target_file = target_languages_root / rel_path
        previous_file = existing_files.get(rel_path)
        if previous_file is not None and filecmp.cmp(source_file, previous_file, shallow=False):
            continue
        if previous_file is not None:
            invalidated_units += _mark_translations_for_source_change(project_root, rel_path, previous_file, source_file)
        _copy_file(source_file, target_file)
        synced_source_files.append(rel_path.as_posix())

    for rel_path, previous_file in existing_files.items():
        if rel_path in source_files:
            continue
        previous_file.unlink()
        _prune_empty_directories(previous_file.parent, target_languages_root)
        removed_source_files.append(rel_path.as_posix())

    return SourceSyncResult(
        project_root=project_root,
        synced_source_files=tuple(synced_source_files),
        removed_source_files=tuple(removed_source_files),
        invalidated_units=invalidated_units,
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


def _copy_file(source_file: Path, target_file: Path) -> None:
    target_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_file, target_file)


def _translation_roots(project_root: Path) -> list[Path]:
    languages_root = project_root / "languages"
    if not languages_root.is_dir():
        return []
    return [
        path
        for path in sorted(languages_root.iterdir(), key=lambda item: item.name.casefold())
        if path.is_dir() and path.name.startswith("#")
    ]


def _mark_translations_for_source_change(
    project_root: Path,
    rel_path: Path,
    previous_source: Path,
    next_source: Path | None,
) -> int:
    suffix = rel_path.suffix.lower()
    if suffix == ".dbt":
        return _mark_dbt_translations_for_review(project_root, rel_path, previous_source, next_source)
    if suffix == ".txt":
        return _mark_plain_text_translations_for_review(project_root, rel_path, previous_source, next_source)
    return 0


def _mark_plain_text_translations_for_review(
    project_root: Path,
    rel_path: Path,
    previous_source: Path,
    next_source: Path | None,
) -> int:
    if next_source is None:
        return 0
    previous_text = load_plain_text(previous_source).text
    next_text = load_plain_text(next_source).text
    if previous_text == next_text:
        return 0
    flagged = 0
    for language_root in _translation_roots(project_root):
        target_path = language_root / rel_path
        if not target_path.is_file():
            continue
        if not load_plain_text(target_path).text:
            continue
        set_source_review_many(project_root, language_root.name, (f"text:{rel_path.as_posix()}",), True)
        flagged += 1
    return flagged


def _mark_dbt_translations_for_review(
    project_root: Path,
    rel_path: Path,
    previous_source: Path,
    next_source: Path | None,
) -> int:
    if next_source is None:
        return 0
    previous_doc = load_dbt(previous_source)
    next_doc = load_dbt(next_source)
    previous_index = previous_doc.row_index
    next_index = next_doc.row_index
    flagged = 0

    for language_root in _translation_roots(project_root):
        target_path = language_root / rel_path
        if not target_path.is_file():
            continue
        target_doc = load_dbt(target_path)
        target_fields = translatable_fields(rel_path.name, target_doc.string_columns)
        review_uids: list[str] = []
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
                    review_uids.append(f"dbt:{rel_path.name}:{key[0]}:{key[1]}:{target_field}")
        if review_uids:
            set_source_review_many(project_root, language_root.name, tuple(review_uids), True)
            flagged += len(review_uids)
    return flagged


def _prune_empty_directories(path: Path, stop_at: Path) -> None:
    current = path
    while current != stop_at and current.exists():
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent
