from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from types import SimpleNamespace
import uuid

from . import project as project_module
from . import cache as cache_module
from . import recovery as recovery_module
from . import file_utils as file_utils_module
from . import source_sync as source_sync_module
from . import settings as settings_module
from .ai import (
    GoogleTranslateProvider,
    LlmNeighborContext,
    LlmSuggestionContext,
    OpenAICompatibleProvider,
    TranslationProviderError,
    build_llm_contexts,
)
from .cache import cache_path, confirmed_uids, need_work_uids, source_review_uids
from .code_index import CodeReference, CodeReferenceIndex, build_code_reference_index
from .code_window_context import (
    DARK_PANEL_TEXT,
    PARCHMENT_TEXT,
    PreviewWindowContext,
    best_window_context,
    engine_window_context,
)
from .codec_adapter import Guild2Codec, load_codec_for_language
from .git_history import GitCommit, GitError, LanguageGit, TranslationLogEntry, combine_entries, format_entries
from .history import OperationHistory, TranslationOperation, UnitChange
from .self_tests.project_refresh import assert_saved_file_refresh
from .self_tests.git_commit import assert_tracked_git_commit_skips_redundant_add
from .self_tests.diagnostics import assert_diagnostics_are_bounded_and_content_free
from .self_tests.performance import AI_CONTEXT_BUILD_LIMIT_SECONDS, assert_within_budget
from .i18n import set_language, status_text, translate
from .format_io import load_dbt, load_plain_text, matching_source_field, row_key
from .gui_semantics import gui_resource_info
from .preview import GLYPH_MARK, PreviewAtom, PreviewDocument, PreviewService
from .recovery import apply_recovery_draft, clear_recovery_draft, load_recovery_draft, recovery_path, save_recovery_draft
from .project import (
    MISSING_WORK_STATUSES,
    Project,
    SaveValidationError,
    STATUS_EXTRA,
    STATUS_IGNORED,
    STATUS_PENDING_DELETE,
    STATUS_REVIEW,
    STATUS_TODO,
    STATUS_TRANSLATED,
    TODO_REASON_EMPTY,
    TODO_REASON_IMPORT_REVIEW,
    TODO_REASON_MANUAL_REVIEW,
    TODO_REASON_MISSING_ROW,
    TODO_REASON_SAME_AS_SOURCE,
    TODO_REASON_SOURCE_CHANGED,
    TranslationUnit,
    UnitRef,
)
from .settings import AppSettings, load_settings, save_settings
from .self_tests.clipboard import assert_entry_clipboard_decoder
from .self_tests.code_semantics import (
    assert_code_index_handles_families_and_binary_gui,
    assert_cross_file_function_summaries_bind_arguments_and_expand_returns,
    assert_cross_file_function_summaries_follow_nested_dependencies,
    assert_cross_file_return_labels_flow_only_to_real_callers,
    assert_display_call_contracts_match_engine_signatures,
    assert_code_semantics_follow_fields_panels_and_initdata,
    assert_feedback_message_contracts_do_not_depend_on_label_names,
    assert_dynamic_table_and_engine_label_semantics_are_preserved,
    assert_code_semantics_are_scope_and_role_aware,
    assert_code_semantics_resolve_local_function_returns,
    assert_placeholder_values_avoid_ambiguous_random_branches,
    assert_placeholder_reference_selection_is_coherent,
    assert_variadic_runtime_arguments_map_to_placeholder_positions,
)
from .self_tests.code_index_lazy import (
    assert_code_index_requests_selected_and_visible_rows_without_moving_viewport,
    assert_lazy_code_index_loads_cached_value_providers,
    assert_lazy_code_index_links_cached_cross_file_facts,
    assert_lazy_code_index_prioritizes_requested_labels_and_invalidates_cache,
    assert_lazy_code_index_survives_unwritable_cache,
)
from .self_tests.performance import (
    LARGE_BATCH_MIN_ENTRIES,
    LARGE_BATCH_SAVE_LIMIT_SECONDS,
    assert_within_budget,
)
from .self_tests.preview_context_selection import (
    assert_game_preview_parts_use_the_selected_call_site,
    assert_preview_context_selection_keeps_arguments_and_style_coherent,
    assert_preview_context_selection_keeps_the_current_dynamic_branch,
    assert_preview_context_selection_prefers_displayed_runtime_labels,
    assert_preview_context_selection_understands_returned_label_roles,
)
from .self_tests.preview_localization import (
    assert_editor_changes_reach_preview_localization,
    assert_project_localization_updates_invalidate_placeholder_previews,
)
from .source_sync import (
    discover_game_source_projects,
    local_project_roots,
    managed_vanilla_project_root,
    sync_source_project,
    sync_vanilla_sources,
)
from .validation import (
    FORMAT_GUIDE,
    FORMAT_TOOLTIP,
    ValidationIssue,
    format_tokens,
    normalize_color_token_spacing,
    validate_translation,
)


def tool_root() -> Path:
    return Path(__file__).resolve().parents[1]


def project_root() -> Path:
    root = tool_root()
    if (root / "languages").is_dir():
        return root
    sources = root / "sources"
    if sources.is_dir():
        candidates = [sources / "Vanilla", *sorted(sources.iterdir())]
        for candidate in candidates:
            languages = candidate / "languages"
            if all(
                path.is_file()
                for path in (
                    languages / "Text.dbt",
                    languages / "Tooltips.dbt",
                    languages / "#chinese" / "Text.dbt",
                    languages / "#chinese" / "Tooltips.dbt",
                )
            ):
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
        if unit.filter_status() not in {STATUS_TODO, STATUS_REVIEW, STATUS_TRANSLATED, STATUS_EXTRA, STATUS_IGNORED}
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


def remove_matching_target_rows(root: Path, file_name: str, count: int) -> None:
    source_doc = load_dbt(root / "languages" / file_name)
    target_path = root / "languages" / "#chinese" / file_name
    target_doc = load_dbt(target_path)
    source_index = source_doc.row_index
    rows = [row for row in target_doc.rows if row_key(file_name, row) in source_index][:count]
    if len(rows) != count:
        raise AssertionError(f"{file_name} fixture does not contain {count} matching target rows")
    text = target_doc.text
    for row in rows:
        text = text.replace(row.original_line, "", 1)
    target_path.write_bytes(text.encode(target_doc.profile.encoding))


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


def assert_code_reference_index_avoids_db_and_uses_vanilla_fallback() -> None:
    temp = Path(tempfile.gettempdir()) / f"translator_tool_smoke_code_refs_{uuid.uuid4().hex[:8]}"
    try:
        game_root = temp / "game"
        (game_root / "Scripts").mkdir(parents=True, exist_ok=True)
        (game_root / "GUI" / "Hud").mkdir(parents=True, exist_ok=True)
        (game_root / "DB" / "Languages").mkdir(parents=True, exist_ok=True)
        (game_root / "Scripts" / "Mission.lua").write_text(
            'MsgBox("Actor", nil, "@L_TRIAL_REMINDER_HEAD", "@L_TRIAL_REMINDER_BODY", var1)\n',
            encoding="utf-8",
        )
        (game_root / "Scripts" / "Dynamic.lua").write_text(
            "\n".join(
                (
                    'MsgQuick("", "@L_DYNAMIC_BRANCH_+"..choice)',
                    'MsgQuick("", "@L_THIEF_066_BURGLEAHOUSE_FAILURES_+3")',
                    'MsgBoxNoWait("All", nil, "@L_WAR_END_LOOSE_HEAD_+0", "@L_WAR_END_LOOSE_BODY_+0", "@L_SCENARIO_LORD_"..enemy.."_+1")',
                )
            ),
            encoding="utf-8",
        )
        (game_root / "Scripts" / "Multiline.lua").write_text(
            "\n".join(
                (
                    'MsgBox("Actor",',
                    '    helper("comma, inside"),',
                    '    "@L_MULTILINE_HEAD_+0",',
                    '    "@L_MULTILINE_BODY_+0",',
                    '    GetID("Actor"))',
                )
            ),
            encoding="utf-8",
        )
        (game_root / "GUI" / "Hud" / "Panel.gui").write_text(
            'SetText("@L_GUI_ONLY_+0", citylabel)\n',
            encoding="utf-8",
        )
        (game_root / "DB" / "Languages" / "Text.dbt").write_text(
            'MsgBox("Actor", nil, "@L_SHOULD_NOT_BE_SCANNED")\n',
            encoding="utf-8",
        )
        vanilla_project = temp / "sources" / "Vanilla"
        vanilla_project.mkdir(parents=True, exist_ok=True)
        vanilla_index = build_code_reference_index(game_root, vanilla_project)
        vanilla_refs = vanilla_index.references_for("TRIAL_REMINDER_HEAD")
        if vanilla_refs.project_count != 1 or vanilla_refs.project[0].call_name != "MsgBox":
            raise AssertionError("vanilla code reference was not indexed from Scripts")
        if vanilla_index.references_for("SHOULD_NOT_BE_SCANNED").project_count:
            raise AssertionError("code reference index scanned DB unexpectedly")
        dynamic_refs = vanilla_index.references_for("DYNAMIC_BRANCH_+1")
        if dynamic_refs.project_count != 1 or dynamic_refs.project[0].path.name != "Dynamic.lua":
            raise AssertionError("dynamic _+n code reference fallback was not indexed")
        static_mismatch_refs = vanilla_index.references_for("THIEF_066_BURGLEAHOUSE_FAILURES_+1")
        if static_mismatch_refs.project_count:
            raise AssertionError("a fixed _+3 label was treated as a dynamic _+n reference")
        static_exact_refs = vanilla_index.references_for("THIEF_066_BURGLEAHOUSE_FAILURES_+3")
        if static_exact_refs.project_count != 1 or static_exact_refs.project[0].path.name != "Dynamic.lua":
            raise AssertionError("fixed _+n code reference was not indexed exactly")
        concatenated_refs = vanilla_index.references_for("SCENARIO_LORD_ENEMY_+1")
        if concatenated_refs.project_count != 1 or concatenated_refs.project[0].path.name != "Dynamic.lua":
            raise AssertionError("concatenated dynamic label code reference fallback was not indexed")
        underscore_refs = vanilla_index.references_for("_TRIAL_REMINDER_HEAD")
        if underscore_refs.project_count != 1 or underscore_refs.project[0].path.name != "Mission.lua":
            raise AssertionError("leading underscore label fallback was not indexed")
        multiline_refs = vanilla_index.references_for("MULTILINE_BODY_+0")
        if multiline_refs.project_count != 1:
            raise AssertionError("multiline code reference was not indexed")
        multiline = multiline_refs.project[0]
        if multiline.call_name != "MsgBox" or multiline.argument_index != 3:
            raise AssertionError(f"multiline call context was wrong: {multiline!r}")
        if len(multiline.arguments) < 5 or multiline.arguments[4] != 'GetID("Actor")':
            raise AssertionError(f"multiline argument expressions were not captured: {multiline.arguments!r}")
        gui_refs = vanilla_index.references_for("GUI_ONLY_+0")
        if gui_refs.project_count != 1 or gui_refs.project[0].path.suffix.casefold() != ".gui":
            raise AssertionError("GUI code reference was not indexed")

        mod_project = temp / "sources" / "Reforged"
        mod_project.mkdir(parents=True, exist_ok=True)
        (game_root / "mods" / "Reforged" / "Scripts").mkdir(parents=True, exist_ok=True)
        (game_root / "mods" / "Reforged" / "GUI").mkdir(parents=True, exist_ok=True)
        (game_root / "mods" / "Reforged" / "Scripts" / "Mod.lua").write_text(
            'MsgQuick("", "@L_MOD_ONLY_+0")\n',
            encoding="utf-8",
        )
        (game_root / "mods" / "Reforged" / "GUI" / "ModPanel.gui").write_text(
            'SetText("@L_MOD_GUI_ONLY_+0")\n',
            encoding="utf-8",
        )
        mod_index = build_code_reference_index(game_root, mod_project)
        if mod_index.references_for("MOD_ONLY_+0").project_count != 1:
            raise AssertionError("mod code reference was not indexed from mod Scripts")
        if mod_index.references_for("MOD_GUI_ONLY_+0").project_count != 1:
            raise AssertionError("mod code reference was not indexed from mod GUI")
        fallback = mod_index.references_for("TRIAL_REMINDER_HEAD")
        if fallback.project_count != 0 or fallback.vanilla_count != 1:
            raise AssertionError("mod code reference index did not keep vanilla fallback")
    finally:
        safe_rmtree(temp)


def assert_stale_code_index_workers_are_released() -> None:
    from . import app as app_module

    stale = SimpleNamespace(token=7)
    window = SimpleNamespace(code_reference_workers=[stale], code_reference_index_token=8)
    app_module.TranslatorWindow._code_reference_index_ready(window, 7, CodeReferenceIndex())
    if window.code_reference_workers:
        raise AssertionError("a completed stale code-index worker remained retained")

    stale = SimpleNamespace(token=9)
    window = SimpleNamespace(code_reference_workers=[stale], code_reference_index_token=10)
    app_module.TranslatorWindow._code_reference_index_failed(window, 9, "ignored")
    if window.code_reference_workers:
        raise AssertionError("a failed stale code-index worker remained retained")


def assert_code_window_context_extracts_window_labels_and_buttons() -> None:
    temp = Path(tempfile.gettempdir()) / f"translator_tool_smoke_code_window_{uuid.uuid4().hex[:8]}"
    try:
        game_root = temp / "game"
        (game_root / "Scripts").mkdir(parents=True, exist_ok=True)
        (game_root / "Scripts" / "Hud").mkdir(parents=True, exist_ok=True)
        (game_root / "GUI" / "Hud").mkdir(parents=True, exist_ok=True)
        (game_root / "Scripts" / "Hud" / "GameHud.lua").write_text(
            "\n".join(
                (
                    'this:AddPanel("SayPanel", 10, "GUI/Hud/CustomSay.gui", false)',
                    'this:AddPanel("CustomSheet", 11, "GUI/Hud/CustomSheet.gui", false)',
                )
            ),
            encoding="utf-8",
        )
        (game_root / "GUI" / "Hud" / "CustomSay.gui").write_bytes(
            b"\x03\x00"
            b"Hud/NoCompression/Priority3/PanelBackground_01.tga\x00"
            b"Hud/borders/Border_Gold_02.tga\x00"
        )
        (game_root / "GUI" / "Hud" / "CustomSheet.gui").write_bytes(
            b"\x03\x00"
            b"Hud/sheets/evidences/bg_buch.tga\x00"
            b"Hud/borders/Border_Gold_02.tga\x00"
        )
        window_script = game_root / "Scripts" / "Window.lua"
        window_script.write_text(
            "\n".join(
                (
                    'MsgBox("","", "@P"..',
                    '    "@B[1,@L_MEASURE_WUERDENTRAGEREMPFANGEN_ASK_+0]"..',
                    '    "@B[2,@L_MEASURE_WUERDENTRAGEREMPFANGEN_ASK_+1]"..',
                    '    "@B[3,@L_MEASURE_WUERDENTRAGEREMPFANGEN_ASK_+2]",',
                    '    "@L_MEASURE_WUERDENTRAGEREMPFANGEN_HEAD_+0",',
                    '    "@L_MEASURE_WUERDENTRAGEREMPFANGEN_BODY_+1",stimmung,ort)',
                    'MsgQuick("", "@L_SHORT_NOTICE_+0", GetID("Owner"))',
                    'local dynamicButtons = ""',
                    'local enemy1 = "@L_SCENARIO_WAR_"..enemy.."_+0"',
                    'local enemy2 = "@L_SCENARIO_WAR_"..enemy.."_+0"',
                    'local enemy3 = "@L_SCENARIO_WAR_"..enemy.."_+0"',
                    'local enemy4 = "@L_SCENARIO_WAR_"..enemy.."_+0"',
                    'dynamicButtons = dynamicButtons.."@B[1,"..enemy1.."]"',
                    'dynamicButtons = dynamicButtons.."@B[2,"..enemy2.."]"',
                    'dynamicButtons = dynamicButtons.."@B[3,"..enemy3.."]"',
                    'dynamicButtons = dynamicButtons.."@B[4,"..enemy4.."]"',
                    'MsgBox("", "", "@P"..dynamicButtons.."@B[5,@L_MEASURE_WUERDENTRAGEREMPFANGEN_NONE_+0]",',
                    '    "@L_MEASURE_WUERDENTRAGEREMPFANGEN_HEAD_+0",',
                    '    "@L_MEASURE_WUERDENTRAGEREMPFANGEN_BODY_+0")',
                    'MsgNewsNoWait("All","","@C[@L_KONTOR_MISSIONS_OFFER_ITEMS_COOLDOWN_+0,%5i,%6l]","economie",-1,',
                    '    "@L_KONTOR_MISSIONS_OFFER_ITEMS_HEAD_+"..random,',
                    '    "@L_KONTOR_MISSIONS_OFFER_ITEMS_TEXT_+"..random,',
                    '    GetID("City"), Offering, ItemLabel, Gametime, DestTime, ID)',
                    'MsgBoxNoWait("","", "@L_MEASURE_SHOWWARFACTORS_HEAD_+0",',
                    '    "@L_MEASURE_SHOWWARFACTORS_BODY_+1",',
                    '    "@L_SCENARIO_WAR_"..land.."_+1", "@L_SCENARIO_WAR_"..enemy.."_+1")',
                    'MsgBox("", "", "@P@B[1,@L_CONTRACTARSENAL_HIRE_OPTION_+0]"..',
                    '    "@B[5,@L_CONTRACTARSENAL_HIRE_OPTION_+1]"..',
                    '    "@B[10,@L_CONTRACTARSENAL_HIRE_OPTION_+2]"..',
                    '    "@B[0,@L_REPLACEMENTS_BUTTONS_CANCEL_+0]",',
                    '    "@L_CONTRACTARSENAL_HIRE_MAIN_HEAD_+0",',
                    '    "@L_CONTRACTARSENAL_HIRE_MAIN_BODY_+1",',
                    '    "_WAR_MERC_"..label.."_MALE_+0","_WAR_MERC_"..label.."_MORE_+0",cost,cost*5,cost*10)',
                    'MsgSayInteraction("","Child","",',
                    '    "@B[0,@L_MEASURE_BUYGOLDRING_OPTION_+0]"..',
                    '    "@B[1,@L_MEASURE_BUYGOLDRING_OPTION_+1]",',
                    '    ms_buygoldring_AIDecide,',
                    '    "@L_MEASURE_BUYGOLDRING_QUESTION_+0",',
                    '    GetID(""), Cost)',
                    'MsgQuest("#Player", 0, "MB_OK",',
                    '    "@L_TUTORIAL_CAMERA_NAME", "@L_TUTORIAL_CAMERA_SUCCESS")',
                    'ShowTutorialBoxNoWait(100, 700, 470, 180, 1, LEFTLOWER_NOARROW,',
                    '    "@L_TUTORIAL_MOVEMENT_NAME", "@L_TUTORIAL_MOVEMENT_TASK", "")',
                    'MsgNewsNoWait("", "", "panel_nobility_title_deed", "intrigue", -1,',
                    '    "@L_CERTIFICATE_HEAD", "@L_CERTIFICATE_BODY")',
                    'SimAddDatebookEntry("accuser", EventTime, "courtbuilding",',
                    '    "@L_TRIAL_DATEBOOK_HEAD_+0",',
                    '    "@L_TRIAL_DATEBOOK_BODY_+0", GetID("accuser"), GetSettlementID(""))',
                    'CityScheduleCutsceneEvent("settlement", "council_date", "",',
                    '    "BeginCouncilMeeting", 17, 6, "@L_COUNCIL_SCHEDULE_+0")',
                    'InitData("SayPanel", 0, "@L_PANEL_HEAD_+0", "@L_PANEL_BODY_+0", Cost)',
                    'this:AddSheetToTabGroup("Diary", "CustomSheet", "@L_CUSTOM_TAB_+0")',
                    'InitAlias("Destination", MEASUREINIT_SELECTION, "", "@L_SELECT_TARGET_+0", 0)',
                    'CreateImportantPersonSection("Family", "@L_IMPORTANT_SECTION_+0")',
                    'BlackBoardAddPamphlet("BlackBoard", "Destination", "@L_PAMPHLET_BODY_+0")',
                )
            ),
            encoding="utf-8",
        )
        project_root = temp / "sources" / "Vanilla"
        project_root.mkdir(parents=True, exist_ok=True)
        index = build_code_reference_index(game_root, project_root)
        window_script.unlink()
        refs = index.references_for("MEASURE_WUERDENTRAGEREMPFANGEN_BODY_+1").project
        context = best_window_context(refs, "MEASURE_WUERDENTRAGEREMPFANGEN_BODY_+1")
        if context is None:
            raise AssertionError("code window context was not built for MsgBox")
        if context.header_label != "measure_wuerdentragerempfangen_head_+0":
            raise AssertionError(f"wrong header label from MsgBox context: {context!r}")
        if context.body_label != "measure_wuerdentragerempfangen_body_+1":
            raise AssertionError(f"wrong body label from MsgBox context: {context!r}")
        if context.call_name != "msgbox":
            raise AssertionError(f"window context did not retain the rendering call: {context!r}")
        if tuple(button.label for button in context.buttons) != (
            "measure_wuerdentragerempfangen_ask_+0",
            "measure_wuerdentragerempfangen_ask_+1",
            "measure_wuerdentragerempfangen_ask_+2",
        ):
            raise AssertionError(f"button labels were not extracted from @B tokens: {context.buttons!r}")
        short_refs = index.references_for("SHORT_NOTICE_+0").project
        short_context = best_window_context(short_refs, "SHORT_NOTICE_+0")
        if short_context is None or short_context.background != "overlay":
            raise AssertionError(f"MsgQuick should use its transparent HUD overlay profile: {short_context!r}")
        if short_context.default_color != DARK_PANEL_TEXT:
            raise AssertionError(f"dark panel default text color should be white: {short_context!r}")
        variable_button_refs = index.references_for("MEASURE_WUERDENTRAGEREMPFANGEN_BODY_+0").project
        variable_button_context = best_window_context(variable_button_refs, "_MEASURE_WUERDENTRAGEREMPFANGEN_BODY_+0")
        if variable_button_context is None:
            raise AssertionError("variable-built MsgBox buttons did not produce a window context")
        if tuple((button.identifier, button.label) for button in variable_button_context.buttons) != (
            ("1", "scenario_war_*_+0"),
            ("2", "scenario_war_*_+0"),
            ("3", "scenario_war_*_+0"),
            ("4", "scenario_war_*_+0"),
            ("5", "measure_wuerdentragerempfangen_none_+0"),
        ):
            raise AssertionError(f"variable-built buttons were not extracted: {variable_button_context.buttons!r}")
        news_refs = index.references_for("KONTOR_MISSIONS_OFFER_ITEMS_TEXT_+2").project
        news_context = best_window_context(news_refs, "KONTOR_MISSIONS_OFFER_ITEMS_TEXT_+2")
        if news_context is None:
            raise AssertionError("code window context was not built for dynamic MsgNewsNoWait labels")
        if news_context.call_name != "msgnewsnowait" or news_context.background != "overlay":
            raise AssertionError(f"MsgNews should retain its HUD entry style: {news_context!r}")
        if news_context.header_label != "kontor_missions_offer_items_head_+2":
            raise AssertionError(f"dynamic MsgNews head should follow the current concrete suffix: {news_context!r}")
        if news_context.body_label != "kontor_missions_offer_items_text_+2":
            raise AssertionError(f"dynamic MsgNews body should ignore cooldown/control labels: {news_context!r}")
        if news_context.category != "economie" or news_context.icon_asset != "Hud/news/economie.tga":
            raise AssertionError(f"MsgNews category did not select its real icon: {news_context!r}")
        argument_refs = index.references_for("SCENARIO_WAR_*_+1").project
        argument_context = best_window_context(argument_refs, "SCENARIO_WAR_*_+1")
        if argument_context is None:
            raise AssertionError("runtime argument labels should reuse the surrounding message window")
        if argument_context.header_label != "measure_showwarfactors_head_+0":
            raise AssertionError(f"runtime argument context used the wrong head label: {argument_context!r}")
        if argument_context.body_label != "measure_showwarfactors_body_+1":
            raise AssertionError(f"runtime argument context used the wrong body label: {argument_context!r}")
        merc_refs = index.references_for("WAR_MERC_TROOPER_MORE_+0").project
        if not merc_refs:
            raise AssertionError("dynamic non-@L DB labels were not indexed")
        merc_context = best_window_context(merc_refs, "_WAR_MERC_TROOPER_MORE_+0")
        if merc_context is None:
            raise AssertionError("dynamic DB label arguments should reuse the surrounding message window")
        if merc_context.body_label != "contractarsenal_hire_main_body_+1":
            raise AssertionError(f"dynamic DB label context used the wrong body label: {merc_context!r}")
        interaction_refs = index.references_for("MEASURE_BUYGOLDRING_OPTION_+0").project
        interaction_context = best_window_context(interaction_refs, "MEASURE_BUYGOLDRING_OPTION_+0")
        if interaction_context is None:
            raise AssertionError("MsgSayInteraction context was not built for button labels")
        if interaction_context.header_label:
            raise AssertionError(f"MsgSayInteraction AI callback was treated as a title: {interaction_context!r}")
        if interaction_context.body_label != "measure_buygoldring_question_+0":
            raise AssertionError(f"MsgSayInteraction body label was not extracted: {interaction_context!r}")
        if interaction_context.speaker != "Child" or interaction_context.panel == "":
            raise AssertionError(f"dialog presentation arguments were not retained: {interaction_context!r}")
        quest_refs = index.references_for("TUTORIAL_CAMERA_SUCCESS").project
        quest_context = best_window_context(quest_refs, "TUTORIAL_CAMERA_SUCCESS")
        if (
            quest_context is None
            or quest_context.header_label != "tutorial_camera_name"
            or quest_context.body_label != "tutorial_camera_success"
            or quest_context.surface != "questbox"
        ):
            raise AssertionError(f"MsgQuest used shifted title/body arguments: {quest_context!r}")
        tutorial_refs = index.references_for("TUTORIAL_MOVEMENT_TASK").project
        tutorial_context = best_window_context(tutorial_refs, "TUTORIAL_MOVEMENT_TASK")
        if (
            tutorial_context is None
            or tutorial_context.header_label != "tutorial_movement_name"
            or tutorial_context.body_label != "tutorial_movement_task"
            or tutorial_context.surface != "tutorial"
        ):
            raise AssertionError(
                f"ShowTutorialBoxNoWait used coordinate arguments as labels: {tutorial_context!r}"
            )
        certificate_refs = index.references_for("CERTIFICATE_BODY").project
        certificate_context = best_window_context(certificate_refs, "CERTIFICATE_BODY")
        if (
            certificate_context is None
            or certificate_context.kind != "document"
            or certificate_context.layout != "document"
            or certificate_context.background_asset != "Hud/messagebox/mbback1.tga"
        ):
            raise AssertionError(
                f"news panel parameter did not select the title-deed document: {certificate_context!r}"
            )
        datebook_refs = index.references_for("TRIAL_DATEBOOK_BODY_+0").project
        datebook_context = best_window_context(datebook_refs, "TRIAL_DATEBOOK_BODY_+0")
        if datebook_context is None or datebook_context.kind != "datebook":
            raise AssertionError(f"datebook entry did not receive its own preview style: {datebook_context!r}")
        if datebook_context.header_label != "trial_datebook_head_+0":
            raise AssertionError(f"datebook title was not paired with its body: {datebook_context!r}")
        if datebook_context.body_label != "trial_datebook_body_+0":
            raise AssertionError(f"datebook body was not retained: {datebook_context!r}")
        if (
            datebook_context.layout != "book"
            or datebook_context.background_asset != "Hud/sheets/evidences/bg_buch.tga"
            or datebook_context.icon_asset
        ):
            raise AssertionError(f"datebook entry did not use the real book sheet: {datebook_context!r}")
        schedule_refs = index.references_for("COUNCIL_SCHEDULE_+0").project
        schedule_context = best_window_context(schedule_refs, "COUNCIL_SCHEDULE_+0")
        if schedule_context is None or schedule_context.surface != "city_schedule":
            raise AssertionError(
                f"city schedule text was collapsed into another presentation: {schedule_context!r}"
            )
        if (
            schedule_context.gui_resource != "GUI/Hud/panel_cityschedule.gui"
            or schedule_context.layout != "panel"
        ):
            raise AssertionError(f"city schedule did not retain its own GUI profile: {schedule_context!r}")
        panel_refs = index.references_for("PANEL_BODY_+0").project
        panel_context = best_window_context(panel_refs, "PANEL_BODY_+0")
        if (
            panel_context is None
            or panel_context.surface != "measure_choice"
            or panel_context.gui_resource != "GUI/Hud/CustomSay.gui"
            or panel_context.background_asset
            != "Hud/NoCompression/Priority3/PanelBackground_01.tga"
            or panel_context.frame_asset != "Hud/borders/Border_Gold_02.tga"
        ):
            raise AssertionError(
                f"registered panel resource did not refine the semantic preview: {panel_context!r}"
            )
        tab_context = best_window_context(
            index.references_for("CUSTOM_TAB_+0").project,
            "CUSTOM_TAB_+0",
        )
        if (
            tab_context is None
            or tab_context.surface != "gui_embedded"
            or tab_context.gui_resource != "GUI/Hud/CustomSheet.gui"
            or tab_context.layout != "book"
            or tab_context.background_asset != "Hud/sheets/evidences/bg_buch.tga"
        ):
            raise AssertionError(
                f"a tab label did not inherit its registered sheet resources: {tab_context!r}"
            )
        selection_context = best_window_context(
            index.references_for("SELECT_TARGET_+0").project,
            "SELECT_TARGET_+0",
        )
        if (
            selection_context is None
            or selection_context.surface != "measure_choice"
            or selection_context.body_label != "select_target_+0"
        ):
            raise AssertionError(
                f"InitAlias target text did not receive a selection preview: {selection_context!r}"
            )
        important_context = best_window_context(
            index.references_for("IMPORTANT_SECTION_+0").project,
            "IMPORTANT_SECTION_+0",
        )
        if (
            important_context is None
            or important_context.surface != "important_persons"
            or important_context.layout != "book"
        ):
            raise AssertionError(
                f"important-person sections lost their real sheet profile: {important_context!r}"
            )
        pamphlet_context = best_window_context(
            index.references_for("PAMPHLET_BODY_+0").project,
            "PAMPHLET_BODY_+0",
        )
        if (
            pamphlet_context is None
            or pamphlet_context.surface != "pamphlet"
            or pamphlet_context.gui_resource != "GUI/Hud/panel_pamphletsheet.gui"
            or pamphlet_context.icon_asset != "Hud/Hud_Icons/Pamphlet.tga"
        ):
            raise AssertionError(
                f"blackboard text did not receive its pamphlet presentation: {pamphlet_context!r}"
            )
    finally:
        safe_rmtree(temp)


def assert_code_preview_unit_lookup_accepts_leading_underscore_labels() -> None:
    from .app import TranslatorWindow

    current = SimpleNamespace(
        file_rel="languages/Text.dbt",
        label="_MESSAGES_SLANDER_SPEECH_THEFT_+0",
    )
    window = SimpleNamespace(
        model=SimpleNamespace(
            units=(
                SimpleNamespace(file_rel="languages/Text.dbt", label="_MEASURE_HEAD_+0"),
                SimpleNamespace(file_rel="languages/Text.dbt", label="_MEASURE_BODY_+1"),
                SimpleNamespace(file_rel="languages/Text.dbt", label="_MEASURE_ASK_+0"),
                SimpleNamespace(file_rel="languages/Text.dbt", label="_SCENARIO_WAR_GERMANY_+0"),
            )
        )
    )
    found = TranslatorWindow._unit_for_normalized_label(window, "languages/Text.dbt", "measure_ask_+0")
    if found is None or found.label != "_MEASURE_ASK_+0":
        raise AssertionError("code preview unit lookup did not accept the DB leading underscore label")
    wildcard = TranslatorWindow._unit_for_normalized_label(window, "languages/Text.dbt", "scenario_war_*_+0")
    if wildcard is None or wildcard.label != "_SCENARIO_WAR_GERMANY_+0":
        raise AssertionError("code preview unit lookup did not resolve dynamic wildcard labels")
    selected = TranslatorWindow._unit_for_context_label(
        window,
        current,
        "messages_slander_speech_*_+0",
    )
    if selected is not current:
        raise AssertionError("a dynamic code label did not preserve the matching selected preview entry")


def assert_game_preview_draws_all_buttons() -> None:
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage, QPainter

    service = PreviewService()
    buttons = tuple(
        PreviewDocument.from_atoms(f"Button {index}", [PreviewAtom(f"Button {index}", 0, 8)])
        for index in range(5)
    )
    drawn: list[str] = []

    def fake_draw_document(_painter: object, document: PreviewDocument, **kwargs: object) -> int:
        drawn.append(document.display_text)
        top = kwargs.get("top", 0)
        return int(top) + 1 if isinstance(top, int) else 1

    service._draw_game_document = fake_draw_document  # type: ignore[method-assign]
    service._draw_game_button_background = lambda _painter, _rect: True  # type: ignore[method-assign]
    service.game_window_image(None, None, target=False, buttons=buttons)
    if drawn != [button.display_text for button in buttons]:
        raise AssertionError(f"game preview should draw every button without truncation: {drawn!r}")
    layout_service = PreviewService()
    layout_service._draw_game_document = fake_draw_document  # type: ignore[method-assign]
    layout_service._draw_game_button_background = lambda _painter, _rect: True  # type: ignore[method-assign]
    compact = layout_service.game_window_image(
        None,
        PreviewDocument.from_atoms("Hi", [PreviewAtom("Hi", 0, 2)]),
        target=False,
    )
    if compact.width() != 344 or compact.height() != 240:
        raise AssertionError(f"short parchment previews should use the compact layout: {compact.size()!r}")
    gui_temp = Path(tempfile.mkdtemp(prefix="translator_tool_gui_geometry_"))
    try:
        identifiers = {
            name: bytes((0x91 + index, 0xA2 + index, 0xB3 + index, 0xC4 + index))
            for index, name in enumerate(
                (
                    "NODE_NAME",
                    "ABS_X",
                    "ABS_Y",
                    "ABS_WIDTH",
                    "ABS_HEIGHT",
                    "HALIGN",
                ),
                start=1,
            )
        }

        def integer(name: str, value: int) -> bytes:
            return identifiers[name] + b"\x01" + value.to_bytes(4, "little", signed=True)

        def node(name: str, *, y: int, width: int, height: int, align: int | None = None) -> bytes:
            raw_name = name.encode("ascii") + b"\0"
            values = (
                integer("ABS_Y", y)
                + integer("ABS_WIDTH", width)
                + integer("ABS_HEIGHT", height)
            )
            if align is not None:
                values += integer("HALIGN", align)
            return (
                values
                + identifiers["NODE_NAME"]
                + b"\x02"
                + len(raw_name).to_bytes(4, "little")
                + raw_name
            )

        schema = b"".join(
            name.encode("ascii") + b"\0" + b"\0" * 4 + identifier
            for name, identifier in identifiers.items()
        )
        resource = gui_temp / "GUI" / "Hud" / "panel_messagebox.gui"
        resource.parent.mkdir(parents=True)
        resource.write_bytes(
            schema
            + node("Entrys", y=50, width=358, height=236, align=4)
            + node("Messagebox", y=5, width=481, height=376)
        )
        geometry = gui_resource_info(resource)
        if (
            geometry is None
            or geometry.root_size != (481, 376)
            or geometry.content_rect != (61, 50, 358, 236)
        ):
            raise AssertionError(
                f"binary GUI content geometry was not recovered: {geometry!r}"
            )
        gui_service = PreviewService(gui_temp)
        gui_context = PreviewWindowContext(
            "message",
            "parchment",
            PARCHMENT_TEXT,
            layout="parchment",
            gui_resource="GUI/Hud/panel_messagebox.gui",
        )
        gui_layout = gui_service._game_window_layout(
            gui_context,
            None,
            PreviewDocument.from_atoms(
                "Short body",
                [PreviewAtom("Short body", 0, 10)],
            ),
            (),
        )
        if (
            (gui_layout.width, gui_layout.height) != (385, 301)
            or gui_layout.top != 40
            or gui_layout.left_margin != 49
            or gui_layout.right_margin != 50
        ):
            raise AssertionError(
                f"messagebox preview ignored its GUI content rectangle: {gui_layout!r}"
            )
    finally:
        shutil.rmtree(gui_temp, ignore_errors=True)
    dialogue = layout_service.game_window_image(
        None,
        None,
        target=False,
        context=PreviewWindowContext("short", "dark_panel", DARK_PANEL_TEXT),
        buttons=tuple(
            PreviewDocument.from_atoms(f"Choice {index}", [PreviewAtom(f"Choice {index}", 0, 8)])
            for index in range(4)
        ),
    )
    if dialogue.width() != 520 or dialogue.height() != 430:
        raise AssertionError(f"dialogue previews with many choices should use the large layout: {dialogue.size()!r}")
    title_service = PreviewService()
    title_service._draw_game_document = fake_draw_document  # type: ignore[method-assign]
    requested_images: list[str] = []

    def fake_ui_image(name: str) -> QImage | None:
        requested_images.append(name)
        if name == "header_red.tga":
            image = QImage(265, 22, QImage.Format.Format_ARGB32)
            image.fill(0xFFFF0000)
            return image
        return None

    title_service.ui_image = fake_ui_image  # type: ignore[method-assign]
    title_service.game_window_image(
        PreviewDocument.from_atoms("Title", [PreviewAtom("Title", 0, 5)]),
        PreviewDocument.from_atoms("Body", [PreviewAtom("Body", 0, 4)]),
        target=False,
        context=PreviewWindowContext("tooltip", "dark_panel", DARK_PANEL_TEXT),
    )
    if "header_red.tga" not in requested_images:
        raise AssertionError("tooltip title preview did not request the game red title bar asset")

    asset_service = PreviewService()
    requested_assets: list[str] = []

    def fake_asset(name: str) -> QImage:
        requested_assets.append(name)
        image = QImage(12, 12, QImage.Format.Format_ARGB32)
        image.fill(0xFFFFFFFF)
        return image

    asset_service.ui_image = fake_asset  # type: ignore[method-assign]
    message = PreviewWindowContext("message", "parchment", PARCHMENT_TEXT, call_name="msgbox")
    asset_service._game_window_background(message, 344, 240)
    if requested_assets != ["Hud/messagebox/mbback0.tga"]:
        raise AssertionError(f"MsgBox should request its actual GUI background: {requested_assets!r}")
    requested_assets.clear()
    dialogue_context = PreviewWindowContext(
        "short",
        "dark_panel",
        DARK_PANEL_TEXT,
        call_name="msgsayinteraction",
    )
    asset_service._game_window_background(dialogue_context, 380, 148)
    if requested_assets != ["Hud/NoCompression/Priority3/PanelBackground_01.tga"]:
        raise AssertionError(f"MsgSay should request its actual panel texture: {requested_assets!r}")
    requested_assets.clear()
    datebook_canvas = QImage(380, 148, QImage.Format.Format_ARGB32)
    datebook_canvas.fill(0)
    datebook_painter = QPainter(datebook_canvas)
    asset_service._draw_game_window_decoration(
        datebook_painter,
        PreviewWindowContext(
            "datebook",
            "dark_panel",
            DARK_PANEL_TEXT,
            call_name="simadddatebookentry",
        ),
        datebook_canvas.rect(),
    )
    datebook_painter.end()
    if requested_assets != ["Hud/news/schedule.tga"]:
        raise AssertionError(f"datebook preview should request the schedule icon: {requested_assets!r}")
    requested_assets.clear()
    button_canvas = QImage(220, 50, QImage.Format.Format_ARGB32)
    button_canvas.fill(0)
    button_painter = QPainter(button_canvas)
    asset_service._draw_game_button_background(button_painter, QRect(5, 5, 201, 30))
    button_painter.end()
    if not requested_assets or requested_assets[0] != "Hud/NoCompression/btn_green_large.tga":
        raise AssertionError(f"dialog buttons should use the in-game green button texture: {requested_assets!r}")
    if any("startmenuebutton" in name.casefold() for name in requested_assets):
        raise AssertionError(f"dialog preview should not use main-menu button assets: {requested_assets!r}")


def assert_onscreen_help_preview_pairs_name_and_description() -> None:
    from .app import TranslatorWindow

    name = SimpleNamespace(file_rel="Text.dbt", label="ONSCREENHELP_9_ACTION_IMPACT_CoId_NAME_+0")
    description = SimpleNamespace(file_rel="Text.dbt", label="ONSCREENHELP_9_ACTION_IMPACT_CoId_DESCRIPTION_+0")
    tooltip = SimpleNamespace(file_rel="Text.dbt", label="ONSCREENHELP_9_ACTION_IMPACT_CoId_TOOLTIP_+0")
    window = SimpleNamespace(model=SimpleNamespace(units=(name, description, tooltip)))
    paired_name, paired_description = TranslatorWindow._paired_preview_units(window, description)
    if paired_name is not name or paired_description is not description:
        raise AssertionError("ONSCREENHELP DESCRIPTION did not pair with NAME")
    tooltip_head, tooltip_body = TranslatorWindow._paired_preview_units(window, tooltip)
    if tooltip_head is not None or tooltip_body is not tooltip:
        raise AssertionError("ONSCREENHELP TOOLTIP should not be paired into the help window body")


def assert_name_tooltip_preview_pairs_title_and_body() -> None:
    from .app import TranslatorWindow

    name = SimpleNamespace(file_rel="Text.dbt", label="BUILDING_CityWall_NAME_+1")
    tooltip = SimpleNamespace(file_rel="Text.dbt", label="_BUILDING_CityWall_TOOLTIP_+0")
    window = SimpleNamespace(
        model=SimpleNamespace(units=(name, tooltip)),
        _code_references_for_unit=lambda _unit: (),
    )
    window._paired_preview_units = lambda unit: TranslatorWindow._paired_preview_units(window, unit)
    paired_name, paired_tooltip = TranslatorWindow._paired_preview_units(window, name)
    if paired_name is not name or paired_tooltip is not tooltip:
        raise AssertionError("NAME should pair with the matching TOOLTIP body")
    paired_name, paired_tooltip = TranslatorWindow._paired_preview_units(window, tooltip)
    if paired_name is not name or paired_tooltip is not tooltip:
        raise AssertionError("TOOLTIP should pair back to the matching NAME title")
    context, header, body, buttons, _references = TranslatorWindow._game_preview_parts(window, tooltip)
    if context is None or context.kind != "tooltip" or context.background != "dark_panel":
        raise AssertionError(f"NAME/TOOLTIP pairs should use the tooltip preview profile: {context!r}")
    if context.default_color != DARK_PANEL_TEXT:
        raise AssertionError(f"tooltip preview should use the dark panel text color: {context!r}")
    if (
        context.background_asset != "Hud/NoCompression/Priority3/PanelBackground_01.tga"
        or context.title_asset != "Hud/NoCompression/header_red.tga"
    ):
        raise AssertionError(f"tooltip pair did not use the real tooltip assets: {context!r}")
    if header is not name or body is not tooltip or buttons:
        raise AssertionError("NAME/TOOLTIP preview parts were not assembled as title/body")


def assert_engine_owned_preview_styles() -> None:
    from .app import TranslatorWindow

    market = SimpleNamespace(
        uid="market",
        file_rel="Text.dbt",
        label="_GENERAL_TOOLTIPS_BUILDING_MARKET_+0",
        source_text="Market in %1SN",
    )
    window = SimpleNamespace(
        model=SimpleNamespace(units=(market,)),
        _code_references_for_unit=lambda _unit: (),
    )
    window._paired_preview_units = lambda unit: TranslatorWindow._paired_preview_units(window, unit)
    context, header, body, buttons, references = TranslatorWindow._game_preview_parts(window, market)
    if context is None or context.kind != "tooltip" or context.background != "dark_panel":
        raise AssertionError(f"engine tooltip should use its dark tooltip profile: {context!r}")
    if header is not None or body is not market or buttons or references:
        raise AssertionError("body-only engine tooltip preview parts were assembled incorrectly")

    help_name = SimpleNamespace(
        uid="help-name",
        file_rel="Text.dbt",
        label="ONSCREENHELP_9_ACTION_IMPACT_CoId_NAME_+0",
        source_text="Impact",
    )
    help_description = SimpleNamespace(
        uid="help-description",
        file_rel="Text.dbt",
        label="ONSCREENHELP_9_ACTION_IMPACT_CoId_DESCRIPTION_+0",
        source_text="Description",
    )
    help_window = SimpleNamespace(
        model=SimpleNamespace(units=(help_name, help_description)),
        _code_references_for_unit=lambda _unit: (),
    )
    help_window._paired_preview_units = lambda unit: TranslatorWindow._paired_preview_units(help_window, unit)
    context, header, body, _, _ = TranslatorWindow._game_preview_parts(help_window, help_description)
    if context is None or context.kind != "onscreen_help":
        raise AssertionError(f"engine onscreen help should use its native panel profile: {context!r}")
    if (
        context.background_asset != "Hud/sheets/OnscreenHelp/bg.tga"
        or context.title_asset != "Hud/NoCompression/header_red.tga"
    ):
        raise AssertionError(f"onscreen help did not use its own GUI assets: {context!r}")
    if header is not help_name or body is not help_description:
        raise AssertionError("engine onscreen help should retain structural title/body pairing")

    status = engine_window_context("_SETTLEMENTSTATE_HEADLINE_+0")
    if status is None or status.kind != "status" or not status.header_label:
        raise AssertionError(f"engine status headline should be a dark panel header: {status!r}")
    if PreviewService._game_window_background_name(status) != "Hud/NoCompression/Priority3/PanelBackground_01.tga":
        raise AssertionError("engine status preview should request the in-game panel texture")
    if engine_window_context("_UNRELATED_TEXT_+0") is not None:
        raise AssertionError("unknown labels must not receive a guessed engine window style")


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
        if updated.filter_status() != STATUS_REVIEW:
            raise AssertionError("changed translation should enter the needs-attention queue")
    finally:
        safe_rmtree(temp)


def assert_failed_source_sync_restores_workflow_cache(root: Path) -> None:
    temp = Path(tempfile.gettempdir()) / f"translator_tool_smoke_source_rollback_{uuid.uuid4().hex[:8]}"
    original_atomic_write_many = source_sync_module.atomic_write_many
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
        source_field = matching_source_field(unit.ref.target_field, source_doc.string_columns)
        source_row = source_doc.row_index[(int(unit.record_id), unit.label)]
        source_row.set_raw(source_field, source_row.get(source_field) + " [rollback]")
        (source_root / unit.file_rel).write_bytes(source_doc.render_bytes())

        managed_source = project_root / "languages" / unit.file_rel
        managed_before = managed_source.read_bytes()
        workflow_path = cache_path(project_root)
        cache_before = workflow_path.read_bytes() if workflow_path.exists() else None
        calls = 0

        def fail_source_commit(writes, deletions=()):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise PermissionError("injected source transaction failure")
            return original_atomic_write_many(writes, deletions)

        source_sync_module.atomic_write_many = fail_source_commit
        try:
            sync_source_project(source_root, project_root)
        except PermissionError:
            pass
        else:
            raise AssertionError("source sync rollback test did not inject a transaction failure")
        if managed_source.read_bytes() != managed_before:
            raise AssertionError("failed source sync changed a managed source file")
        cache_after = workflow_path.read_bytes() if workflow_path.exists() else None
        if cache_after != cache_before:
            raise AssertionError("failed source sync did not restore workflow review metadata")
    finally:
        source_sync_module.atomic_write_many = original_atomic_write_many
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


def assert_manual_status_cache() -> None:
    temp = Path(tempfile.gettempdir()) / f"translator_tool_smoke_manual_status_{uuid.uuid4().hex[:8]}"
    try:
        temp.mkdir(parents=True, exist_ok=False)
        unit = TranslationUnit(
            uid="unit:1",
            file_rel="Text.dbt",
            record_id="1",
            label="_TEST_+0",
            field_name="english",
            source_text="Same text",
            translate_text="Same text",
            status=STATUS_TODO,
            ref=UnitRef(kind="dbt", target_doc=SimpleNamespace(path=temp / "Text.dbt")),
        )
        project = Project(
            root=temp,
            languages_root=temp / "languages",
            language="#chinese",
            codec=None,
            source_docs={},
            source_text_docs={},
            target_dbt_docs={},
            target_text_docs={},
            units=[unit],
            source_order={},
            unit_index={unit.uid: unit},
            insertion_anchors={},
        )
        if unit.todo_reason != TODO_REASON_SAME_AS_SOURCE:
            raise AssertionError("same-as-source fixture should start as untranslated")
        project.set_units_confirmed((unit,), True)
        if unit.filter_status() != STATUS_TRANSLATED or unit.todo_reason:
            raise AssertionError("confirmed same-as-source translation should be treated as translated")
        if unit.uid not in confirmed_uids(temp, "#chinese"):
            raise AssertionError("confirmed translation flag was not persisted")
        project.set_units_need_work((unit,), True)
        if unit.review_reason != TODO_REASON_MANUAL_REVIEW or unit.filter_status() != STATUS_REVIEW:
            raise AssertionError("manual need-work mark did not enter the needs-attention status")
        if unit.uid not in need_work_uids(temp, "#chinese"):
            raise AssertionError("manual need-work flag was not persisted")
        if unit.uid in confirmed_uids(temp, "#chinese"):
            raise AssertionError("manual need-work mark should clear confirmed state")
        project.set_units_source_review((unit,), True)
        if unit.review_reason != TODO_REASON_SOURCE_CHANGED:
            raise AssertionError("source review should replace manual need-work reason")
        if unit.uid in need_work_uids(temp, "#chinese"):
            raise AssertionError("source review should clear manual need-work cache")
        project.set_units_ignored((unit,), True)
        if unit.filter_status() != STATUS_IGNORED:
            raise AssertionError("ignored mark should still override manual status flags")
        if unit.uid in confirmed_uids(temp, "#chinese") or unit.uid in need_work_uids(temp, "#chinese"):
            raise AssertionError("ignored mark should clear confirmed and need-work caches")
    finally:
        safe_rmtree(temp)


def assert_workflow_cache_updates_once(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_cache_transaction_")
    original_atomic_write = cache_module.atomic_write
    writes = 0
    try:
        project = Project.load(temp, "#chinese", enable_codec=False)
        unit = next(item for item in project.units if not item.is_extra)

        def count_atomic_write(path: Path, data: bytes) -> None:
            nonlocal writes
            writes += 1
            original_atomic_write(path, data)

        cache_module.atomic_write = count_atomic_write
        project.set_units_need_work((unit,), True)
        if writes != 1:
            raise AssertionError(f"one workflow transition wrote the cache {writes} times")
        if unit.uid not in need_work_uids(temp, "#chinese"):
            raise AssertionError("transactional workflow cache update lost the requested state")
        if unit.uid in confirmed_uids(temp, "#chinese"):
            raise AssertionError("transactional workflow cache update kept conflicting confirmation state")
    finally:
        cache_module.atomic_write = original_atomic_write
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
        reloaded.set_text("alpha$C [10,20,30]beta")
        saved.save([reloaded], auto_space_before_color_tokens=True)
        spaced = Project.load(temp, "#chinese")
        spaced_unit = next(item for item in spaced.units if item.uid == unit.uid)
        if spaced_unit.current_text != "alpha $C [10,20,30]beta":
            raise AssertionError("save skipped color-token normalization when $C and [ were separated")
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
    try:
        remove_matching_target_rows(temp, "Text.dbt", 1)
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
    finally:
        safe_rmtree(temp)


def assert_missing_insertions_follow_file_order(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_missing_order_")
    remove_matching_target_rows(temp, "Text.dbt", 2)
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


def assert_failed_save_does_not_mutate_loaded_documents(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_failed_save_state_")
    try:
        project = Project.load(temp, "#chinese", enable_codec=False)
        deletable = next(unit for unit in project.units if unit.ref.kind == "dbt" and unit.ref.target_row is not None)
        invalid = next(unit for unit in project.units if unit.uid != deletable.uid and unit.ref.kind == "dbt")
        deletable.set_pending_delete(True)
        invalid.set_text(invalid.current_text + " audit")
        invalid.initial_issues.append(ValidationIssue("error", "audit blocker", "audit"))
        target_row = deletable.ref.target_row
        target_doc = deletable.ref.target_doc
        try:
            project.save()
        except SaveValidationError:
            pass
        else:
            raise AssertionError("failed-save state test did not block the save")
        if target_row.deleted or target_doc.is_changed():
            raise AssertionError("a blocked save mutated the loaded target document")
    finally:
        safe_rmtree(temp)


def assert_atomic_write_many_rolls_back_partial_commit() -> None:
    temp = Path(tempfile.gettempdir()) / f"translator_tool_smoke_atomic_batch_{uuid.uuid4().hex[:8]}"
    temp.mkdir(parents=True)
    first = temp / "first.dbt"
    second = temp / "second.dbt"
    first.write_bytes(b"first-before")
    second.write_bytes(b"second-before")
    original_replace = file_utils_module.os.replace
    def failing_replace(source, target) -> None:
        if Path(source).suffix == ".tmp" and Path(target) == second:
            raise PermissionError("injected second-file replacement failure")
        original_replace(source, target)

    file_utils_module.os.replace = failing_replace
    try:
        try:
            file_utils_module.atomic_write_many({first: b"first-after", second: b"second-after"})
        except PermissionError:
            pass
        else:
            raise AssertionError("atomic batch test did not inject a replacement failure")
        if first.read_bytes() != b"first-before" or second.read_bytes() != b"second-before":
            raise AssertionError("atomic batch failure did not restore every original file")
        leftovers = tuple(path.name for path in temp.iterdir() if path.suffix in {".tmp", ".bak"})
        if leftovers:
            raise AssertionError(f"atomic batch rollback left temporary files: {leftovers}")
    finally:
        file_utils_module.os.replace = original_replace
        safe_rmtree(temp)


def assert_recovery_draft_round_trip(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_recovery_")
    settings_root = Path(tempfile.gettempdir()) / f"translator_tool_smoke_recovery_settings_{uuid.uuid4().hex[:8]}"
    previous_localappdata = os.environ.get("LOCALAPPDATA")
    try:
        os.environ["LOCALAPPDATA"] = str(settings_root)
        project = Project.load(temp, "#chinese", enable_codec=False)
        edited = next(unit for unit in project.units if unit.ref.kind == "dbt" and unit.ref.target_row is not None)
        deleted = next(
            unit
            for unit in project.units
            if unit.uid != edited.uid and unit.ref.kind == "dbt" and unit.ref.target_row is not None
        )
        recovered_text = edited.current_text + " recovery"
        edited.set_text(recovered_text)
        deleted.set_pending_delete(True)
        if save_recovery_draft(project) != 2:
            raise AssertionError("recovery draft did not include every unsaved text/delete change")

        reloaded = Project.load(temp, "#chinese", enable_codec=False)
        draft = load_recovery_draft(temp, "#chinese")
        if draft is None:
            raise AssertionError("recovery draft could not be loaded")
        restored, skipped = apply_recovery_draft(reloaded, draft)
        if restored != 2 or skipped:
            raise AssertionError("recovery draft did not restore the expected entries")
        if reloaded.unit_by_uid(edited.uid).current_text != recovered_text:
            raise AssertionError("recovery draft lost edited translation text")
        if not reloaded.unit_by_uid(deleted.uid).pending_delete:
            raise AssertionError("recovery draft lost a pending deletion")

        clear_recovery_draft(temp, "#chinese")
        if recovery_path(temp, "#chinese").exists():
            raise AssertionError("clearing recovery left the draft on disk")
    finally:
        if previous_localappdata is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = previous_localappdata
        safe_rmtree(settings_root)
        safe_rmtree(temp)


def assert_recovery_draft_limits_match(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_recovery_limit_")
    settings_root = Path(tempfile.gettempdir()) / f"translator_tool_settings_{uuid.uuid4().hex[:8]}"
    previous_localappdata = os.environ.get("LOCALAPPDATA")
    original_limit = recovery_module.MAX_RECOVERY_UNITS
    try:
        os.environ["LOCALAPPDATA"] = str(settings_root)
        project = Project.load(temp, "#chinese", enable_codec=False)
        units = [item for item in project.units if item.source_text and not item.is_extra][:2]
        if len(units) != 2:
            raise AssertionError("recovery limit fixture does not contain two editable units")
        recovery_module.MAX_RECOVERY_UNITS = 1
        project.apply_unit_edits(((units[0], units[0].current_text + "a", None),))
        if save_recovery_draft(project) != 1:
            raise AssertionError("recovery draft rejected its exact supported entry limit")
        valid_before = recovery_path(project.root, project.language).read_bytes()

        project.apply_unit_edits(((units[1], units[1].current_text + "b", None),))
        try:
            save_recovery_draft(project)
        except OSError:
            pass
        else:
            raise AssertionError("recovery draft saved more entries than its loader accepts")
        if recovery_path(project.root, project.language).read_bytes() != valid_before:
            raise AssertionError("oversized recovery update replaced the last valid draft")
        loaded = load_recovery_draft(project.root, project.language)
        if loaded is None or len(loaded.units) != 1:
            raise AssertionError("the last valid recovery draft became unreadable after an oversized update")
    finally:
        recovery_module.MAX_RECOVERY_UNITS = original_limit
        if previous_localappdata is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = previous_localappdata
        safe_rmtree(settings_root)
        safe_rmtree(temp)


def assert_project_edit_state_keeps_confirmation_consistent(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_edit_state_")
    try:
        project = Project.load(temp, "#chinese", enable_codec=False)
        unit = next(item for item in project.units if item.translate_text and not item.is_extra)
        original = unit.current_text
        project.set_units_confirmed((unit,), True)

        project.apply_unit_edits(((unit, original + "x", None),))
        if unit.confirmed or unit.uid in confirmed_uids(temp, "#chinese"):
            raise AssertionError("editing a confirmed translation did not clear confirmation")
        project.apply_unit_edits(((unit, original, None),))
        if not unit.confirmed or unit.uid not in confirmed_uids(temp, "#chinese"):
            raise AssertionError("reverting an editor change did not restore confirmation")

        project.apply_unit_edits(((unit, original, True),))
        if unit.confirmed or not unit.pending_delete:
            raise AssertionError("marking a confirmed translation for deletion kept stale confirmation")
        project.apply_unit_edits(((unit, original, False),))
        if not unit.confirmed or unit.pending_delete:
            raise AssertionError("undoing a deletion did not restore confirmation")
    finally:
        safe_rmtree(temp)


def assert_large_batch_save_stays_interactive(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_large_save_")
    try:
        project = Project.load(temp, "#chinese", enable_codec=False)
        edited = [
            unit
            for unit in project.units
            if unit.ref.kind == "dbt" and not unit.is_extra and unit.source_text
        ]
        for unit in edited:
            unit.set_text(unit.current_text + "x")
        started = time.perf_counter()
        project.save(edited)
        elapsed = time.perf_counter() - started
        if len(edited) >= LARGE_BATCH_MIN_ENTRIES:
            assert_within_budget(
                "large batch save",
                elapsed,
                LARGE_BATCH_SAVE_LIMIT_SECONDS,
                detail=f"{len(edited)} entries",
            )
    finally:
        safe_rmtree(temp)


def assert_unsaved_translation_status(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_status_")
    remove_matching_target_rows(temp, "Text.dbt", 1)
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
    translated = next(
        item
        for item in project.units
        if item.filter_status() == STATUS_TRANSLATED and item.ref.target_row is not None
    )
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
        if unit.review_reason != TODO_REASON_IMPORT_REVIEW or unit.display_status() != STATUS_REVIEW:
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
        if unit.review_reason != TODO_REASON_IMPORT_REVIEW or unit.display_status() != STATUS_REVIEW:
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
                ui_theme="guild2",
                last_project_root=expected[0],
                recent_project_roots=expected,
                enable_chinese_codec=True,
                auto_space_before_color_tokens_on_save=True,
                preview_scope="all",
                preview_translation_font_dir="C:/game/Hud/chinese",
                preview_ui_assets_dir="C:/game/Hud/Sets.dat",
                editor_zoom_steps=3,
            )
        )
        loaded = load_settings()
        if (
            loaded.ui_language != "zh-CN"
            or
            loaded.ui_theme != "guild2"
            or
            loaded.last_project_root != expected[0]
            or loaded.recent_project_roots != expected[:8]
            or not loaded.enable_chinese_codec
            or not loaded.auto_space_before_color_tokens_on_save
            or loaded.preview_scope != "all"
            or loaded.preview_translation_font_dir != "C:/game/Hud/chinese"
            or loaded.preview_ui_assets_dir != "C:/game/Hud/Sets.dat"
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
    from PySide6.QtCore import QItemSelection, QItemSelectionModel, Qt
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
        save_settings(AppSettings(last_project_root=str(temp)))
        app = QApplication.instance()
        created_app = app is None
        if app is None:
            app = QApplication([])
        LanguageGit(temp, "#chinese", codec_root=root).ensure_repository(AppSettings())
        original_ensure_repository = app_module.LanguageGit.ensure_repository
        main_thread_id = threading.get_ident()
        git_init_thread_ids: list[int] = []

        def tracked_ensure_repository(git: LanguageGit, settings: AppSettings) -> bool:
            git_init_thread_ids.append(threading.get_ident())
            return original_ensure_repository(git, settings)

        app_module.LanguageGit.ensure_repository = tracked_ensure_repository
        win = TranslatorWindow()
        git_init_deadline = time.monotonic() + 10.0
        while not win.git_ready and not win._git_init_failed and time.monotonic() < git_init_deadline:
            QTest.qWait(20)
            app.processEvents()
        app_module.LanguageGit.ensure_repository = original_ensure_repository
        if not git_init_thread_ids:
            raise AssertionError("Git initialization did not run during project startup")
        if main_thread_id in git_init_thread_ids:
            raise AssertionError("Git initialization blocked the Qt UI thread during project startup")
        if not win.git_ready:
            raise AssertionError("background Git initialization did not finish successfully")

        unit = next(item for item in win.model.units if item.ref.kind == "dbt" and item.source_text)
        original = unit.current_text
        original_dirty_count = win.project.dirty_count()
        win.project.set_units_confirmed((unit,), True)
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
        if win.translation_edit.document().isUndoAvailable():
            raise AssertionError("translation editor retained a second native undo stack")

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
        if unit.is_dirty or win.project.dirty_count() != original_dirty_count:
            raise AssertionError("editor undo restored the original text but left it marked unsaved")
        confirmed_after_undo = unit.uid in confirmed_uids(win.project.root, win.project.language)
        if not unit.confirmed or not confirmed_after_undo:
            raise AssertionError(
                "editor undo restored the original text but not its saved confirmation state: "
                f"memory={unit.confirmed!r}, cache={confirmed_after_undo!r}"
            )
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

        win.only_missing.setChecked(False)
        win.status_combo.setCurrentIndex(win.status_combo.findData(app_module.STATUS_FILTER_ALL))
        win._apply_filters()
        app.processEvents()
        if not win._restore_selected_row(second.uid):
            raise AssertionError("document-mode selection smoke test could not select its DBT entry")
        app.processEvents()

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
        if (
            win.current_uid != second.uid
            or win.table.currentIndex().data(Qt.ItemDataRole.UserRole) != second.uid
        ):
            raise AssertionError(
                "returning from guide txt mode did not restore the last selected table entry: "
                f"current={win.current_uid!r}, table={win.table.currentIndex().data(Qt.ItemDataRole.UserRole)!r}, "
                f"anchor={win._filter_anchor_uid!r}, expected={second.uid!r}"
            )
        win.translation_edit.setFocus(Qt.FocusReason.OtherFocusReason)
        app.processEvents()

        QTest.keyClick(win.translation_edit, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        app.processEvents()
        if second.current_text != second_original:
            raise AssertionError("editor undo did not fall back to entry history after returning from guide txt mode")

        win.history.clear()
        win._set_editor_unit(second)
        win.translation_edit.setFocus(Qt.FocusReason.OtherFocusReason)
        app.processEvents()
        cycle_baseline = second.current_text
        cycle_second = cycle_baseline + "2"
        cycle_third = cycle_baseline + "3"
        for text in (cycle_second, cycle_third):
            win.translation_edit.selectAll()
            win.translation_edit.insertPlainText(text)
            app.processEvents()
            QTest.qWait(TYPING_GROUP_DELAY_MS + 120)
            app.processEvents()

        for expected in (cycle_second, cycle_baseline):
            QTest.keyClick(win.translation_edit, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
            app.processEvents()
            if second.current_text != expected:
                raise AssertionError("editor-local undo did not restore the expected text sequence")
        QTest.keyClick(win.translation_edit, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        app.processEvents()
        if second.current_text != cycle_baseline:
            raise AssertionError("exhausted editor undo replayed the same typing through entry history")

        other_before = unit.current_text
        win._replace_unit_text(unit, other_before + " external", "external edit after local undo")
        app.processEvents()
        QTest.keyClick(win.translation_edit, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        app.processEvents()
        if unit.current_text != other_before:
            raise AssertionError(
                "an exhausted editor-local stack swallowed undo for the latest application operation"
            )

        win.only_missing.setChecked(False)
        win.status_combo.setCurrentIndex(win.status_combo.findData(app_module.STATUS_FILTER_ALL))
        win._apply_filters()
        app.processEvents()
        translated_for_filter = next(
            item
            for item in win.model.units
            if item.filter_status() == STATUS_TRANSLATED
            and item.source_text
            and not win.model.is_recently_translated(item)
        )
        win._restore_selected_row(translated_for_filter.uid)
        app.processEvents()
        if win.current_uid != translated_for_filter.uid:
            raise AssertionError("filter-selection smoke test could not select a translated entry")
        win.only_missing.setChecked(True)
        app.processEvents()
        translated_source_index = win.model.index(win.model.row_for_uid(translated_for_filter.uid), 0)
        if win.proxy.mapFromSource(translated_source_index).isValid():
            raise AssertionError("only-untranslated filter did not hide the translated selection")
        win.only_missing.setChecked(False)
        app.processEvents()
        if win.current_uid != translated_for_filter.uid or win.table.currentIndex().data(Qt.ItemDataRole.UserRole) != translated_for_filter.uid:
            raise AssertionError("clearing only-untranslated did not restore the previously selected entry")

        search_selection_target = next(
            item
            for item in reversed(win.model.units)
            if item.uid != translated_for_filter.uid
            and item.file_rel == str(win.file_combo.currentData())
            and item.label
            and item.label != translated_for_filter.label
            and '"' not in item.label
        )
        target_source_index = win.model.index(win.model.row_for_uid(search_selection_target.uid), 0)
        win.search_edit.setFocus(Qt.FocusReason.OtherFocusReason)
        win.search_edit.setText(f'label:"{search_selection_target.label}"')
        QTest.qWait(320)
        app.processEvents()
        if win.current_uid != translated_for_filter.uid:
            raise AssertionError("search filtering discarded the last explicitly selected entry")
        if not win.search_edit.hasFocus():
            raise AssertionError("search filtering moved keyboard focus out of the search box")
        win.search_edit.clear()
        QTest.qWait(320)
        app.processEvents()
        if (
            win.current_uid != translated_for_filter.uid
            or win.table.currentIndex().data(Qt.ItemDataRole.UserRole) != translated_for_filter.uid
        ):
            raise AssertionError("clearing search did not restore the last selected entry")

        win.search_edit.setText(f'label:"{search_selection_target.label}"')
        QTest.qWait(320)
        app.processEvents()
        target_proxy_index = win.proxy.mapFromSource(target_source_index)
        if not target_proxy_index.isValid():
            raise AssertionError("search-selection smoke test did not reveal its target entry")
        win.table.setCurrentIndex(target_proxy_index)
        win.table.selectRow(target_proxy_index.row())
        app.processEvents()
        win.search_edit.clear()
        QTest.qWait(320)
        app.processEvents()
        if (
            win.current_uid != search_selection_target.uid
            or win.table.currentIndex().data(Qt.ItemDataRole.UserRole) != search_selection_target.uid
        ):
            raise AssertionError("clearing search ignored the entry most recently selected in filtered results")
        restored_source_index = win.model.index(win.model.row_for_uid(search_selection_target.uid), 0)
        restored_proxy_index = win.proxy.mapFromSource(restored_source_index)
        if not win.table.visualRect(restored_proxy_index).intersects(win.table.viewport().rect()):
            raise AssertionError("clearing search kept the selected UID but left its row outside the visible viewport")

        previous_file_filter = win.file_combo.currentData()
        win.file_combo.setCurrentIndex(win.file_combo.findData(app_module.FILE_FILTER_ALL))
        win.status_combo.setCurrentIndex(win.status_combo.findData(app_module.STATUS_FILTER_ALL))
        win.only_missing.setChecked(False)
        win.search_edit.clear()
        win._apply_filters()
        app.processEvents()
        first_proxy_index = win.proxy.index(0, 0)
        first_uid = first_proxy_index.data(Qt.ItemDataRole.UserRole)
        win.table.setCurrentIndex(first_proxy_index)
        win.table.selectRow(0)
        app.processEvents()
        scroll_bar = win.table.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())
        app.processEvents()
        scroll_before_ai_refresh = scroll_bar.value()
        if scroll_before_ai_refresh <= 0:
            raise AssertionError("AI scrolling regression fixture does not contain a scrollable table")
        if win.table.visualRect(first_proxy_index).intersects(win.table.viewport().rect()):
            raise AssertionError("AI scrolling regression fixture did not move the selection off-screen")
        win.ai_filter_refresh_pending = True
        win._refresh_ai_filter()
        app.processEvents()
        if scroll_bar.value() != scroll_before_ai_refresh:
            raise AssertionError("AI filter refresh overrode the user's manual table scroll position")
        if win.table.currentIndex().data(Qt.ItemDataRole.UserRole) != first_uid:
            raise AssertionError("AI filter refresh changed the selected entry while preserving scrolling")
        win.file_combo.setCurrentIndex(win.file_combo.findData(previous_file_filter))
        win._apply_filters()
        app.processEvents()

        search_unit = next(
            item
            for item in win.model.units
            if item.label
            and item.source_text
            and any(char.isupper() for char in item.label)
            and '"' not in item.source_text
        )
        search_source_index = win.model.index(win.model.row_for_uid(search_unit.uid), 0)
        win.search_edit.setText(f'label:{search_unit.label.lower()}')
        win._apply_filters()
        app.processEvents()
        if not win.proxy.mapFromSource(search_source_index).isValid():
            raise AssertionError("case-insensitive field search did not match a label")
        win.search_edit.case_button.setChecked(True)
        app.processEvents()
        if win.proxy.mapFromSource(search_source_index).isValid():
            raise AssertionError("Aa case-sensitive search still matched a differently-cased label")
        win.search_edit.setText(
            f'label:"{search_unit.label}", source:"{search_unit.source_text}"'
        )
        win._apply_filters()
        app.processEvents()
        if not win.proxy.mapFromSource(search_source_index).isValid():
            raise AssertionError("quoted comma-separated AND search did not match all requested fields")
        win.search_edit.setText(f'label:"{search_unit.label}"， -id:{search_unit.record_id}')
        win._apply_filters()
        app.processEvents()
        if win.proxy.mapFromSource(search_source_index).isValid():
            raise AssertionError("Chinese-comma search did not apply the excluded ID condition")
        bracket_clauses = app_module.parse_search_query(
            '$C[1,2,3], label:test',
            case_sensitive=False,
        )
        if len(bracket_clauses) != 2 or bracket_clauses[0].needle != '$c[1,2,3]':
            raise AssertionError("search parser split commas inside a color token")
        win.search_edit.clear()
        win.search_edit.case_button.setChecked(False)
        win._apply_filters()
        app.processEvents()

        source_order_uids = tuple(item.uid for item in win.project.units)
        visible_source_order = tuple(
            win._unit_from_proxy_index(win.proxy.index(row, 0)).uid for row in range(win.proxy.rowCount())
        )
        for column in range(win.model.columnCount()):
            win.table.sortByColumn(column, Qt.SortOrder.AscendingOrder)
            app.processEvents()
            if tuple(item.uid for item in win.project.units) != source_order_uids:
                raise AssertionError(f"sorting column {column} changed the project's source/save order")
        win.reset_sort_button.click()
        app.processEvents()
        restored_visible_order = tuple(
            win._unit_from_proxy_index(win.proxy.index(row, 0)).uid for row in range(win.proxy.rowCount())
        )
        if restored_visible_order != visible_source_order:
            raise AssertionError("clearing table sorting did not restore source display order")

        clipboard_units = [win._unit_from_proxy_index(win.proxy.index(row, 0)) for row in range(4)]
        if any(unit is None for unit in clipboard_units):
            raise AssertionError("entry clipboard smoke test needs four visible translation entries")
        clipboard_units = [unit for unit in clipboard_units if unit is not None]
        source_texts = tuple(unit.source_text for unit in clipboard_units)
        copied_before = tuple(unit.current_text for unit in clipboard_units[:2])
        copied_texts = ("clipboard translation A", "clipboard translation B")
        win._replace_units_state(
            clipboard_units[:2],
            {unit.uid: text for unit, text in zip(clipboard_units[:2], copied_texts)},
            False,
            "clipboard setup",
        )
        win.history.clear()

        def select_proxy_rows(first: int, last: int) -> None:
            selection_model = win.table.selectionModel()
            current = win.proxy.index(first, 0)
            win.table.setCurrentIndex(current)
            selection = QItemSelection(current, win.proxy.index(last, win.model.columnCount() - 1))
            selection_model.select(
                selection,
                QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows,
            )
            win.table.setFocus(Qt.FocusReason.OtherFocusReason)
            app.processEvents()

        select_proxy_rows(0, 1)
        QTest.keyClick(win.table, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        app.processEvents()
        clipboard_mime = QApplication.clipboard().mimeData()
        if not clipboard_mime.hasFormat(app_module.ENTRY_CLIPBOARD_MIME):
            raise AssertionError("entry copy did not publish the app clipboard format")
        external_fields = clipboard_mime.text().splitlines()[0].split("\t")
        if len(external_fields) != 3 or external_fields[2] != "clipboard translation A":
            raise AssertionError("entry copy did not publish tab-separated label, source, and translation text")

        target_before = tuple(unit.current_text for unit in clipboard_units[2:])
        select_proxy_rows(2, 2)
        QTest.keyClick(win.table, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        app.processEvents()
        if tuple(unit.current_text for unit in clipboard_units[2:]) != copied_texts:
            raise AssertionError("multi-entry paste did not fill consecutive translations from the selected row")
        if tuple(unit.source_text for unit in clipboard_units) != source_texts:
            raise AssertionError("entry paste modified source text")
        win.undo()
        app.processEvents()
        if tuple(unit.current_text for unit in clipboard_units[2:]) != target_before:
            raise AssertionError("one undo did not restore the complete multi-entry paste")

        select_proxy_rows(0, 0)
        QTest.keyClick(win.table, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        app.processEvents()
        select_proxy_rows(2, 3)
        QTest.keyClick(win.table, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        app.processEvents()
        if tuple(unit.current_text for unit in clipboard_units[2:]) != (copied_texts[0], copied_texts[0]):
            raise AssertionError("one copied translation was not pasted into every selected entry")
        if tuple(unit.source_text for unit in clipboard_units) != source_texts:
            raise AssertionError("one-to-many entry paste modified source text")
        win.undo()
        app.processEvents()
        if tuple(unit.current_text for unit in clipboard_units[2:]) != target_before:
            raise AssertionError("one undo did not restore the complete one-to-many paste")

        ai_unit = clipboard_units[2]
        ai_before = ai_unit.current_text
        ai_after = ai_before + " AI result"
        win.ai_changes = []
        win.ai_failures = []
        win.ai_is_batch = False
        win._collect_ai_result(ai_unit.uid, ai_after)
        if ai_unit.current_text != ai_after:
            raise AssertionError("successful AI result was counted but not applied to the translation")
        win._finish_ai("AI result smoke test")
        win.table.setFocus(Qt.FocusReason.OtherFocusReason)
        win.undo()
        app.processEvents()
        if ai_unit.current_text != ai_before:
            raise AssertionError("one undo did not restore an applied AI translation")

        attention_units = tuple(clipboard_units[2:])
        win.project.set_units_source_review(attention_units, True)
        win.model.refresh_units(attention_units)
        win.proxy.refresh_rows()
        win._update_counts()
        app.processEvents()
        if not win.review_attention_button.isVisible() or "2" not in win.review_attention_button.text():
            raise AssertionError("source-update review entries did not show the prominent attention button")
        status_index = win.model.index(win.model.row_for_uid(attention_units[0].uid), win.model.STATUS)
        if status_index.data() != STATUS_REVIEW:
            raise AssertionError("source-update review entry still used the untranslated status badge")

        todo_index = win.status_combo.findData(app_module.STATUS_FILTER_TODO)
        win.status_combo.setCurrentIndex(todo_index)
        win._apply_filters()
        app.processEvents()
        for unit in attention_units:
            source_index = win.model.index(win.model.row_for_uid(unit.uid), 0)
            if win.proxy.mapFromSource(source_index).isValid():
                raise AssertionError("needs-attention entry was still mixed into the needs-translation filter")

        win.review_attention_button.click()
        app.processEvents()
        if win.status_combo.currentData() != app_module.STATUS_FILTER_REVIEW:
            raise AssertionError("attention button did not activate the dedicated review filter")
        visible_review_uids = {
            unit.uid
            for row in range(win.proxy.rowCount())
            if (unit := win._unit_from_proxy_index(win.proxy.index(row, 0))) is not None
        }
        if not {unit.uid for unit in attention_units}.issubset(visible_review_uids):
            raise AssertionError("dedicated review filter did not reveal all source-update entries")
        if any(not win.model.unit_for_uid(uid).requires_manual_review for uid in visible_review_uids):
            raise AssertionError("dedicated review filter included an ordinary untranslated entry")
        win.project.set_units_source_review(attention_units, False)
        win.model.refresh_units(attention_units)
        win.status_combo.setCurrentIndex(win.status_combo.findData(app_module.STATUS_FILTER_ALL))
        win.only_missing.setChecked(False)
        win._apply_filters()
        win._update_counts()
        win._replace_units_state(
            clipboard_units[:2],
            {unit.uid: text for unit, text in zip(clipboard_units[:2], copied_before)},
            False,
            "clipboard cleanup",
        )
        win.history.clear()
        QApplication.clipboard().clear()

        save_candidates = [
            item
            for item in win.model.units[len(win.model.units) // 2 :]
            if item.ref.kind == "dbt" and item.ref.target_row is not None and not item.pending_delete
        ]
        save_unit = save_candidates[len(save_candidates) // 2]
        save_before = save_unit.current_text
        save_after = save_before + "x"
        win._filter_anchor_uid = save_unit.uid
        if not win._restore_selected_row(save_unit.uid):
            raise AssertionError("save refresh fixture could not select a lower-table translation")
        win._replace_unit_text(save_unit, save_after, "save refresh smoke test")
        load_calls = 0
        original_load_project = win.load_project

        def tracked_load_project(discard_changes: bool = False) -> None:
            nonlocal load_calls
            load_calls += 1
            original_load_project(discard_changes=discard_changes)

        win.load_project = tracked_load_project  # type: ignore[method-assign]
        win.save_all()
        win.load_project = original_load_project  # type: ignore[method-assign]
        app.processEvents()
        saved_unit = win.project.unit_by_uid(save_unit.uid)
        if load_calls:
            raise AssertionError("ordinary save fell back to a full-project reload")
        if saved_unit is None or saved_unit.current_text != save_after or saved_unit.is_dirty:
            raise AssertionError("ordinary save did not refresh the durable translation in place")
        current_index = win.table.currentIndex()
        if current_index.data(Qt.ItemDataRole.UserRole) != save_unit.uid:
            raise AssertionError("ordinary save moved away from the selected translation")
        if not win.table.visualRect(current_index).intersects(win.table.viewport().rect()):
            raise AssertionError("ordinary save left the selected translation outside the viewport")

        win.table.setFocus(Qt.FocusReason.OtherFocusReason)
        win.undo()
        app.processEvents()
        if win.project.unit_by_uid(save_unit.uid).current_text != save_before:
            raise AssertionError("save refresh broke UID-based undo for the saved translation")
        win.redo()
        app.processEvents()
        if win.project.unit_by_uid(save_unit.uid).current_text != save_after:
            raise AssertionError("save refresh broke UID-based redo for the saved translation")
        win.history.clear()

        win.close()
        app.processEvents()
    finally:
        if "original_ensure_repository" in locals():
            app_module.LanguageGit.ensure_repository = original_ensure_repository
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
    context = LlmSuggestionContext("Text.dbt", "1", "DoNotSend")
    translated = provider.translate("Cost: %1t", dbt_field=True, context=context)
    if (
        translated != "测试 %1t"
        or "__TG_FMT_0000__" not in transport.last_url
        or "DoNotSend" in transport.last_url
    ):
        raise AssertionError("AI translation did not preserve protected format tokens")
    broken = GoogleTranslateProvider(
        "https://example.invalid/translate", "en", "zh-CN", FakeGoogleTransport("测试内容")
    )
    try:
        broken.translate("Cost: %1t", dbt_field=True)
    except TranslationProviderError:
        return
    raise AssertionError("AI result missing a protected token was accepted")


class CaptureTranslationTransport:
    def __init__(self) -> None:
        self.payload = None

    def get_json(self, url: str):
        raise AssertionError("OpenAI provider must not issue a GET request")

    def post_json(self, url: str, payload, headers):
        self.payload = payload
        return {"choices": [{"message": {"content": "译文 __TG_FMT_0000__"}}]}


def assert_ai_translation_context() -> None:
    units = [
        SimpleNamespace(
            uid=f"unit-{index}",
            file_rel="Text.dbt",
            record_id=str(index),
            label=f"Label{index}",
            field_name="Text",
            source_text=f"Source {index}",
        )
        for index in range(5)
    ]
    contexts = build_llm_contexts(units, ("unit-2",))
    context = contexts["unit-2"]
    expected_relations = ["previous 2", "previous 1", "next 1", "next 2"]
    if [neighbor.relation for neighbor in context.neighbors] != expected_relations:
        raise AssertionError("AI context did not retain the two nearest entries on each side")
    transport = CaptureTranslationTransport()
    provider = OpenAICompatibleProvider("https://example.invalid/v1", "test-model", "test-key", transport)
    translated = provider.translate("Cost: %1t", dbt_field=True, context=context)
    if translated != "译文 %1t":
        raise AssertionError("contextual AI translation did not restore protected tokens")
    prompt = transport.payload["messages"][1]["content"]
    for snippet in ("file: Text.dbt", "id: 2", "label: Label2", "field: Text", "Source 1", "Source 3"):
        if snippet not in prompt:
            raise AssertionError(f"AI translation prompt missed context snippet: {snippet}")
    if "Cost: __TG_FMT_0000__" not in prompt:
        raise AssertionError("AI translation prompt did not protect the current source")

    large_units = [
        SimpleNamespace(
            uid=f"large-{index}",
            file_rel=f"File{index // 1000}.dbt",
            record_id=str(index),
            label=f"Label{index}",
            field_name="Text",
            source_text="Source",
        )
        for index in range(20_000)
    ]
    started = time.perf_counter()
    large_contexts = build_llm_contexts(large_units, (unit.uid for unit in large_units))
    elapsed = time.perf_counter() - started
    if len(large_contexts) != len(large_units):
        raise AssertionError("AI context builder omitted requested units")
    assert_within_budget("AI context build", elapsed, AI_CONTEXT_BUILD_LIMIT_SECONDS)


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
        "$N $Z $L $R $T $> $< $C[1,2,3,255] $F[Body] $S[12] $B[label] "
        "$[ornament$] #E[NT_NEUTRAL] #SP+ #SP- @NMale @L_TEST_KEY_+n @T\"fallback\""
    )
    tokens = format_tokens(syntax)
    required = {
        "%1NAME",
        "%11GG",
        "%14SN",
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
        dialect=FORMAT_TOOLTIP,
    )
    if any(issue.code == "unknown-format" for issue in tooltip_macros):
        raise AssertionError("tooltip macros or @N gender tags were not recognized")
    guide_tip = validate_translation(
        "<text>\nCombat in the world is dangerous. Defend your {tip:CART}carts{/tip}.\n</text>",
        "<text>\n战斗很危险。保护你的{TIP : CART }货车{/ TIP}。\n</text>",
        dbt_field=False,
        dialect=FORMAT_GUIDE,
    )
    if not any(issue.code in {"format-missing", "format-extra"} for issue in guide_tip):
        raise AssertionError("guide tip tags should remain case-sensitive and spacing-sensitive in txt files")
    guide_quote = validate_translation(
        "<text>Safe</text>",
        '<text>"Crash risk"</text>',
        dbt_field=False,
        dialect=FORMAT_GUIDE,
    )
    if not any(issue.code == "guide-quote" for issue in guide_quote):
        raise AssertionError("plain double quotes in Guide text should be rejected as a crash risk")
    if not any(issue.code == "guide-quote" and issue.blocks_save for issue in guide_quote):
        raise AssertionError("plain double quotes in Guide text should block saving")
    guide_attr_quote = validate_translation(
        '<list>[type="bullet"]<item>Safe</item></list>',
        '<list>[type="bullet"]<item>Safe</item></list>',
        dbt_field=False,
        dialect=FORMAT_GUIDE,
    )
    if any(issue.code == "guide-quote" for issue in guide_attr_quote):
        raise AssertionError("double quotes inside legal Guide attributes should not be warned")
    dbt_quote = validate_translation("Safe", '中文"引号', dbt_field=True)
    if not any(issue.code == "dbt-quote" and issue.blocks_save for issue in dbt_quote):
        raise AssertionError("plain double quotes in DBT text should block saving")
    chinese_punctuation = validate_translation("Safe", "“中文”，‘标点’。", dbt_field=True)
    if chinese_punctuation:
        raise AssertionError("Chinese punctuation should not produce Translation-Kit warnings")
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
    quote_style_translation = validate_translation(">Invite< %1SN", ">邀请< %1SN", dbt_field=True)
    if any(issue.code in {"format-missing", "format-extra", "unknown-format"} for issue in quote_style_translation):
        raise AssertionError(">...< text decoration should not produce format-token warnings")
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


def assert_format_dialects_are_isolated() -> None:
    tooltip = "%gold_icon%%n%%char_name%"
    tooltip_tokens = format_tokens(tooltip, dialect=FORMAT_TOOLTIP)
    if tooltip_tokens != {"%gold_icon%": 1, "%n%": 1, "%char_name%": 1}:
        raise AssertionError("Tooltips.dbt named placeholders were not parsed by their own dialect")
    if any(token in format_tokens(tooltip) for token in tooltip_tokens):
        raise AssertionError("Tooltips.dbt named placeholders leaked into the ordinary DBT dialect")

    guide = '<header>Title</header><text>{key:CURSOR_UP}{tip:CART}cart{/tip}</text>'
    guide_tokens = format_tokens(guide, dialect=FORMAT_GUIDE)
    required = {"<header>", "</header>", "<text>", "</text>", "{key:CURSOR_UP}", "{tip:CART}", "{/tip}"}
    if not required.issubset(guide_tokens):
        raise AssertionError("Guide XML-like tokens were not parsed by the Guide dialect")
    if any(token in format_tokens(guide) for token in required):
        raise AssertionError("Guide tokens leaked into the ordinary DBT dialect")


def assert_reordered_tokens_are_not_highlighted_as_missing() -> None:
    from .app import _missing_source_token_ranges

    source = "%1SN owns %2GG and has %3t."
    target = "%3t：%2GG，所有者 %1SN。"
    if _missing_source_token_ranges(source, target):
        raise AssertionError("reordered placeholders were still highlighted as missing")
    missing = _missing_source_token_ranges(source, "%2GG，所有者 %1SN。")
    expected_start = source.index("%3t")
    if missing != [(expected_start, expected_start + len("%3t"))]:
        raise AssertionError("counter-based missing-placeholder highlighting selected the wrong occurrence")


def assert_preview_i18n_and_symbol_mapping() -> None:
    temp = Path(tempfile.gettempdir()) / f"translator_tool_smoke_preview_{uuid.uuid4().hex[:8]}"
    try:
        source_root = temp / "DB" / "Languages"
        target_root = source_root / "#chinese"
        target_root.mkdir(parents=True)
        header_source = (
            "// Table File\n"
            "Table Description:\n"
            '"id" INT 0 | "label" STRING 0 | "english" STRING 0 |\n'
            "Data:\n"
        )
        header_target = (
            "// Table File\n"
            "Table Description:\n"
            '"id" INT 0 | "label" STRING 0 | "chinese" STRING 0 |\n'
            "Data:\n"
        )
        rows_source = (
            '1 "_NAMES_ENGLISH_MALE_+0" "Jack" |\n'
            '2 "_NAMES_ENGLISH_SURNAMES_+0" "Smith" |\n'
            '3 "_PREVIEW_LABEL_+0" "Preview label" |\n'
            '4 "_ITEM_RING_NAME_+0" "Ruby ring" |\n'
            '5 "_BUILDING_Church2b_NAME_+0" "Catholic church" |\n'
            '6 "_BUILDING_Church2b_POOL_+0" "The Almighty" |\n'
            '7 "_CHARACTERS_1_CLASSES_patron_NAME_+0" "Patron" |\n'
            '8 "_CHARACTERS_1_CLASSES_patron_LEVEL_+0" "Worker" |\n'
            '9 "_CHARACTERS_2_PROFESSIONS_baker_NAME_+0" "Baker" |\n'
            '10 "_CHARACTERS_3_OFFICES_NAME_Mayor_+0" "Mayor" |\n'
            '11 "_CHARACTERS_3_TITLES_NAME_+0" "Serf" |\n'
            '12 "_SCENARIO_WAR_GERMANY_+0" "The German Empire" |\n'
            '13 "_WAR_MERC_TROOPER_MORE_+0" "Pikeman" |\n'
            '14 "_MEASURE_WUERDENTRAGEREMPFANGEN_BODY_+2" "The diplomat from %2l is impressed." |\n'
            '15 "_DIPLOMAT_NAME_DENMARK_+0" "Denmark" |\n'
            '16 "_WAR_MERC_TROOPER_MALE_+0" "Trooper" |\n'
            '17 "_CHARACTERS_3_TITLES_NAME_+9" "Citizen" |\n'
            '18 "SubstSimFullDescOffice_+0" "%1ST %1SV %1SD, %1SA in %2NAME" |\n'
            '19 "SubstSimFullDescNoOffice_+0" "%1ST %1SV %1SD" |\n'
        )
        rows_target = (
            '1 "_NAMES_ENGLISH_MALE_+0" "杰克" |\n'
            '2 "_NAMES_ENGLISH_SURNAMES_+0" "史密斯" |\n'
            '3 "_PREVIEW_LABEL_+0" "预览标签" |\n'
            '4 "_BUILDING_Church2b_NAME_+0" "天主教堂" |\n'
            '5 "_BUILDING_Church2b_POOL_+0" "全能的上帝" |\n'
            '6 "_CHARACTERS_1_CLASSES_patron_NAME_+0" "庇护者" |\n'
            '7 "_CHARACTERS_1_CLASSES_patron_LEVEL_+0" "工人" |\n'
            '8 "_CHARACTERS_2_PROFESSIONS_baker_NAME_+0" "面包师" |\n'
            '9 "_CHARACTERS_3_OFFICES_NAME_Mayor_+0" "市长" |\n'
            '10 "_CHARACTERS_3_TITLES_NAME_+9" "市民" |\n'
            '11 "SubstSimFullDescOffice_+0" "%1ST %1SV·%1SD，%2NAME的%1SA" |\n'
            '12 "SubstSimFullDescNoOffice_+0" "%1ST %1SV·%1SD" |\n'
        )
        (source_root / "Text.dbt").write_text(header_source + rows_source, encoding="utf-8")
        (target_root / "Text.dbt").write_text(header_target + rows_target, encoding="utf-8")
        (temp / "DB" / "Classes.dbt").write_text(
            (
                "// Table File\n"
                "Table Description:\n"
                '"id" INT -1 | "name" STRING 0 |\n'
                "Data:\n"
                '1 "patron" |\n'
            ),
            encoding="utf-8",
        )
        (temp / "DB" / "Professions.dbt").write_text(
            (
                "// Table File\n"
                "Table Description:\n"
                '"id" INT -1 | "name" STRING 0 | "classid" INT 0 |\n'
                "Data:\n"
                '4 "baker" 1 |\n'
            ),
            encoding="utf-8",
        )
        (temp / "DB" / "Offices.dbt").write_text(
            (
                "// Table File\n"
                "Table Description:\n"
                '"id" INT -1 | "title" STRING 0 | "settlementlevel" INT 0 | '
                '"income" INT 0 | "level" INT 0 |\n'
                "Data:\n"
                '1 "Mayor" 2 250 1 |\n'
            ),
            encoding="utf-8",
        )

        service = PreviewService(temp, "#chinese")
        raw = "%1SN $S[2012] %2t @L_PREVIEW_LABEL_+0"
        source = service.render(raw, unit_key="same-entry", file_rel="Text.dbt", kind="dbt", target=False)
        target = service.render(raw, unit_key="same-entry", file_rel="Text.dbt", kind="dbt", target=True)
        if "Jack Smith" not in source.display_text or "杰克 史密斯" not in target.display_text:
            raise AssertionError("the same preview identity was not localized independently on both sides")
        if "Preview label" not in source.display_text or "预览标签" not in target.display_text:
            raise AssertionError("@L localization preview did not use matching source and target labels")
        for file_rel in ("Text.dbt", "Tooltips.dbt"):
            plain_argument = service.render(
                "Value %1",
                unit_key=f"plain-argument:{file_rel}",
                file_rel=file_rel,
                kind="dbt",
                target=False,
            )
            if plain_argument.display_text != "Value Argument 1":
                raise AssertionError(
                    f"suffix-free placeholder crashed or rendered incorrectly in {file_rel}: "
                    f"{plain_argument.display_text!r}"
                )
        adjacent_suffix = service.render(
            "Term: %2it Rate: %2.1f/d",
            unit_key="format-boundaries",
            file_rel="Text.dbt",
            kind="dbt",
            target=False,
        )
        if adjacent_suffix.display_text != "Term: 12t Rate: 12.5/d":
            raise AssertionError(
                "preview did not distinguish a known Guild suffix from an adjacent "
                f"literal or a precision printf token: {adjacent_suffix.display_text!r}"
            )
        label_seed_left = service.render(
            "%1SN",
            unit_key="left-uid",
            label="SAME_PREVIEW_LABEL",
            file_rel="Text.dbt",
            kind="dbt",
            target=True,
        )
        label_seed_right = service.render(
            "%1SN",
            unit_key="right-uid",
            label="SAME_PREVIEW_LABEL",
            file_rel="Text.dbt",
            kind="dbt",
            target=True,
        )
        if label_seed_left.display_text != label_seed_right.display_text:
            raise AssertionError("placeholder preview should be seeded by label instead of uid")
        if not any(atom.glyph_id == 2012 and atom.text == GLYPH_MARK for atom in source.atoms):
            raise AssertionError("$S[2012] was not routed to the live glyph preview")
        if not any(atom.glyph_id == 2002 for atom in source.atoms):
            raise AssertionError("%2t did not preview the game's coin symbol")
        strong_placeholders = service.render(
            "%1GG | %1GN | %1GT | %4SN | %4SV | %4SZ | %4SK | %4ST | %4SA | %4SD | %4SB | %4SL",
            unit_key="same-entry",
            label="STRONG_PLACEHOLDERS",
            file_rel="Text.dbt",
            kind="dbt",
            target=False,
        )
        if not strong_placeholders.display_text.startswith(
            "Catholic church『The Almighty』 | The Almighty | Catholic church"
        ):
            raise AssertionError(
                "building placeholders did not project one coherent building entity: "
                f"{strong_placeholders.display_text!r}"
            )
        target_building = service.render(
            "%1GG | %1GN | %1GT",
            unit_key="same-entry",
            label="STRONG_PLACEHOLDERS",
            file_rel="Text.dbt",
            kind="dbt",
            target=True,
        )
        if target_building.display_text != "天主教堂『全能的上帝』 | 全能的上帝 | 天主教堂":
            raise AssertionError(
                "localized building placeholders did not retain the same entity and game format: "
                f"{target_building.display_text!r}"
            )
        character_projection = (
            "Jack Smith | Jack | Citizen Jack Smith, Mayor in London | "
            "Patron | Citizen | Mayor | Smith | Baker | Worker"
        )
        if character_projection not in strong_placeholders.display_text:
            raise AssertionError(
                "character placeholders did not project one coherent character entity: "
                f"{strong_placeholders.display_text!r}"
            )
        target_character = service.render(
            "%4SN | %4SV | %4SZ | %4SK | %4ST | %4SA | %4SD | %4SB | %4SL",
            unit_key="same-entry",
            label="STRONG_PLACEHOLDERS",
            file_rel="Text.dbt",
            kind="dbt",
            target=True,
        )
        if target_character.display_text != (
            "杰克 史密斯 | 杰克 | 市民 杰克·史密斯，伦敦的市长 | "
            "庇护者 | 市民 | 市长 | 史密斯 | 面包师 | 工人"
        ):
            raise AssertionError(
                "localized character placeholders did not use the game description template: "
                f"{target_character.display_text!r}"
            )
        target_description_without_office = service.render(
            "%4SZ",
            unit_key="same-entry",
            label="STRONG_PLACEHOLDERS",
            file_rel="Text.dbt",
            kind="dbt",
            target=True,
        )
        if target_description_without_office.display_text != "市民 杰克·史密斯":
            raise AssertionError(
                "full character descriptions invented an office without placeholder evidence: "
                f"{target_description_without_office.display_text!r}"
            )
        cross_suffix_projection = service.render(
            "%1GG | %1NAME | %4SN | %4NAME | %5DN | %5NAME | %5DS",
            unit_key="same-entry",
            label="CROSS_SUFFIX_PLACEHOLDERS",
            file_rel="Text.dbt",
            kind="dbt",
            target=False,
        )
        cross_parts = cross_suffix_projection.display_text.split(" | ")
        if cross_parts[1:6] != [
            "The Almighty",
            "Jack Smith",
            "Jack Smith",
            "Smith",
            "Smith",
        ]:
            raise AssertionError(
                "NAME did not reuse the strongest semantic projection for the same argument: "
                f"{cross_suffix_projection.display_text!r}"
            )
        if not any(
            atom.text == GLYPH_MARK and atom.glyph_id is not None
            for atom in cross_suffix_projection.atoms
        ):
            raise AssertionError("the dynasty entity did not project a crest glyph for DS")
        literal_projection = service.render(
            "%1n | %2i | %3f | %4t | %5s",
            unit_key="same-entry",
            label="LITERAL_PLACEHOLDERS",
            file_rel="Text.dbt",
            kind="dbt",
            target=False,
            references=(
                CodeReference(
                    "LITERAL_PLACEHOLDERS",
                    temp / "Scripts" / "LiteralValues.lua",
                    1,
                    1,
                    "MsgQuick",
                    1,
                    (
                        '""',
                        '"@L_LITERAL_PLACEHOLDERS"',
                        "42",
                        "7",
                        "3.50",
                        "1250",
                        '"ready"',
                    ),
                    runtime_arguments=("42", "7", "3.50", "1250", '"ready"'),
                    role="body",
                ),
            ),
        )
        if literal_projection.display_text.replace(GLYPH_MARK, "") != "42 | 7 | 3.5 | 1250 | ready":
            raise AssertionError(
                "preview did not display direct scalar values supplied by the caller: "
                f"{literal_projection.display_text!r}"
            )
        if not any(atom.glyph_id == 2002 for atom in literal_projection.atoms):
            raise AssertionError("literal money preview lost the game's coin symbol")

        quoted_placeholder = service.render(
            "with >%2l< and %1NAMEsuffix",
            unit_key="same-entry",
            label="QUOTED_PLACEHOLDER",
            file_rel="Text.dbt",
            kind="dbt",
            target=False,
        )
        if "%2l" in quoted_placeholder.display_text or "%1NAME" in quoted_placeholder.display_text:
            raise AssertionError("placeholders inside >...< or followed by plain text were left raw")
        if "『" not in quoted_placeholder.display_text or "』" not in quoted_placeholder.display_text:
            raise AssertionError(">...< should use the game's visible corner quotes")
        open_quote = service.render(
            "before >unfinished and closed<",
            unit_key="same-entry",
            label="OPEN_QUOTE",
            file_rel="Text.dbt",
            kind="dbt",
            target=False,
        )
        if open_quote.display_text != "before 『unfinished and closed』":
            raise AssertionError(
                f"unpaired angle markers did not use game corner quotes: {open_quote.display_text!r}"
            )
        service.update_project_localization(
            "_RELATED_TEXT_+0",
            "first$Nsecond >quoted<",
            "第一行$N第二行 >引用<",
        )
        related_newline = service.render(
            "%1l",
            unit_key="same-entry",
            label="RELATED_NEWLINE",
            file_rel="Text.dbt",
            kind="dbt",
            target=False,
            references=(
                CodeReference(
                    "RELATED_NEWLINE",
                    temp / "Scripts" / "Related.lua",
                    1,
                    1,
                    "MsgQuick",
                    1,
                    runtime_arguments=("RelatedLabel",),
                    runtime_argument_values=(("@L_RELATED_TEXT_+0",),),
                    runtime_argument_kinds=(("label",),),
                    role="body",
                ),
            ),
        )
        if related_newline.display_text != "first\nsecond 『quoted』":
            raise AssertionError(
                f"format controls in a referenced label were not rendered: {related_newline.display_text!r}"
            )

        semantic = service.render(
            "%1SN >%2l< >%3l<",
            unit_key="same-entry",
            label="SEMANTIC_PLACEHOLDER",
            file_rel="Text.dbt",
            kind="dbt",
            target=False,
            references=(
                CodeReference(
                    "SEMANTIC_PLACEHOLDER",
                    temp / "Scripts" / "Semantic.lua",
                    1,
                    1,
                    "MsgBox",
                    2,
                    (
                        '"Actor"',
                        "nil",
                        '"@L_SEMANTIC_PLACEHOLDER"',
                        'GetID("Owner")',
                        "citylabel",
                        "ItemLabel[item1]",
                    ),
                ),
            ),
        )
        if "Jack Smith" not in semantic.display_text or "London" not in semantic.display_text or "Ruby ring" not in semantic.display_text:
            raise AssertionError(f"code-semantic placeholder preview did not use character/city/item fallbacks: {semantic.display_text!r}")
        head_body_semantic = service.render(
            "%1NAME",
            unit_key="same-entry",
            label="SEMANTIC_HEAD",
            file_rel="Text.dbt",
            kind="dbt",
            target=False,
            references=(
                CodeReference(
                    "SEMANTIC_HEAD",
                    temp / "Scripts" / "HeadBody.lua",
                    1,
                    1,
                    "MsgBoxNoWait",
                    2,
                    (
                        '"Actor"',
                        "false",
                        '"@L_SEMANTIC_HEAD"',
                        '"@L_SEMANTIC_BODY"',
                        'GetID("Owner")',
                    ),
                ),
            ),
        )
        if head_body_semantic.display_text != "Object 1":
            raise AssertionError(
                "head/body argument mapping forced an unproven object type for plain NAME"
            )
        dynamic_label_argument = service.render(
            "%1l hereby demands %3t from %2DN. signed %4l",
            unit_key="same-entry",
            label="WAR_END_LOOSE_BODY_+1",
            file_rel="Text.dbt",
            kind="dbt",
            target=False,
            references=(
                CodeReference(
                    "WAR_END_LOOSE_BODY_+1",
                    temp / "Scripts" / "War.lua",
                    1,
                    1,
                    "feedback_MessagePolitics",
                    2,
                    (
                        '"family"',
                        '"@L_WAR_END_LOOSE_HEAD_+1"',
                        '"@L_WAR_END_LOOSE_BODY_+1"',
                        '"@L_SCENARIO_WAR_"..enemy.."_+0"',
                        'GetDynastyID("family")',
                        "dynmoney",
                        '"@L_SCENARIO_LORD_"..enemy.."_+1"',
                    ),
                ),
            ),
        )
        if "The German Empire" not in dynamic_label_argument.display_text:
            raise AssertionError(
                "dynamic @L arguments after BODY should be used as placeholder values, "
                f"got {dynamic_label_argument.display_text!r}"
            )
        interaction_button = service.render(
            "%1SV",
            unit_key="same-entry",
            label="MEASURE_BUYGOLDRING_OPTION_+0",
            file_rel="Text.dbt",
            kind="dbt",
            target=False,
            references=(
                CodeReference(
                    "MEASURE_BUYGOLDRING_OPTION_+0",
                    temp / "Scripts" / "Interaction.lua",
                    1,
                    1,
                    "MsgSayInteraction",
                    3,
                    (
                        '""',
                        '"Child"',
                        '""',
                        '"@B[0,@L_MEASURE_BUYGOLDRING_OPTION_+0]".."@B[1,@L_MEASURE_BUYGOLDRING_OPTION_+1]"',
                        '"@L_MEASURE_BUYGOLDRING_HEAD_+0"',
                        '"@L_MEASURE_BUYGOLDRING_QUESTION_+0"',
                        'GetID("")',
                        "Cost",
                    ),
                ),
            ),
        )
        interaction_question = service.render(
            "%2t",
            unit_key="same-entry",
            label="MEASURE_BUYGOLDRING_QUESTION_+0",
            file_rel="Text.dbt",
            kind="dbt",
            target=False,
            references=(
                CodeReference(
                    "MEASURE_BUYGOLDRING_QUESTION_+0",
                    temp / "Scripts" / "Interaction.lua",
                    1,
                    1,
                    "MsgSayInteraction",
                    5,
                    (
                        '""',
                        '"Child"',
                        '""',
                        '"@B[0,@L_MEASURE_BUYGOLDRING_OPTION_+0]".."@B[1,@L_MEASURE_BUYGOLDRING_OPTION_+1]"',
                        '"@L_MEASURE_BUYGOLDRING_HEAD_+0"',
                        '"@L_MEASURE_BUYGOLDRING_QUESTION_+0"',
                        'GetID("")',
                        "Cost",
                    ),
                ),
            ),
        )
        if "Jack" not in interaction_button.display_text:
            raise AssertionError(f"MsgSayInteraction button placeholders should start after head/body labels: {interaction_button.display_text!r}")
        if not any(atom.glyph_id == 2002 for atom in interaction_question.atoms):
            raise AssertionError("MsgSayInteraction body placeholders did not map %2t to the second runtime argument")
        contract_args = (
            '""',
            '""',
            '"@P@B[1,@L_CONTRACTARSENAL_HIRE_OPTION_+0]"',
            '"@L_CONTRACTARSENAL_HIRE_MAIN_HEAD_+0"',
            '"@L_CONTRACTARSENAL_HIRE_MAIN_BODY_+1"',
            '"_WAR_MERC_"..label.."_MALE_+0"',
            '"_WAR_MERC_"..label.."_MORE_+0"',
            "cost",
            "cost*5",
            "cost*10",
        )
        contract_button = service.render(
            "%1l %2l %3t",
            unit_key="same-entry",
            label="CONTRACTARSENAL_HIRE_OPTION_+0",
            file_rel="Text.dbt",
            kind="dbt",
            target=False,
            references=(
                CodeReference(
                    "CONTRACTARSENAL_HIRE_OPTION_+0",
                    temp / "Scripts" / "ContractArsenal.lua",
                    1,
                    1,
                    "MsgBox",
                    2,
                    contract_args,
                ),
            ),
        )
        contract_body = service.render(
            "%1l %2l %3t",
            unit_key="same-entry",
            label="CONTRACTARSENAL_HIRE_MAIN_BODY_+1",
            file_rel="Text.dbt",
            kind="dbt",
            target=False,
            references=(
                CodeReference(
                    "CONTRACTARSENAL_HIRE_MAIN_BODY_+1",
                    temp / "Scripts" / "ContractArsenal.lua",
                    1,
                    1,
                    "MsgBox",
                    4,
                    contract_args,
                ),
            ),
        )
        for document in (contract_button, contract_body):
            if "Trooper" not in document.display_text or "Pikeman" not in document.display_text:
                raise AssertionError(f"dynamic DB label concatenation did not resolve mercenary labels: {document.display_text!r}")
            if not any(atom.glyph_id == 2002 for atom in document.atoms):
                raise AssertionError("dynamic DB label argument mapping skipped the cost argument")
        scripts_root = temp / "Scripts"
        scripts_root.mkdir(exist_ok=True)
        variable_labels_path = scripts_root / "VariableLabels.lua"
        variable_labels_path.write_text(
            "\n".join(
                (
                    'local trooperlabel',
                    'trooperlabel = "_WAR_MERC_TROOPER_MORE_+0"',
                    'local ort = "@L_DIPLOMAT_NAME_"..enemy.."_+0"',
                    'local stimmung = ""',
                    'stimmung = stimmung.."@L_MEASURE_WUERDENTRAGEREMPFANGEN_BODY_+2"',
                    'MsgBox("", "", "", "@L_VARIABLE_HEAD_+0", "@L_VARIABLE_BODY_+0", trooperlabel)',
                    'MsgBox("", "", "", "@L_MEASURE_WUERDENTRAGEREMPFANGEN_HEAD_+0", "@L_MEASURE_WUERDENTRAGEREMPFANGEN_BODY_+1", stimmung, ort)',
                )
            ),
            encoding="utf-8",
        )
        variable_label = service.render(
            "%1l",
            unit_key="same-entry",
            label="VARIABLE_BODY_+0",
            file_rel="Text.dbt",
            kind="dbt",
            target=False,
            references=(
                CodeReference(
                    "VARIABLE_BODY_+0",
                    variable_labels_path,
                    6,
                    1,
                    "MsgBox",
                    4,
                    ('""', '""', '""', '"@L_VARIABLE_HEAD_+0"', '"@L_VARIABLE_BODY_+0"', "trooperlabel"),
                ),
            ),
        )
        nested_variable_label = service.render(
            "%1l",
            unit_key="same-entry",
            label="MEASURE_WUERDENTRAGEREMPFANGEN_BODY_+1",
            file_rel="Text.dbt",
            kind="dbt",
            target=False,
            references=(
                CodeReference(
                    "MEASURE_WUERDENTRAGEREMPFANGEN_BODY_+1",
                    variable_labels_path,
                    7,
                    1,
                    "MsgBox",
                    4,
                    (
                        '""',
                        '""',
                        '""',
                        '"@L_MEASURE_WUERDENTRAGEREMPFANGEN_HEAD_+0"',
                        '"@L_MEASURE_WUERDENTRAGEREMPFANGEN_BODY_+1"',
                        "stimmung",
                        "ort",
                    ),
                ),
            ),
        )
        if "Pikeman" not in variable_label.display_text:
            raise AssertionError(f"label variables should resolve through simple code assignments: {variable_label.display_text!r}")
        if "The diplomat from Denmark is impressed" not in nested_variable_label.display_text:
            raise AssertionError(f"nested label variables should reuse the same runtime arguments: {nested_variable_label.display_text!r}")
        suffix_priority = service.render(
            "%1SA %1SN",
            unit_key="same-entry",
            label="SUFFIX_PRIORITY",
            file_rel="Text.dbt",
            kind="dbt",
            target=False,
            references=(
                CodeReference(
                    "SUFFIX_PRIORITY",
                    temp / "Scripts" / "Office.lua",
                    1,
                    1,
                    "MsgNews",
                    1,
                    ('"@L_SUFFIX_PRIORITY"', 'GetID("MrTorture")', 'GetID("Destination")'),
                ),
            ),
        )
        if "Jack Smith" not in suffix_priority.display_text or suffix_priority.display_text.startswith("Jack Smith "):
            raise AssertionError("explicit suffix semantics should beat GetID-based code semantics")
        name_city = service.render(
            "%1NAME",
            unit_key="same-entry",
            label="NAME_CITY",
            file_rel="Text.dbt",
            kind="dbt",
            target=False,
            references=(
                CodeReference(
                    "NAME_CITY",
                    temp / "Scripts" / "City.lua",
                    1,
                    1,
                    "MsgNews",
                    0,
                    ('"@L_NAME_CITY"', 'GetSettlementID("Officer")'),
                ),
            ),
        )
        if "London" not in name_city.display_text:
            raise AssertionError("NAME should use a city only when its own caller argument proves settlement semantics")
        name_unknown = service.render(
            "%1NAME",
            unit_key="same-entry",
            label="NAME_UNKNOWN",
            file_rel="Text.dbt",
            kind="dbt",
            target=False,
            references=(
                CodeReference(
                    "NAME_UNKNOWN",
                    temp / "Scripts" / "Ship.lua",
                    1,
                    1,
                    "MsgNews",
                    0,
                    ('"@L_NAME_UNKNOWN"', 'GetID("Destination")'),
                ),
            ),
        )
        if name_unknown.display_text != "Object 1":
            raise AssertionError(
                f"NAME inferred a concrete object type without caller evidence: {name_unknown.display_text!r}"
            )
        weak_priority = service.render(
            "%2l",
            unit_key="same-entry",
            label="WEAK_PRIORITY",
            file_rel="Text.dbt",
            kind="dbt",
            target=False,
            references=(
                CodeReference("WEAK_PRIORITY", temp / "Scripts" / "Building.lua", 1, 1, "MsgQuick", 1, ('""', '"@L_WEAK_PRIORITY_+3"', 'GetID("")', 'GetID("WorkBuilding")')),
                CodeReference("WEAK_PRIORITY", temp / "Scripts" / "Item.lua", 1, 1, "MsgQuick", 1, ('""', '"@L_WEAK_PRIORITY_+0"', 'GetID("")', 'ItemLabel[item1]')),
            ),
        )
        if "Ruby ring" not in weak_priority.display_text:
            raise AssertionError("weak label placeholders should prefer item semantics across fallback references")

        header = service.render(
            "$[Header text$]",
            unit_key="same-entry",
            file_rel="Text.dbt",
            kind="dbt",
            target=False,
        )
        if "Header text" not in header.display_text or any(atom.glyph_id is not None for atom in header.atoms):
            raise AssertionError("$[...$] header decoration was confused with $S[...] symbol syntax")

        tooltip = service.render(
            "%gold% %gold_icon%%n%%char_name% $S[2012]",
            unit_key="same-entry",
            file_rel="Tooltips.dbt",
            kind="dbt",
            target=True,
        )
        if (
            "\n" not in tooltip.display_text
            or not any(atom.glyph_id == 2002 for atom in tooltip.atoms)
            or not any(atom.glyph_id == 2012 for atom in tooltip.atoms)
        ):
            raise AssertionError("Tooltips.dbt named macros did not produce a localized preview")

        guide = service.render(
            "<header>Controls</header><text>{key:CURSOR_UP}</text><list><item>First</item><item>Second</item></list><table><row><cell>A</cell><cell>B</cell></row></table>",
            unit_key="same-entry",
            file_rel="Guides/Controls.txt",
            kind="text",
            target=True,
        )
        if "CURSOR UP" not in guide.display_text or "<header>" in guide.display_text:
            raise AssertionError("Guide markup did not use the Guide preview dialect")
        if "\n\n" in guide.display_text:
            raise AssertionError(f"Guide preview spacing was too loose: {guide.display_text!r}")
        if any(atom.replacement for atom in guide.atoms):
            raise AssertionError("Guide preview should render as final style without placeholder underlines")
        guide_crash = service.render(
            '<text>"Crash risk"</text>',
            unit_key="same-entry",
            file_rel="Guides/Controls.txt",
            kind="text",
            target=False,
        )
        if "crash" not in guide_crash.display_text.casefold() or "Crash risk" in guide_crash.display_text:
            raise AssertionError("Guide preview should be blocked by plain double quotes")
    finally:
        safe_rmtree(temp)


def assert_preview_editor_restores_raw_placeholder_on_edit() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QTextCharFormat, QTextCursor, QTextImageFormat
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from .app import PreviewPlainTextEdit

    app = QApplication.instance()
    created_app = app is None
    if app is None:
        app = QApplication([])
    service = PreviewService(None, "#chinese")
    editor = PreviewPlainTextEdit()
    editor.set_preview_builder(
        lambda text: service.render(
            text,
            unit_key="editor-entry",
            file_rel="Text.dbt",
            kind="dbt",
            target=False,
        ),
        lambda _glyph_id: None,
    )
    editor.setPlainText("%1SN")
    editor.set_preview_enabled(True)
    if editor.toPlainText() != "%1SN" or editor.rendered_preview.display_text == "%1SN":
        raise AssertionError("input preview did not preserve raw placeholder text behind the visual replacement")
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    editor.setTextCursor(cursor)
    QTest.keyClick(editor, Qt.Key.Key_Backspace)
    app.processEvents()
    if editor.toPlainText() != "%1S" or editor.rendered_preview.display_text != "%1S":
        raise AssertionError("editing preview content did not immediately fall back to the edited raw placeholder")
    editor.setPlainText("hello %1SN world")
    preview_text = editor.rendered_preview.display_text
    cursor = editor.textCursor()
    cursor.setPosition(preview_text.index("Character") + 2)
    editor.setTextCursor(cursor)
    QTest.keyClick(editor, Qt.Key.Key_Backspace)
    app.processEvents()
    document_cursor = QTextCursor(editor.document())
    document_cursor.select(QTextCursor.SelectionType.Document)
    char_format = document_cursor.charFormat()
    if char_format.background().style() != Qt.BrushStyle.NoBrush:
        raise AssertionError("editing inside a placeholder leaked its preview background to the entire editor")
    if char_format.underlineStyle() != QTextCharFormat.UnderlineStyle.NoUnderline:
        raise AssertionError("editing inside a placeholder leaked its preview underline to the entire editor")
    editor.set_zoom_factor(1.0)
    base_size = editor.document().defaultFont().pointSizeF()
    editor.set_zoom_factor(1.1)
    zoomed_size = editor.document().defaultFont().pointSizeF()
    if abs(zoomed_size / base_size - 1.1) > 0.01:
        raise AssertionError("editor zoom did not apply the requested percentage")
    editor.setPlainText("zoom %1SN")
    if abs(editor.document().defaultFont().pointSizeF() - zoomed_size) > 0.01:
        raise AssertionError("rebuilding a preview lost the editor zoom")
    glyph = QImage(10, 20, QImage.Format.Format_RGBA8888)
    glyph.fill(0xFFFFFFFF)
    editor.set_preview_builder(
        lambda text: service.render(
            text,
            unit_key="editor-entry",
            file_rel="Text.dbt",
            kind="dbt",
            target=False,
        ),
        lambda _glyph_id: glyph,
    )
    editor.setPlainText("$S[2012]OBJ")
    glyph_span = next(span for span in editor.rendered_preview.spans if span.atom.glyph_id is not None)
    glyph_cursor = QTextCursor(editor.document())
    glyph_cursor.setPosition(glyph_span.display_start)
    glyph_cursor.movePosition(QTextCursor.MoveOperation.NextCharacter, QTextCursor.MoveMode.KeepAnchor)
    glyph_format = QTextImageFormat(glyph_cursor.charFormat())
    glyph_width = glyph_format.width()
    glyph_height = glyph_format.height()
    if (
        not glyph_format.isValid()
        or glyph_height <= 0
        or abs(glyph_width / glyph_height - 0.5) > 0.01
    ):
        raise AssertionError("inline game glyph did not preserve its aspect ratio")
    editor.close()
    if created_app:
        app.quit()


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

    bounded = OperationHistory(max_operations=2, max_changes=2, max_text_chars=8)
    for number in range(3):
        bounded.push(TranslationOperation(str(number), (UnitChange(str(number), "a", "b"),)))
    if bounded.take_undo().label != "2" or bounded.take_undo().label != "1" or bounded.take_undo() is not None:
        raise AssertionError("bounded undo history did not discard only its oldest operation")
    oversized = OperationHistory(max_operations=1, max_changes=1, max_text_chars=1)
    latest = TranslationOperation("large", (UnitChange("1", "before", "after"), UnitChange("2", "x", "y")))
    oversized.push(latest)
    if oversized.take_undo() != latest:
        raise AssertionError("a single operation larger than the history budget was not kept undoable")


def assert_git_history(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_git_")
    try:
        git = LanguageGit(temp)
        git.ensure_repository(AppSettings())
        project = Project.load(temp, "#chinese")
        same_as_source = next(
            item
            for item in project.units
            if item.todo_reason == TODO_REASON_SAME_AS_SOURCE and item.ref.target_row is not None
        )
        same_previous = same_as_source.current_text
        same_as_source.set_text(same_previous + " history update")
        same_result = project.save([same_as_source])
        same_commit = git.commit_saved(
            same_result.changed_files, same_result.saved_units, same_result.deleted_units
        )
        if same_commit is None:
            raise AssertionError("Git commit was not created for a same-as-source translation update")
        if "update 1" not in same_commit.subject or "add 1" in same_commit.subject:
            raise AssertionError("same-as-source commit summary was incorrectly counted as an addition")
        same_entries = git.entries_for_commit(same_commit.full_hash)
        same_entry = next(
            (
                entry
                for entry in same_entries
                if entry.label == same_as_source.label and entry.field_name == same_as_source.field_name
            ),
            None,
        )
        if same_entry is None or same_entry.kind != "更新":
            raise AssertionError("a non-empty translation matching its source was incorrectly logged as an addition")
        if same_entry.previous_text != same_previous or same_entry.translated_text != same_as_source.current_text:
            raise AssertionError("same-as-source history did not preserve the real before and after text")
        project.reload_saved_files(same_result.changed_files)

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


def assert_history_dialog_search_and_entry_timeline() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from .app import HistoryDialog

    commits = [
        GitCommit("b" * 40, "bbbbbbb", datetime.fromtimestamp(2), "second change"),
        GitCommit("a" * 40, "aaaaaaa", datetime.fromtimestamp(1), "first change"),
    ]
    early = TranslationLogEntry("新增", "Text.dbt", "10", "Greeting", "Text", "Hello", "First")
    later = TranslationLogEntry("更新", "Text.dbt", "10", "Greeting", "Text", "Hello", "Second", "First")
    entries = {commits[0].full_hash: [later], commits[1].full_hash: [early]}

    class FakeHistoryGit:
        project_root = Path("HistoryFixture")
        language = "#chinese"

        def list_all_commits(self):
            return list(commits)

        def entries_for_commit(self, commit: str):
            return list(entries.get(commit, ()))

        def entries_for_commits(self, hashes):
            return [entry for commit in hashes for entry in entries.get(commit, ())]

    app = QApplication.instance()
    created_app = app is None
    if app is None:
        app = QApplication([])
    dialog = HistoryDialog(
        FakeHistoryGit(),  # type: ignore[arg-type]
        focus_key=("Text.dbt", "10", "Greeting", "Text"),
    )
    dialog.show()
    try:
        for _ in range(100):
            app.processEvents()
            if dialog.entries.count() == 1 and dialog._index_worker is None:
                break
            QTest.qWait(20)
        if dialog.entries.count() != 1:
            raise AssertionError("entry-history index did not group repeated changes by translation field")
        if dialog.entries.currentRow() != 0:
            raise AssertionError("opening history for a unit did not select its entry timeline")
        content = dialog.content.toPlainText()
        if "Greeting" not in content or "First" not in content or "Second" not in content or "2" not in content:
            raise AssertionError("entry timeline did not show its count and every before/after value")

        dialog.commit_search.setText("Second")
        app.processEvents()
        if dialog.commits.item(0).isHidden() or not dialog.commits.item(1).isHidden():
            raise AssertionError("commit search did not include indexed translation content")
        dialog.entry_search.setText("Hello Greeting")
        app.processEvents()
        if dialog.entries.item(0).isHidden():
            raise AssertionError("entry-history search did not match source text and label together")
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()
        if created_app:
            app.quit()


def assert_git_subprocess_hides_console() -> None:
    kwargs = LanguageGit._subprocess_kwargs(text=True)
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        if kwargs.get("creationflags") != subprocess.CREATE_NO_WINDOW:
            raise AssertionError("Git subprocesses did not request CREATE_NO_WINDOW on Windows")
        if "startupinfo" not in kwargs:
            raise AssertionError("Git subprocesses did not provide hidden-window startup info on Windows")


def assert_git_subprocess_timeout_is_reported() -> None:
    git = LanguageGit(Path(tempfile.gettempdir()), enable_codec=False)
    original_run = subprocess.run

    def timed_out(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("git", LanguageGit.COMMAND_TIMEOUT_SECONDS)

    subprocess.run = timed_out
    try:
        try:
            git._run("status")
        except GitError as exc:
            if str(LanguageGit.COMMAND_TIMEOUT_SECONDS) not in str(exc):
                raise AssertionError("Git timeout error did not report its bounded wait") from exc
        else:
            raise AssertionError("Git timeout did not become a recoverable GitError")
    finally:
        subprocess.run = original_run


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
    same_source_early = TranslationLogEntry("更新", "Text.dbt", "12", "Same", "Text", "Same", "First", "Same")
    same_source_later = TranslationLogEntry("更新", "Text.dbt", "12", "Same", "Text", "Same", "Second", "First")
    same_source_merged = combine_entries(((same_source_early,), (same_source_later,)))
    if len(same_source_merged) != 1 or same_source_merged[0].kind != "更新":
        raise AssertionError("combined history reclassified a same-as-source update as an addition")
    if same_source_merged[0].before_text != "Same" or same_source_merged[0].translated_text != "Second":
        raise AssertionError("combined same-as-source history lost its real before or after text")
    reverted = TranslationLogEntry("更新", "Text.dbt", "10", "Greeting", "Text", "Hello", "Hello", "您好")
    if combine_entries(((early,), (reverted,))):
        raise AssertionError("combined history kept an entry whose final translation reverted to the starting text")
    rendered = format_entries(combined)
    if rendered.count("Text.dbt") != 1 or rendered.count("Tooltips.dbt") != 1:
        raise AssertionError("history format repeated a file heading")
    if "Hello → 您好" not in rendered:
        raise AssertionError("history format did not render the final translation")


def assert_git_history_keeps_dbt_changes_without_source_row() -> None:
    temp = Path(tempfile.gettempdir()) / f"translator_tool_smoke_git_missing_source_{uuid.uuid4().hex[:8]}"
    try:
        temp.mkdir(parents=True, exist_ok=True)
        git = LanguageGit(temp)
        source = (
            b"Table Description:\n"
            b'"id" INT 0 |"label" STRING 0 |"english" STRING 0 |\n'
            b'2 "_OTHER_+0" "Source" |\n'
        )
        before = (
            b"Table Description:\n"
            b'"id" INT 0 |"label" STRING 0 |"chinese" STRING 0 |\n'
            b'1 "_WOA_CREATEDBY_+0" "A %1s, %3s %4n B" |\n'
        )
        after = (
            b"Table Description:\n"
            b'"id" INT 0 |"label" STRING 0 |"chinese" STRING 0 |\n'
            b'1 "_WOA_CREATEDBY_+0" "A %1s, %4n %3s B" |\n'
        )
        entries = git._dbt_entries("Text.dbt", source, before, after)
        if len(entries) != 1:
            raise AssertionError(f"history dropped a DBT change whose source row was missing: {entries!r}")
        if entries[0].label != "_WOA_CREATEDBY_+0" or entries[0].translated_text != "A %1s, %4n %3s B":
            raise AssertionError("history did not preserve the DBT target diff when source row was missing")
    finally:
        safe_rmtree(temp)


def assert_git_history_keeps_selected_commit_entries(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_git_selected_entries_")
    try:
        git = LanguageGit(temp)
        git.ensure_repository(AppSettings())
        project = Project.load(temp, "#chinese")
        unit = next(item for item in project.units if item.file_rel == "Text.dbt" and item.source_text)
        first = "A %1s, %3s %4n B"
        second = "A %1s, %4n %3s B"
        unit.set_text(first)
        first_result = project.save([unit])
        first_commit = git.commit_saved(first_result.changed_files, first_result.saved_units, first_result.deleted_units)
        if first_commit is None:
            raise AssertionError("first selected-entry history commit was not created")
        project = Project.load(temp, "#chinese")
        unit = next(item for item in project.units if item.uid == unit.uid)
        unit.set_text(second)
        second_result = project.save([unit])
        second_commit = git.commit_saved(second_result.changed_files, second_result.saved_units, second_result.deleted_units)
        if second_commit is None:
            raise AssertionError("second selected-entry history commit was not created")
        project = Project.load(temp, "#chinese")
        unit = next(item for item in project.units if item.uid == unit.uid)
        unit.set_text(unit.source_text)
        revert_result = project.save([unit])
        revert_commit = git.commit_saved(revert_result.changed_files, revert_result.saved_units, revert_result.deleted_units)
        if revert_commit is None:
            raise AssertionError("revert selected-entry history commit was not created")
        entries = git.entries_for_commits((first_commit.full_hash, second_commit.full_hash, revert_commit.full_hash))
        if not entries:
            raise AssertionError("history returned an empty net result even though selected commits changed text")
        if not any(entry.translated_text == second for entry in entries):
            raise AssertionError("history did not preserve the placeholder reorder update")
    finally:
        safe_rmtree(temp)


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
    assert_diagnostics_are_bounded_and_content_free()
    assert_font_glyph_validation(root)
    assert_round_trip(root)
    assert_statuses(root)
    assert_loaded_order_matches_file_lines(root)
    assert_local_project_roots_detect_sources_projects()
    assert_discover_game_source_projects_detects_vanilla_and_mods()
    assert_code_reference_index_avoids_db_and_uses_vanilla_fallback()
    assert_display_call_contracts_match_engine_signatures()
    assert_code_semantics_are_scope_and_role_aware()
    assert_code_semantics_resolve_local_function_returns()
    assert_code_semantics_follow_fields_panels_and_initdata()
    assert_feedback_message_contracts_do_not_depend_on_label_names()
    assert_dynamic_table_and_engine_label_semantics_are_preserved()
    assert_cross_file_return_labels_flow_only_to_real_callers()
    assert_cross_file_function_summaries_bind_arguments_and_expand_returns()
    assert_cross_file_function_summaries_follow_nested_dependencies()
    assert_code_index_handles_families_and_binary_gui()
    assert_placeholder_reference_selection_is_coherent()
    assert_placeholder_values_avoid_ambiguous_random_branches()
    assert_variadic_runtime_arguments_map_to_placeholder_positions()
    assert_preview_context_selection_keeps_arguments_and_style_coherent()
    assert_preview_context_selection_keeps_the_current_dynamic_branch()
    assert_preview_context_selection_prefers_displayed_runtime_labels()
    assert_preview_context_selection_understands_returned_label_roles()
    assert_project_localization_updates_invalidate_placeholder_previews()
    assert_editor_changes_reach_preview_localization()
    assert_game_preview_parts_use_the_selected_call_site()
    assert_lazy_code_index_prioritizes_requested_labels_and_invalidates_cache()
    assert_lazy_code_index_links_cached_cross_file_facts()
    assert_lazy_code_index_loads_cached_value_providers()
    assert_lazy_code_index_survives_unwritable_cache()
    assert_code_index_requests_selected_and_visible_rows_without_moving_viewport()
    assert_stale_code_index_workers_are_released()
    assert_code_window_context_extracts_window_labels_and_buttons()
    assert_code_preview_unit_lookup_accepts_leading_underscore_labels()
    assert_game_preview_draws_all_buttons()
    assert_onscreen_help_preview_pairs_name_and_description()
    assert_name_tooltip_preview_pairs_title_and_body()
    assert_engine_owned_preview_styles()
    assert_startup_prefers_local_sources_over_game_root()
    assert_sync_vanilla_sources_only_imports_originals()
    assert_sync_source_project_invalidates_changed_translations(root)
    assert_failed_source_sync_restores_workflow_cache(root)
    assert_save_existing(root)
    assert_save_auto_formats_color_tokens(root)
    assert_save_guides_plain_text_uses_source_profile(root)
    assert_save_creates_missing_target_dbt_incrementally(root)
    assert_save_removes_extra_target_row(root)
    assert_save_missing(root)
    assert_failed_save_does_not_mutate_loaded_documents(root)
    assert_atomic_write_many_rolls_back_partial_commit()
    assert_recovery_draft_round_trip(root)
    assert_recovery_draft_limits_match(root)
    assert_project_edit_state_keeps_confirmation_consistent(root)
    assert_large_batch_save_stays_interactive(root)
    refresh_temp = make_temp_project(root, "translator_tool_saved_file_refresh_")
    try:
        assert_saved_file_refresh(refresh_temp, tool_root())
    finally:
        safe_rmtree(refresh_temp)
    assert_entry_clipboard_decoder()
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
    assert_workflow_cache_updates_once(root)
    assert_manual_status_cache()
    assert_operation_history()
    assert_ai_token_protection()
    assert_ai_translation_context()
    assert_linebreak_format_is_ignored()
    assert_guild2_format_grammar()
    assert_format_dialects_are_isolated()
    assert_reordered_tokens_are_not_highlighted_as_missing()
    assert_preview_i18n_and_symbol_mapping()
    assert_preview_editor_restores_raw_placeholder_on_edit()
    assert_llm_suggestion_stream()
    assert_llm_suggestion_context_prompt()
    assert_git_history(root)
    assert_history_dialog_search_and_entry_timeline()
    assert_git_subprocess_hides_console()
    assert_git_subprocess_timeout_is_reported()
    assert_tracked_git_commit_skips_redundant_add()
    assert_git_commit_display()
    assert_git_pending_is_scoped_to_active_language(root)
    assert_git_history_list_is_scoped_to_active_language(root)
    assert_git_recovers_stale_index_lock(root)
    assert_combined_git_history_format()
    assert_git_history_keeps_dbt_changes_without_source_row()
    assert_git_history_keeps_selected_commit_entries(root)
    print("translator_tool self-test ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
