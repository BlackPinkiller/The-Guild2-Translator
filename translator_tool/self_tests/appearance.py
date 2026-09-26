from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ..git_history import GitCommit, TranslationLogEntry
from ..history_render import render_entry_timeline_html
from ..theme import INTERFACE_COLORS


def assert_theme_text_contrast_and_history_consistency() -> None:
    def luminance(color: str) -> float:
        channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
        return sum(channel * weight for channel, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

    commit = GitCommit("a" * 40, "aaaaaaa", datetime(2026, 9, 26), "Change <entry>")
    entry = TranslationLogEntry("更新", "Text.dbt", "1", "Greeting", "Text", "<source> %1SN", "新译文 %1SN", "旧译文 %1SN")
    for theme, colors in INTERFACE_COLORS.items():
        for foreground, background in (
            ("text", "base"), ("muted", "panel"), ("accent_text", "accent"),
            ("selection_text", "selection"), ("success_text", "success_bg"),
            ("warning_text", "warning_bg"), ("danger_text", "danger_bg"), ("info_text", "info_bg"),
        ):
            light, dark = sorted((luminance(colors[foreground]), luminance(colors[background])), reverse=True)
            if (light + 0.05) / (dark + 0.05) < 4.5:
                raise AssertionError(f"{theme} {foreground}/{background} has insufficient text contrast")
        rendered = render_entry_timeline_html([(commit, entry)], theme=theme)
        if f"background: {colors['base']}" not in rendered or f'bgcolor="{colors["panel"]}"' not in rendered:
            raise AssertionError("history timeline did not use the active theme's backgrounds")
        if "&lt;source&gt;" not in rendered or "&lt;entry&gt;" not in rendered:
            raise AssertionError("themed history interpreted file content as HTML")
        if f"background: {colors['base']}" not in render_entry_timeline_html([], theme=theme):
            raise AssertionError("empty history timeline did not follow the active theme")


def assert_appearance_preserves_editor_state_and_file_bytes() -> None:
    from PySide6.QtCore import QEvent, QObject, Qt, QTimer
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QBoxLayout

    from .. import app as ui
    from ..project import Project
    from ..settings import AppSettings
    from ..i18n import current_language, set_language

    app = QApplication.instance() or QApplication([])
    previous_theme = ui._active_theme()
    previous_font = QFont(app.font())
    previous_language = current_language()
    # The Windows offscreen backend does not enumerate the same font set as
    # the native display plugin. Register the real UI font for layout checks.
    font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc"
    if font_path.is_file():
        if QFontDatabase.addApplicationFont(str(font_path)) < 0:
            raise AssertionError("could not register the UI font for layout regression")
    with TemporaryDirectory(prefix="translator_appearance_") as directory, ExitStack() as stack:
        root = Path(directory)
        target = root / "languages" / "#chinese" / "Text.dbt"
        target.parent.mkdir(parents=True)
        source = target.parent.parent / "Text.dbt"
        def data(field: str, prefix: str) -> bytes:
            header = f'\ufeff"id" INT |"label" STRING |"{field}" STRING |\r\nData:\r\n'
            rows = '\r\n'.join(f'{index} "ENTRY_{index:04d}" "{prefix} {index} %1SN" |' for index in range(480))
            return (header + rows).encode("utf-16-le")
        source_raw, target_raw = data("english", "Source"), data("chinese", "译文")
        source.write_bytes(source_raw)
        target.write_bytes(target_raw)
        stack.enter_context(patch.object(ui, "load_settings", return_value=AppSettings(ui_language="zh-CN")))
        stack.enter_context(patch.object(ui, "save_settings", side_effect=AssertionError("appearance test wrote user settings")))
        for method in ("_startup_project_root", "_startup_game_root", "choose_project_folder", "_start_code_reference_index", "_start_preview_localization"):
            stack.enter_context(patch.object(ui.TranslatorWindow, method, return_value=None))
        window = ui.TranslatorWindow()
        try:
            window.project_root = root
            window._load_language_choices()
            project = Project.load(root, "#chinese", enable_codec=False)
            window._activate_project(project)
            window.only_missing.setChecked(False)
            window.file_combo.setCurrentText("Text.dbt")
            window._apply_filters()
            window.resize(1480, 920)
            window.show()
            app.processEvents()
            unit = project.units[350]
            window._restore_selected_row(unit.uid)
            window.translation_edit.set_raw_cursor_position(4)
            before_text = window.translation_edit.toPlainText()
            for theme in ("dark", "modern", "dark"):
                window._apply_runtime_theme(theme)
                app.processEvents()
                if window.translation_edit.toPlainText() != before_text or window.translation_edit.raw_cursor_position() != 4:
                    raise AssertionError("theme change modified translation text or its caret")
                if window.current_uid != unit.uid or project.has_dirty_units():
                    raise AssertionError("theme change altered selected entry or project dirty state")
            window.search_edit.setFocus()
            # Qt applies the old layout minimum before the first resize event;
            # the second move models continuing to drag after the toolbar reflows.
            for _ in range(2):
                window.resize(1060, 760)
                app.processEvents()
            if window.width() > 1060 or window.toolbar_layout.direction() != QBoxLayout.Direction.LeftToRight:
                raise AssertionError(
                    f"toolbar kept the large-window minimum width after reflow: window={window.width()}, "
                    f"filters={window.toolbar_filters.minimumSizeHint().width()}, search={window.toolbar_search.minimumSizeHint().width()}, "
                    f"direction={window.toolbar_layout.direction()}"
                    f", widgets={[(child.objectName(), child.minimumSizeHint().width()) for child in window.centralWidget().findChildren(ui.QWidget) if child.minimumSizeHint().width() > 900]}"
                )
            for widget in (window.language_combo, window.file_combo, window.search_edit, window.batch_ai_button):
                rect = widget.rect().translated(widget.mapTo(window, widget.rect().topLeft()))
                if not window.rect().contains(rect) or widget.width() < widget.minimumWidth():
                    raise AssertionError("narrow toolbar clipped a control")
            for locale in ("en", "zh-CN"):
                set_language(locale)
                window._retranslate_ui()
                app.processEvents()
                controls = (window.file_label, window.file_combo, window.status_label, window.status_combo,
                            window.only_missing, window.only_format_warnings)
                rects = [widget.rect().translated(widget.mapTo(window, widget.rect().topLeft())) for widget in controls]
                if any(first.intersects(second) for index, first in enumerate(rects) for second in rects[index + 1:]):
                    raise AssertionError(f"{locale} compact filters overlapped after changing language")
                if window.width() > 1060:
                    raise AssertionError("changing language widened the compact workspace")
            if window.current_uid != unit.uid or not window.table.visualRect(window.table.currentIndex()).intersects(window.table.viewport().rect()):
                raise AssertionError("toolbar reflow lost the selected row or left it outside the viewport")
            if not window.search_edit.hasFocus():
                raise AssertionError("toolbar reflow stole search focus")
            if window.search_edit.width() != 220:
                raise AssertionError("entry search stretched across the workspace")
            for panel in (window.source_box, window.translation_box):
                if panel.findChild(ui.QFrame, "editorHeader").height() > 32:
                    raise AssertionError("editor header consumed more than one compact control row")
            window._update_issue_detail(unit)
            if window.issue_label.isVisible():
                raise AssertionError("normal format status still occupied an extra row below the editors")
            if window.issue_label.property("severity") != "normal":
                raise AssertionError("valid text was shown as a warning")
            project.apply_unit_edits(((unit, "Missing token", None),))
            window._update_issue_detail(unit)
            if window.issue_label.property("severity") != "warning":
                raise AssertionError("missing format token was not shown as a warning")
            project.apply_unit_edits(((unit, before_text, True),))
            window._update_issue_detail(unit)
            if window.issue_label.property("severity") != "error":
                raise AssertionError("pending deletion was not distinguished from a normal entry")
            project.apply_unit_edits(((unit, before_text, False),))
            window._set_editor_unit(unit)

            # An idle hover must not keep repainting the complete table.
            class PaintCounter(QObject):
                count = 0
                def eventFilter(self, watched, event):
                    if event.type() == QEvent.Type.Paint:
                        self.count += 1
                    return False
            counter = PaintCounter(window.table)
            window.table.viewport().installEventFilter(counter)
            window.model.cancel_format_scan()
            window.ai_delegate._set_hover(unit.uid)
            app.processEvents()
            before_paints = counter.count
            QTest.qWait(180)
            app.processEvents()
            if counter.count - before_paints > 1 or any(timer.isActive() for timer in window.ai_delegate.findChildren(QTimer)):
                raise AssertionError("idle AI button hover continuously repainted the table")

            replacement = "修改后的译文 %1SN"
            window.translation_edit.setFocus()
            window.translation_edit.selectAll()
            window.translation_edit.insertPlainText(replacement)
            window._commit_typing_operation()
            if project.unit_by_uid(unit.uid).current_text != replacement:
                raise AssertionError("styled editor did not update the selected translation")
            window._restore_selected_row(project.units[351].uid)
            app.processEvents()
            if project.unit_by_uid(unit.uid).current_text != replacement or target.read_bytes() != target_raw:
                raise AssertionError("switching entries lost the edit or implicitly rewrote the file")
            window._restore_selected_row(unit.uid)
            app.processEvents()
            if window.translation_edit.toPlainText() != replacement:
                raise AssertionError("returning to an edited entry lost its draft")
            result = project.save((unit,))
            expected = target_raw.replace(before_text.encode("utf-16-le"), replacement.encode("utf-16-le"), 1)
            if target.read_bytes() != expected or source.read_bytes() != source_raw:
                raise AssertionError("themed editing changed file encoding, BOM, newlines, order, or unrelated bytes")
            project.reload_saved_files(result.changed_files)
            window.model.refresh_project(project)
            window._restore_selected_row(unit.uid)
            app.processEvents()

            window.translation_edit.setFocus()
            QTest.keyClick(window.translation_edit, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
            app.processEvents()
            if not window.search_edit.hasFocus():
                raise AssertionError("Ctrl+F did not focus the entry search")
            window.translation_edit.setFocus()
            app.processEvents()
            if window.translation_box.property("focused") is not True:
                raise AssertionError("the active translation editor has no focus indication")
            current = project.unit_by_uid(unit.uid)
            if hasattr(window, "confirm_current_button"):
                raise AssertionError("the editor still requires a separate confirmation step")
            window.search_edit.setText("NO_MATCHING_ENTRY_IN_THIS_FIXTURE")
            window._apply_filters()
            app.processEvents()
            if window.proxy.rowCount() or not window.empty_table_label.isVisible():
                raise AssertionError("empty search results did not explain how to continue")
            window.search_edit.clear()
            window._apply_filters()
            app.processEvents()
            if window.empty_table_label.isVisible():
                raise AssertionError("empty search overlay covered restored entries")

            # Follow-up marks are workflow state, independent of format problems.
            baseline_status_height = window.statusBar().height()
            marked = project.units[350]
            updated = project.units[20]
            invalid = project.units[349]
            invalid_before = invalid.current_text
            project.set_units_need_work((marked,), True)
            project.set_units_source_review((updated,), True)
            project.apply_unit_edits(((invalid, "Missing token", None),))
            window.model.refresh_units((marked, updated, invalid))
            window._update_counts()
            app.processEvents()
            attention = window.review_attention_button
            if not attention.isVisible() or "2" not in attention.text():
                raise AssertionError("follow-up count omitted a manual mark or counted a format problem")
            if attention.height() > 24 or window.statusBar().height() > baseline_status_height:
                raise AssertionError("follow-up action enlarged the status bar")
            if attention.width() > attention.fontMetrics().horizontalAdvance(attention.text()) + 24:
                raise AssertionError(f"follow-up action used oversized button padding: {attention.width()} vs text {attention.fontMetrics().horizontalAdvance(attention.text())}, hint {attention.sizeHint()}")
            footer = window.statusBar()
            count_rect = window.counts_label.rect().translated(window.counts_label.mapTo(footer, window.counts_label.rect().topLeft()))
            attention_rect = attention.rect().translated(attention.mapTo(footer, attention.rect().topLeft()))
            message_rect = footer.message_label.rect().translated(footer.message_label.mapTo(footer, footer.message_label.rect().topLeft()))
            if (message_rect.left() < 8 or footer.width() - attention_rect.right() < 8
                    or attention_rect.left() - count_rect.right() < 10
                    or abs(count_rect.center().y() - attention_rect.center().y()) > 1):
                raise AssertionError("status content lost its margins, spacing or vertical alignment")
            footer.showMessage("Long status message " * 100, 1)
            app.processEvents()
            if footer.message_label.toolTip() != footer.currentMessage():
                raise AssertionError("long status message lost its full-text tooltip")
            if footer.message_label.geometry().intersects(window.counts_label.geometry()):
                raise AssertionError("long status message overlapped the statistics")
            # A long paint pass can consume a single qWait before the timeout
            # event is dispatched. Wait for the state transition, bounded at 1 s.
            for _ in range(100):
                if not footer.currentMessage():
                    break
                QTest.qWait(10)
            if footer.currentMessage() or footer.message_label.text():
                raise AssertionError(f"status message timeout left stale text: {footer.currentMessage()!r}, {footer.message_label.text()!r}")
            window._restore_selected_row(marked.uid)
            window._update_issue_detail(marked)
            if window.issue_label.isVisible() or any(issue.needs_action for issue in marked.active_issues()):
                raise AssertionError("manual follow-up was presented as a format warning")
            status_index = window.model.index(window.model.row_for_uid(marked.uid), window.model.STATUS)
            if "手动" not in status_index.data(Qt.ItemDataRole.ToolTipRole):
                raise AssertionError("follow-up tooltip omitted why the entry was marked")
            # A project-wide count must reveal the marks even after narrowing search.
            window.only_format_warnings.setChecked(True)
            window.search_edit.setText("NO_MATCHING_ENTRY_IN_THIS_FIXTURE")
            window._apply_filters()
            attention.click()
            app.processEvents()
            visible = {window.proxy.unit_at(row).uid for row in range(window.proxy.rowCount())}
            if visible != {marked.uid, updated.uid} or window.only_format_warnings.isChecked():
                raise AssertionError("follow-up action mixed format errors with marks or kept hiding marked entries")
            save = window.top_buttons[0]
            save_rect = save.rect().translated(save.mapTo(window, save.rect().topLeft()))
            language_rect = window.language_combo.rect().translated(window.language_combo.mapTo(window, window.language_combo.rect().topLeft()))
            if not 0 < save_rect.left() - language_rect.right() <= 24 or save_rect.center().y() != language_rect.center().y():
                raise AssertionError("save action was detached from project and language controls")
            project.set_units_need_work((marked,), False)
            project.set_units_source_review((updated,), False)
            project.apply_unit_edits(((invalid, invalid_before, None),))
            window.model.refresh_units((marked, updated, invalid))
            window.status_combo.setCurrentIndex(window.status_combo.findData(ui.STATUS_FILTER_ALL))
            window._apply_filters()
            window._update_counts()
            app.processEvents()
            if attention.isVisible():
                raise AssertionError("cleared follow-up marks left a stale status bar action")
            window._restore_selected_row(current.uid)

            # Document mode hides the table, but file navigation must remain
            # available, including after returning from an expanded preview.
            with patch.object(window, "_is_document_file_selected", return_value=True), patch.object(window, "_current_document_unit", return_value=current):
                window._sync_document_layout()
                app.processEvents()
                if window.table_frame.isVisible() or not window.file_combo.isVisible():
                    raise AssertionError("document mode hid the file selector along with the entry table")
            window._sync_document_layout()
            app.processEvents()
            if not window.table_frame.isVisible() or not window.file_combo.isVisible():
                raise AssertionError("returning to entries did not restore the workspace")
        finally:
            window._clear_loaded_project()
            window.close()
            window.deleteLater()
            app.processEvents()
            ui.apply_theme(app, previous_theme)
            app.setFont(previous_font)
            set_language(previous_language)


def assert_supported_theme_settings_and_assets() -> None:
    import json
    from PySide6.QtGui import QImage
    from ..settings import load_settings, settings_path

    root = Path(__file__).resolve().parents[2]
    if (root / "translator_tool/game_theme.py").exists() or (root / "assets/game_theme").exists():
        raise AssertionError("obsolete game theme implementation or assets remain")
    for name in ("guide_surface_hires.png", "guide_header_hires.png", "guide_parchment_hires.png", "guide_scrollbar.png", "scrollup.png", "scrolldown.png"):
        if QImage(str(root / "assets/guide_preview" / name)).isNull():
            raise AssertionError(f"guide preview lost its {name} content asset")
    for theme in ("modern", "dark"):
        for name in ("chevron", "check"):
            if QImage(str(root / "assets/interface" / f"{name}-{theme}.svg")).isNull():
                raise AssertionError("a native control icon could not be rendered")
    with TemporaryDirectory(prefix="translator_theme_settings_") as directory, patch.dict(os.environ, {"LOCALAPPDATA": directory}):
        path = settings_path()
        path.parent.mkdir(parents=True)
        raw = json.dumps({"ui_theme": "guild2", "ui_language": "zh-CN", "editor_zoom_steps": 4}).encode("utf-8")
        path.write_bytes(raw)
        settings = load_settings()
        if (settings.ui_theme, settings.ui_language, settings.editor_zoom_steps) != ("modern", "zh-CN", 4):
            raise AssertionError("removed theme selection did not safely resolve to a supported theme")
        if path.read_bytes() != raw:
            raise AssertionError("reading an obsolete theme unexpectedly rewrote user settings")


def assert_interface_scale_settings() -> None:
    import json
    from PySide6.QtWidgets import QApplication
    from ..app import SettingsDialog
    from ..settings import AppSettings, UI_SCALE_PERCENTAGES, load_settings, save_settings, settings_path
    from ..ui_style import configure_interface_scale

    app = QApplication.instance() or QApplication([])
    with TemporaryDirectory(prefix="translator_ui_scale_") as directory, patch.dict(os.environ, {"LOCALAPPDATA": directory}):
        for percent in UI_SCALE_PERCENTAGES:
            settings = AppSettings(ui_scale_percent=percent, editor_zoom_steps=4)
            save_settings(settings)
            if load_settings().ui_scale_percent != percent:
                raise AssertionError("interface scale was not persisted")
            dialog = SettingsDialog(settings)
            try:
                if dialog.ui_scale.isEditable() or dialog.ui_scale.currentData() != percent:
                    raise AssertionError("interface scale did not use a fixed-choice control")
                result = dialog.result_settings()
                if result.ui_scale_percent != percent or result.editor_zoom_steps != 4:
                    raise AssertionError("interface scale was mixed with editor text zoom")
                if (not dialog.preview_display_group.isAncestorOf(dialog.preview_scope)
                        or dialog.interface_group.isAncestorOf(dialog.preview_scope)):
                    raise AssertionError("entry-list preview range was grouped with interface settings")
                if percent == 150:
                    dialog.resize(720, 360)
                    dialog.show()
                    app.processEvents()
                    if dialog.height() > 360 or not dialog.rect().contains(dialog.buttons.geometry()):
                        raise AssertionError("scaled settings pushed Save and Cancel beyond the window")
                    dialog.tabs.setCurrentIndex(1)
                    page = dialog.tabs.widget(1)
                    app.processEvents()
                    if page.verticalScrollBar().maximum() <= 0:
                        raise AssertionError("scaled settings did not allow scrolling to the remaining controls")
                    page.ensureWidgetVisible(dialog.preview_ui_assets_dir)
                    app.processEvents()
                    control = dialog.preview_ui_assets_dir
                    rect = control.rect().translated(control.mapTo(page.viewport(), control.rect().topLeft()))
                    if not page.viewport().rect().contains(rect):
                        raise AssertionError("preview asset control was unreachable in a short settings window")
            finally:
                dialog.close()
                dialog.deleteLater()
        for invalid in (None, True, "125", 125.0, 0, -100, 999, [], {}):
            raw = json.dumps({"ui_scale_percent": invalid}).encode("utf-8")
            settings_path().write_bytes(raw)
            if load_settings().ui_scale_percent != 100 or settings_path().read_bytes() != raw:
                raise AssertionError("invalid interface scale did not safely default without rewriting settings")
        with patch.dict(os.environ):
            configure_interface_scale(125)
            if os.environ["QT_SCALE_FACTOR"] != "1.25":
                raise AssertionError("interface scale was not passed to Qt")
    app.processEvents()


def assert_search_control_geometry_and_help() -> None:
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QHelpEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QHBoxLayout, QToolButton, QWidget
    from .. import app as ui
    from ..i18n import translate

    app = QApplication.instance() or QApplication([])
    previous_theme = ui._active_theme()
    toolbar = QWidget()
    toolbar.setObjectName("toolbar")
    layout = QHBoxLayout(toolbar)
    search = ui.SearchLineEdit()
    search.setFixedWidth(220)
    search.setClearButtonEnabled(True)
    layout.addWidget(search)
    toolbar.show()
    try:
        for theme in ("modern", "dark"):
            ui.apply_theme(app, theme)
            for query in ("", 'source:"Hello, friend", -translation:market', ""):
                search.setText(query)
                app.processEvents()
                case_rect = search.case_button.geometry()
                if case_rect.top() < 3 or search.height() - case_rect.bottom() - 1 < 3:
                    raise AssertionError("Aa control touched the search field's top or bottom border")
                if not search.rect().contains(case_rect):
                    raise AssertionError("Aa control escaped the search field")
                if query:
                    clear = next(button for button in search.findChildren(QToolButton) if button is not search.case_button)
                    if case_rect.right() >= clear.geometry().left() - 1:
                        raise AssertionError("Aa overlapped the native clear button")
                    search.case_button.setChecked(False)
                    QTest.mouseClick(search.case_button, Qt.MouseButton.LeftButton)
                    if not search.case_button.isChecked() or search.text() != query:
                        raise AssertionError("Aa click changed the search text or did not toggle matching")
                    QTest.mouseClick(clear, Qt.MouseButton.LeftButton)
                    if search.text() or not search.case_button.isChecked():
                        raise AssertionError("native clear button lost its behavior or reset the case option")
            for locale in ("en", "zh-CN"):
                help_text = translate("toolbar.search_shortcut", locale=locale)
                if not all(prefix in help_text for prefix in ("label:", "id:", "source:", "translation:", "file:", "status:")):
                    raise AssertionError("search help omitted a supported field")
                search.setToolTip(help_text)
                point = search.rect().center()
                help_event = QHelpEvent(QEvent.Type.ToolTip, point, search.mapToGlobal(point))
                app.sendEvent(search, help_event)
                popup = search.help_popup
                if popup is None or not popup.isVisible() or popup.text() != help_text:
                    raise AssertionError("search hover did not show the localized help")
                geometry = popup.geometry()
                popup.fade.setCurrentTime(popup.fade.duration() // 2)
                if not 0 < popup.windowOpacity() < 1 or popup.geometry() != geometry:
                    raise AssertionError("search help moved instead of fading in place")
                popup.fade.setCurrentTime(popup.fade.duration())
                app.sendEvent(search, QEvent(QEvent.Type.Leave))
                popup.fade.setCurrentTime(popup.fade.duration() // 2)
                if not popup.isVisible() or not 0 < popup.windowOpacity() < 1 or popup.geometry() != geometry:
                    raise AssertionError("search help did not fade out at its original position")
                # Re-entry during fade-out must reverse from the current opacity.
                opacity = popup.windowOpacity()
                app.sendEvent(search, help_event)
                if popup.windowOpacity() != opacity or popup.geometry() != geometry:
                    raise AssertionError("reopening search help jumped in opacity or position")
                popup.fade.setCurrentTime(popup.fade.duration())
                QTest.keyClick(search, Qt.Key.Key_A)
                popup.fade.setCurrentTime(popup.fade.duration())
                if popup.isVisible() or popup.expiry.isActive():
                    raise AssertionError("typing did not finish dismissing search help")
                app.sendEvent(search, help_event)
                toolbar.hide()
                if popup.isVisible() or popup.expiry.isActive():
                    raise AssertionError("search help outlived its hidden owner")
                toolbar.show()
                search.clear()
                app.processEvents()
    finally:
        toolbar.close()
        toolbar.deleteLater()
        ui.apply_theme(app, previous_theme)
        app.processEvents()


def assert_auxiliary_dialog_layout_and_state() -> None:
    from dataclasses import replace
    from types import SimpleNamespace
    from unittest.mock import Mock
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QAbstractItemView, QDialogButtonBox
    from .. import app as ui
    from ..i18n import current_language, set_language
    from ..settings import AppSettings, protect_secret, reveal_secret
    from ..source_sync import SourceProjectSpec, SourceSyncPlan, SourceSyncFileChange

    app = QApplication.instance() or QApplication([])
    previous_theme, previous_language = ui._active_theme(), current_language()
    original = AppSettings(preview_scope="translation", preview_window_scale_percent=175,
                           provider="deepl", deepl_plan="pro", editor_zoom_steps=4,
                           preview_translation_font_dir="F:/Example/Fonts", preview_ui_assets_dir="F:/Example/UI",
                           deepl_api_key_protected=protect_secret("fixture-deepl"),
                           openai_api_key_protected=protect_secret("fixture-openai"))
    try:
        for locale in ("zh-CN", "en"):
            set_language(locale)
            for theme in ("modern", "dark"):
                ui.apply_theme(app, theme)
                settings = replace(original, ui_language=locale, ui_theme=theme)
                dialog = ui.SettingsDialog(settings)
                try:
                    dialog.show()
                    dialog.tabs.setCurrentIndex(1)
                    app.processEvents()
                    page = dialog.tabs.widget(1)
                    scope, scale = dialog.preview_scope, dialog.preview_window_scale
                    if scope.parentWidget() is not scale.parentWidget():
                        raise AssertionError("preview range and scale are not in the same section")
                    if not 0 < scale.y() - scope.geometry().bottom() <= 12:
                        raise AssertionError("unrelated content separated preview range from scale")
                    for control in (scope, scale):
                        rect = control.rect().translated(control.mapTo(page.viewport(), control.rect().topLeft()))
                        if not page.viewport().rect().contains(rect):
                            raise AssertionError("preview range and scale are not visible together")
                    if not dialog.tabs.widget(2).isAncestorOf(dialog.provider):
                        raise AssertionError("translation provider is separated from its configuration page")
                    dialog.tabs.setCurrentIndex(2)
                    for provider in ("google", "openai", "deepl"):
                        dialog.provider.setCurrentIndex(dialog.provider.findData(provider))
                        if dialog.provider_tabs.currentIndex() != ("google", "deepl", "openai").index(provider):
                            raise AssertionError("choosing a provider did not reveal its configuration")
                    # Inspecting another provider's key must not change the selected service.
                    dialog.provider_tabs.setCurrentIndex(2)
                    for page_index in (3, 0, 1, 2):
                        dialog.tabs.setCurrentIndex(page_index)
                    result = dialog.result_settings()
                    for field in AppSettings.__dataclass_fields__:
                        actual, expected = getattr(result, field), getattr(settings, field)
                        if field.endswith("_protected"):
                            actual, expected = reveal_secret(actual), reveal_secret(expected)
                        if actual != expected:
                            raise AssertionError(f"dialog navigation changed setting {field}")
                    cancel = dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel)
                    QTest.mouseClick(cancel, Qt.MouseButton.LeftButton)
                    if dialog.isVisible() or settings != replace(original, ui_language=locale, ui_theme=theme):
                        raise AssertionError("cancelling settings changed the original configuration")
                finally:
                    dialog.close()
                    dialog.deleteLater()

        with TemporaryDirectory(prefix="translator_dialog_regression_") as directory:
            root = Path(directory)
            specs = [SourceProjectSpec(f"Project {index:02d}", "mod", root / "game" / str(index),
                                       root / "app" / str(index), index % 2 == 0) for index in range(24)]
            with patch.object(ui, "discover_game_source_projects", return_value=specs), \
                    patch.object(ui.ProjectManagerDialog, "_start_worker") as start:
                manager = ui.ProjectManagerDialog(root / "game", root / "app", lambda spec: False, lambda *_: "")
                try:
                    manager.resize(700, 420)
                    manager.show()
                    app.processEvents()
                    first, second = manager.rows[:2]
                    for row in (first, second):
                        if row.added_check.isChecked() != row.spec.added or row.added_check.isEnabled():
                            raise AssertionError("project checkbox did not reflect read-only added state")
                        QTest.mouseClick(row.added_check, Qt.MouseButton.LeftButton)
                        if row.added_check.isChecked() != row.spec.added:
                            raise AssertionError("clicking a status indicator changed project state")
                        indicator = row.added_check.geometry()
                        name = row.name_label.rect().translated(row.name_label.mapTo(row, row.name_label.rect().topLeft()))
                        if indicator.right() >= name.left() or not row.rect().contains(indicator):
                            raise AssertionError("project status checkbox overlapped the name or escaped its row")
                    if first.add_button.isVisible() or not first.update_button.isVisible():
                        raise AssertionError("added project did not expose exactly the update action")
                    if not second.add_button.isVisible() or second.update_button.isVisible():
                        raise AssertionError("new project did not expose exactly the add action")
                    for row, button in ((first, first.update_button), (second, second.add_button)):
                        rect = button.rect().translated(button.mapTo(row, button.rect().topLeft()))
                        if not row.rect().contains(rect):
                            raise AssertionError("project action escaped its row")
                        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                        start.assert_called_with("plan", row.spec)
                    if manager.close_button.width() > 160:
                        raise AssertionError("project manager stretched Close across the whole footer")
                    last = manager.rows[-1]
                    manager.scroll.ensureWidgetVisible(last)
                    app.processEvents()
                    rect = last.rect().translated(last.mapTo(manager.scroll.viewport(), last.rect().topLeft()))
                    if not manager.scroll.viewport().rect().contains(rect):
                        raise AssertionError("last project was not reachable in the scroll viewport")
                    manager._worker = SimpleNamespace(cancel=Mock())
                    manager._worker_token = 1
                    manager._worker_spec = specs[0]
                    manager.feedback_label.setText("scanning fixture")
                    manager.feedback_label.show()
                    no_changes = SourceSyncPlan(specs[0].source_root, specs[0].project_root, (), (), "fixture")
                    manager._worker_planned(1, no_changes)
                    if not manager.feedback_label.isVisible() or "scanning fixture" in manager.feedback_label.text():
                        raise AssertionError("completed source check left stale scanning feedback")
                    manager._worker = SimpleNamespace(cancel=Mock())
                    manager._worker_spec = specs[0]
                    manager._clear_worker(1)
                    if manager.feedback_label.isVisible():
                        raise AssertionError("finished operation left stale feedback behind")
                    cancel = Mock()
                    manager._worker = SimpleNamespace(cancel=cancel)
                    manager._worker_token = 1
                    manager._worker_spec = specs[-1]
                    QTest.keyClick(manager, Qt.Key.Key_Escape)
                    if not manager.isVisible() or not manager._close_when_idle:
                        raise AssertionError("Escape bypassed project operation cancellation")
                    cancel.assert_called_once()
                    manager._clear_worker(1)
                    app.processEvents()
                    if manager.isVisible():
                        raise AssertionError("project manager did not close after cancellation completed")
                finally:
                    manager._worker = None
                    manager.close()
                    manager.deleteLater()
            changes = (SourceSyncFileChange("Text.dbt", "modified", 2, 3, 1),
                       SourceSyncFileChange("Guides/Example.txt", "added", 1))
            plan = SourceSyncPlan(specs[0].source_root, specs[0].project_root, changes, (), "fixture")
            confirmation = ui.SourceSyncConfirmationDialog(specs[0], plan)
            try:
                confirmation.show()
                app.processEvents()
                if confirmation.details.editTriggers() != QAbstractItemView.EditTrigger.NoEditTriggers:
                    raise AssertionError("source-update preview unexpectedly allowed editing")
                for row, change in enumerate(changes):
                    values = tuple(confirmation.details.item(row, column).text() for column in range(1, 5))
                    expected = (change.rel_path, str(change.added_entries), str(change.modified_entries), str(change.removed_entries))
                    if values != expected:
                        raise AssertionError("source-update table changed the plan order or entry counts")
            finally:
                confirmation.close()
                confirmation.deleteLater()
    finally:
        ui.apply_theme(app, previous_theme)
        set_language(previous_language)
        app.processEvents()
