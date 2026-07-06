from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from types import SimpleNamespace
import uuid

from . import project as project_module
from . import settings as settings_module
from .ai import (
    GoogleTranslateProvider,
    LlmNeighborContext,
    LlmSuggestionContext,
    OpenAICompatibleProvider,
    TranslationProviderError,
)
from .cache import source_review_uids
from .codec_adapter import Guild2Codec, load_codec_for_language
from .git_history import GitCommit, LanguageGit, TranslationLogEntry, combine_entries, format_entries
from .history import OperationHistory, TranslationOperation, UnitChange
from .i18n import set_language, status_text, translate
from .format_io import load_dbt, load_plain_text, matching_source_field, row_key
from .project import (
    MISSING_WORK_STATUSES,
    Project,
    STATUS_EXTRA,
    STATUS_IGNORED,
    STATUS_PENDING_DELETE,
    STATUS_TODO,
    STATUS_TRANSLATED,
    TODO_REASON_EMPTY,
    TODO_REASON_IMPORT_REVIEW,
    TODO_REASON_MISSING_ROW,
    TODO_REASON_SOURCE_CHANGED,
)
from .settings import AppSettings, load_settings, save_settings
from .source_sync import (
    discover_game_source_projects,
    local_project_roots,
    managed_vanilla_project_root,
    sync_source_project,
    sync_vanilla_sources,
)
from .validation import format_tokens, normalize_color_token_spacing, validate_translation


def tool_root() -> Path:
    return Path(__file__).resolve().parents[1]


def project_root() -> Path:
    root = tool_root()
    if (root / "languages").is_dir():
        return root
    sources = root / "sources"
    if sources.is_dir():
        for candidate in sorted(sources.iterdir()):
            if candidate.is_dir() and (candidate / "languages").is_dir():
                return candidate
    return root


def assert_round_trip(root: Path) -> None:
    for path in sorted((root / "languages").rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".dbt", ".txt"}:
            continue
        doc = load_dbt(path) if path.suffix.lower() == ".dbt" else load_plain_text(path)
        rendered = doc.render_bytes()
        if rendered != path.read_bytes():
            raise AssertionError(f"round-trip changed bytes: {path}")


def assert_statuses(root: Path) -> None:
    project = Project.load(root, "#chinese", codec_root=tool_root())
    if not any(unit.file_rel == "Text.dbt" and unit.filter_status() == STATUS_TRANSLATED for unit in project.units):
        raise AssertionError("Text.dbt translated rows were not loaded")
    invalid_statuses = {
        unit.filter_status()
        for unit in project.units
        if unit.filter_status() not in {STATUS_TODO, STATUS_TRANSLATED, STATUS_EXTRA, STATUS_IGNORED}
    }
    if invalid_statuses:
        raise AssertionError(f"unexpected simplified statuses were exposed: {sorted(invalid_statuses)!r}")
    if (root / "languages" / "Guides").exists() and not any(
        unit.file_rel.startswith("Guides/") and unit.source_text for unit in project.units
    ):
        raise AssertionError("Guides source files were not matched to translated Guides")
    if any(unit.file_rel == "Tables.dbt" for unit in project.units):
        raise AssertionError("Tables.dbt must not be exposed as a translation unit")


def assert_loaded_order_matches_file_lines(root: Path) -> None:
    project = Project.load(root, "#chinese", codec_root=tool_root())
    last_position: dict[str, tuple[int, int]] = {}
    for unit in project.units:
        if unit.ref.kind != "dbt":
            continue
        position = (unit.ref.display_order, unit.ref.field_order)
        previous = last_position.get(unit.file_rel)
        if previous is not None and position < previous:
            raise AssertionError(f"table order diverged from {unit.file_rel} line order")
        last_position[unit.file_rel] = position


def copy_project_subset(src_root: Path, dst_root: Path) -> None:
    (dst_root / "encoder" / "data").mkdir(parents=True)
    shutil.copy2(tool_root() / "encoder" / "guild2_codec.py", dst_root / "encoder")
    shutil.copy2(tool_root() / "encoder" / "data" / "guild2_write_codec.json", dst_root / "encoder" / "data")
    shutil.copy2(tool_root() / "encoder" / "data" / "guild2_read_codec.json", dst_root / "encoder" / "data")
    (dst_root / "languages" / "#chinese").mkdir(parents=True)
    for name in ["Text.dbt", "Tooltips.dbt"]:
        shutil.copy2(src_root / "languages" / name, dst_root / "languages" / name)
        shutil.copy2(src_root / "languages" / "#chinese" / name, dst_root / "languages" / "#chinese" / name)


def make_temp_project(root: Path, prefix: str) -> Path:
    temp = Path(tempfile.gettempdir()) / f"{prefix}{uuid.uuid4().hex[:8]}"
    temp.mkdir(parents=True, exist_ok=False)
    copy_project_subset(root, temp)
    return temp


def safe_rmtree(path: Path) -> None:
    try:
        shutil.rmtree(path)
    except OSError:
        pass


def assert_sync_vanilla_sources_only_imports_originals() -> None:
    temp = Path(tempfile.gettempdir()) / f"translator_tool_smoke_vanilla_sync_{uuid.uuid4().hex[:8]}"
    try:
        game_root = temp / "game"
        source_languages = game_root / "DB" / "Languages"
        (source_languages / "Guides").mkdir(parents=True, exist_ok=True)
        (source_languages / "#german").mkdir(parents=True, exist_ok=True)
        (source_languages / "Text.dbt").write_text("source-text", encoding="utf-8")
        (source_languages / "Guides" / "Intro.txt").write_text("guide-source", encoding="utf-8")
        (source_languages / "#german" / "Text.dbt").write_text("translated-text", encoding="utf-8")

        app_root = temp / "app"
        project_root = managed_vanilla_project_root(app_root)
        languages_root = project_root / "languages"
        (languages_root / "#manual").mkdir(parents=True, exist_ok=True)
        (languages_root / "#manual" / "keep.dbt").write_text("keep", encoding="utf-8")
        (languages_root / ".git").mkdir(parents=True, exist_ok=True)
        (languages_root / "Old.dbt").write_text("stale", encoding="utf-8")
        (languages_root / ".gitignore").write_text("# keep\n", encoding="utf-8")

        synced = sync_vanilla_sources(game_root, project_root)
        if synced != project_root:
            raise AssertionError("sync_vanilla_sources did not return the managed project root")
        if (languages_root / "Old.dbt").exists():
            raise AssertionError("stale vanilla source files were not replaced during sync")
        if (languages_root / "#german").exists():
            raise AssertionError("translation folders from the game install should not be imported")
        if (languages_root / "Text.dbt").read_text(encoding="utf-8") != "source-text":
            raise AssertionError("vanilla DBT source was not copied into the managed project")
        if (languages_root / "Guides" / "Intro.txt").read_text(encoding="utf-8") != "guide-source":
            raise AssertionError("vanilla guide source was not copied into the managed project")
        if not (languages_root / "#manual").exists():
            raise AssertionError("sync should preserve existing translation folders")
        if (languages_root / "#chinese").exists():
            raise AssertionError("sync should not auto-create a default translation folder")
        if not (languages_root / ".git").exists():
            raise AssertionError("sync should preserve the managed language git repository")
        if not (languages_root / ".gitignore").exists():
            raise AssertionError("app-side metadata files should be preserved during vanilla sync")
    finally:
        safe_rmtree(temp)


def assert_local_project_roots_detect_sources_projects() -> None:
    temp = Path(tempfile.gettempdir()) / f"translator_tool_smoke_local_sources_{uuid.uuid4().hex[:8]}"
    try:
        (temp / "sources" / "Reforged" / "languages").mkdir(parents=True, exist_ok=True)
        (temp / "sources" / "Reforged" / "languages" / "Text.dbt").write_text("source", encoding="utf-8")
        (temp / "sources" / "Vanilla" / "languages").mkdir(parents=True, exist_ok=True)
        (temp / "sources" / "Vanilla" / "languages" / "Tooltips.dbt").write_text("source", encoding="utf-8")
        (temp / "sources" / "Empty" / "languages").mkdir(parents=True, exist_ok=True)
        (temp / "sources" / "OnlyTranslations" / "languages" / "#chinese").mkdir(parents=True, exist_ok=True)

        roots = local_project_roots(temp)
        names = [root.name for root in roots]
        if names != ["Reforged", "Vanilla"]:
            raise AssertionError(f"local source projects were not discovered correctly: {names!r}")
    finally:
        safe_rmtree(temp)


def assert_discover_game_source_projects_detects_vanilla_and_mods() -> None:
    temp = Path(tempfile.gettempdir()) / f"translator_tool_smoke_game_projects_{uuid.uuid4().hex[:8]}"
    try:
        game_root = temp / "game"
        (game_root / "DB" / "Languages").mkdir(parents=True, exist_ok=True)
        (game_root / "DB" / "Languages" / "Text.dbt").write_text("source", encoding="utf-8")
        (game_root / "mods" / "Reforged" / "DB" / "Languages").mkdir(parents=True, exist_ok=True)
        (game_root / "mods" / "Reforged" / "DB" / "Languages" / "Text.dbt").write_text("mod-source", encoding="utf-8")
        (game_root / "mods" / "TranslationOnly" / "DB" / "Languages").mkdir(parents=True, exist_ok=True)
        (game_root / "mods" / "TranslationOnly" / "DB" / "Languages" / "Text.dbt").write_text("skip", encoding="utf-8")
        (game_root / "mods" / "TranslationOnly" / "modinfo.txt").write_text("Type=Translation\n", encoding="utf-8")
        (game_root / "mods" / "NoLanguages").mkdir(parents=True, exist_ok=True)
        app_root = temp / "app"

        projects = discover_game_source_projects(game_root, app_root)
        names = [(project.name, project.kind, project.added) for project in projects]
        if names != [("Vanilla", "vanilla", False), ("Reforged", "mod", False)]:
            raise AssertionError(f"game project discovery returned unexpected entries: {names!r}")

        (app_root / "sources" / "Reforged" / "languages").mkdir(parents=True, exist_ok=True)
        (app_root / "sources" / "Reforged" / "languages" / "Text.dbt").write_text("cached", encoding="utf-8")
        projects = discover_game_source_projects(game_root, app_root)
        reforged = next(project for project in projects if project.name == "Reforged")
        if not reforged.added:
            raise AssertionError("existing local project should be marked as added")
    finally:
        safe_rmtree(temp)


def assert_sync_source_project_invalidates_changed_translations(root: Path) -> None:
    temp = Path(tempfile.gettempdir()) / f"translator_tool_smoke_source_update_{uuid.uuid4().hex[:8]}"
    try:
        project_root = temp / "app" / "sources" / "Reforged"
        source_root = temp / "game" / "DB" / "Languages"
        copy_project_subset(root, project_root)
        source_root.mkdir(parents=True, exist_ok=True)
        for name in ["Text.dbt", "Tooltips.dbt"]:
            shutil.copy2(root / "languages" / name, source_root / name)

        project = Project.load(project_root, "#chinese", codec_root=tool_root())
        unit = next(
            item
            for item in project.units
            if item.ref.kind == "dbt" and item.status == STATUS_TRANSLATED and item.source_text and item.translate_text
        )
        source_doc = load_dbt(source_root / unit.file_rel)
        target_field = unit.ref.target_field
        source_field = matching_source_field(target_field, source_doc.string_columns)
        key = (int(unit.record_id), unit.label)
        source_row = source_doc.row_index.get(key)
        if source_row is None:
            raise AssertionError(f"could not find source row for {unit.uid}")
        source_row.set_raw(source_field, source_row.get(source_field) + " [updated]")
        (source_root / unit.file_rel).write_bytes(source_doc.render_bytes())
        before_bytes = (project_root / "languages" / "#chinese" / unit.file_rel).read_bytes()

        result = sync_source_project(source_root, project_root)
        if result.invalidated_units < 1:
            raise AssertionError("source sync did not invalidate any changed translations")
        after_bytes = (project_root / "languages" / "#chinese" / unit.file_rel).read_bytes()
        if after_bytes != before_bytes:
            raise AssertionError("source sync should not rewrite translated files when only marking review")
        if unit.uid not in source_review_uids(project_root, "#chinese"):
            raise AssertionError("source sync did not persist the source-change review flag")

        reloaded = Project.load(project_root, "#chinese", codec_root=tool_root())
        updated = next(item for item in reloaded.units if item.uid == unit.uid)
        if updated.source_text == unit.source_text:
            raise AssertionError("source sync did not refresh the updated source text")
        if updated.current_text != unit.current_text:
            raise AssertionError("source sync should keep the existing translation text")
        if updated.review_reason != TODO_REASON_SOURCE_CHANGED:
            raise AssertionError("changed translation should be flagged for manual confirmation")
        if updated.filter_status() not in MISSING_WORK_STATUSES:
            raise AssertionError("changed translation should re-enter the untranslated filter")
    finally:
        safe_rmtree(temp)


def assert_source_review_cache(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_source_review_")
    project = Project.load(temp, "#chinese")
    unit = next(item for item in project.units if item.filter_status() == STATUS_TRANSLATED and item.translate_text)
    project.set_units_source_review((unit,), True)
    reloaded = Project.load(temp, "#chinese")
    reloaded_unit = next(item for item in reloaded.units if item.uid == unit.uid)
    if reloaded_unit.review_reason != TODO_REASON_SOURCE_CHANGED:
        raise AssertionError("source review flag was not persisted in cache")
    reloaded.set_units_source_review((reloaded_unit,), False)
    reloaded_again = Project.load(temp, "#chinese")
    if next(item for item in reloaded_again.units if item.uid == unit.uid).review_reason:
        raise AssertionError("source review flag was not removed from cache")
    safe_rmtree(temp)


def assert_startup_prefers_local_sources_over_game_root() -> None:
    from . import app as app_module
    from .app import TranslatorWindow

    temp = Path(tempfile.gettempdir()) / f"translator_tool_smoke_startup_sources_{uuid.uuid4().hex[:8]}"
    previous_app_root = app_module.APP_ROOT
    try:
        (temp / "sources" / "Reforged" / "languages").mkdir(parents=True, exist_ok=True)
        (temp / "sources" / "Reforged" / "languages" / "Text.dbt").write_text("source", encoding="utf-8")
        game_root = temp / "Game"
        (game_root / "DB" / "Languages").mkdir(parents=True, exist_ok=True)
        (game_root / "DB" / "Languages" / "Text.dbt").write_text("game-source", encoding="utf-8")

        app_module.APP_ROOT = temp
        window = TranslatorWindow.__new__(TranslatorWindow)
        window.settings = SimpleNamespace(last_project_root=str(game_root), recent_project_roots=[])
        startup_root = TranslatorWindow._startup_project_root(window)
        if startup_root != temp / "sources" / "Reforged":
            raise AssertionError(f"startup should prefer local sources project, got: {startup_root!r}")
    finally:
        app_module.APP_ROOT = previous_app_root
        safe_rmtree(temp)


def assert_save_existing(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_existing_")
    project = Project.load(temp, "#chinese")
    unit = next(unit for unit in project.units if unit.file_rel == "Text.dbt" and unit.filter_status() == STATUS_TRANSLATED)
    target_path = temp / "languages" / "#chinese" / "Text.dbt"
    original = target_path.read_bytes()
    before_doc = load_dbt(target_path)
    unit.set_text(unit.current_text + "!")
    result = project.save([unit])
    if not result.changed_files:
        raise AssertionError("save_existing did not write a file")
    if target_path.read_bytes() == original:
        raise AssertionError("save_existing did not update the target file")
    after_doc = load_dbt(target_path)
    changed_key = (int(unit.record_id), unit.label)
    if len(before_doc.rows) != len(after_doc.rows):
        raise AssertionError("save_existing changed the existing row count")
    for before_row, after_row in zip(before_doc.rows, after_doc.rows):
        if row_key("Text.dbt", before_row) != changed_key and before_row.original_line != after_row.original_line:
            raise AssertionError("save_existing rewrote an untouched DBT line")
    if (temp / "backups").exists():
        raise AssertionError("Git-backed save unexpectedly created a backup directory")
    safe_rmtree(temp)


def assert_save_auto_formats_color_tokens(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_color_spacing_")
    try:
        project = Project.load(temp, "#chinese")
        unit = next(unit for unit in project.units if unit.file_rel == "Text.dbt" and unit.filter_status() == STATUS_TRANSLATED)
        unit.set_text(
            "$C[10,20,30]句首中$C[225,214,158]测试，$C[255,255,255]恢复#E[NT_NEUTRAL]$C[225,214,158]颜色测试$N$N$C[255,255,255]对齐"
        )
        project.save([unit], auto_space_before_color_tokens=True)
        saved = Project.load(temp, "#chinese")
        reloaded = next(item for item in saved.units if item.uid == unit.uid)
        expected = (
            "$C[10,20,30]句首中 $C[225,214,158]测试， $C[255,255,255]恢复 #E[NT_NEUTRAL]$C[225,214,158]颜色测试 $N$N$C[255,255,255]对齐"
        )
        if reloaded.current_text != expected:
            raise AssertionError("save did not normalize color-token spacing with the expected exceptions")
    finally:
        safe_rmtree(temp)


def assert_save_guides_plain_text_uses_source_profile(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_guides_txt_")
    try:
        source_path = temp / "languages" / "Guides" / "Intro.txt"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_text = "Guide Title\r\nGuide Body\r\n"
        source_path.write_bytes(source_text.encode("utf-16"))

        project = Project.load(temp, "#chinese")
        unit = next(item for item in project.units if item.file_rel == "Guides/Intro.txt")
        if unit.source_text != source_text or unit.current_text != "":
            raise AssertionError("guide text files were not loaded as plain source text")

        translated_text = "甲😀\n乙"
        unit.set_text(translated_text)
        result = project.save([unit])
        target_path = temp / "languages" / "#chinese" / "Guides" / "Intro.txt"
        if not result.changed_files or target_path not in result.changed_files:
            raise AssertionError("guide text save did not write the translated txt file")
        expected_bytes = "甲😀\r\n乙\r\n".encode("utf-16")
        if target_path.read_bytes() != expected_bytes:
            raise AssertionError("guide text save did not preserve the source encoding and newline style")

        reloaded = Project.load(temp, "#chinese")
        updated = next(item for item in reloaded.units if item.file_rel == "Guides/Intro.txt")
        if updated.current_text != "甲😀\r\n乙\r\n":
            raise AssertionError("guide text reload did not preserve the plain-text translation content")
    finally:
        safe_rmtree(temp)


def assert_save_creates_missing_target_dbt_incrementally(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_missing_target_dbt_")
    try:
        target_path = temp / "languages" / "#chinese" / "Text.dbt"
        if target_path.exists():
            target_path.unlink()

        project = Project.load(temp, "#chinese")
        missing_units = [unit for unit in project.units if unit.file_rel == "Text.dbt" and unit.source_text]
        if not missing_units:
            raise AssertionError("missing target DBT file did not expose source rows as translatable units")
        unit = missing_units[0]
        if unit.filter_status() != STATUS_TODO or unit.todo_reason != TODO_REASON_MISSING_ROW:
            raise AssertionError("missing target DBT rows were not classified as missing translations")
        unit.set_text("增量保存测试")
        result = project.save([unit])
        if not target_path.exists() or target_path not in result.changed_files:
            raise AssertionError("saving into a missing target DBT did not create the translated file")

        saved = load_dbt(target_path)
        if saved.string_columns != ["label", "chinese"]:
            raise AssertionError("new target DBT file did not derive the translated-column header correctly")
        if len(saved.rows) != 1:
            raise AssertionError("incremental DBT save should only write the translated row")
        saved_row = saved.row_index.get((int(unit.record_id), unit.label))
        if saved_row is None or saved_row.get("chinese") != project.codec.encode("增量保存测试"):
            raise AssertionError("incremental DBT save did not persist the translated row with the expected raw text")
    finally:
        safe_rmtree(temp)


def assert_save_removes_extra_target_row(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_extra_")
    try:
        target_path = temp / "languages" / "#chinese" / "Text.dbt"
        target_doc = load_dbt(target_path)
        base_row = target_doc.rows[0]
        extra_id = base_row.row_id + 900000
        extra_line = base_row.original_line.replace(str(base_row.row_id), str(extra_id), 1)
        target_path.write_bytes(
            target_doc.text.replace(base_row.original_line, base_row.original_line + extra_line, 1).encode(
                target_doc.profile.encoding
            )
        )
        project = Project.load(temp, "#chinese")
        extra = next(
            unit
            for unit in project.units
            if unit.status == STATUS_EXTRA and unit.ref.kind == "dbt" and unit.record_id == str(extra_id)
        )
        assert extra.ref.target_row is not None
        key = (extra.ref.target_row.row_id, extra.label)
        extra.set_pending_delete(True)
        result = project.save([extra])
        if not result.changed_files or [item.uid for item in result.deleted_units] != [extra.uid]:
            raise AssertionError("saving a marked extra target row did not delete it")
        if key in load_dbt(target_path).row_index:
            raise AssertionError("extra target row remained after save")
    finally:
        safe_rmtree(temp)


def assert_save_missing(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_missing_")
    project = Project.load(temp, "#chinese")
    unit = next(
        unit
        for unit in project.units
        if unit.file_rel == "Text.dbt" and unit.filter_status() == STATUS_TODO and unit.todo_reason == TODO_REASON_MISSING_ROW
    )
    unit.set_text(unit.source_text or "test")
    result = project.save([unit])
    if not result.changed_files:
        raise AssertionError("save_missing did not write a file")
    reloaded = Project.load(temp, "#chinese")
    saved = [item for item in reloaded.units if item.file_rel == unit.file_rel and item.record_id == unit.record_id and item.label == unit.label]
    if not saved or saved[0].todo_reason == TODO_REASON_MISSING_ROW:
        raise AssertionError("inserted missing row did not reload as an existing row")
    safe_rmtree(temp)


def assert_missing_insertions_follow_file_order(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_missing_order_")
    project = Project.load(temp, "#chinese")
    missing = [
        unit
        for unit in project.units
        if unit.file_rel == "Text.dbt" and unit.filter_status() == STATUS_TODO and unit.todo_reason == TODO_REASON_MISSING_ROW
    ][:2]
    if len(missing) < 2:
        safe_rmtree(temp)
        return
    for unit in missing:
        unit.set_text(unit.source_text)
    project.save(list(reversed(missing)))
    after = load_dbt(temp / "languages" / "#chinese" / "Text.dbt")
    positions = []
    for unit in missing:
        key = (int(unit.record_id), unit.label)
        row = after.row_index.get(key)
        if row is None:
            raise AssertionError("missing row was not inserted")
        positions.append((unit.ref.source_order, row.line_index))
    if [line for _source, line in sorted(positions)] != sorted(line for _source, line in positions):
        raise AssertionError("missing rows were not inserted in original file order")
    safe_rmtree(temp)


def assert_unsaved_translation_status(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_status_")
    project = Project.load(temp, "#chinese")
    unit = next(
        item
        for item in project.units
        if item.filter_status() == STATUS_TODO and item.todo_reason == TODO_REASON_MISSING_ROW and item.source_text
    )
    if unit.display_status() != STATUS_TODO or unit.todo_reason != TODO_REASON_MISSING_ROW:
        raise AssertionError("an untouched missing row no longer reported missing status")
    unit.set_text("AI translated")
    if unit.display_status() != STATUS_TRANSLATED or unit.filter_status() != STATUS_TRANSLATED:
        raise AssertionError("an unsaved translated unit did not report translated status")
    translated = next(item for item in project.units if item.filter_status() == STATUS_TRANSLATED)
    translated.set_text(translated.current_text + "x")
    if translated.display_status() != STATUS_TRANSLATED or translated.filter_status() != STATUS_TRANSLATED or not translated.is_dirty:
        raise AssertionError("an edited translated unit did not keep a translated status with a dirty marker")
    translated.set_text("")
    saved_empty = project.save([translated])
    if not saved_empty.changed_files or saved_empty.deleted_units:
        raise AssertionError("an empty translation should save as an empty override instead of deleting it")
    reloaded = Project.load(temp, "#chinese")
    updated = next(item for item in reloaded.units if item.uid == translated.uid)
    if updated.filter_status() != STATUS_TODO or updated.todo_reason != TODO_REASON_EMPTY:
        raise AssertionError("an empty translation did not reload as an empty target override")
    updated.set_pending_delete(True)
    if updated.display_status() != STATUS_PENDING_DELETE or updated.filter_status() != STATUS_TODO:
        raise AssertionError("a marked deletion did not expose the pending-delete status")
    removed = reloaded.save([updated])
    if not removed.changed_files or [item.uid for item in removed.deleted_units] != [updated.uid]:
        raise AssertionError("a marked deletion did not remove the existing override")
    safe_rmtree(temp)


def assert_mod_label_match_inserts_source_formatted_row(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_label_match_")
    try:
        target_path = temp / "languages" / "#chinese" / "Text.dbt"
        target_doc = load_dbt(target_path)
        target_row = target_doc.rows[0]
        original_key = row_key("Text.dbt", target_row)
        source_doc = load_dbt(temp / "languages" / "Text.dbt")
        source_row = source_doc.row_index[original_key]
        old_line = target_row.original_line
        new_line = old_line.replace(str(target_row.row_id), str(target_row.row_id + 900000), 1)
        target_path.write_bytes(target_doc.text.replace(old_line, new_line, 1).encode(target_doc.profile.encoding))

        project = Project.load(temp, "#chinese")
        unit = next(
            item
            for item in project.units
            if item.file_rel == "Text.dbt" and item.record_id == str(source_row.row_id) and item.label == original_key[1]
        )
        if unit.review_reason != TODO_REASON_IMPORT_REVIEW or unit.display_status() != STATUS_TODO:
            raise AssertionError("a unique mod label match was not marked for review")
        if unit.ref.target_row is not None or not unit.is_dirty:
            raise AssertionError("label match did not stage a source-row insertion")
        legacy_key = (target_row.row_id + 900000, original_key[1])
        unit.set_pending_delete(True)
        removed = project.save([unit])
        if not removed.changed_files or [item.uid for item in removed.deleted_units] != [unit.uid]:
            raise AssertionError("a marked label-match deletion did not remove its old override")
        saved_after_delete = load_dbt(target_path)
        if original_key in saved_after_delete.row_index or legacy_key in saved_after_delete.row_index:
            raise AssertionError("a marked label-match deletion did not remove the legacy target row cleanly")

        # Restore the temporary legacy row so the next branch verifies that a
        # non-empty review edit inserts a new source-formatted row.
        target_path.write_bytes(target_doc.text.replace(old_line, new_line, 1).encode(target_doc.profile.encoding))
        project = Project.load(temp, "#chinese")
        unit = next(
            item
            for item in project.units
            if item.file_rel == "Text.dbt" and item.record_id == str(source_row.row_id) and item.label == original_key[1]
        )
        unit.set_text(unit.current_text + "x")
        if unit.review_reason != TODO_REASON_IMPORT_REVIEW or unit.display_status() != STATUS_TODO:
            raise AssertionError("editing a review item should keep the manual-check reason until it is confirmed")
        project.save([unit])
        saved = load_dbt(target_path)
        inserted = saved.row_index.get(original_key)
        if inserted is None or legacy_key not in saved.row_index:
            raise AssertionError("label match did not retain the legacy extra row and insert the source key")
        source_prefix = source_row.original_line[: source_row.fields[0].end]
        inserted_prefix = inserted.original_line[: inserted.fields[0].end]
        if source_prefix != inserted_prefix:
            raise AssertionError("inserted label-match row did not preserve the source file layout")
    finally:
        safe_rmtree(temp)


def assert_project_history_settings(root: Path) -> None:
    temp = Path(tempfile.gettempdir()) / f"translator_tool_smoke_settings_{uuid.uuid4().hex[:8]}"
    previous = os.environ.get("LOCALAPPDATA")
    try:
        os.environ["LOCALAPPDATA"] = str(temp)
        expected = [str(root / f"project-{number}") for number in range(10)]
        save_settings(
            AppSettings(
                ui_language="zh-CN",
                last_project_root=expected[0],
                recent_project_roots=expected,
                enable_chinese_codec=True,
                auto_space_before_color_tokens_on_save=True,
                editor_zoom_steps=3,
            )
        )
        loaded = load_settings()
        if (
            loaded.ui_language != "zh-CN"
            or
            loaded.last_project_root != expected[0]
            or loaded.recent_project_roots != expected[:8]
            or not loaded.enable_chinese_codec
            or not loaded.auto_space_before_color_tokens_on_save
            or loaded.editor_zoom_steps != 3
        ):
            raise AssertionError("project folder history was not persisted safely")
    finally:
        if previous is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = previous
        safe_rmtree(temp)


def assert_git_binding_tracks_project_root() -> None:
    from .app import TranslatorWindow

    temp = Path(tempfile.gettempdir()) / f"translator_tool_smoke_git_binding_{uuid.uuid4().hex[:8]}"
    try:
        vanilla = temp / "sources" / "Vanilla"
        reforged = temp / "sources" / "Reforged"
        for project_root in (vanilla, reforged):
            (project_root / "languages" / "#chinese").mkdir(parents=True, exist_ok=True)
            (project_root / "languages" / "Text.dbt").write_text("source", encoding="utf-8")

        window = TranslatorWindow.__new__(TranslatorWindow)
        window.project_root = reforged
        window.settings = AppSettings(enable_chinese_codec=True)
        window.git = LanguageGit(vanilla, "#chinese", codec_root=tool_root())
        if TranslatorWindow._git_matches_current_project(window, "#chinese"):
            raise AssertionError("git binding should not match after switching to a different project root")

        window.git = LanguageGit(reforged, "#chinese", codec_root=tool_root())
        if not TranslatorWindow._git_matches_current_project(window, "#chinese"):
            raise AssertionError("git binding should match the active project root for the same language")
    finally:
        safe_rmtree(temp)


def assert_language_combo_offers_create_action() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from PySide6.QtGui import QStandardItemModel

    from .app import LANGUAGE_ACTION_NEW, LANGUAGE_ACTION_SEPARATOR, PopupSelectionComboBox, TranslatorWindow

    temp = Path(tempfile.gettempdir()) / f"translator_tool_smoke_language_combo_{uuid.uuid4().hex[:8]}"
    try:
        (temp / "languages" / "#chinese").mkdir(parents=True, exist_ok=True)
        (temp / "languages" / "Text.dbt").write_text("source", encoding="utf-8")
        app = QApplication.instance()
        created_app = app is None
        if app is None:
            app = QApplication([])
        window = TranslatorWindow.__new__(TranslatorWindow)
        window.project_root = temp
        window.project = None
        window.language_combo = PopupSelectionComboBox()
        choices = TranslatorWindow._load_language_choices(window, "#chinese")
        if choices != ["#chinese"]:
            raise AssertionError(f"language choices should only return real translation folders: {choices!r}")
        if window.language_combo.isEditable():
            raise AssertionError("language combo should no longer be editable")
        separator_index = window.language_combo.findData(LANGUAGE_ACTION_SEPARATOR)
        if separator_index < 0:
            raise AssertionError("language combo is missing the separator before the create action")
        action_index = window.language_combo.findData(LANGUAGE_ACTION_NEW)
        if action_index < 0:
            raise AssertionError("language combo is missing the create-new-language action")
        model = window.language_combo.model()
        if isinstance(model, QStandardItemModel):
            separator_item = model.item(separator_index)
            if separator_item is None or separator_item.isEnabled():
                raise AssertionError("language separator should be disabled")
        if created_app:
            app.quit()
    finally:
        safe_rmtree(temp)


def assert_bundled_settings_are_isolated_by_location() -> None:
    temp = Path(tempfile.gettempdir()) / f"translator_tool_smoke_settings_iso_{uuid.uuid4().hex[:8]}"
    previous_localappdata = os.environ.get("LOCALAPPDATA")
    previous_frozen = getattr(settings_module.sys, "frozen", None)
    previous_executable = settings_module.sys.executable
    try:
        os.environ["LOCALAPPDATA"] = str(temp)
        dev_dir = settings_module.settings_dir()
        if dev_dir.name != "dev":
            raise AssertionError("development settings directory no longer uses the dev namespace")

        settings_module.sys.frozen = True  # type: ignore[attr-defined]
        settings_module.sys.executable = str(temp / "first" / "TheGuild2Translator.exe")
        first = settings_module.settings_dir()
        settings_module.sys.executable = str(temp / "second" / "TheGuild2Translator.exe")
        second = settings_module.settings_dir()
        if first == second:
            raise AssertionError("bundled settings directories were not isolated by executable location")
        if first.parent.name != "bundled" or second.parent.name != "bundled":
            raise AssertionError("bundled settings directory did not use the bundled namespace")
    finally:
        if previous_localappdata is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = previous_localappdata
        settings_module.sys.executable = previous_executable
        if previous_frozen is None:
            try:
                delattr(settings_module.sys, "frozen")
            except AttributeError:
                pass
        else:
            settings_module.sys.frozen = previous_frozen  # type: ignore[attr-defined]
        safe_rmtree(temp)


def assert_editor_undo_stays_local(root: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QTextCursor
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from . import app as app_module
    from .app import TYPING_GROUP_DELAY_MS, TranslatorWindow

    temp = make_temp_project(root, "translator_tool_smoke_editor_undo_")
    settings_dir = Path(tempfile.gettempdir()) / f"translator_tool_smoke_editor_settings_{uuid.uuid4().hex[:8]}"
    previous_localappdata = os.environ.get("LOCALAPPDATA")
    previous_managed_root = app_module.MANAGED_PROJECT_ROOT
    try:
        guide_source = temp / "languages" / "Guides" / "Intro.txt"
        guide_source.parent.mkdir(parents=True, exist_ok=True)
        guide_source.write_bytes("Guide Title\r\nGuide Body\r\n".encode("utf-16"))
        os.environ["LOCALAPPDATA"] = str(settings_dir)
        app_module.MANAGED_PROJECT_ROOT = temp
        save_settings(AppSettings())
        app = QApplication.instance()
        created_app = app is None
        if app is None:
            app = QApplication([])
        win = TranslatorWindow()

        unit = next(item for item in win.model.units if item.ref.kind == "dbt" and item.source_text)
        original = unit.current_text
        win.current_uid = unit.uid
        win._set_editor_unit(unit)
        win.show()
        app.processEvents()

        win.translation_edit.setFocus(Qt.FocusReason.OtherFocusReason)
        app.processEvents()
        cursor = win.translation_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        win.translation_edit.setTextCursor(cursor)
        win.translation_edit.insertPlainText("x")
        app.processEvents()
        if win.translation_edit.toPlainText() != original + "x":
            raise AssertionError("editor typing smoke test did not update the translation editor")

        undo_calls = 0
        original_undo = win.undo

        def wrapped_undo() -> None:
            nonlocal undo_calls
            undo_calls += 1
            original_undo()

        win.undo = wrapped_undo  # type: ignore[method-assign]
        QTest.keyClick(win.translation_edit, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        app.processEvents()
        if undo_calls != 1:
            raise AssertionError("one Ctrl+Z should trigger only one editor undo")
        if win.translation_edit.toPlainText() != original or unit.current_text != original:
            raise AssertionError("editor undo did not restore only the in-progress text edit")
        if win.current_uid != unit.uid:
            raise AssertionError("editor undo unexpectedly changed the selected translation unit")
        if win.translation_edit.textCursor().position() == 0 and original:
            raise AssertionError("editor undo unexpectedly reset the caret to the start of the text")

        QTest.keyClick(win.translation_edit, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        app.processEvents()
        if win.translation_edit.toPlainText() != original + "x" or unit.current_text != original + "x":
            raise AssertionError("editor redo did not restore the in-progress text edit")

        win.translation_edit.insertPlainText("a")
        app.processEvents()
        QTest.qWait(TYPING_GROUP_DELAY_MS + 120)
        app.processEvents()

        win.translation_edit.insertPlainText("b")
        app.processEvents()
        QTest.keyClick(win.translation_edit, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        app.processEvents()
        if win.current_uid != unit.uid:
            raise AssertionError("editor undo unexpectedly changed the selected unit during continued editing")
        if win.translation_edit.textCursor().position() == 0 and unit.current_text:
            raise AssertionError("editor undo unexpectedly reset the caret during continued editing")

        second = next(
            item for item in win.model.units if item.ref.kind == "dbt" and item.source_text and item.uid != unit.uid
        )
        second_original = second.current_text
        win._restore_selected_row(second.uid)
        app.processEvents()
        win.current_uid = second.uid
        win._set_editor_unit(second)
        win.translation_edit.setFocus(Qt.FocusReason.OtherFocusReason)
        app.processEvents()
        cursor = win.translation_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        win.translation_edit.setTextCursor(cursor)
        win.translation_edit.insertPlainText("z")
        app.processEvents()
        QTest.qWait(TYPING_GROUP_DELAY_MS + 120)
        app.processEvents()
        if second.current_text != second_original + "z":
            raise AssertionError("second unit typing smoke test did not update the translation editor")

        QTest.keyClick(win.translation_edit, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        app.processEvents()
        if win.current_uid != second.uid:
            raise AssertionError("editor undo unexpectedly switched away from the active entry")
        if unit.current_text != win.model.unit_for_uid(unit.uid).current_text:
            raise AssertionError("editor undo unexpectedly altered a different entry")

        win.table.setFocus(Qt.FocusReason.OtherFocusReason)
        app.processEvents()
        win.undo()
        app.processEvents()
        if second.current_text == second_original + "z":
            raise AssertionError("table-level undo did not restore the latest committed entry edit")

        win._replace_unit_text(second, second_original + "q", "smoke test history")
        app.processEvents()
        if second.current_text != second_original + "q":
            raise AssertionError("dbt edit before document-mode switch did not commit as expected")

        guides_index = win.file_combo.findData("Guides/Intro.txt")
        if guides_index < 0:
            raise AssertionError("guide txt smoke test entry is missing from file filter")
        win.file_combo.setCurrentIndex(guides_index)
        app.processEvents()

        dbt_index = win.file_combo.findData(second.file_rel)
        if dbt_index < 0:
            raise AssertionError("dbt file is missing from file filter after leaving guide txt mode")
        win.file_combo.setCurrentIndex(dbt_index)
        app.processEvents()
        win._restore_selected_row(second.uid)
        app.processEvents()
        win.translation_edit.setFocus(Qt.FocusReason.OtherFocusReason)
        app.processEvents()

        QTest.keyClick(win.translation_edit, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        app.processEvents()
        if second.current_text != second_original:
            raise AssertionError("editor undo did not fall back to entry history after returning from guide txt mode")

        win.close()
        app.processEvents()
    finally:
        app_module.MANAGED_PROJECT_ROOT = previous_managed_root
        if previous_localappdata is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = previous_localappdata
        safe_rmtree(settings_dir)
        safe_rmtree(temp)
        if "created_app" in locals() and created_app:
            app.quit()


def assert_ui_language_switching() -> None:
    previous = set_language("en")
    try:
        set_language("en")
        if translate("button.save") != "Save" or status_text(STATUS_TRANSLATED) != "Translated":
            raise AssertionError("English UI localization did not resolve expected labels")
        set_language("zh-CN")
        if translate("button.save") != "保存" or status_text(STATUS_TRANSLATED) != "已翻译":
            raise AssertionError("Chinese UI localization did not resolve expected labels")
    finally:
        set_language(previous)


def assert_external_project_uses_tool_codec(root: Path) -> None:
    temp = Path(tempfile.gettempdir()) / f"translator_tool_smoke_external_codec_{uuid.uuid4().hex[:8]}"
    temp.mkdir(parents=True, exist_ok=False)
    (temp / "languages" / "#chinese").mkdir(parents=True)
    for name in ["Text.dbt", "Tooltips.dbt"]:
        shutil.copy2(root / "languages" / name, temp / "languages" / name)
        shutil.copy2(root / "languages" / "#chinese" / name, temp / "languages" / "#chinese" / name)
    project = Project.load(temp, "#chinese", codec_root=tool_root())
    if not project.units:
        raise AssertionError("external project did not load with the tool codec")
    LanguageGit(temp, codec_root=tool_root())
    safe_rmtree(temp)


def assert_packaged_runtime_finds_sibling_codec(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_packaged_codec_")
    try:
        runtime_root = temp / "_internal"
        runtime_root.mkdir(parents=True, exist_ok=True)
        codec = load_codec_for_language(runtime_root, "#chinese")
        if codec is None:
            raise AssertionError("packaged runtime did not find a sibling Chinese codec directory")
        if codec.decode(codec.encode("测试")) != "测试":
            raise AssertionError("packaged runtime sibling codec did not round-trip")
    finally:
        safe_rmtree(temp)


def assert_non_chinese_language_bypasses_codec(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_non_chinese_codec_")
    try:
        korean_root = temp / "languages" / "#korean"
        korean_root.mkdir(parents=True, exist_ok=True)
        git = LanguageGit(temp, "#korean")
        git.ensure_repository(AppSettings())

        project = Project.load(temp, "#korean")
        if project.codec is not None:
            raise AssertionError("non-Chinese language unexpectedly loaded the Chinese codec")
        unit = next(item for item in project.units if item.file_rel == "Text.dbt" and item.source_text)
        if unit.font_codec is not None:
            raise AssertionError("non-Chinese DBT units unexpectedly enabled glyph-codec validation")
        unit.set_text("건강:")
        result = project.save([unit])

        target_path = korean_root / "Text.dbt"
        if not result.changed_files or target_path not in result.changed_files:
            raise AssertionError("non-Chinese DBT save did not write the target file")
        saved = load_dbt(target_path)
        saved_row = saved.row_index.get((int(unit.record_id), unit.label))
        if saved_row is None or saved_row.get("korean") != "건강:":
            raise AssertionError("non-Chinese DBT save incorrectly codec-encoded raw text")

        reloaded = Project.load(temp, "#korean")
        updated = next(item for item in reloaded.units if item.uid == unit.uid)
        if updated.current_text != "건강:":
            raise AssertionError("non-Chinese DBT reload incorrectly decoded raw text")

        commit = git.commit_saved(result.changed_files, result.saved_units, result.deleted_units)
        if commit is None:
            raise AssertionError("non-Chinese save did not create a Git commit")
        entries = git.entries_for_commit(commit.full_hash)
        if not entries or entries[0].translated_text != "건강:":
            raise AssertionError("non-Chinese Git history incorrectly decoded raw text")
    finally:
        safe_rmtree(temp)


def assert_chinese_without_codec_uses_plain_text(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_missing_chinese_codec_")
    try:
        safe_rmtree(temp / "encoder")
        git = LanguageGit(temp, "#chinese")
        git.ensure_repository(AppSettings())

        project = Project.load(temp, "#chinese")
        if project.codec is not None:
            raise AssertionError("Chinese project unexpectedly loaded a missing codec")
        unit = next(item for item in project.units if item.file_rel == "Text.dbt" and item.source_text)
        if unit.font_codec is not None:
            raise AssertionError("Chinese DBT units unexpectedly enabled glyph-codec validation without a codec")
        unit.set_text("测试")
        result = project.save([unit])

        target_path = temp / "languages" / "#chinese" / "Text.dbt"
        if not result.changed_files or target_path not in result.changed_files:
            raise AssertionError("Chinese save without codec did not write the target file")
        saved = load_dbt(target_path)
        saved_row = saved.row_index.get((int(unit.record_id), unit.label))
        if saved_row is None or saved_row.get("chinese") != "测试":
            raise AssertionError("Chinese save without codec did not preserve plain text")

        reloaded = Project.load(temp, "#chinese")
        updated = next(item for item in reloaded.units if item.uid == unit.uid)
        if updated.current_text != "测试":
            raise AssertionError("Chinese reload without codec did not preserve plain text")

        commit = git.commit_saved(result.changed_files, result.saved_units, result.deleted_units)
        if commit is None:
            raise AssertionError("Chinese save without codec did not create a Git commit")
        entries = git.entries_for_commit(commit.full_hash)
        if not entries or entries[0].translated_text != "测试":
            raise AssertionError("Chinese Git history without codec did not preserve plain text")
    finally:
        safe_rmtree(temp)


def assert_chinese_setting_can_disable_codec(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_disabled_chinese_codec_")
    try:
        git = LanguageGit(temp, "#chinese", codec_root=tool_root(), enable_codec=False)
        git.ensure_repository(AppSettings(enable_chinese_codec=False))

        project = Project.load(temp, "#chinese", codec_root=tool_root(), enable_codec=False)
        if project.codec is not None:
            raise AssertionError("Chinese project unexpectedly loaded the codec while the setting was disabled")
        unit = next(item for item in project.units if item.file_rel == "Text.dbt" and item.source_text)
        if unit.font_codec is not None:
            raise AssertionError("Chinese DBT units unexpectedly kept glyph validation while the setting was disabled")
        unit.set_text("测试")
        if any(issue.code == "font-glyph" for issue in unit.issues()):
            raise AssertionError("disabled Chinese codec setting should skip glyph validation")
        result = project.save([unit])

        target_path = temp / "languages" / "#chinese" / "Text.dbt"
        if not result.changed_files or target_path not in result.changed_files:
            raise AssertionError("disabled Chinese codec save did not write the target file")
        saved = load_dbt(target_path)
        saved_row = saved.row_index.get((int(unit.record_id), unit.label))
        if saved_row is None or saved_row.get("chinese") != "测试":
            raise AssertionError("disabled Chinese codec save did not preserve plain Chinese text")

        reloaded = Project.load(temp, "#chinese", codec_root=tool_root(), enable_codec=False)
        updated = next(item for item in reloaded.units if item.uid == unit.uid)
        if updated.current_text != "测试":
            raise AssertionError("disabled Chinese codec reload did not preserve plain Chinese text")

        commit = git.commit_saved(result.changed_files, result.saved_units, result.deleted_units)
        if commit is None:
            raise AssertionError("disabled Chinese codec save did not create a Git commit")
        entries = git.entries_for_commit(commit.full_hash)
        if not entries or entries[0].translated_text != "测试":
            raise AssertionError("disabled Chinese codec Git history did not preserve plain Chinese text")
    finally:
        safe_rmtree(temp)


def assert_validation_warnings_do_not_block() -> None:
    issues = validate_translation("Value %1n", "bad %2n", dbt_field=True)
    if any(issue.blocks_save for issue in issues):
        raise AssertionError("format validation warning unexpectedly blocked saving")
    if not any(issue.code == "argument-index" for issue in issues):
        raise AssertionError("invalid argument index was not reported as a warning")
    fullwidth = validate_translation("Value", "％１Ａ", dbt_field=True)
    if any("全角" in issue.message or "fullwidth" in issue.message.lower() for issue in fullwidth):
        raise AssertionError("fullwidth characters should be validated only through the codec")


def assert_ignore_cache(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_ignore_")
    project = Project.load(temp, "#chinese")
    unit = next(item for item in project.units if item.status in MISSING_WORK_STATUSES)
    uid = unit.uid
    project.set_unit_ignored(unit, True)
    reloaded = Project.load(temp, "#chinese")
    reloaded_unit = next(item for item in reloaded.units if item.uid == uid)
    if not reloaded_unit.ignored:
        raise AssertionError("ignored translation unit was not persisted in cache")
    reloaded.set_unit_ignored(reloaded_unit, False)
    reloaded_again = Project.load(temp, "#chinese")
    if next(item for item in reloaded_again.units if item.uid == uid).ignored:
        raise AssertionError("ignored translation unit was not removed from cache")
    safe_rmtree(temp)


def assert_codec(root: Path) -> None:
    codec = Guild2Codec.load(tool_root())
    text = "测试"
    if codec.decode(codec.encode(text)) != text:
        raise AssertionError("codec encode/decode did not round-trip")


def assert_font_glyph_validation(root: Path) -> None:
    project = Project.load(root, "#chinese", codec_root=tool_root())
    unit = next(unit for unit in project.units if unit.source_text)
    unit.set_text("ΩЖ가")
    if any(issue.code == "font-glyph" for issue in unit.issues()):
        raise AssertionError("non-CJK Unicode should not be reported as missing font glyphs")
    unit.set_text("😀")
    if not any(issue.code == "font-glyph" and "😀" in issue.message for issue in unit.issues()):
        raise AssertionError("emoji glyph validation did not flag an unsupported character")
    unit.set_text("𠀀")
    if not any(issue.code == "font-glyph" and "𠀀" in issue.message for issue in unit.issues()):
        raise AssertionError("unmapped CJK glyph validation did not flag an unsupported character")
    previous = project_module.ENABLE_FONT_GLYPH_VALIDATION
    try:
        project_module.ENABLE_FONT_GLYPH_VALIDATION = False
        if any(issue.code == "font-glyph" for issue in unit.issues()):
            raise AssertionError("internal font glyph switch did not disable validation")
    finally:
        project_module.ENABLE_FONT_GLYPH_VALIDATION = previous


class FakeGoogleTransport:
    def __init__(self, response: str) -> None:
        self.response = response
        self.last_url = ""

    def get_json(self, url: str):
        self.last_url = url
        return [[[self.response, "", None, None]]]

    def post_json(self, url: str, payload, headers):
        raise AssertionError("Google provider must not issue a POST request")


def assert_ai_token_protection() -> None:
    transport = FakeGoogleTransport("测试 __TG_FMT_0000__")
    provider = GoogleTranslateProvider("https://example.invalid/translate", "en", "zh-CN", transport)
    translated = provider.translate("Cost: %1t", dbt_field=True)
    if translated != "测试 %1t" or "__TG_FMT_0000__" not in transport.last_url:
        raise AssertionError("AI translation did not preserve protected format tokens")
    broken = GoogleTranslateProvider(
        "https://example.invalid/translate", "en", "zh-CN", FakeGoogleTransport("测试内容")
    )
    try:
        broken.translate("Cost: %1t", dbt_field=True)
    except TranslationProviderError:
        return
    raise AssertionError("AI result missing a protected token was accepted")


def assert_linebreak_format_is_ignored() -> None:
    issues = validate_translation("First$NSecond", "First Second", dbt_field=True)
    if any(issue.blocks_save and "$N" in issue.message for issue in issues):
        raise AssertionError("$N line-break differences must not block translation saves")
    if any("$N" in issue.message for issue in issues):
        raise AssertionError("$N line-break differences should remain ignored")


def assert_guild2_format_grammar() -> None:
    syntax = (
        "%1NAME %2n %3i %4f %5t %6c %7z %8j %9s %10l "
        "%11GG %12GN %13GT %% %> %< %14SN %15Sn %16SV %17Sv %18SZ %19Sz "
        "%20SK %21ST %22SA %23SD %24SB %25SL %26DN %27DS "
        "%gold_icon% %measure:LevelUpCity:name% %officer:guildmaster:rogue% "
        "$N $Z $L $R $T $> $< $C[1,2,3,255] $F[Body] $S[12] $B[label] "
        "$[ornament$] #E[NT_NEUTRAL] #SP+ #SP- @NMale @L_TEST_KEY_+n @T\"fallback\""
    )
    tokens = format_tokens(syntax)
    required = {
        "%1NAME",
        "%11GG",
        "%14SN",
        "%gold_icon%",
        "%measure:LevelUpCity:name%",
        "$C[1,2,3,255]",
        "$[ornament$]",
        "#SP+",
        "@NMale",
        "@L_TEST_KEY_+n",
    }
    if not required.issubset(tokens):
        raise AssertionError("Guild 2 format grammar did not recognize all core token forms")
    colors = format_tokens("$C[255,0,0] $C[115, 5,20] $C[255,90,90,255]")
    if len(colors) != 3:
        raise AssertionError("RGB/RGBA color directives with optional whitespace were not recognized")
    plural = format_tokens("The %1DNs disagree")
    if plural != {"%1DN": 1}:
        raise AssertionError("plural suffix after a dynasty placeholder was parsed as an invalid token")
    decoration = format_tokens("$[ ($] $[ ornament $] $[ $(")
    if sum(count for token, count in decoration.items() if token.startswith("$[")) != 3:
        raise AssertionError("ornamental bracket syntax was not recognized robustly")
    decoration_issues = validate_translation("$[ ($] Label %1n", "Label %1n", dbt_field=True)
    if any(issue.code in {"format-missing", "format-extra", "unknown-format"} for issue in decoration_issues):
        raise AssertionError("ornamental bracket syntax produced a format false positive")
    literal_dollars = validate_translation("$A $( $? $foo", "plain", dbt_field=True)
    if any(issue.code == "unknown-format" for issue in literal_dollars):
        raise AssertionError("literal dollar escapes should not produce unknown-format warnings")
    tooltip_macros = validate_translation(
        "%gold_icon%%n%%char_name% @NMale",
        "%gold_icon%%n%%char_name% @NMale",
        dbt_field=True,
    )
    if any(issue.code == "unknown-format" for issue in tooltip_macros):
        raise AssertionError("tooltip macros or @N gender tags were not recognized")
    guide_tip = validate_translation(
        "<text>\nCombat in the world is dangerous. Defend your {tip:CART}carts{/tip}.\n</text>",
        "<text>\n战斗很危险。保护你的{TIP : CART }货车{/ TIP}。\n</text>",
        dbt_field=False,
    )
    if not any(issue.code in {"format-missing", "format-extra"} for issue in guide_tip):
        raise AssertionError("guide tip tags should remain case-sensitive and spacing-sensitive in txt files")
    literal_percent = validate_translation(
        "Weak beer has 3-6% of alcohol and costs 50%.",
        "淡啤酒酒精度为 3-6%，价格是 50%。",
        dbt_field=True,
    )
    if any(issue.code == "unknown-format" for issue in literal_percent):
        raise AssertionError("literal percentage signs produced false unknown-format warnings")
    decorated_percent = validate_translation(
        "Prerequisites: The title %$C[225,214,158]Commoner%$C[255,255,255]",
        "Prerequisites: The title %$C[225,214,158]Commoner%$C[255,255,255]",
        dbt_field=True,
    )
    if any(issue.code == "unknown-format" for issue in decorated_percent):
        raise AssertionError("literal percent wrappers around color markup produced false warnings")
    glued_argument = validate_translation("%2NAMEwe confirm with this", "%2NAMEwe confirm with this", dbt_field=True)
    if any(issue.code == "unknown-format" for issue in glued_argument):
        raise AssertionError("argument placeholders glued to following text produced false unknown-format warnings")
    glued_building = validate_translation("Building %2GG6小时", "Building %2GG6小时", dbt_field=True)
    if any(issue.code == "unknown-format" for issue in glued_building):
        raise AssertionError("building placeholders glued to following digits produced false unknown-format warnings")
    percent_equivalence = validate_translation("%1i%%", "%1i%", dbt_field=True)
    if percent_equivalence:
        raise AssertionError("single and double percent signs were not treated as equivalent literal percent markup")
    gender_case = validate_translation("@Nmale", "@NMale", dbt_field=True)
    if gender_case:
        raise AssertionError("@N gender suffix comparison should be case-insensitive")
    gender_typo = validate_translation("@Nmal", "@NMale", dbt_field=True)
    if gender_typo:
        raise AssertionError("@N gender suffix typo repair should not produce a false warning")
    gender_missing = validate_translation("@NMale", "", dbt_field=True)
    if not any("@NMale" in issue.message for issue in gender_missing):
        raise AssertionError("missing gender suffix should still produce a warning")
    false_tab = validate_translation("Damage.$The cure is rest.", "Damage. The cure is rest.", dbt_field=True)
    if any("$T" in issue.message for issue in false_tab):
        raise AssertionError("embedded $T in plain text was misread as a layout token")
    source_fix = validate_translation("%1NAE", "%1NAME", dbt_field=True)
    if any(issue.code in {"argument-index", "format-extra", "unknown-format"} for issue in source_fix):
        raise AssertionError("repairing a malformed source placeholder still produced a false-positive warning")
    if not any(issue.code == "source-format-suspect" for issue in source_fix):
        raise AssertionError("repairing a malformed source placeholder should leave a lightweight source-format marker")
    source_drop = validate_translation("Rate %A", "Rate", dbt_field=True)
    if any(issue.code == "unknown-format" for issue in source_drop):
        raise AssertionError("dropping an invalid source-only marker should not create an unknown-format warning")
    if not any(issue.code == "source-format-suspect" for issue in source_drop):
        raise AssertionError("dropping an invalid source-only marker should leave a lightweight source-format marker")
    color_spacing = normalize_color_token_spacing(
        "$C[1,2,3]开头甲$C[4,5,6]乙，$C[7,8,9]丙#E[NT_NEUTRAL]$C[10,11,12]丁测试$N$N$C[13,14,15]戊"
    )
    if color_spacing != "$C[1,2,3]开头甲 $C[4,5,6]乙， $C[7,8,9]丙 #E[NT_NEUTRAL]$C[10,11,12]丁测试 $N$N$C[13,14,15]戊":
        raise AssertionError("save-time color-token spacing normalization did not respect its exceptions")
    color_spacing_at_start = normalize_color_token_spacing("$N$N$C[13,14,15]句首")
    if color_spacing_at_start != "$N$N$C[13,14,15]句首":
        raise AssertionError("save-time color-token spacing normalization should not insert before a token run at line start")
    if any(issue.blocks_save for issue in validate_translation(syntax, syntax, dbt_field=False)):
        raise AssertionError("valid Guild 2 syntax was rejected")
    compatible = validate_translation("Name: %1SN", "姓名：%1SV", dbt_field=True)
    if any(issue.blocks_save for issue in compatible) or not any(issue.code == "argument-variant" for issue in compatible):
        raise AssertionError("SN/SV compatible character-name variant was not accepted")
    wrong_index = validate_translation("Name: %1SN", "姓名：%2SN", dbt_field=True)
    if any(issue.blocks_save for issue in wrong_index) or not any(issue.code == "argument-index" for issue in wrong_index):
        raise AssertionError("invalid argument index was not retained as a non-blocking warning")
    wrong_type = validate_translation("Name: %1SN", "数值：%1n", dbt_field=True)
    if any(issue.blocks_save for issue in wrong_type) or not any(issue.code == "argument-type" for issue in wrong_type):
        raise AssertionError("incompatible argument type was not retained as a non-blocking warning")
    unknown = validate_translation("Plain text", "未知 %A", dbt_field=True)
    if any(issue.blocks_save for issue in unknown) or not any(issue.code == "unknown-format" for issue in unknown):
        raise AssertionError("unknown format token was not reduced to a non-blocking warning")


class FakeStreamingTransport:
    def get_json(self, url: str):
        raise AssertionError("LLM suggestion must not issue a GET request")

    def post_json(self, url: str, payload, headers):
        raise AssertionError("stream-capable transport should use SSE")

    def post_sse(self, url: str, payload, headers):
        if not payload.get("stream"):
            raise AssertionError("LLM suggestion did not request streaming")
        yield {"choices": [{"delta": {"content": "推荐译文：测试"}}]}
        yield {"choices": [{"delta": {"content": "\n说明：保留 %1s"}}]}


def assert_llm_suggestion_stream() -> None:
    provider = OpenAICompatibleProvider(
        "https://example.invalid/v1", "test-model", "test-key", FakeStreamingTransport()
    )
    response = "".join(provider.stream_suggestion("Hello %1s", ""))
    if "推荐译文：测试" not in response or "说明：保留 %1s" not in response:
        raise AssertionError("LLM suggestion stream was not assembled correctly")


def assert_operation_history() -> None:
    values = {"first": ("旧一", False), "second": ("旧二", False)}
    history = OperationHistory()
    history.push(TranslationOperation("连续编辑", (UnitChange("first", "旧一", "新一"),)))
    values["first"] = ("新一", False)
    history.push(
        TranslationOperation(
            "AI 批量翻译",
            (UnitChange("first", "新一", "AI 一"), UnitChange("second", "旧二", "AI 二")),
        )
    )
    values.update({"first": ("AI 一", False), "second": ("AI 二", False)})
    history.push(TranslationOperation("标记删除", (UnitChange("second", "AI 二", "AI 二", False, True),)))
    values["second"] = ("AI 二", True)
    history.undo(lambda uid, text, deleted: values.__setitem__(uid, (text, deleted)))
    if values != {"first": ("AI 一", False), "second": ("AI 二", False)}:
        raise AssertionError("delete-mark undo did not restore the previous delete state")
    history.undo(lambda uid, text, deleted: values.__setitem__(uid, (text, deleted)))
    if values != {"first": ("新一", False), "second": ("旧二", False)}:
        raise AssertionError("batch undo did not restore exactly one whole operation")
    history.undo(lambda uid, text, deleted: values.__setitem__(uid, (text, deleted)))
    if values["first"] != ("旧一", False) or values["second"] != ("旧二", False):
        raise AssertionError("undo crossed or missed a translation unit")
    history.redo(lambda uid, text, deleted: values.__setitem__(uid, (text, deleted)))
    if values["first"] != ("新一", False):
        raise AssertionError("redo did not restore the expected operation")
    history.redo(lambda uid, text, deleted: values.__setitem__(uid, (text, deleted)))
    if values != {"first": ("AI 一", False), "second": ("AI 二", False)}:
        raise AssertionError("redo did not restore the batch translation")
    history.redo(lambda uid, text, deleted: values.__setitem__(uid, (text, deleted)))
    if values["second"] != ("AI 二", True):
        raise AssertionError("redo did not restore the delete mark")


def assert_git_history(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_git_")
    try:
        git = LanguageGit(temp)
        git.ensure_repository(AppSettings())
        project = Project.load(temp, "#chinese")
        unit = next(item for item in project.units if item.file_rel == "Text.dbt" and item.filter_status() == STATUS_TRANSLATED)
        unit.set_text(unit.current_text + "测试")
        result = project.save([unit])
        commit = git.commit_saved(result.changed_files, result.saved_units, result.deleted_units)
        if commit is None:
            raise AssertionError("Git commit was not created after saving")
        entries = git.entries_for_commit(commit.full_hash)
        if not entries or entries[0].translated_text != unit.current_text:
            raise AssertionError("Git history did not decode the saved translation entry")
        rendered = format_entries(entries)
        if "→" not in rendered or "Text.dbt" not in rendered:
            raise AssertionError("Git history is not rendering original-to-translation output")

        deleted_text = unit.current_text
        reloaded = Project.load(temp, "#chinese")
        deleted_unit = next(item for item in reloaded.units if item.uid == unit.uid)
        deleted_unit.set_pending_delete(True)
        deleted_result = reloaded.save([deleted_unit])
        delete_commit = git.commit_saved(
            deleted_result.changed_files, deleted_result.saved_units, deleted_result.deleted_units
        )
        if delete_commit is None:
            raise AssertionError("Git delete commit was not created after saving")
        delete_entries = git.entries_for_commit(delete_commit.full_hash)
        if not delete_entries or delete_entries[0].kind != "删除":
            raise AssertionError("Git history did not report the deleted translation entry")
        if delete_entries[0].previous_text != deleted_text or delete_entries[0].translated_text != "":
            raise AssertionError("Git delete history did not preserve the removed translation text")
        delete_rendered = format_entries(delete_entries)
        if translate("history.formatted_entry.deleted") not in delete_rendered:
            raise AssertionError("Git history text output did not label deleted entries")
    finally:
        safe_rmtree(temp)


def assert_git_subprocess_hides_console() -> None:
    kwargs = LanguageGit._subprocess_kwargs(text=True)
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        if kwargs.get("creationflags") != subprocess.CREATE_NO_WINDOW:
            raise AssertionError("Git subprocesses did not request CREATE_NO_WINDOW on Windows")
        if "startupinfo" not in kwargs:
            raise AssertionError("Git subprocesses did not provide hidden-window startup info on Windows")


def assert_git_pending_is_scoped_to_active_language(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_git_scope_")
    git = LanguageGit(temp)
    git.ensure_repository(AppSettings())
    source_path = temp / "languages" / "Text.dbt"
    source_path.write_bytes(source_path.read_bytes() + b"\n")
    if git.has_pending_changes():
        raise AssertionError("source-language changes must not show as pending translation commits")
    target_path = temp / "languages" / "#chinese" / "Text.dbt"
    target_path.write_bytes(target_path.read_bytes() + b"\n")
    if not git.has_pending_changes():
        raise AssertionError("active-language changes were not detected as pending")
    safe_rmtree(temp)


def assert_git_history_list_is_scoped_to_active_language(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_git_history_scope_")
    try:
        git_chinese = LanguageGit(temp, "#chinese")
        git_chinese.ensure_repository(AppSettings())

        chinese_target = temp / "languages" / "#chinese" / "Text.dbt"
        chinese_target.write_bytes(chinese_target.read_bytes() + b"\n")
        chinese_commit = git_chinese.commit_pending()
        if chinese_commit is None:
            raise AssertionError("Chinese history scope test did not create a pending-language commit")

        korean_root = temp / "languages" / "#korean"
        korean_root.mkdir(parents=True, exist_ok=True)
        korean_target = korean_root / "Text.dbt"
        korean_target.write_bytes((temp / "languages" / "Text.dbt").read_bytes())
        git_korean = LanguageGit(temp, "#korean")
        korean_commit = git_korean.commit_pending()
        if korean_commit is None:
            raise AssertionError("Korean history scope test did not create a pending-language commit")

        chinese_hashes = {commit.full_hash for commit in git_chinese.list_commits()}
        korean_hashes = {commit.full_hash for commit in git_korean.list_commits()}
        if chinese_commit.full_hash not in chinese_hashes:
            raise AssertionError("Chinese history list did not include the active Chinese commit")
        if korean_commit.full_hash in chinese_hashes:
            raise AssertionError("Chinese history list unexpectedly included a Korean-only commit")
        if korean_commit.full_hash not in korean_hashes:
            raise AssertionError("Korean history list did not include the active Korean commit")
        if chinese_commit.full_hash in korean_hashes:
            raise AssertionError("Korean history list unexpectedly included a Chinese-only commit")
    finally:
        safe_rmtree(temp)


def assert_git_recovers_stale_index_lock(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_git_lock_")
    try:
        git = LanguageGit(temp)
        git.ensure_repository(AppSettings())
        target = temp / "languages" / "#chinese" / "Text.dbt"
        target.write_bytes(target.read_bytes() + b"\n")
        lock = temp / "languages" / ".git" / "index.lock"
        lock.write_bytes(b"")
        stale = time.time() - LanguageGit.STALE_INDEX_LOCK_SECONDS - 1
        os.utime(lock, (stale, stale))
        if git.commit_pending() is None:
            raise AssertionError("stale Git index lock was not recovered for pending commit")
        if lock.exists():
            raise AssertionError("stale Git index lock was not removed")
    finally:
        safe_rmtree(temp)


def assert_combined_git_history_format() -> None:
    early = TranslationLogEntry("新增", "Text.dbt", "10", "Greeting", "Text", "Hello", "你好")
    later = TranslationLogEntry("更新", "Text.dbt", "10", "Greeting", "Text", "Hello", "您好", "你好")
    other = TranslationLogEntry("新增", "Tooltips.dbt", "2", "Tip", "Text", "Save", "保存")
    combined = combine_entries(((early, other), (later,)))
    by_label = {entry.label: entry for entry in combined}
    greeting = by_label.get("Greeting")
    if greeting is None or greeting.kind != "新增" or greeting.translated_text != "您好" or greeting.previous_text is not None:
        raise AssertionError("combined history did not keep the net add-result across several commits")
    revised_early = TranslationLogEntry("更新", "Text.dbt", "11", "Farewell", "Text", "Bye", "再见", "拜拜")
    revised_later = TranslationLogEntry("更新", "Text.dbt", "11", "Farewell", "Text", "Bye", "回头见", "再见")
    merged_update = combine_entries(((revised_early,), (revised_later,)))
    if len(merged_update) != 1 or merged_update[0].kind != "更新":
        raise AssertionError("combined history lost a net update")
    if merged_update[0].before_text != "拜拜" or merged_update[0].translated_text != "回头见":
        raise AssertionError("combined history did not preserve the earliest old text and the latest new text")
    reverted = TranslationLogEntry("更新", "Text.dbt", "10", "Greeting", "Text", "Hello", "Hello", "您好")
    if combine_entries(((early,), (reverted,))):
        raise AssertionError("combined history kept an entry whose final translation reverted to the starting text")
    rendered = format_entries(combined)
    if rendered.count("Text.dbt") != 1 or rendered.count("Tooltips.dbt") != 1:
        raise AssertionError("history format repeated a file heading")
    if "Hello → 您好" not in rendered:
        raise AssertionError("history format did not render the final translation")


def assert_git_commit_display() -> None:
    timestamp = datetime.fromtimestamp(1_700_000_000)
    commit = GitCommit("a" * 40, "abcdef1", timestamp, "translation: add 3, update 2 (Text.dbt, Tooltips.dbt)")
    display = commit.display
    if "translation:" in display:
        raise AssertionError("commit list display should not expose the raw translation prefix")
    if (
        translate("history.change.add", count=3) not in display
        or translate("history.change.update", count=2) not in display
        or "Text.dbt, Tooltips.dbt" not in display
    ):
        raise AssertionError("commit list display did not summarize translation commits correctly")
    delete_commit = GitCommit("c" * 40, "89abcde", timestamp, "translation: delete 4 (Text.dbt)")
    if translate("history.change.delete", count=4) not in delete_commit.display or "Text.dbt" not in delete_commit.display:
        raise AssertionError("delete-only translation commits were not summarized correctly")
    pending = GitCommit("b" * 40, "1234567", timestamp, "translation: commit pending language changes")
    if translate("history.subject.pending") not in pending.display:
        raise AssertionError("pending translation commit display was not simplified")


class CaptureStreamingTransport:
    def __init__(self) -> None:
        self.payload = None

    def get_json(self, url: str):
        raise AssertionError("LLM suggestion must not issue a GET request")

    def post_json(self, url: str, payload, headers):
        self.payload = payload
        return {"choices": [{"message": {"content": "ok"}}]}


def assert_llm_suggestion_context_prompt() -> None:
    transport = CaptureStreamingTransport()
    provider = OpenAICompatibleProvider("https://example.invalid/v1", "test-model", "test-key", transport)
    context = LlmSuggestionContext(
        file_rel="Text.dbt",
        record_id="100",
        label="OfficeTitle",
        neighbors=(
            LlmNeighborContext("前1条", "OfficeDesc", "The office of the town clerk.", "99"),
            LlmNeighborContext("后1条", "OfficeButton", "Open the office.", "101"),
        ),
    )
    response = "".join(provider.stream_suggestion_with_context("Town Clerk", "", context))
    if response != "ok":
        raise AssertionError("context fallback response was not returned")
    payload = transport.payload
    if payload is None:
        raise AssertionError("context suggestion did not issue a request")
    prompt = payload["messages"][1]["content"]
    for snippet in ("Label：OfficeTitle", "前1条", "OfficeDesc", "The office of the town clerk.", "后1条", "OfficeButton"):
        if snippet not in prompt:
            raise AssertionError(f"LLM suggestion prompt missed context snippet: {snippet}")


def main() -> int:
    root = project_root()
    assert_codec(root)
    assert_font_glyph_validation(root)
    assert_round_trip(root)
    assert_statuses(root)
    assert_loaded_order_matches_file_lines(root)
    assert_local_project_roots_detect_sources_projects()
    assert_discover_game_source_projects_detects_vanilla_and_mods()
    assert_startup_prefers_local_sources_over_game_root()
    assert_sync_vanilla_sources_only_imports_originals()
    assert_sync_source_project_invalidates_changed_translations(root)
    assert_save_existing(root)
    assert_save_auto_formats_color_tokens(root)
    assert_save_guides_plain_text_uses_source_profile(root)
    assert_save_creates_missing_target_dbt_incrementally(root)
    assert_save_removes_extra_target_row(root)
    assert_save_missing(root)
    assert_missing_insertions_follow_file_order(root)
    assert_unsaved_translation_status(root)
    assert_mod_label_match_inserts_source_formatted_row(root)
    assert_project_history_settings(root)
    assert_git_binding_tracks_project_root()
    assert_language_combo_offers_create_action()
    assert_bundled_settings_are_isolated_by_location()
    assert_editor_undo_stays_local(root)
    assert_ui_language_switching()
    assert_external_project_uses_tool_codec(root)
    assert_packaged_runtime_finds_sibling_codec(root)
    assert_non_chinese_language_bypasses_codec(root)
    assert_chinese_without_codec_uses_plain_text(root)
    assert_chinese_setting_can_disable_codec(root)
    assert_validation_warnings_do_not_block()
    assert_ignore_cache(root)
    assert_source_review_cache(root)
    assert_operation_history()
    assert_ai_token_protection()
    assert_linebreak_format_is_ignored()
    assert_guild2_format_grammar()
    assert_llm_suggestion_stream()
    assert_llm_suggestion_context_prompt()
    assert_git_history(root)
    assert_git_subprocess_hides_console()
    assert_git_commit_display()
    assert_git_pending_is_scoped_to_active_language(root)
    assert_git_history_list_is_scoped_to_active_language(root)
    assert_git_recovers_stale_index_lock(root)
    assert_combined_git_history_format()
    print("translator_tool self-test ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
