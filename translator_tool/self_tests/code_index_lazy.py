from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import shutil
import tempfile

from .. import code_index_lazy as lazy_module
from ..code_index_lazy import LazyCodeIndexBuilder


def assert_lazy_code_index_prioritizes_requested_labels_and_invalidates_cache() -> None:
    temp = Path(tempfile.mkdtemp(prefix="translator_tool_lazy_code_index_"))
    original_revision = lazy_module.ANALYZER_REVISION
    original_analyze_code_file = lazy_module.analyze_code_file
    try:
        game = temp / "game"
        project = temp / "sources" / "Vanilla"
        scripts = game / "Scripts"
        scripts.mkdir(parents=True)
        project.mkdir(parents=True)
        selected_path = scripts / "Selected.lua"
        selected_path.write_text(
            'MsgQuick("", "@L_MESSAGES_SLANDER_SPEECH_"..EvidenceLabel.."_+0", Value)',
            encoding="utf-8",
        )
        (scripts / "Later.lua").write_text(
            'MsgQuick("", "@L_LATER_BODY_+0", Other)',
            encoding="utf-8",
        )
        cache_path = temp / "cache.json"
        builder = LazyCodeIndexBuilder(game, project, cache_path=cache_path)
        selected = builder.analyze_labels(("MESSAGES_SLANDER_SPEECH_THEFT_+0",))
        references = selected.references_for("MESSAGES_SLANDER_SPEECH_THEFT_+0").project
        if len(references) != 1 or references[0].path != selected_path:
            raise AssertionError(f"selected dynamic label was not analyzed first: {references!r}")
        if builder.progress.analyzed != 1 or builder.complete:
            raise AssertionError(f"targeted analysis eagerly indexed unrelated files: {builder.progress!r}")
        while not builder.complete:
            selected.merge(builder.analyze_next_batch(1))
        if selected.references_for("LATER_BODY_+0").project_count != 1:
            raise AssertionError("lazy batches did not merge into the same incremental index")
        builder.close()
        if not cache_path.is_file():
            raise AssertionError("lazy code facts were not persisted")

        def fail_if_reparsed(*_args, **_kwargs):
            raise AssertionError("a valid semantic file cache was reparsed")

        lazy_module.analyze_code_file = fail_if_reparsed
        warm = LazyCodeIndexBuilder(game, project, cache_path=cache_path)
        cached = warm.analyze_labels(("LATER_BODY_+0",))
        if cached.references_for("LATER_BODY_+0").project_count != 1:
            raise AssertionError("warm semantic cache did not return the requested reference")
        warm.close()

        lazy_module.analyze_code_file = original_analyze_code_file
        later_path = scripts / "Later.lua"
        later_path.write_text(
            'MsgQuick("", "@L_UPDATED_LATER_BODY_+0", ChangedValue)',
            encoding="utf-8",
        )
        changed = LazyCodeIndexBuilder(game, project, cache_path=cache_path)
        updated = changed.analyze_labels(("UPDATED_LATER_BODY_+0",))
        if updated.references_for("UPDATED_LATER_BODY_+0").project_count != 1:
            raise AssertionError("changed script content did not invalidate its cached facts")
        changed.close()

        calls = 0

        def count_reparse(*args, **kwargs):
            nonlocal calls
            calls += 1
            return original_analyze_code_file(*args, **kwargs)

        lazy_module.ANALYZER_REVISION = original_revision + "-test"
        lazy_module.analyze_code_file = count_reparse
        revised = LazyCodeIndexBuilder(game, project, cache_path=cache_path)
        revised.analyze_labels(("UPDATED_LATER_BODY_+0",))
        revised.close()
        if calls != 1:
            raise AssertionError(f"analyzer revision did not invalidate semantic facts: {calls}")
    finally:
        lazy_module.ANALYZER_REVISION = original_revision
        lazy_module.analyze_code_file = original_analyze_code_file
        shutil.rmtree(temp, ignore_errors=True)


def assert_code_index_requests_selected_and_visible_rows_without_moving_viewport() -> None:
    from ..app import CodeIndexWorker, TranslatorWindow

    worker = CodeIndexWorker(1, None, None)
    worker.request_labels(("visible",), 1)
    worker.request_labels(("selected",), 0)
    if not worker._has_requested():
        raise AssertionError("queued code-context request was not visible to batch preemption")
    if worker._take_requested() != ("selected",):
        raise AssertionError("selected code-context request did not outrank visible prefetch")
    if worker._take_requested() != ("visible",):
        raise AssertionError("visible prefetch was lost after the selected request")

    units = tuple(
        SimpleNamespace(
            label=f"LABEL_{row}_+0",
            ref=SimpleNamespace(kind="dbt"),
        )
        for row in range(10)
    )
    requested: list[tuple[tuple[str, ...], int]] = []
    scroll_calls: list[object] = []
    fake_worker = SimpleNamespace(
        request_labels=lambda labels, priority: requested.append((tuple(labels), priority))
    )
    fake_table = SimpleNamespace(
        viewport=lambda: SimpleNamespace(height=lambda: 90),
        rowAt=lambda y: 2 if y == 0 else 4,
        verticalHeader=lambda: SimpleNamespace(defaultSectionSize=lambda: 30),
        scrollTo=lambda *args: scroll_calls.append(args),
    )
    window = SimpleNamespace(
        code_reference_workers=[fake_worker],
        table_frame=SimpleNamespace(isVisible=lambda: True),
        proxy=SimpleNamespace(rowCount=lambda: len(units), index=lambda row, _column: row),
        table=fake_table,
        _unit_from_proxy_index=lambda row: units[row],
    )
    TranslatorWindow._request_visible_code_contexts(window)
    labels, priority = requested[-1]
    if priority != 1 or labels != tuple(unit.label for unit in units[:8]):
        raise AssertionError(f"wrong visible-row prefetch range or priority: {requested[-1]!r}")
    if scroll_calls:
        raise AssertionError("visible code-context prefetch moved the table viewport")


def assert_lazy_code_index_survives_unwritable_cache() -> None:
    temp = Path(tempfile.mkdtemp(prefix="translator_tool_lazy_cache_failure_"))
    try:
        game = temp / "game"
        project = temp / "sources" / "Vanilla"
        scripts = game / "Scripts"
        scripts.mkdir(parents=True)
        project.mkdir(parents=True)
        (scripts / "Message.lua").write_text(
            'MsgQuick("", "@L_CACHE_FAILURE_BODY_+0", Value)',
            encoding="utf-8",
        )
        blocking_parent = temp / "not_a_directory"
        blocking_parent.write_text("occupied", encoding="utf-8")
        builder = LazyCodeIndexBuilder(
            game,
            project,
            cache_path=blocking_parent / "cache.json",
        )
        index = builder.analyze_labels(("CACHE_FAILURE_BODY_+0",))
        builder.close()
        if index.references_for("CACHE_FAILURE_BODY_+0").project_count != 1:
            raise AssertionError("cache persistence failure discarded the in-memory code index")
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def assert_lazy_code_index_links_cached_cross_file_facts() -> None:
    temp = Path(tempfile.mkdtemp(prefix="translator_tool_lazy_cross_file_"))
    original_analyze_code_file = lazy_module.analyze_code_file
    try:
        game = temp / "game"
        project = temp / "sources" / "Vanilla"
        scripts = game / "Scripts"
        scripts.mkdir(parents=True)
        project.mkdir(parents=True)
        helper = scripts / "helper.lua"
        caller = scripts / "caller.lua"
        helper.write_text(
            'function MakeBody(kind) local Label="@L_LAZY_REMOTE_+" return Label..kind end',
            encoding="utf-8",
        )
        caller.write_text(
            'function Main() local Body=helper_MakeBody(Variant) MsgQuick("", Body, Actor) end',
            encoding="utf-8",
        )
        cache_path = temp / "cache.json"
        cold = LazyCodeIndexBuilder(game, project, cache_path=cache_path)
        index = cold.analyze_labels(("LAZY_REMOTE_+4",))
        linked = next(
            (
                item
                for item in index.references_for("LAZY_REMOTE_+4").project
                if item.call_name == "MsgQuick"
            ),
            None,
        )
        if linked is None or linked.path != caller:
            raise AssertionError(f"targeted lazy analysis did not follow the returned-label caller: {linked!r}")
        if cold.progress.analyzed != 2:
            raise AssertionError(f"cross-file targeted analysis scanned unrelated files: {cold.progress!r}")
        cold.close()

        def fail_if_reparsed(*_args, **_kwargs):
            raise AssertionError("cached cross-file semantic facts were reparsed")

        lazy_module.analyze_code_file = fail_if_reparsed
        warm = LazyCodeIndexBuilder(game, project, cache_path=cache_path)
        cached = warm.analyze_labels(("LAZY_REMOTE_+4",))
        if not any(
            item.call_name == "MsgQuick"
            for item in cached.references_for("LAZY_REMOTE_+4").project
        ):
            raise AssertionError("warm cache did not relink returned-label facts to their caller")
        warm.close()

        lazy_module.analyze_code_file = original_analyze_code_file
        helper.write_text(
            'function MakeBody(kind) local Label="@L_LAZY_UPDATED_+" return Label..kind end',
            encoding="utf-8",
        )
        changed = LazyCodeIndexBuilder(game, project, cache_path=cache_path)
        updated = changed.analyze_labels(("LAZY_UPDATED_+4",))
        if not any(
            item.call_name == "MsgQuick"
            for item in updated.references_for("LAZY_UPDATED_+4").project
        ):
            raise AssertionError("changed return summary did not invalidate and relink cached callers")
        changed.close()
    finally:
        lazy_module.analyze_code_file = original_analyze_code_file
        shutil.rmtree(temp, ignore_errors=True)


def assert_lazy_code_index_loads_cached_value_providers() -> None:
    temp = Path(tempfile.mkdtemp(prefix="translator_tool_lazy_values_"))
    original_analyze_code_file = lazy_module.analyze_code_file
    try:
        game = temp / "game"
        project = temp / "sources" / "Vanilla"
        scripts = game / "Scripts"
        scripts.mkdir(parents=True)
        project.mkdir(parents=True)
        (scripts / "helper.lua").write_text(
            "\n".join(
                (
                    "function MakeValues(kind)",
                    '    return "@L_ITEM_"..kind.."_NAME_+0", "Tail"',
                    "end",
                )
            ),
            encoding="utf-8",
        )
        (scripts / "caller.lua").write_text(
            'function Main() MsgQuick("", "@L_LAZY_VALUE_BODY_+0", helper_MakeValues("BREAD")) end',
            encoding="utf-8",
        )
        (scripts / "unrelated.lua").write_text(
            'function Unrelated() return "@L_NOT_REQUESTED_+0" end',
            encoding="utf-8",
        )
        cache_path = temp / "cache.json"
        cold = LazyCodeIndexBuilder(game, project, cache_path=cache_path)
        index = cold.analyze_labels(("LAZY_VALUE_BODY_+0",))
        resolved = next(
            (
                item
                for item in index.references_for("LAZY_VALUE_BODY_+0").project
                if item.runtime_argument_values
                == (("@L_ITEM_BREAD_NAME_+0",), ("Tail",))
            ),
            None,
        )
        if resolved is None:
            raise AssertionError("targeted lazy analysis did not load the value provider summary")
        if resolved.runtime_argument_kinds != (("label",), ("text",)):
            raise AssertionError(
                f"targeted lazy analysis lost semantic value types: {resolved!r}"
            )
        if cold.progress.analyzed != 2:
            raise AssertionError(
                f"value-provider analysis scanned unrelated files: {cold.progress!r}"
            )
        cold.close()

        def fail_if_reparsed(*_args, **_kwargs):
            raise AssertionError("cached function value summaries were reparsed")

        lazy_module.analyze_code_file = fail_if_reparsed
        warm = LazyCodeIndexBuilder(game, project, cache_path=cache_path)
        cached = warm.analyze_labels(("LAZY_VALUE_BODY_+0",))
        if not any(
            item.runtime_argument_values
            == (("@L_ITEM_BREAD_NAME_+0",), ("Tail",))
            and item.runtime_argument_kinds == (("label",), ("text",))
            for item in cached.references_for("LAZY_VALUE_BODY_+0").project
        ):
            raise AssertionError("warm cache did not restore and link function value summaries")
        warm.close()
    finally:
        lazy_module.analyze_code_file = original_analyze_code_file
        shutil.rmtree(temp, ignore_errors=True)
