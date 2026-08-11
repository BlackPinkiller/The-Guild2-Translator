from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json
import shutil
import tempfile

from .. import code_index_lazy as lazy_module
from ..code_index import CodeReference, CodeReferenceIndex
from ..code_index_lazy import LazyCodeIndexBuilder


def assert_code_facts_cache_defers_and_yields_manifest_write() -> None:
    temp = Path(tempfile.mkdtemp(prefix="translator_tool_code_cache_flush_"))
    original_atomic_write = lazy_module.atomic_write
    original_sleep = lazy_module.time.sleep
    original_yield_seconds = lazy_module.CACHE_SERIALIZE_YIELD_SECONDS
    writes: list[bytes] = []
    yields: list[float] = []
    try:
        lazy_module.atomic_write = lambda _path, data: writes.append(data)
        lazy_module.time.sleep = lambda seconds: yields.append(seconds)
        lazy_module.CACHE_SERIALIZE_YIELD_SECONDS = 0.0
        cache = lazy_module.CodeFactsCache(temp / "cache.json")
        cache._files["fixture"] = {
            "used_ns": 1,
            "references": [{"label": f"LABEL_{number}"} for number in range(400)],
        }
        cache._mark_dirty()
        cache.flush_if_needed()
        if writes:
            raise AssertionError("code-facts cache rewrote its manifest before completion or close")
        cache.flush_if_needed(force=True)
        if len(writes) != 1:
            raise AssertionError("forced cache flush did not preserve shutdown persistence")
        if not yields:
            raise AssertionError("large cache serialization did not yield to the GUI thread")
        payload = json.loads(writes[0].decode("utf-8"))
        if len(payload.get("files", {}).get("fixture", {}).get("references", ())) != 400:
            raise AssertionError("cooperative cache serialization changed the manifest payload")
    finally:
        lazy_module.atomic_write = original_atomic_write
        lazy_module.time.sleep = original_sleep
        lazy_module.CACHE_SERIALIZE_YIELD_SECONDS = original_yield_seconds
        shutil.rmtree(temp, ignore_errors=True)


def assert_incremental_code_index_merge_preserves_compiled_wildcards() -> None:
    first = CodeReference("family_*_+*", Path("First.lua"), 1, 1)
    second = CodeReference("family_*_+*", Path("Second.lua"), 2, 1)
    exact = CodeReference("exact_+0", Path("Exact.lua"), 3, 1)
    index = CodeReferenceIndex({"family_*_+*": (first,)})

    if index.references_for("FAMILY_BRANCH_+0").project != (first,):
        raise AssertionError("wildcard fixture did not resolve before incremental merging")
    assert index._project_wildcards is not None
    compiled_before = index._project_wildcards["family_*_+*"][0]

    index.merge(CodeReferenceIndex({"exact_+0": (exact,)}))
    if index._project_wildcards["family_*_+*"][0] is not compiled_before:
        raise AssertionError("an exact-label merge recompiled an unrelated wildcard")
    index.merge(CodeReferenceIndex({"family_*_+*": (second,)}))
    if index._project_wildcards["family_*_+*"][0] is not compiled_before:
        raise AssertionError("a wildcard-reference merge needlessly recompiled its pattern")
    if index.references_for("FAMILY_BRANCH_+0").project != (first, second):
        raise AssertionError("incremental wildcard cache did not receive merged references")


def assert_lazy_batches_advance_without_rescanning_prefix() -> None:
    temp = Path(tempfile.mkdtemp(prefix="translator_tool_lazy_batch_cursor_"))
    try:
        game = temp / "game"
        project = temp / "sources" / "Vanilla"
        scripts = game / "Scripts"
        scripts.mkdir(parents=True)
        project.mkdir(parents=True)
        for number in range(40):
            (scripts / f"Batch{number:02d}.lua").write_text(
                f'MsgQuick("", "@L_BATCH_{number:02d}_BODY_+0", Value)',
                encoding="utf-8",
            )

        class CountingFiles:
            def __init__(self, items) -> None:
                self.items = tuple(items)
                self.reads = 0

            def __len__(self) -> int:
                return len(self.items)

            def __getitem__(self, index):
                self.reads += 1
                return self.items[index]

            def __iter__(self):
                for item in self.items:
                    self.reads += 1
                    yield item

        builder = LazyCodeIndexBuilder(game, project, cache_path=temp / "cache.json")
        builder.prepare()
        counted = CountingFiles(builder.files)
        builder.files = counted  # type: ignore[assignment]
        while not builder.complete:
            builder.analyze_next_batch(1)
        builder.close()
        if counted.reads > len(counted) * 2:
            raise AssertionError(
                f"lazy batches repeatedly rescanned completed file prefixes: {counted.reads} reads"
            )
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def assert_lazy_code_index_loads_effective_item_id_ranges() -> None:
    temp = Path(tempfile.mkdtemp(prefix="translator_tool_item_ranges_"))
    try:
        game = temp / "game"
        project = temp / "sources" / "Reforged"
        scripts = game / "mods" / "Reforged" / "Scripts"
        scripts.mkdir(parents=True)
        project.mkdir(parents=True)
        base_items = game / "DB" / "Items.dbt"
        mod_items = game / "mods" / "Reforged" / "DB" / "Items.dbt"
        base_items.parent.mkdir(parents=True)
        mod_items.parent.mkdir(parents=True)
        base_items.write_text(
            'Table Description:\n"id" INT -1 |"name" STRING 0 |\nData:\n'
            '241 "Iron" |\n242 "Silver" |\n',
            encoding="utf-8",
        )
        mod_items.write_text(
            'Table Description:\n"id" INT -1 |"name" STRING 0 |\nData:\n'
            '241 "TemperedIron" |\n242 "~" |\n243 "Gold" |\n',
            encoding="utf-8",
        )
        script = scripts / "ItemRange.lua"
        script.write_text(
            "\n".join(
                (
                    "function Run()",
                    "  local ItemID = RuntimeItem()",
                    "  if ItemID == 241 or ItemID == 242 or ItemID == 243 then",
                    '    MsgQuick("", "@L_ITEM_RANGE_BODY_+0", ItemGetLabel(ItemID, true))',
                    "  end",
                    "end",
                )
            ),
            encoding="utf-8",
        )
        builder = LazyCodeIndexBuilder(
            game,
            project,
            cache_path=temp / "cache.json",
        )
        index = builder.analyze_labels(("ITEM_RANGE_BODY_+0",))
        builder.close()
        references = index.references_for("ITEM_RANGE_BODY_+0").project
        if len(references) != 1 or references[0].runtime_argument_values != (
            (
                "_ITEM_TemperedIron_NAME_+0",
                "_ITEM_Silver_NAME_+0",
                "_ITEM_Gold_NAME_+0",
            ),
        ):
            raise AssertionError(
                f"the lazy index did not load the effective mod item ID range: {references!r}"
            )
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def assert_lazy_code_index_resolves_database_backed_label_domains() -> None:
    temp = Path(tempfile.mkdtemp(prefix="translator_tool_database_domains_"))
    try:
        game = temp / "game"
        project = temp / "sources" / "Reforged"
        scripts = game / "mods" / "Reforged" / "Scripts"
        scripts.mkdir(parents=True)
        project.mkdir(parents=True)
        base_database = game / "DB" / "Lordship.dbt"
        mod_database = game / "mods" / "Reforged" / "DB" / "Lordship.dbt"
        base_database.parent.mkdir(parents=True)
        mod_database.parent.mkdir(parents=True)
        header = (
            'Table Description:\n"id" INT -1 |"name" STRING 0 |'
            '"enemy1" STRING 0 |"enemy2" STRING 0 |\nData:\n'
        )
        base_database.write_text(
            header
            + '1 "base" "spain" "france" |\n'
            + '2 "north" "hansa" "england" |\n',
            encoding="utf-8",
        )
        mod_database.write_text(
            header + '1 "modded" "italy" "austria" |\n',
            encoding="utf-8",
        )
        (scripts / "trade.lua").write_text(
            "\n".join(
                (
                    "function GetRealmName(Slot)",
                    '  return GetDatabaseValue("Lordship", 1, "enemy"..Slot)',
                    "end",
                    "function RealmDisplay(Slot)",
                    '  if Slot == nil then return "@L_KR_KONTOR_NOCROWN_+0" end',
                    '  return "@L_SCENARIO_WAR_"..trade_GetRealmName(Slot).."_+2"',
                    "end",
                )
            ),
            encoding="utf-8",
        )
        (scripts / "Permit.lua").write_text(
            "\n".join(
                (
                    "function Run()",
                    "  local lordName = trade_RealmDisplay(Realm)",
                    '  MsgQuick("", "@L_KR_PERMIT_HAVE_+0", lordName)',
                    "end",
                )
            ),
            encoding="utf-8",
        )
        builder = LazyCodeIndexBuilder(
            game,
            project,
            cache_path=temp / "cache.json",
        )
        index = builder.analyze_labels(("KR_PERMIT_HAVE_+0",))
        builder.close()
        references = index.references_for("KR_PERMIT_HAVE_+0").project
        if len(references) != 1:
            raise AssertionError(f"database-backed caller was not indexed once: {references!r}")
        values = references[0].runtime_argument_values
        expected = {
            "@L_KR_KONTOR_NOCROWN_+0",
            "@L_SCENARIO_WAR_italy_+2",
            "@L_SCENARIO_WAR_austria_+2",
            "@L_SCENARIO_WAR_hansa_+2",
            "@L_SCENARIO_WAR_england_+2",
        }
        if len(values) != 1 or set(values[0]) != expected:
            raise AssertionError(
                "a finite database label domain degraded into an unrelated wildcard: "
                f"{values!r}"
            )
        if any("*" in value or "RANDOMTALK" in value for value in values[0]):
            raise AssertionError(f"database label candidates escaped their runtime column: {values!r}")
        kinds = dict(zip(values[0], references[0].runtime_argument_kinds[0]))
        if kinds.get("@L_KR_KONTOR_NOCROWN_+0") != "label" or any(
            kinds.get(value) != "label_domain"
            for value in expected
            if value != "@L_KR_KONTOR_NOCROWN_+0"
        ):
            raise AssertionError(f"database label provenance was not preserved: {kinds!r}")
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def assert_lazy_code_index_prioritizes_requested_labels_and_invalidates_cache() -> None:
    temp = Path(tempfile.mkdtemp(prefix="translator_tool_lazy_code_index_"))
    original_revision = lazy_module.ANALYZER_REVISION
    original_analyze_code_file = lazy_module.analyze_code_file
    original_lexical_blob = lazy_module._lexical_blob
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
        lazy_module._lexical_blob = fail_if_reparsed
        warm = LazyCodeIndexBuilder(game, project, cache_path=cache_path)
        cached = warm.analyze_labels(("LATER_BODY_+0",))
        if cached.references_for("LATER_BODY_+0").project_count != 1:
            raise AssertionError("warm semantic cache did not return the requested reference")
        warm.close()

        lazy_module.analyze_code_file = original_analyze_code_file
        lazy_module._lexical_blob = original_lexical_blob
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
        lazy_module._lexical_blob = original_lexical_blob
        shutil.rmtree(temp, ignore_errors=True)


def assert_code_index_requests_selected_and_visible_rows_without_moving_viewport() -> None:
    from ..app import CodeIndexWorker, TranslatorWindow
    from ..code_index_worker import run_code_index_loop
    from queue import Empty, Queue

    worker = CodeIndexWorker(1, None, None)
    worker.request_labels(("visible",), 1)
    worker.request_labels(("selected",), 0)
    if not worker._has_requested():
        raise AssertionError("queued code-context request was not visible to batch preemption")
    if worker._take_requested() != (0, 2, ("selected",)):
        raise AssertionError("selected code-context request did not outrank visible prefetch")
    if worker._take_requested() != (1, 1, ("visible",)):
        raise AssertionError("visible prefetch was lost after the selected request")
    worker.request_labels(("old-selected",), 0)
    worker.request_labels(("new-selected",), 0)
    if worker._take_requested() != (0, 4, ("new-selected",)):
        raise AssertionError("a stale selected-row request delayed the current selection")

    calls: list[tuple[str, ...]] = []
    ready: list[str] = []
    command_queue: Queue[object] = Queue()
    result_queue: Queue[object] = Queue()

    class FakeBuilder:
        def __init__(self) -> None:
            self.complete = False

        @property
        def progress(self):
            return lazy_module.LazyIndexProgress(len(calls), 4, self.complete)

        def analyze_labels(self, labels, *, cancelled):
            calls.append(tuple(labels))
            if labels == ("visible-old",):
                command_queue.put(("request", 0, 2, ("selected-now",)))
                if not cancelled():
                    raise AssertionError("selected request did not preempt visible-row analysis")
            elif labels == ("selected-now",):
                command_queue.put(("request", 0, 3, ("selected-new",)))
                command_queue.put(("request", 1, 4, ("visible-new",)))
                if not cancelled():
                    raise AssertionError("a stale active selection blocked the new selected row")
            elif labels == ("selected-new",):
                pass
            elif labels == ("visible-new",):
                self.complete = True
            return CodeReferenceIndex()

        def analyze_next_batch(self, *_args, **_kwargs):
            raise AssertionError("worker ignored queued priority requests")

    command_queue.put(("request", 1, 1, ("visible-old",)))
    run_code_index_loop(FakeBuilder(), command_queue, result_queue, lambda: False)  # type: ignore[arg-type]
    while True:
        try:
            result = result_queue.get_nowait()
        except Empty:
            break
        if isinstance(result, tuple) and result and result[0] == "labels_ready":
            ready.extend(result[1])
    if calls != [
        ("visible-old",),
        ("selected-now",),
        ("selected-new",),
        ("visible-new",),
    ]:
        raise AssertionError(f"priority requests ran in the wrong order: {calls!r}")
    if ready != ["selected-new", "visible-new"]:
        raise AssertionError(f"interrupted or empty priority results reported wrong readiness: {ready!r}")

    temp = Path(tempfile.mkdtemp(prefix="translator_tool_code_index_process_"))
    try:
        game = temp / "game"
        project = temp / "sources" / "Vanilla"
        scripts = game / "Scripts"
        scripts.mkdir(parents=True)
        project.mkdir(parents=True)
        script = scripts / "Selected.lua"
        script.write_text(
            'MsgQuick("", "@L_SELECTED_PROCESS_BODY_+0", Value)',
            encoding="utf-8",
        )
        process_worker = CodeIndexWorker(2, game, project)
        partials: list[CodeReferenceIndex] = []
        process_ready: list[str] = []
        failures: list[str] = []
        process_worker.signals.partial.connect(
            lambda _token, index, _progress: partials.append(index)
        )
        process_worker.signals.labels_ready.connect(
            lambda _token, labels: process_ready.extend(labels)
        )
        process_worker.signals.failed.connect(
            lambda _token, message: failures.append(message)
        )
        process_worker.request_labels(("SELECTED_PROCESS_BODY_+0",), 0)
        process_worker.run()
        if failures:
            raise AssertionError(f"isolated code-index process failed: {failures!r}")
        if process_ready != ["selected_process_body_+0"]:
            raise AssertionError(f"isolated selected request was not reported ready: {process_ready!r}")
        merged = CodeReferenceIndex()
        for partial in partials:
            merged.merge(partial)
        references = merged.references_for("SELECTED_PROCESS_BODY_+0").project
        if len(references) != 1 or references[0].path != script:
            raise AssertionError(f"isolated process returned the wrong code reference: {references!r}")
    finally:
        shutil.rmtree(temp, ignore_errors=True)

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
        code_reference_labels_ready=set(),
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


def assert_hot_ui_updates_are_coalesced() -> None:
    from ..app import TranslatorWindow
    from ..i18n import translate

    class FakeTimer:
        def __init__(self) -> None:
            self.active = False
            self.starts = 0

        def isActive(self) -> bool:
            return self.active

        def start(self) -> None:
            self.active = True
            self.starts += 1

        def stop(self) -> None:
            self.active = False

    preview_timer = FakeTimer()
    display_refreshes: list[None] = []
    preview_refreshes: list[str] = []
    current = [SimpleNamespace(uid="selected", label="SELECTED_+0")]
    game_preview_cache = {"stale": object()}
    window = SimpleNamespace(
        code_reference_index_token=7,
        code_reference_index=CodeReferenceIndex(),
        code_reference_index_complete=False,
        code_reference_labels_ready=set(),
        code_context_preview_uid="",
        code_context_preview_timer=preview_timer,
        _game_preview_cache=game_preview_cache,
        source_edit=SimpleNamespace(
            refresh_preview=lambda: preview_refreshes.append("source")
        ),
        translation_edit=SimpleNamespace(
            refresh_preview=lambda: preview_refreshes.append("translation")
        ),
        _update_code_reference_display=lambda: display_refreshes.append(None),
        _update_preview_tooltips=lambda: preview_refreshes.append("tooltips"),
        _current_unit=lambda: current[0],
        _current_code_reference_set=lambda: SimpleNamespace(active=(object(),)),
    )
    for analyzed in (1, 2):
        TranslatorWindow._code_reference_index_partial(
            window,
            7,
            CodeReferenceIndex(),
            lazy_module.LazyIndexProgress(analyzed, 10, False),
        )
    if display_refreshes or preview_refreshes or preview_timer.starts:
        raise AssertionError("background code-index batches performed selection-path UI work")
    TranslatorWindow._code_reference_labels_ready(window, 7, ("SELECTED_+0",))
    if (
        "selected_+0" not in window.code_reference_labels_ready
        or len(display_refreshes) != 1
        or preview_refreshes
        or preview_timer.starts != 1
        or window.code_context_preview_uid != "selected"
    ):
        raise AssertionError("selected code status was not cheap and immediate with preview work deferred")
    preview_timer.active = False
    TranslatorWindow._refresh_current_code_context_preview(window)
    if preview_refreshes != ["source", "translation", "tooltips"] or game_preview_cache:
        raise AssertionError("settled selected context did not refresh its preview once")
    window.code_context_preview_uid = "old-selection"
    current[0] = SimpleNamespace(uid="new-selection", label="NEW_+0")
    TranslatorWindow._refresh_current_code_context_preview(window)
    if preview_refreshes != ["source", "translation", "tooltips"]:
        raise AssertionError("a stale code-context callback refreshed previews after row switching")

    shown_text: list[str] = []
    enabled: list[bool] = []
    empty_window = SimpleNamespace(
        code_reference_index=CodeReferenceIndex(),
        code_reference_index_complete=False,
        code_reference_labels_ready={"selected_+0"},
        code_reference_label=SimpleNamespace(
            setText=lambda text: shown_text.append(text),
            text=lambda: shown_text[-1],
        ),
        source_code_button=SimpleNamespace(
            setEnabled=lambda value: enabled.append(value),
            setToolTip=lambda _text: None,
        ),
        source_box=object(),
        _current_unit=lambda: SimpleNamespace(
            label="SELECTED_+0",
            ref=SimpleNamespace(kind="dbt"),
        ),
        _current_code_reference_set=lambda: SimpleNamespace(
            project_count=0,
            vanilla_count=0,
        ),
        _project_is_mod=lambda: False,
    )
    TranslatorWindow._update_code_reference_display(empty_window)
    if shown_text[-1] != translate("code.references.zero") or enabled[-1]:
        raise AssertionError("an empty priority result stayed loading until the full index completed")

    counts_timer = FakeTimer()
    counts_window = SimpleNamespace(counts_refresh_timer=counts_timer)
    TranslatorWindow._schedule_counts_update(counts_window)
    TranslatorWindow._schedule_counts_update(counts_window)
    if counts_timer.starts != 2:
        raise AssertionError("typing count refresh is not a trailing debounce")

    class FakeUnit:
        uid = "fixture"
        translate_text = "base"
        current_text = "base"
        pending_delete = False

        @property
        def is_dirty(self) -> bool:
            return self.current_text != self.translate_text

        def filter_status(self) -> str:
            return "translated"

    unit = FakeUnit()
    texts = iter(("basex", "basexy", "base"))
    title_refreshes: list[None] = []
    editor_window = SimpleNamespace(
        loading_editor=False,
        typing_uid="",
        typing_before="",
        typing_before_deleted=False,
        translation_edit=SimpleNamespace(toPlainText=lambda: next(texts)),
        typing_timer=SimpleNamespace(start=lambda: None),
        model=SimpleNamespace(refresh_unit=lambda _unit: None),
        _current_unit=lambda: unit,
        _commit_typing_operation=lambda: None,
        _set_unit_text=lambda item, text: setattr(item, "current_text", text),
        _update_recent_translation_marker=lambda *_args: None,
        _update_issue_detail=lambda _unit: None,
        _update_preview_tooltips=lambda: None,
        _refresh_editor_highlights=lambda: None,
        _schedule_counts_update=lambda: None,
        _update_window_title=lambda: title_refreshes.append(None),
        _schedule_recovery_snapshot=lambda: None,
    )
    for _ in range(3):
        TranslatorWindow._on_editor_changed(editor_window)
    if len(title_refreshes) != 2:
        raise AssertionError(
            "typing rescanned the full-project dirty count without a dirty-state transition"
        )

    tooltip_values: list[str] = []
    tooltip_button = SimpleNamespace(
        isChecked=lambda: False,
        setToolTip=lambda value: tooltip_values.append(value),
    )
    tooltip_window = SimpleNamespace(
        source_preview_button=tooltip_button,
        translation_preview_button=tooltip_button,
        _current_unit=lambda: SimpleNamespace(uid="placeholder-row"),
        preview_service=SimpleNamespace(
            render=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("row switching eagerly rendered a hidden preview tooltip")
            )
        ),
    )
    TranslatorWindow._update_preview_tooltips(tooltip_window)
    if tooltip_values != ["", ""]:
        raise AssertionError("selected preview buttons kept stale native tooltip content")


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
            "\n".join(
                (
                    "function Main()",
                    '    MsgQuick("", "@L_LAZY_VALUE_BODY_+0", helper_MakeValues("BREAD"))',
                    '    local LocalLabel = helper_MakeValues("WINE")',
                    '    MsgQuick("", "@L_LAZY_LOCAL_VALUE_BODY_+0", LocalLabel)',
                    "end",
                )
            ),
            encoding="utf-8",
        )
        (scripts / "unrelated.lua").write_text(
            'function Unrelated() return "@L_NOT_REQUESTED_+0" end',
            encoding="utf-8",
        )
        cache_path = temp / "cache.json"
        cold = LazyCodeIndexBuilder(game, project, cache_path=cache_path)
        index = cold.analyze_labels(("LAZY_VALUE_BODY_+0", "LAZY_LOCAL_VALUE_BODY_+0"))
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
        if not any(
            item.runtime_argument_values == (("@L_ITEM_WINE_NAME_+0",),)
            and item.runtime_argument_kinds == (("label",),)
            for item in index.references_for("LAZY_LOCAL_VALUE_BODY_+0").project
        ):
            raise AssertionError("targeted lazy analysis lost a provider hidden by a local variable")
        if cold.progress.analyzed != 2:
            raise AssertionError(
                f"value-provider analysis scanned unrelated files: {cold.progress!r}"
            )
        cold.close()

        def fail_if_reparsed(*_args, **_kwargs):
            raise AssertionError("cached function value summaries were reparsed")

        lazy_module.analyze_code_file = fail_if_reparsed
        warm = LazyCodeIndexBuilder(game, project, cache_path=cache_path)
        cached = warm.analyze_labels(("LAZY_VALUE_BODY_+0", "LAZY_LOCAL_VALUE_BODY_+0"))
        if not any(
            item.runtime_argument_values
            == (("@L_ITEM_BREAD_NAME_+0",), ("Tail",))
            and item.runtime_argument_kinds == (("label",), ("text",))
            for item in cached.references_for("LAZY_VALUE_BODY_+0").project
        ):
            raise AssertionError("warm cache did not restore and link function value summaries")
        if not any(
            item.runtime_argument_values == (("@L_ITEM_WINE_NAME_+0",),)
            and item.runtime_argument_kinds == (("label",),)
            for item in cached.references_for("LAZY_LOCAL_VALUE_BODY_+0").project
        ):
            raise AssertionError("warm cache did not relink a locally assigned provider value")
        warm.close()
    finally:
        lazy_module.analyze_code_file = original_analyze_code_file
        shutil.rmtree(temp, ignore_errors=True)
