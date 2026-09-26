from __future__ import annotations

from time import perf_counter


LARGE_BATCH_MIN_ENTRIES = 10_000
LARGE_BATCH_SAVE_LIMIT_SECONDS = 8.0
CLIPBOARD_DECODE_LIMIT_SECONDS = 2.0
SAVED_FILE_REFRESH_LIMIT_SECONDS = 1.5
AI_CONTEXT_BUILD_LIMIT_SECONDS = 0.5
CACHED_SEARCH_FILTER_LIMIT_SECONDS = 0.25
LABEL_CATALOG_FAMILY_LOOKUP_LIMIT_SECONDS = 1.0
SINGLE_SAVE_REFRESH_LIMIT_SECONDS = 0.75
FORMAT_SCAN_SLICE_LIMIT_SECONDS = 0.15
HISTORY_LONG_DIFF_LIMIT_SECONDS = 0.2
HISTORY_SEARCH_PREPARE_LIMIT_SECONDS = 1.5


def assert_history_long_text_and_search_stay_bounded() -> None:
    from datetime import datetime
    import threading

    from ..git_history import GitCommit, TranslationLogEntry
    from ..history_render import inline_diff_html
    from ..history_search import build_history_search_index, matches_terms, query_terms

    text = "正文 repeated " * 3000
    started = perf_counter()
    rendered = inline_diff_html(text, text + "x")
    assert_within_budget("36k-character appended history diff", perf_counter() - started, HISTORY_LONG_DIFF_LIMIT_SECONDS)
    if not rendered.endswith('<span class="diff-add">x</span>'):
        raise AssertionError("large appended history diff did not isolate the new character")
    started = perf_counter()
    inline_diff_html("AB" * 18_000, "BA" * 18_000)
    assert_within_budget("36k-character repetitive replacement diff", perf_counter() - started, HISTORY_LONG_DIFF_LIMIT_SECONDS)
    commit = GitCommit("a" * 40, "aaaaaaa", datetime(2026, 1, 1), "Save translations")
    events = [(commit, TranslationLogEntry(
        "更新", "Text.dbt", str(index), f"Label{index}", "Text", "Hello", f"新译文{index}", "旧译文",
    )) for index in range(20_000)]
    started = perf_counter()
    index = build_history_search_index((commit,), events, threading.Event())
    assert_within_budget("20k-change history search preparation", perf_counter() - started, HISTORY_SEARCH_PREPARE_LIMIT_SECONDS)
    assert index is not None
    terms = query_terms("hello 新译文19999")
    started = perf_counter()
    matches = [key for key in index.entry_keys if matches_terms(index.entry_blobs[key], terms)]
    assert_within_budget("20k-entry history search", perf_counter() - started, CACHED_SEARCH_FILTER_LIMIT_SECONDS)
    if matches != [events[-1][1].change_key]:
        raise AssertionError("large history search returned the wrong entry")


def assert_incremental_save_and_format_scan_stay_bounded() -> None:
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from unittest.mock import patch

    from PySide6.QtWidgets import QApplication

    from .. import app as app_module
    from ..app import UnitTableModel
    from ..project import Project

    app = QApplication.instance() or QApplication([])
    with TemporaryDirectory(prefix="translator_performance_") as directory:
        root = Path(directory).resolve()
        language = root / "languages" / "#english"
        language.mkdir(parents=True)
        header = '"id" INT |"label" STRING |"english" STRING |\nData:\n'
        rows = ''.join(f'{index} "LABEL_{index}" "Text %1SN {index}" |\n' for index in range(20_000))
        for path in (language.parent / "Text.dbt", language / "Text.dbt"):
            if not path.resolve().is_relative_to(root):
                raise AssertionError("performance fixture escaped its temporary directory")
            path.write_text(header + rows, encoding="utf-8")
        project = Project.load(root, "#english", enable_codec=False)
        model = UnitTableModel(project)
        try:
            untouched = project.units[0]
            model.has_format_warning(0)
            cached_search = model.search_blob(0)
            edited = project.units[-2]
            project.apply_unit_edits(((edited, "Changed %1SN", None),))
            model.refresh_unit(edited)
            resets: list[None] = []
            model.modelReset.connect(lambda: resets.append(None))
            with patch.object(app_module, "_search_blob", wraps=app_module._search_blob) as search_calls:
                started = perf_counter()
                result = project.save()
                project.reload_saved_files(result.changed_files)
                model.refresh_project(project)
                elapsed = perf_counter() - started
            assert_within_budget("20k-row single save and model refresh", elapsed, SINGLE_SAVE_REFRESH_LIMIT_SECONDS)
            if resets or search_calls.call_count != 1:
                raise AssertionError("one saved row reset the model or rebuilt unrelated search entries")
            if project.units[0] is not untouched or model.search_blob(0) is not cached_search:
                raise AssertionError("save discarded an untouched row or its search cache")
            refreshed = project.unit_by_uid(edited.uid)
            if refreshed is None or refreshed.is_dirty or "Changed" not in model.search_blob(19_998, case_sensitive=True):
                raise AssertionError("incremental model refresh exposed stale saved text")
            if model.units_for_exact_label(refreshed.label) != (refreshed,):
                raise AssertionError("label lookup retained a replaced saved unit")

            model.cancel_format_scan()
            started = perf_counter()
            model._scan_format_warnings()
            assert_within_budget("first format scan slice", perf_counter() - started, FORMAT_SCAN_SLICE_LIMIT_SECONDS)
            if model.format_warnings_ready:
                raise AssertionError("first format slice validated the entire 20k-row project")
            # Editing an already scanned row must invalidate it exactly once.
            project.apply_unit_edits(((untouched, "Missing argument", None),))
            model.refresh_unit(untouched)
            model.refresh_unit(untouched)
            if model._format_changed_rows != {0}:
                raise AssertionError("format scan did not coalesce edits to an already scanned row")
            while not model.format_warnings_ready:
                started = perf_counter()
                model._scan_format_warnings()
                assert_within_budget("format scan slice", perf_counter() - started, FORMAT_SCAN_SLICE_LIMIT_SECONDS)
            if not model.has_format_warning(0):
                raise AssertionError("format scan lost a changed row's warning")
            project.apply_unit_edits(((untouched, untouched.source_text, None),))
            model.refresh_unit(untouched)
            model._scan_format_warnings()
            if model.has_format_warning(0):
                raise AssertionError("format scan retained a repaired warning")
            model.clear()
            if model._format_scan_timer.isActive() or model._format_changed_rows:
                raise AssertionError("clearing the project left format work pending")
        finally:
            model.cancel_format_scan()


def assert_within_budget(name: str, elapsed: float, limit: float, *, detail: str = "") -> None:
    if elapsed <= limit:
        return
    suffix = f" ({detail})" if detail else ""
    raise AssertionError(f"{name} is too slow: {elapsed:.3f}s > {limit:.3f}s{suffix}")


def assert_large_cached_search_filter_stays_interactive() -> None:
    from ..app import FILE_FILTER_ALL, STATUS_FILTER_ALL, UnitFilterProxyModel, UnitTableModel

    row_count = 20_000
    model = UnitTableModel()
    # This regression isolates the cached unfielded-search path. The proxy
    # should scan the parallel blobs once instead of asking Qt to call back
    # into Python for every source row.
    model.units = [None] * row_count  # type: ignore[list-item]
    model._search_rows = [
        f"file text label_{row} source translation"
        for row in range(row_count)
    ]
    model._search_rows_case_sensitive = list(model._search_rows)
    proxy = UnitFilterProxyModel()
    proxy.only_missing = False
    proxy.setSourceModel(model)

    started = perf_counter()
    proxy.set_filters(
        file_filter=FILE_FILTER_ALL,
        status_filter=STATUS_FILTER_ALL,
        only_missing=False,
        only_format_warnings=False,
        query="label_19999",
    )
    elapsed = perf_counter() - started
    assert_within_budget(
        "cached 20k-row search filter",
        elapsed,
        CACHED_SEARCH_FILTER_LIMIT_SECONDS,
        detail=f"visible={proxy.rowCount()}",
    )
    if proxy.rowCount() != 1 or proxy.mapToSource(proxy.index(0, 0)).row() != 19_999:
        raise AssertionError("cached large-project search returned the wrong source row")


def assert_table_display_does_not_validate_unrelated_columns() -> None:
    from types import SimpleNamespace

    from PySide6.QtCore import Qt

    from .. import app as app_module
    from ..app import UnitTableModel

    format_calls = 0
    original_format_diff_text = app_module._format_diff_text

    def counted_format_diff_text(_unit) -> str:
        nonlocal format_calls
        format_calls += 1
        return "ok"

    unit = SimpleNamespace(
        file_rel="Text.dbt",
        record_id="1",
        label="LABEL",
        source_text="source",
        current_text="translation",
        display_status=lambda: "translated",
    )
    model = UnitTableModel()
    model.units = [unit]
    try:
        app_module._format_diff_text = counted_format_diff_text
        for column in range(model.columnCount()):
            if column != UnitTableModel.FORMAT:
                model.data(model.index(0, column), Qt.ItemDataRole.DisplayRole)
        if format_calls:
            raise AssertionError("painting ordinary table columns performed format validation")
        model.data(model.index(0, UnitTableModel.FORMAT), Qt.ItemDataRole.DisplayRole)
    finally:
        app_module._format_diff_text = original_format_diff_text
    if format_calls != 1:
        raise AssertionError(f"format column performed {format_calls} validations instead of one")


def assert_large_label_catalog_family_lookup_is_cached() -> None:
    from ..script_semantics import (
        _catalog_family_separators,
        _semantic_label_identity,
    )

    catalog = frozenset(
        f"family_{index}{'_+' if index % 2 == 0 else '+'}0"
        for index in range(20_000)
    )
    _catalog_family_separators.cache_clear()
    started = perf_counter()
    resolved = tuple(
        _semantic_label_identity(f"family_{index}", "family", catalog)
        for index in range(2_000)
    )
    elapsed = perf_counter() - started
    assert_within_budget(
        "cached 20k-label family lookup",
        elapsed,
        LABEL_CATALOG_FAMILY_LOOKUP_LIMIT_SECONDS,
        detail=f"cache={_catalog_family_separators.cache_info()}",
    )
    expected = tuple(
        f"family_{index}{'_+' if index % 2 == 0 else '+'}*"
        for index in range(2_000)
    )
    if resolved != expected:
        raise AssertionError("cached label-family separators changed lookup semantics")
