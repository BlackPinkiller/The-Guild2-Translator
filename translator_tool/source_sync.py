from __future__ import annotations

import filecmp
import shutil
from pathlib import Path


DEFAULT_TRANSLATION_LANGUAGE = "#chinese"
VANILLA_PROJECT_NAME = "Vanilla"


def managed_vanilla_project_root(app_root: Path) -> Path:
    return app_root / "sources" / VANILLA_PROJECT_NAME


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

def copy_missing_or_changed(item: Path, destination: Path) -> None:
    if item.is_dir():
        if destination.exists() and not destination.is_dir():
            destination.unlink()

        destination.mkdir(parents=True, exist_ok=True)

        for item in sorted(item.iterdir(), key=lambda path: path.name.casefold()):
            if item.name.startswith("#"):
                continue

            copy_missing_or_changed(item, destination / item.name)

    else:
        if not destination.exists():
            shutil.copy2(item, destination)
        elif not destination.is_file():
            shutil.rmtree(destination)
            shutil.copy2(item, destination)
        elif not filecmp.cmp(item, destination, shallow=False):
            shutil.copy2(item, destination)


def sync_vanilla_sources(game_root: Path, project_root: Path) -> Path:
    source_root = game_languages_root(game_root)
    if not source_root.is_dir():
        raise FileNotFoundError(f"languages directory not found: {source_root}")
    if not has_vanilla_source_entries(source_root):
        raise ValueError(f"no vanilla source entries found under: {source_root}")

    target_languages_root = project_root / "languages"
    target_languages_root.mkdir(parents=True, exist_ok=True)

    # Switching the game root should rebuild the managed vanilla workspace from
    # source files only. Keep dotfile metadata, and should NOT drop previous translation
    for item in sorted(source_root.iterdir(), key=lambda path: path.name.casefold()):
        if item.name.startswith("#"):
            continue
        destination = target_languages_root / item.name
        copy_missing_or_changed(item, destination)

    return project_root
