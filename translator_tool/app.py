from __future__ import annotations

from collections import Counter
from dataclasses import replace
from difflib import SequenceMatcher
import html
import math
from pathlib import Path
import re
import sys
import threading
import time
from typing import Callable, Iterable

from PySide6.QtCore import (
    QAbstractTableModel,
    QEvent,
    QModelIndex,
    QObject,
    QPoint,
    QRunnable,
    QSignalBlocker,
    QSortFilterProxyModel,
    Qt,
    QThreadPool,
    QTimer,
    QRectF,
    Signal,
)
from PySide6.QtGui import QAction, QCloseEvent, QColor, QFont, QKeyEvent, QKeySequence, QPainter, QPalette, QPen, QStandardItemModel, QSyntaxHighlighter, QTextCharFormat, QTextCursor, QWheelEvent
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionViewItem,
    QTableView,
    QTextEdit,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from .ai import (
    OpenAICompatibleProvider,
    TranslationProvider,
    TranslationProviderError,
    llm_provider_from_settings,
    provider_from_settings,
)
from .codec_adapter import Guild2Codec
from .git_history import GitCommit, GitError, LanguageGit, TranslationLogEntry
from .history import OperationHistory, TranslationOperation, UnitChange
from .i18n import current_language, history_kind_text, set_language, status_text, todo_reason_text, translate, ui_language_options
from .project import (
    ENABLE_FONT_GLYPH_VALIDATION,
    MISSING_WORK_STATUSES,
    Project,
    ProjectError,
    TODO_REASON_SOURCE_CHANGED,
    STATUS_EXTRA,
    STATUS_IGNORED,
    STATUS_PENDING_DELETE,
    STATUS_TODO,
    STATUS_TRANSLATED,
    SaveValidationError,
    TranslationUnit,
)
from .settings import AppSettings, load_settings, protect_secret, reveal_secret, save_settings
from .source_sync import (
    DEFAULT_TRANSLATION_LANGUAGE,
    SourceProjectSpec,
    discover_game_source_projects,
    ensure_translation_dir,
    game_languages_root,
    has_vanilla_source_entries,
    local_project_roots,
    managed_vanilla_project_root,
    sync_source_project,
    sync_vanilla_sources,
)
from .validation import (
    COLOR_TOKEN_RE,
    CHINESE_QUOTE_RE,
    HIGHLIGHT_RE,
    TOKEN_RE,
    format_counter_items,
    format_tokens,
    split_soft_color_tokens,
)


BUNDLED_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])).resolve()
APP_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else BUNDLED_ROOT
DEFAULT_PROJECT_ROOT = BUNDLED_ROOT
MANAGED_PROJECT_ROOT = managed_vanilla_project_root(APP_ROOT)
TYPING_GROUP_DELAY_MS = 750
FILE_FILTER_ALL = "__all_files__"
STATUS_FILTER_ALL = "__all_statuses__"
STATUS_FILTER_TODO = "__needs_translation__"
LANGUAGE_ACTION_NEW = "__new_language__"
LANGUAGE_ACTION_SEPARATOR = "__language_separator__"


class UnitTableModel(QAbstractTableModel):
    FILE, ID, LABEL, SOURCE, TRANSLATION, STATUS, FORMAT, AI = range(8)
    HEADER_KEYS = (
        "table.file",
        "table.id",
        "table.label",
        "table.source",
        "table.translation",
        "table.status",
        "table.format",
        "table.ai",
    )
    WIDTHS = (88, 60, 240, 300, 300, 60, 40, 55)

    def __init__(self, project: Project | None = None) -> None:
        super().__init__()
        self.project = project
        self.units: list[TranslationUnit] = list(project.units) if project else []
        self._row_by_uid: dict[str, int] = {}
        self._search: dict[str, str] = {}
        self._format_warning: dict[str, bool] = {}
        self._glyph_warning: dict[str, bool] = {}
        self._recently_translated: set[str] = set()
        self._rebuild_indexes()

    def set_project(self, project: Project) -> None:
        self.beginResetModel()
        self.project = project
        self.units = list(project.units)
        self._format_warning.clear()
        self._glyph_warning.clear()
        self._recently_translated.clear()
        self._rebuild_indexes()
        self.endResetModel()

    def clear(self) -> None:
        self.beginResetModel()
        self.project = None
        self.units = []
        self._search.clear()
        self._row_by_uid.clear()
        self._format_warning.clear()
        self._glyph_warning.clear()
        self._recently_translated.clear()
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.units)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.HEADER_KEYS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return translate(self.HEADER_KEYS[section])
        return super().headerData(section, orientation, role)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if not index.isValid() or index.row() >= len(self.units):
            return None
        unit = self.units[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return unit.uid
        if role == Qt.ItemDataRole.ToolTipRole:
            if index.column() == self.FORMAT:
                return _format_diff_tooltip(unit)
            if index.column() == self.SOURCE:
                return unit.source_text
            if index.column() == self.TRANSLATION:
                return unit.current_text
            if index.column() == self.AI:
                if unit.pending_delete:
                    return translate("table.ai_tooltip.delete")
                return translate("table.ai_tooltip")
            if index.column() == self.STATUS:
                suffix = translate("table.status.recent_suffix") if unit.uid in self._recently_translated else ""
                detail = ""
                if unit.filter_status() == STATUS_TODO and unit.todo_reason:
                    detail = "\n" + translate("issue.todo_reason_prefix", text=todo_reason_text(unit.todo_reason))
                return status_text(unit.display_status()) + suffix + detail
        if role == Qt.ItemDataRole.BackgroundRole:
            if unit.pending_delete:
                return QColor("#f2d6d3")
            if self.has_glyph_warning(index.row()):
                return QColor("#f3d9a4")
            return QColor("#dce5b5") if unit.uid in self._recently_translated else None
        if role == Qt.ItemDataRole.ForegroundRole and unit.pending_delete:
            return QColor("#9d0006")
        if role == Qt.ItemDataRole.FontRole and unit.pending_delete:
            font = QFont()
            font.setStrikeOut(True)
            return font
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        column = index.column()
        values = {
            self.FILE: unit.file_rel,
            self.ID: unit.record_id,
            self.LABEL: _clip(unit.label, 72),
            self.SOURCE: _clip(unit.source_text, 130),
            self.TRANSLATION: _clip(unit.current_text, 130),
            self.STATUS: unit.display_status(),
            self.FORMAT: _format_diff_text(unit),
            self.AI: translate("table.ai_action"),
        }
        return values.get(column, "")

    def unit_at(self, row: int) -> TranslationUnit | None:
        return self.units[row] if 0 <= row < len(self.units) else None

    def unit_for_uid(self, uid: str) -> TranslationUnit | None:
        if self.project is None:
            return None
        return self.project.unit_by_uid(uid)

    def search_blob(self, row: int) -> str:
        unit = self.units[row]
        return self._search.get(unit.uid, "")

    def refresh_unit(self, unit: TranslationUnit) -> None:
        row = self._row_by_uid.get(unit.uid)
        if row is None:
            return
        self._search[unit.uid] = _search_blob(unit)
        self._format_warning.pop(unit.uid, None)
        self._glyph_warning.pop(unit.uid, None)
        self.dataChanged.emit(self.index(row, 0), self.index(row, self.columnCount() - 1))

    def set_recently_translated(self, unit: TranslationUnit, recent: bool) -> None:
        if recent:
            self._recently_translated.add(unit.uid)
        else:
            self._recently_translated.discard(unit.uid)
        row = self._row_by_uid.get(unit.uid)
        if row is None:
            return
        self.dataChanged.emit(self.index(row, 0), self.index(row, self.columnCount() - 1))

    def is_recently_translated(self, unit: TranslationUnit) -> bool:
        return unit.uid in self._recently_translated

    @property
    def recently_translated_count(self) -> int:
        return len(self._recently_translated)

    def has_format_warning(self, row: int) -> bool:
        unit = self.unit_at(row)
        if unit is None:
            return False
        if unit.uid not in self._format_warning:
            self._format_warning[unit.uid] = bool(unit.issues())
        return self._format_warning[unit.uid]

    def has_glyph_warning(self, row: int) -> bool:
        unit = self.unit_at(row)
        if unit is None:
            return False
        if unit.uid not in self._glyph_warning:
            self._glyph_warning[unit.uid] = any(issue.code == "font-glyph" for issue in unit.issues())
        return self._glyph_warning[unit.uid]

    def row_for_uid(self, uid: str) -> int | None:
        return self._row_by_uid.get(uid)

    def _rebuild_indexes(self) -> None:
        self._row_by_uid = {unit.uid: index for index, unit in enumerate(self.units)}
        self._search = {unit.uid: _search_blob(unit) for unit in self.units}

    def retranslate(self) -> None:
        if self.columnCount() > 0:
            self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, self.columnCount() - 1)
        if self.rowCount() > 0:
            self.dataChanged.emit(self.index(0, 0), self.index(self.rowCount() - 1, self.columnCount() - 1))


class UnitFilterProxyModel(QSortFilterProxyModel):
    def __init__(self) -> None:
        super().__init__()
        self.file_filter = FILE_FILTER_ALL
        self.status_filter = STATUS_FILTER_ALL
        self.only_missing = True
        self.only_format_warnings = False
        self.query = ""
        # Source order is meaningful for this project. Disabling proxy sorting avoids a
        # multi-second re-sort when the user reveals all 17k+ translation units.
        self.setDynamicSortFilter(False)

    def set_filters(
        self, *, file_filter: str, status_filter: str, only_missing: bool, only_format_warnings: bool, query: str
    ) -> None:
        self.file_filter = file_filter
        self.status_filter = status_filter
        self.only_missing = only_missing
        self.only_format_warnings = only_format_warnings
        self.query = query.strip().lower()
        self.beginFilterChange()
        self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def refresh_rows(self) -> None:
        """Re-evaluate status-dependent rows without resetting the source model."""
        self.beginFilterChange()
        self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        source = self.sourceModel()
        if not isinstance(source, UnitTableModel):
            return False
        unit = source.unit_at(source_row)
        if unit is None:
            return False
        if self.file_filter != FILE_FILTER_ALL and self.file_filter.lower().endswith(".txt"):
            return unit.file_rel == self.file_filter
        if self.file_filter != FILE_FILTER_ALL and unit.file_rel != self.file_filter:
            return False
        effective_status = unit.filter_status()
        keep_visible = source.is_recently_translated(unit)
        if unit.pending_delete:
            keep_visible = True
        if self.status_filter == STATUS_FILTER_TODO and effective_status not in MISSING_WORK_STATUSES and not keep_visible:
            return False
        if self.status_filter == STATUS_FILTER_ALL and self.only_missing and effective_status not in MISSING_WORK_STATUSES and not keep_visible:
            return False
        if self.status_filter not in {STATUS_FILTER_ALL, STATUS_FILTER_TODO} and effective_status != self.status_filter:
            return False
        if self.only_format_warnings and not source.has_format_warning(source_row):
            return False
        return not self.query or self.query in source.search_blob(source_row)


class RowTintDelegate(QStyledItemDelegate):
    """Force model-provided review colors through the stylesheet paint path."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        if _paint_review_background(painter, option, index):
            painter.save()
            font = index.data(Qt.ItemDataRole.FontRole)
            painter.setFont(font if isinstance(font, QFont) else option.font)
            foreground = index.data(Qt.ItemDataRole.ForegroundRole)
            painter.setPen(foreground if isinstance(foreground, QColor) else QColor("#3c3836"))
            text_rect = option.rect.adjusted(5, 0, -5, 0)
            text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
            text = painter.fontMetrics().elidedText(text, option.textElideMode, text_rect.width())
            painter.drawText(text_rect, option.displayAlignment or (Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), text)
            painter.setPen(QColor("#d5c4a1"))
            painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
            painter.restore()
            return
        super().paint(painter, option, index)


class PopupHighlightDelegate(QStyledItemDelegate):
    """Keep the combo's current value visibly marked inside the popup."""

    def __init__(self, combo: QComboBox) -> None:
        super().__init__(combo.view())
        self.combo = combo

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        data = index.data(Qt.ItemDataRole.UserRole)
        if data == LANGUAGE_ACTION_SEPARATOR:
            painter.save()
            painter.setPen(QPen(QColor("#bdae93"), 1))
            y = option.rect.center().y()
            painter.drawLine(option.rect.left() + 8, y, option.rect.right() - 8, y)
            painter.restore()
            return
        is_current_value = index.row() == self.combo.currentIndex()
        is_hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)

        if is_current_value:
            painter.save()
            painter.fillRect(option.rect, QColor("#b8bb26"))
            painter.restore()
            option.palette.setColor(QPalette.ColorRole.Highlight, QColor("#b8bb26"))
            option.palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#3c3836"))
            option.palette.setColor(QPalette.ColorRole.Text, QColor("#3c3836"))
        elif is_hovered or is_selected:
            painter.save()
            painter.fillRect(option.rect, QColor("#d5c4a1"))
            painter.restore()
            option.palette.setColor(QPalette.ColorRole.Highlight, QColor("#d5c4a1"))
            option.palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#3c3836"))
            option.palette.setColor(QPalette.ColorRole.Text, QColor("#3c3836"))

        super().paint(painter, option, index)


class PopupSelectionComboBox(QComboBox):
    """Keep the popup view aligned with the combo's current item."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.view().setItemDelegate(PopupHighlightDelegate(self))
        self.view().setStyleSheet(
            """
            QAbstractItemView {
                background: #f2e5bc;
                border: 2px solid #3c3836;
                selection-background-color: #b8bb26;
                selection-color: #3c3836;
            }
            """
        )

    def showPopup(self) -> None:  # noqa: N802
        super().showPopup()
        QTimer.singleShot(0, self._sync_popup_selection)

    def _sync_popup_selection(self) -> None:
        row = self.currentIndex()
        if row < 0:
            return
        model_index = self.model().index(row, self.modelColumn(), self.rootModelIndex())
        if not model_index.isValid():
            return
        view = self.view()
        view.setCurrentIndex(model_index)
        view.setFocus(Qt.FocusReason.PopupFocusReason)
        selection_model = view.selectionModel()
        if selection_model is not None:
            selection_model.setCurrentIndex(
                model_index,
                selection_model.SelectionFlag.ClearAndSelect,
            )
        view.scrollTo(model_index)


def _paint_review_background(painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> bool:
    tint = index.data(Qt.ItemDataRole.BackgroundRole)
    if not isinstance(tint, QColor) or option.state & QStyle.StateFlag.State_Selected:
        return False
    painter.save()
    painter.fillRect(option.rect, tint)
    painter.restore()
    return True


def _unit_from_model_index(index: QModelIndex) -> TranslationUnit | None:
    model = index.model()
    if isinstance(model, QSortFilterProxyModel):
        source_index = model.mapToSource(index)
        source_model = model.sourceModel()
        return source_model.unit_at(source_index.row()) if isinstance(source_model, UnitTableModel) else None
    if isinstance(model, UnitTableModel):
        return model.unit_at(index.row())
    return None


class AiButtonDelegate(QStyledItemDelegate):
    translate_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None, provider: str = "google") -> None:
        super().__init__(parent)
        self.provider = provider
        self._pressed_uid = ""
        self._hover_uid = ""
        self._hover_phase = 0.0
        self._hover_timer = QTimer(self)
        self._hover_timer.setInterval(45)
        self._hover_timer.timeout.connect(self._advance_hover)
        if isinstance(parent, QTableView):
            parent.setMouseTracking(True)
            parent.viewport().setMouseTracking(True)
            parent.viewport().installEventFilter(self)

    def set_provider(self, provider: str) -> None:
        self.provider = provider
        if self.parent():
            self.parent().viewport().update()

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        unit = _unit_from_model_index(index)
        if isinstance(unit, TranslationUnit) and unit.pending_delete:
            if not _paint_review_background(painter, option, index):
                background = QStyleOptionViewItem(option)
                background.text = ""
                style = option.widget.style() if option.widget else QApplication.style()
                style.drawControl(QStyle.ControlElement.CE_ItemViewItem, background, painter, option.widget)
            painter.save()
            font = painter.font()
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor("#9d0006"))
            painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, status_text(STATUS_PENDING_DELETE))
            painter.restore()
            return
        uid = str(index.data(Qt.ItemDataRole.UserRole) or "")
        pressed = uid == self._pressed_uid
        hovered = uid == self._hover_uid
        painter.save()
        _paint_review_background(painter, option, index)
        rect = option.rect.adjusted(7, 6, -7, -6)
        if pressed:
            rect.translate(2, 3)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if hovered and not pressed:
            rect.translate(0, -1)
        if not pressed:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#3c3836"))
            shadow_offset = 4 if hovered else 3
            painter.drawRoundedRect(rect.translated(3, shadow_offset), 4, 4)
        fill = QColor("#d79921") if self.provider == "google" else QColor("#b16286")
        if hovered:
            fill = fill.lighter(108 + int((math.sin(self._hover_phase) + 1) * 6))
        if pressed:
            fill = fill.darker(115)
        painter.setPen(QPen(QColor("#3c3836"), 2 if not hovered else 3))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, 4, 4)
        if hovered:
            shine = QColor("#fbf1c7")
            shine.setAlpha(150 + int((math.sin(self._hover_phase) + 1) * 40))
            painter.setPen(QPen(shine, 1.5))
            painter.drawRoundedRect(rect.adjusted(3, 3, -3, -3), 2, 2)
        painter.setPen(QColor("#3c3836"))
        font = painter.font()
        font.setBold(True)
        font.setPointSize(max(9, font.pointSize()))
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, translate("table.ai_action"))
        painter.restore()

    def editorEvent(self, event, model, option: QStyleOptionViewItem, index: QModelIndex) -> bool:  # noqa: N802
        unit = _unit_from_model_index(index)
        if isinstance(unit, TranslationUnit) and unit.pending_delete:
            return False
        uid = str(index.data(Qt.ItemDataRole.UserRole) or "")
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._pressed_uid = uid
            if self.parent():
                self.parent().viewport().update(option.rect)
            return True
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            was_pressed = self._pressed_uid == uid
            self._pressed_uid = ""
            if self.parent():
                self.parent().viewport().update(option.rect)
            if was_pressed and option.rect.contains(event.position().toPoint()) and uid:
                self.translate_requested.emit(uid)
                return True
        return super().editorEvent(event, model, option, index)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        table = self.parent()
        if not isinstance(table, QTableView) or watched is not table.viewport():
            return super().eventFilter(watched, event)
        if event.type() == QEvent.Type.MouseMove:
            index = table.indexAt(event.position().toPoint())
            uid = str(index.data(Qt.ItemDataRole.UserRole) or "") if index.isValid() and index.column() == UnitTableModel.AI else ""
            self._set_hover(uid)
            if uid:
                table.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                table.viewport().unsetCursor()
        elif event.type() == QEvent.Type.Leave:
            self._set_hover("")
            table.viewport().unsetCursor()
        return super().eventFilter(watched, event)

    def _set_hover(self, uid: str) -> None:
        if uid == self._hover_uid:
            return
        self._hover_uid = uid
        self._hover_phase = 0.0
        if uid:
            self._hover_timer.start()
        else:
            self._hover_timer.stop()
        table = self.parent()
        if isinstance(table, QTableView):
            table.viewport().update()

    def _advance_hover(self) -> None:
        self._hover_phase += 0.42
        table = self.parent()
        if isinstance(table, QTableView):
            table.viewport().update()


class FormatDiffDelegate(QStyledItemDelegate):
    """Paint a compact, Git-like token delta without wasting a wide column."""

    COLORS = {
        "!": QColor("#cc241d"),
        "?": QColor("#d79921"),
        "~": QColor("#928374"),
        "✓": QColor("#689d6a"),
    }

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        unit = _unit_from_model_index(index)
        if not isinstance(unit, TranslationUnit):
            super().paint(painter, option, index)
            return
        if unit.pending_delete:
            if not _paint_review_background(painter, option, index):
                background = QStyleOptionViewItem(option)
                background.text = ""
                style = option.widget.style() if option.widget else QApplication.style()
                style.drawControl(QStyle.ControlElement.CE_ItemViewItem, background, painter, option.widget)
            painter.save()
            font = painter.font()
            font.setBold(True)
            font.setStrikeOut(True)
            painter.setFont(font)
            painter.setPen(QColor("#9d0006"))
            painter.drawText(option.rect.adjusted(5, 0, -5, 0), Qt.AlignmentFlag.AlignCenter, history_kind_text("删除"))
            painter.restore()
            return

        if not _paint_review_background(painter, option, index):
            background = QStyleOptionViewItem(option)
            background.text = ""
            style = option.widget.style() if option.widget else QApplication.style()
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, background, painter, option.widget)

        marker, _summary = _format_indicator(unit)
        painter.save()
        font = painter.font()
        font.setBold(True)
        font.setPointSize(max(font.pointSize(), 12))
        painter.setFont(font)
        metrics = painter.fontMetrics()
        painter.setPen(self.COLORS.get(marker, QColor("#3c3836")))
        painter.drawText(option.rect.adjusted(5, 0, -5, 0), Qt.AlignmentFlag.AlignCenter, marker)
        painter.restore()


class StatusBadgeDelegate(QStyledItemDelegate):
    STYLES = {
        STATUS_TODO: ("status.todo", "#d79921", "#3c3836"),
        STATUS_TRANSLATED: ("status.translated", "#98971a", "#fbf1c7"),
        STATUS_PENDING_DELETE: ("status.pending_delete", "#cc241d", "#fbf1c7"),
        STATUS_IGNORED: ("status.ignored", "#928374", "#fbf1c7"),
        STATUS_EXTRA: ("status.extra", "#b16286", "#fbf1c7"),
    }

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        status = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        label_key, fill, text = self.STYLES.get(status, ("status.unknown", "#928374", "#fbf1c7"))
        label = translate(label_key) if label_key.startswith("status.") else label_key
        if not _paint_review_background(painter, option, index):
            background = QStyleOptionViewItem(option)
            background.text = ""
            style = option.widget.style() if option.widget else QApplication.style()
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, background, painter, option.widget)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(5, 6, -5, -6)
        painter.setPen(QPen(QColor("#3c3836"), 1.5))
        painter.setBrush(QColor(fill))
        painter.drawRoundedRect(rect, 4, 4)
        font = painter.font()
        font.setBold(True)
        font.setPointSize(max(8, font.pointSize() - 1))
        painter.setFont(font)
        painter.setPen(QColor(text))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
        painter.restore()


class BatchTranslateButton(QPushButton):
    """A toolbar action that doubles as the visible progress and cancel affordance."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(translate("button.batch_ai.idle"), parent)
        self.setObjectName("batchAi")
        self.setMinimumWidth(118)
        self._busy = False
        self._hovering = False
        self._cancelling = False
        self._current = 0
        self._total = 0
        self._angle = 0
        self._spinner = QTimer(self)
        self._spinner.setInterval(55)
        self._spinner.timeout.connect(self._advance_spinner)

    @property
    def busy(self) -> bool:
        return self._busy

    def set_busy(self, busy: bool, total: int = 0) -> None:
        self._busy = busy
        self._cancelling = False
        self._current = 0
        self._total = total if busy else 0
        if busy:
            self._spinner.start()
        else:
            self._spinner.stop()
        self._update_presentation()

    def set_progress(self, current: int, total: int) -> None:
        self._current, self._total = current, total
        self._update_presentation()

    def set_cancelling(self) -> None:
        if not self._busy:
            return
        self._cancelling = True
        self._update_presentation()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovering = True
        self._update_presentation()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovering = False
        self._update_presentation()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if not self._busy or self._hovering or self._cancelling:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#3c3836"), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        spinner_rect = QRectF(10, (self.height() - 14) / 2, 14, 14)
        painter.drawArc(spinner_rect, self._angle * 16, 105 * 16)
        painter.end()

    def _advance_spinner(self) -> None:
        self._angle = (self._angle + 28) % 360
        self.update()

    def _update_presentation(self) -> None:
        if not self._busy:
            self.setText(translate("button.batch_ai.idle"))
            self.setToolTip(translate("button.batch_ai.idle_tooltip"))
            mode = "idle"
        elif self._cancelling:
            self.setText(translate("button.batch_ai.cancelling"))
            self.setToolTip(translate("button.batch_ai.cancelling_tooltip"))
            mode = "cancelling"
        elif self._hovering:
            self.setText(translate("button.batch_ai.cancel"))
            self.setToolTip(translate("button.batch_ai.cancel_tooltip"))
            mode = "cancel"
        else:
            progress = f" {self._current}/{self._total}" if self._total else ""
            self.setText(translate("button.batch_ai.busy", progress=progress))
            self.setToolTip(translate("button.batch_ai.busy_tooltip"))
            mode = "busy"
        if self.property("mode") != mode:
            self.setProperty("mode", mode)
            self.style().unpolish(self)
            self.style().polish(self)
        self.update()


class TokenHighlighter(QSyntaxHighlighter):
    def __init__(self, document, glyph_codec: Guild2Codec | None = None) -> None:
        super().__init__(document)
        self.glyph_codec = glyph_codec
        self.format_token = _text_format("#075a9c")
        self.color_token = _text_format("#7a3e9d")
        self.markup_token = _text_format("#6b6b00")
        self.quote_token = _text_format("#107c10")
        self.bad_token = _text_format("#b00020", underline=True)
        self.warn_token = _text_format("#c45f00", underline=True)
        self.glyph_token = _text_format("#cc241d", underline=True)

    def set_glyph_codec(self, glyph_codec: Guild2Codec | None) -> None:
        self.glyph_codec = glyph_codec
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        for match in HIGHLIGHT_RE.finditer(text):
            token = match.group(0)
            fmt = self.format_token
            if token.startswith("$C") or token.startswith("$S") or token == "$N":
                fmt = self.color_token
            elif token.startswith(("<", "[", "{")):
                fmt = self.markup_token
            elif token.startswith(">") and token.endswith("<"):
                fmt = self.quote_token
            self.setFormat(match.start(), match.end() - match.start(), fmt)
        for match in CHINESE_QUOTE_RE.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), self.warn_token)
        if self.glyph_codec is not None:
            position = 0
            for char in text:
                if self.glyph_codec.unsupported_characters(char):
                    self.setFormat(position, 2 if ord(char) > 0xFFFF else 1, self.glyph_token)
                position += 2 if ord(char) > 0xFFFF else 1


class AiWorkerSignals(QObject):
    translated = Signal(str, str)
    failed = Signal(str, str)
    progress = Signal(int, int)
    finished = Signal()


class AiWorker(QRunnable):
    def __init__(self, provider: TranslationProvider, units: Iterable[TranslationUnit], cancel_event: threading.Event) -> None:
        super().__init__()
        self.provider = provider
        self.units = tuple(units)
        self.cancel_event = cancel_event
        self.signals = AiWorkerSignals()

    def run(self) -> None:
        last_request = 0.0
        total = len(self.units)
        for number, unit in enumerate(self.units, start=1):
            if self.cancel_event.is_set():
                break
            delay = self.provider.request_delay_seconds - (time.monotonic() - last_request)
            if delay > 0 and self.cancel_event.wait(delay):
                break
            try:
                translated = self.provider.translate(unit.source_text, dbt_field=unit.ref.kind == "dbt")
                last_request = time.monotonic()
                self.signals.translated.emit(unit.uid, translated)
            except TranslationProviderError as exc:
                last_request = time.monotonic()
                self.signals.failed.emit(unit.uid, str(exc))
            except Exception as exc:  # keep one malformed remote response from killing a batch
                last_request = time.monotonic()
                self.signals.failed.emit(unit.uid, translate("error.unexpected", error=exc))
            self.signals.progress.emit(number, total)
        self.signals.finished.emit()


class SuggestionWorkerSignals(QObject):
    chunk = Signal(str)
    failed = Signal(str)
    finished = Signal()


class LlmSuggestionWorker(QRunnable):
    def __init__(
        self,
        provider: OpenAICompatibleProvider,
        source_text: str,
        current_translation: str,
        cancel_event: threading.Event,
    ) -> None:
        super().__init__()
        self.provider = provider
        self.source_text = source_text
        self.current_translation = current_translation
        self.cancel_event = cancel_event
        self.signals = SuggestionWorkerSignals()

    def run(self) -> None:
        try:
            for chunk in self.provider.stream_suggestion(self.source_text, self.current_translation):
                if self.cancel_event.is_set():
                    break
                self.signals.chunk.emit(chunk)
        except TranslationProviderError as exc:
            if not self.cancel_event.is_set():
                self.signals.failed.emit(str(exc))
        except Exception as exc:
            if not self.cancel_event.is_set():
                self.signals.failed.emit(translate("error.unexpected", error=exc))
        finally:
            self.signals.finished.emit()


class HistoryRenderWorkerSignals(QObject):
    rendered = Signal(int, str)
    failed = Signal(int, str)


class HistoryRenderWorker(QRunnable):
    def __init__(self, request_id: int, git: LanguageGit, commits_oldest_first: tuple[GitCommit, ...]) -> None:
        super().__init__()
        self.request_id = request_id
        self.git = git
        self.commits_oldest_first = commits_oldest_first
        self.signals = HistoryRenderWorkerSignals()

    def run(self) -> None:
        try:
            hashes = tuple(commit.full_hash for commit in self.commits_oldest_first)
            entries = self.git.entries_for_commits(hashes)
            rendered = _render_history_html(self.commits_oldest_first, entries)
            self.signals.rendered.emit(self.request_id, rendered)
        except (GitError, OSError, UnicodeError) as exc:
            self.signals.failed.emit(self.request_id, str(exc))
        except Exception as exc:
            self.signals.failed.emit(self.request_id, translate("error.unexpected", error=exc))


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._preview_language = settings.ui_language or current_language()
        self.setMinimumWidth(720)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        self._build_general_tab()
        self._build_translation_tab()
        self._build_git_tab()
        self._build_save_tab()

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.provider.currentIndexChanged.connect(self._update_enabled)
        self.ui_language.currentIndexChanged.connect(self._on_language_changed)
        self._retranslate_ui()

    def _build_general_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.interface_group = QGroupBox()
        interface_form = QFormLayout(self.interface_group)
        self.ui_language = QComboBox()
        self.ui_language_label = QLabel()
        interface_form.addRow(self.ui_language_label, self.ui_language)
        layout.addWidget(self.interface_group)

        self.service_group = QGroupBox()
        service_form = QFormLayout(self.service_group)
        self.provider = QComboBox()
        self.provider_label = QLabel()
        service_form.addRow(self.provider_label, self.provider)
        self.provider_note = QLabel()
        self.provider_note.setObjectName("hint")
        self.provider_note.setWordWrap(True)
        service_form.addRow(self.provider_note)
        layout.addWidget(self.service_group)
        layout.addStretch(1)
        self.tabs.addTab(tab, "")

    def _build_translation_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        self.google_group = QGroupBox()
        google_form = QFormLayout(self.google_group)
        self.google_endpoint = QLineEdit(self.settings.google_endpoint)
        self.source_language = QLineEdit(self.settings.source_language)
        self.target_language = QLineEdit(self.settings.target_language)
        self.google_endpoint_label = QLabel()
        self.source_language_label = QLabel()
        self.target_language_label = QLabel()
        google_form.addRow(self.google_endpoint_label, self.google_endpoint)
        google_form.addRow(self.source_language_label, self.source_language)
        google_form.addRow(self.target_language_label, self.target_language)
        layout.addWidget(self.google_group)

        self.openai_group = QGroupBox()
        openai_form = QFormLayout(self.openai_group)
        self.openai_base_url = QLineEdit(self.settings.openai_base_url)
        self.openai_model = QLineEdit(self.settings.openai_model)
        self.openai_key = QLineEdit(reveal_secret(self.settings.openai_api_key_protected))
        self.openai_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.openai_base_url_label = QLabel()
        self.openai_model_label = QLabel()
        self.openai_key_label = QLabel()
        openai_form.addRow(self.openai_base_url_label, self.openai_base_url)
        openai_form.addRow(self.openai_model_label, self.openai_model)
        openai_form.addRow(self.openai_key_label, self.openai_key)
        layout.addWidget(self.openai_group)

        self.translation_note = QLabel()
        self.translation_note.setWordWrap(True)
        self.translation_note.setObjectName("hint")
        layout.addWidget(self.translation_note)
        layout.addStretch(1)
        self.tabs.addTab(tab, "")

    def _build_git_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.git_group = QGroupBox()
        git_form = QFormLayout(self.git_group)
        self.git_name = QLineEdit(self.settings.git_author_name)
        self.git_email = QLineEdit(self.settings.git_author_email)
        self.git_name_label = QLabel()
        self.git_email_label = QLabel()
        git_form.addRow(self.git_name_label, self.git_name)
        git_form.addRow(self.git_email_label, self.git_email)
        layout.addWidget(self.git_group)
        layout.addStretch(1)
        self.tabs.addTab(tab, "")

    def _build_save_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.save_group = QGroupBox()
        save_layout = QVBoxLayout(self.save_group)
        self.auto_space_before_color_tokens = QCheckBox()
        self.auto_space_before_color_tokens.setChecked(self.settings.auto_space_before_color_tokens_on_save)
        save_layout.addWidget(self.auto_space_before_color_tokens)
        self.save_hint = QLabel()
        self.save_hint.setObjectName("hint")
        self.save_hint.setWordWrap(True)
        save_layout.addWidget(self.save_hint)
        layout.addWidget(self.save_group)
        layout.addStretch(1)
        self.tabs.addTab(tab, "")

    def _populate_ui_language_combo(self) -> None:
        current = str(self.ui_language.currentData() or self.settings.ui_language or current_language())
        blocker = QSignalBlocker(self.ui_language)
        self.ui_language.clear()
        for code, label in ui_language_options(locale=self._preview_language):
            self.ui_language.addItem(label, code)
        index = self.ui_language.findData(current)
        self.ui_language.setCurrentIndex(index if index >= 0 else 0)
        del blocker

    def _populate_provider_combo(self) -> None:
        current = str(self.provider.currentData() or self.settings.provider)
        blocker = QSignalBlocker(self.provider)
        self.provider.clear()
        self.provider.addItem(translate("settings.group.google", locale=self._preview_language), "google")
        self.provider.addItem(translate("dialog.ai_service_openai", locale=self._preview_language).lstrip("✦ ").strip(), "openai")
        index = self.provider.findData(current)
        self.provider.setCurrentIndex(index if index >= 0 else 0)
        del blocker

    def _on_language_changed(self) -> None:
        self._preview_language = str(self.ui_language.currentData() or self._preview_language)
        self._retranslate_ui()

    def _retranslate_ui(self) -> None:
        locale = self._preview_language
        self.setWindowTitle(translate("settings.title", locale=locale))
        self._populate_ui_language_combo()
        self._populate_provider_combo()

        self.tabs.setTabText(0, translate("settings.tab.general", locale=locale))
        self.tabs.setTabText(1, translate("settings.tab.translation", locale=locale))
        self.tabs.setTabText(2, translate("settings.tab.git", locale=locale))
        self.tabs.setTabText(3, translate("settings.tab.save", locale=locale))

        self.interface_group.setTitle(translate("settings.group.ui", locale=locale))
        self.service_group.setTitle(translate("settings.group.service", locale=locale))
        self.google_group.setTitle(translate("settings.group.google", locale=locale))
        self.openai_group.setTitle(translate("settings.group.openai", locale=locale))
        self.git_group.setTitle(translate("settings.group.git", locale=locale))
        self.save_group.setTitle(translate("settings.group.save", locale=locale))

        self.ui_language_label.setText(translate("settings.ui_language", locale=locale))
        self.provider_label.setText(translate("settings.provider", locale=locale))
        self.google_endpoint_label.setText(translate("settings.endpoint", locale=locale))
        self.source_language_label.setText(translate("settings.source_language", locale=locale))
        self.target_language_label.setText(translate("settings.target_language", locale=locale))
        self.openai_base_url_label.setText(translate("settings.base_url", locale=locale))
        self.openai_model_label.setText(translate("settings.model", locale=locale))
        self.openai_key_label.setText(translate("settings.api_key", locale=locale))
        self.git_name_label.setText(translate("settings.author_name", locale=locale))
        self.git_email_label.setText(translate("settings.email", locale=locale))
        self.auto_space_before_color_tokens.setText(translate("settings.auto_space_before_color_tokens", locale=locale))
        self.save_hint.setText(translate("settings.save_hint", locale=locale))
        self.translation_note.setText(translate("settings.note", locale=locale))

        save_button = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        cancel_button = self.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if save_button is not None:
            save_button.setText(translate("settings.button.save", locale=locale))
        if cancel_button is not None:
            cancel_button.setText(translate("settings.button.cancel", locale=locale))
        self._update_enabled()

    def _update_enabled(self) -> None:
        locale = self._preview_language
        if self.provider.currentData() == "openai":
            self.provider_note.setText(translate("settings.provider_note.openai", locale=locale))
        else:
            self.provider_note.setText(translate("settings.provider_note.google", locale=locale))

    def result_settings(self) -> AppSettings:
        return replace(
            self.settings,
            ui_language=str(self.ui_language.currentData() or current_language()),
            provider=str(self.provider.currentData()),
            google_endpoint=self.google_endpoint.text().strip(),
            source_language=self.source_language.text().strip() or "en",
            target_language=self.target_language.text().strip() or "zh-CN",
            openai_base_url=self.openai_base_url.text().strip(),
            openai_model=self.openai_model.text().strip(),
            openai_api_key_protected=protect_secret(self.openai_key.text().strip()),
            git_author_name=self.git_name.text().strip() or "The Guild 2 Translator",
            git_author_email=self.git_email.text().strip() or "translator@local",
            auto_space_before_color_tokens_on_save=self.auto_space_before_color_tokens.isChecked(),
        )


class NewLanguageDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle(translate("dialog.new_language_title"))
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        hint = QLabel(translate("dialog.new_language_detail"))
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        layout.addWidget(hint)

        row = QHBoxLayout()
        self.prefix = QLineEdit("#")
        self.prefix.setReadOnly(True)
        self.prefix.setFixedWidth(42)
        self.prefix.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.prefix)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(translate("dialog.new_language_placeholder"))
        self.name_edit.returnPressed.connect(self.accept)
        row.addWidget(self.name_edit, 1)
        layout.addLayout(row)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_button is not None:
            ok_button.setText(translate("dialog.new_language_confirm"))
        cancel_button = self.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_button is not None:
            cancel_button.setText(translate("dialog.cancel"))
        layout.addWidget(self.buttons)

    def result_language(self) -> str:
        return "#" + self.name_edit.text().strip().lstrip("#")


class ProjectManagerRow(QFrame):
    add_requested = Signal(object)
    update_requested = Signal(object)

    def __init__(self, spec: SourceProjectSpec, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("projectManagerRow")
        self.spec = spec

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)

        action_layout = QVBoxLayout()
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(6)
        self.add_button = QToolButton()
        self.add_button.setObjectName("projectAddButton")
        self.add_button.setFixedWidth(36)
        self.add_button.clicked.connect(lambda: self.add_requested.emit(self.spec))
        action_layout.addWidget(self.add_button, 0, Qt.AlignmentFlag.AlignTop)

        self.added_check = QCheckBox()
        self.added_check.setEnabled(False)
        self.added_check.setChecked(True)
        self.added_check.setObjectName("projectAddedCheck")
        action_layout.addWidget(self.added_check, 0, Qt.AlignmentFlag.AlignTop)
        action_layout.addStretch(1)
        layout.addLayout(action_layout)

        details_layout = QVBoxLayout()
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(5)
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        self.name_label = QLabel()
        self.name_label.setObjectName("projectManagerName")
        header_layout.addWidget(self.name_label)

        self.kind_badge = QLabel()
        self.kind_badge.setObjectName("projectKindBadge")
        header_layout.addWidget(self.kind_badge)

        self.state_badge = QLabel()
        self.state_badge.setObjectName("projectStateBadge")
        header_layout.addWidget(self.state_badge)
        header_layout.addStretch(1)
        details_layout.addLayout(header_layout)

        self.source_label = QLabel()
        self.source_label.setWordWrap(True)
        self.source_label.setObjectName("projectManagerPath")
        details_layout.addWidget(self.source_label)

        self.project_label = QLabel()
        self.project_label.setWordWrap(True)
        self.project_label.setObjectName("projectManagerPath")
        details_layout.addWidget(self.project_label)

        layout.addLayout(details_layout, 1)

        button_layout = QVBoxLayout()
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(6)
        self.update_button = QPushButton()
        self.update_button.clicked.connect(lambda: self.update_requested.emit(self.spec))
        button_layout.addWidget(self.update_button, 0, Qt.AlignmentFlag.AlignTop)
        button_layout.addStretch(1)
        layout.addLayout(button_layout)
        self.refresh(spec)

    def refresh(self, spec: SourceProjectSpec) -> None:
        self.spec = spec
        self.name_label.setText(spec.name)
        self.kind_badge.setProperty("kind", spec.kind)
        self.kind_badge.style().unpolish(self.kind_badge)
        self.kind_badge.style().polish(self.kind_badge)
        self.kind_badge.setText(
            translate("project.manager.kind.vanilla")
            if spec.kind == "vanilla"
            else translate("project.manager.kind.mod")
        )
        self.state_badge.setProperty("state", "added" if spec.added else "missing")
        self.state_badge.style().unpolish(self.state_badge)
        self.state_badge.style().polish(self.state_badge)
        self.state_badge.setText(
            translate("project.manager.state.added")
            if spec.added
            else translate("project.manager.state.not_added")
        )
        self.source_label.setText(translate("project.manager.source_path", path=str(spec.source_root)))
        self.project_label.setText(translate("project.manager.project_path", path=str(spec.project_root)))
        self.add_button.setVisible(not spec.added)
        self.added_check.setVisible(spec.added)
        self.update_button.setVisible(spec.added)
        self.update_button.setEnabled(spec.added)
        self.add_button.setText(translate("project.manager.add_symbol"))
        self.add_button.setToolTip(translate("project.manager.add_tooltip", name=spec.name))
        self.update_button.setText(translate("project.manager.update"))
        self.update_button.setToolTip(translate("project.manager.update_tooltip", name=spec.name))


class ProjectManagerDialog(QDialog):
    def __init__(
        self,
        game_root: Path,
        app_root: Path,
        sync_callback: Callable[[SourceProjectSpec], str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("projectManagerDialog")
        self.setWindowTitle(translate("dialog.project_manager_title"))
        self.setMinimumSize(880, 520)
        self.game_root = game_root
        self.app_root = app_root
        self.sync_callback = sync_callback
        self.rows: list[ProjectManagerRow] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.summary_label = QLabel()
        self.summary_label.setObjectName("projectManagerSummary")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.game_root_label = QLabel()
        self.game_root_label.setObjectName("projectManagerGameRoot")
        self.game_root_label.setText(translate("project.manager.game_root", path=str(self.game_root)))
        self.game_root_label.setWordWrap(True)
        layout.addWidget(self.game_root_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(10)
        self.scroll.setWidget(self.list_container)
        layout.addWidget(self.scroll, 1)

        self.feedback_label = QLabel()
        self.feedback_label.setObjectName("projectManagerFeedback")
        self.feedback_label.setWordWrap(True)
        self.feedback_label.hide()
        layout.addWidget(self.feedback_label)
        self.refresh_projects()

    def refresh_projects(self) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.rows.clear()

        projects = discover_game_source_projects(self.game_root, self.app_root)
        added_count = sum(project.added for project in projects)
        self.summary_label.setText(
            translate("project.manager.summary", total=len(projects), added=added_count)
        )
        if not projects:
            empty = QLabel(translate("project.manager.empty"))
            empty.setObjectName("hint")
            empty.setWordWrap(True)
            self.list_layout.addWidget(empty)
            self.list_layout.addStretch(1)
            return

        for spec in projects:
            row = ProjectManagerRow(spec, self.list_container)
            row.add_requested.connect(self._sync_project)
            row.update_requested.connect(self._sync_project)
            self.list_layout.addWidget(row)
            self.rows.append(row)
        self.list_layout.addStretch(1)

    def _sync_project(self, spec: SourceProjectSpec) -> None:
        try:
            message = self.sync_callback(spec)
        except Exception as exc:
            QMessageBox.warning(self, translate("dialog.project_manager_title"), str(exc))
            return
        self.feedback_label.setText(message)
        self.feedback_label.show()
        self.refresh_projects()


class SuggestionDialog(QDialog):
    apply_translation = Signal(str)
    dismissed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("suggestionDialog")
        self.setWindowTitle(translate("suggestion.title"))
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setModal(False)
        self.setMinimumSize(350, 200)
        self.resize(350, 250)
        self._markdown = ""
        self._recommended_translation = ""

        layout = QVBoxLayout(self)
        self.loading_label = QLabel(translate("suggestion.loading"))
        self.loading_label.setObjectName("suggestionStatus")
        layout.addWidget(self.loading_label)
        self.content = QTextBrowser()
        self.content.setOpenExternalLinks(False)
        self.content.setPlaceholderText(translate("suggestion.placeholder"))
        layout.addWidget(self.content, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_button is not None:
            close_button.setText(translate("button.close"))
        self.apply_button = buttons.addButton(translate("suggestion.apply"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self._apply)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

    def append_chunk(self, chunk: str) -> None:
        self._markdown += chunk
        self.content.setMarkdown(self._markdown)
        self.content.verticalScrollBar().setValue(self.content.verticalScrollBar().maximum())

    def show_failure(self, message: str) -> None:
        self.loading_label.setText(translate("suggestion.error"))
        self.content.setPlainText(message)

    def complete(self) -> None:
        self.loading_label.setText(translate("suggestion.ready"))
        self._recommended_translation = _extract_recommended_translation(self._markdown)
        self.apply_button.setEnabled(bool(self._recommended_translation))

    def _apply(self) -> None:
        if self._recommended_translation:
            self.apply_translation.emit(self._recommended_translation)
            self.close()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.dismissed.emit()
        super().closeEvent(event)


class HistoryDialog(QDialog):
    def __init__(self, git: LanguageGit, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.git = git
        self.setObjectName("historyDialog")
        self.setWindowTitle(translate("history.dialog.title", project=git.project_root.name, language=git.language))
        self.resize(1180, 720)
        layout = QHBoxLayout(self)
        self.commits = QListWidget()
        self.commits.setObjectName("historyList")
        self.commits.setMinimumWidth(370)
        self.commits.setUniformItemSizes(True)
        self.commits.setSpacing(1)
        self.commits.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        history_column = QVBoxLayout()
        history_column.setContentsMargins(0, 0, 0, 0)
        selection_hint = QLabel(translate("history.selection_hint"))
        selection_hint.setObjectName("historyHint")
        selection_hint.setWordWrap(True)
        history_column.addWidget(selection_hint)
        history_column.addWidget(self.commits, 1)
        history_panel = QWidget()
        history_panel.setLayout(history_column)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(history_panel)
        self.content = QTextBrowser()
        self.content.setObjectName("historyContent")
        self.content.setOpenExternalLinks(False)
        self.content.document().setDocumentMargin(14)
        splitter.addWidget(self.content)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)
        self._items: list[GitCommit] = []
        self._request_id = 0
        self._selected_rows: tuple[int, ...] = ()
        self._rendered_rows: tuple[int, ...] = ()
        self._history_workers: set[HistoryRenderWorker] = set()
        self._selection_timer = QTimer(self)
        self._selection_timer.setSingleShot(True)
        self._selection_timer.setInterval(110)
        self._selection_timer.timeout.connect(self._load_selected_commits)
        try:
            self._items = git.list_commits()
            self.commits.addItems([commit.display for commit in self._items])
        except GitError as exc:
            self.content.setHtml(_history_state_html(translate("history.read_error_title"), str(exc), kind="error"))
        else:
            self.content.setHtml(_history_state_html(translate("history.initial_title"), translate("history.initial_detail")))
        self.commits.itemSelectionChanged.connect(self._show_selected_commits)
        if self._items:
            QTimer.singleShot(0, self._select_latest_commit)

    def _select_latest_commit(self) -> None:
        if not self._items:
            return
        self.commits.setCurrentRow(0)
        item = self.commits.item(0)
        if item is not None:
            item.setSelected(True)

    def _show_selected_commits(self) -> None:
        rows = tuple(sorted((self.commits.row(item) for item in self.commits.selectedItems()), reverse=True))
        self._request_id += 1
        self._selected_rows = rows
        if not rows:
            self._rendered_rows = ()
            self._selection_timer.stop()
            self.content.setHtml(
                _history_state_html(translate("history.state.none_selected_title"), translate("history.state.none_selected_detail"))
            )
            return
        if rows == self._rendered_rows:
            return
        self.content.setHtml(
            _history_state_html(translate("history.loading_title"), translate("history.loading_detail", count=len(rows)))
        )
        self._selection_timer.start()

    def _load_selected_commits(self) -> None:
        rows = self._selected_rows
        if not rows:
            return
        request_id = self._request_id
        commits = tuple(self._items[row] for row in rows)
        worker = HistoryRenderWorker(request_id, self.git, commits)
        self._history_workers.add(worker)
        worker.signals.rendered.connect(lambda *_args, current=worker: self._history_workers.discard(current))
        worker.signals.failed.connect(lambda *_args, current=worker: self._history_workers.discard(current))
        worker.signals.rendered.connect(self._apply_history_render)
        worker.signals.failed.connect(self._apply_history_error)
        QThreadPool.globalInstance().start(worker)

    def _apply_history_render(self, request_id: int, rendered: str) -> None:
        if request_id != self._request_id:
            return
        self._rendered_rows = self._selected_rows
        self.content.setHtml(rendered)

    def _apply_history_error(self, request_id: int, message: str) -> None:
        if request_id != self._request_id:
            return
        self._rendered_rows = ()
        self.content.setHtml(_history_state_html(translate("history.read_selected_error_title"), message, kind="error"))


class TranslatorWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = load_settings()
        set_language(self.settings.ui_language)
        self.setWindowTitle(translate("window.title.unloaded"))
        self.resize(1480, 920)
        self.project_root = self._startup_project_root()
        # The active local project and the source game root are tracked
        # separately. Reopening a sources project must not forget which game
        # install the manager should scan for Vanilla and mods.
        self.game_root = self._startup_game_root()
        self.git: LanguageGit | None = None
        self.git_pending = False
        self.project: Project | None = None
        self.model = UnitTableModel()
        self.proxy = UnitFilterProxyModel()
        self.proxy.setSourceModel(self.model)
        self.history = OperationHistory()
        self.current_uid = ""
        self.last_applied_query = ""
        self.loading_editor = False
        self.typing_uid = ""
        self.typing_before = ""
        self.typing_before_deleted = False
        self._replaying_editor_history = False
        self.editor_zoom_steps = self.settings.editor_zoom_steps
        self.typing_timer = QTimer(self)
        self.typing_timer.setSingleShot(True)
        self.typing_timer.setInterval(TYPING_GROUP_DELAY_MS)
        self.typing_timer.timeout.connect(self._commit_typing_operation)
        self.ai_cancel_event: threading.Event | None = None
        self.ai_results: dict[str, str] = {}
        self.ai_changes: list[UnitChange] = []
        self.ai_failures: list[str] = []
        self.ai_filter_refresh_pending = False
        self.ai_filter_refresh_timer = QTimer(self)
        self.ai_filter_refresh_timer.setSingleShot(True)
        self.ai_filter_refresh_timer.setInterval(120)
        self.ai_filter_refresh_timer.timeout.connect(self._refresh_ai_filter)
        self.ai_worker: AiWorker | None = None
        self.ai_is_batch = False
        self.ai_cancelled = False
        self.suggestion_worker: LlmSuggestionWorker | None = None
        self.suggestion_cancel_event: threading.Event | None = None
        self.suggestion_dialog: SuggestionDialog | None = None
        self.suggestion_uid = ""
        self._table_context_click: tuple[QModelIndex, QPoint] | None = None
        self._suppress_table_context_event = False
        self.thread_pool = QThreadPool.globalInstance()

        self._build_ui()
        if self.project_root is not None:
            choices = self._load_language_choices()
            if choices:
                self.load_project(discard_changes=True)
            else:
                self._clear_loaded_project()
                self._show_language_setup_hint()
        else:
            self._update_project_button()
            self.statusBar().showMessage(translate("status.choose_project"))
            QTimer.singleShot(0, self.choose_project_folder)

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(9)

        titlebar = QFrame()
        titlebar.setObjectName("titlebar")
        title_layout = QHBoxLayout(titlebar)
        title_layout.setContentsMargins(14, 9, 12, 9)
        title_layout.setSpacing(8)
        title_copy = QVBoxLayout()
        title_copy.setSpacing(0)
        self.workspace_title = QLabel("THE GUILD 2 · TRANSLATOR")
        self.workspace_title.setObjectName("workspaceTitle")
        self.workspace_subtitle = QLabel()
        self.workspace_subtitle.setObjectName("workspaceSubtitle")
        title_copy.addWidget(self.workspace_title)
        title_copy.addWidget(self.workspace_subtitle)
        title_layout.addLayout(title_copy)
        title_layout.addStretch(1)
        layout.addWidget(titlebar)

        toolbar = QFrame()
        toolbar.setObjectName("toolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(10, 8, 10, 8)
        toolbar_layout.setSpacing(8)
        layout.addWidget(toolbar)

        self.project_manager_button = QToolButton()
        self.project_manager_button.clicked.connect(self.show_project_manager)
        title_layout.addWidget(self.project_manager_button)
        self.project_button = QToolButton()
        self.project_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.project_button.clicked.connect(self.choose_project_folder)
        self.project_menu = QMenu(self.project_button)
        self.project_menu.aboutToShow.connect(self._populate_project_menu)
        self.project_button.setMenu(self.project_menu)
        title_layout.addWidget(self.project_button)
        self.language_label = QLabel()
        toolbar_layout.addWidget(self.language_label)
        self.language_combo = PopupSelectionComboBox()
        self.language_combo.setMinimumWidth(160)
        self.language_combo.activated.connect(self._on_language_combo_activated)
        toolbar_layout.addWidget(self.language_combo)
        self.status_label = QLabel()
        toolbar_layout.addWidget(self.status_label)
        self.status_combo = PopupSelectionComboBox()
        self.status_combo.currentTextChanged.connect(self._apply_filters)
        toolbar_layout.addWidget(self.status_combo)
        self.file_label = QLabel()
        toolbar_layout.addWidget(self.file_label)
        self.file_combo = PopupSelectionComboBox()
        self.file_combo.setMinimumWidth(190)
        self.file_combo.currentTextChanged.connect(self._apply_filters)
        toolbar_layout.addWidget(self.file_combo)
        self.only_missing = QCheckBox()
        self.only_missing.setChecked(True)
        self.only_missing.toggled.connect(self._apply_filters)
        toolbar_layout.addWidget(self.only_missing)
        self.only_format_warnings = QCheckBox()
        self.only_format_warnings.toggled.connect(self._apply_filters)
        toolbar_layout.addWidget(self.only_format_warnings)
        toolbar_layout.addStretch(1)
        self.search_label = QLabel()
        toolbar_layout.addWidget(self.search_label)
        self.search_edit = QLineEdit()
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumWidth(240)
        self.search_debounce = QTimer(self)
        self.search_debounce.setSingleShot(True)
        self.search_debounce.setInterval(250)
        self.search_debounce.timeout.connect(self._apply_filters)
        self.search_edit.textChanged.connect(self._on_search_changed)
        toolbar_layout.addWidget(self.search_edit)

        self.batch_ai_button = BatchTranslateButton()
        self.batch_ai_button.clicked.connect(self._on_batch_ai_button_clicked)
        toolbar_layout.addWidget(self.batch_ai_button)
        self.top_buttons: list[QPushButton] = []
        for key, slot, primary in (
            ("button.save", self.save_all, True),
            ("button.history", self.show_history, False),
            ("button.settings", self.show_settings, False),
        ):
            button = QPushButton()
            button.setProperty("text_key", key)
            if primary:
                button.setObjectName("primary")
            button.clicked.connect(slot)
            title_layout.addWidget(button)
            self.top_buttons.append(button)
        self.retry_button = QToolButton()
        self.retry_button.clicked.connect(self.retry_commit)
        self.retry_button.setVisible(False)
        title_layout.addWidget(self.retry_button)

        self.counts_label = QLabel()
        self.counts_label.setObjectName("counts")
        layout.addWidget(self.counts_label)

        self.main_splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(self.main_splitter, 1)
        self.table_frame = QFrame()
        table_layout = QVBoxLayout(self.table_frame)
        table_layout.setContentsMargins(0, 0, 0, 0)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(False)
        # Keeping source order is both clearer for translators and dramatically faster
        # when switching the filter from pending entries to the full project.
        self.table.setSortingEnabled(False)
        self.table.setWordWrap(False)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_table_menu)
        self.table.viewport().installEventFilter(self)
        self.table.selectionModel().currentRowChanged.connect(self._on_row_selected)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.horizontalHeader().setStretchLastSection(False)
        for column, width in enumerate(UnitTableModel.WIDTHS):
            self.table.setColumnWidth(column, width)
        self.table.horizontalHeader().setSectionResizeMode(UnitTableModel.SOURCE, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(UnitTableModel.TRANSLATION, QHeaderView.ResizeMode.Stretch)
        self.row_tint_delegate = RowTintDelegate(self.table)
        self.table.setItemDelegate(self.row_tint_delegate)
        self.ai_delegate = AiButtonDelegate(self.table, self.settings.provider)
        self.ai_delegate.translate_requested.connect(self.translate_one_unit)
        self.table.setItemDelegateForColumn(UnitTableModel.AI, self.ai_delegate)
        self.format_delegate = FormatDiffDelegate(self.table)
        self.table.setItemDelegateForColumn(UnitTableModel.FORMAT, self.format_delegate)
        self.status_delegate = StatusBadgeDelegate(self.table)
        self.table.setItemDelegateForColumn(UnitTableModel.STATUS, self.status_delegate)
        table_layout.addWidget(self.table)
        self.main_splitter.addWidget(self.table_frame)

        self.editors_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.source_box, self.source_edit = self._editor_group(True)
        self.translation_box, self.translation_edit = self._editor_group(False)
        self.translation_edit.setUndoRedoEnabled(True)
        self.source_edit.installEventFilter(self)
        self.source_edit.viewport().installEventFilter(self)
        self.translation_edit.installEventFilter(self)
        self.translation_edit.viewport().installEventFilter(self)
        self.translation_edit.textChanged.connect(self._on_editor_changed)
        self.source_highlighter = TokenHighlighter(self.source_edit.document())
        self.translation_highlighter = TokenHighlighter(self.translation_edit.document())
        self.editors_splitter.addWidget(self.source_box)
        self.editors_splitter.addWidget(self.translation_box)
        self.editors_splitter.setSizes([620, 620])
        self.main_splitter.addWidget(self.editors_splitter)
        self.main_splitter.setSizes([560, 270])
        self._table_visible_splitter_sizes = [560, 270]
        self._apply_editor_zoom()

        self.issue_label = QLabel()
        self.issue_label.setObjectName("issues")
        self.issue_label.setWordWrap(True)
        layout.addWidget(self.issue_label)
        self._populate_status_choices()
        self._retranslate_ui()
        self.statusBar().showMessage(translate("status.ready"))

        for shortcut, slot in (
            (QKeySequence.StandardKey.Save, self.save_all),
            (QKeySequence.StandardKey.Undo, self.undo),
            (QKeySequence.StandardKey.Redo, self.redo),
            (QKeySequence("Ctrl+Shift+Z"), self.redo),
            (QKeySequence.StandardKey.ZoomIn, lambda: self._change_editor_zoom(1)),
            (QKeySequence.StandardKey.ZoomOut, lambda: self._change_editor_zoom(-1)),
            (QKeySequence("Ctrl+0"), self._reset_editor_zoom),
        ):
            action = QAction(self)
            action.setShortcut(shortcut)
            action.triggered.connect(slot)
            self.addAction(action)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        editor = self._watched_editor(watched)
        if isinstance(editor, QPlainTextEdit):
            if event.type() == QEvent.Type.ShortcutOverride and isinstance(event, QKeyEvent):
                if event.matches(QKeySequence.StandardKey.Undo) or event.matches(QKeySequence.StandardKey.Redo) or self._is_ctrl_shift_z(event):
                    event.accept()
                    return True
            if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
                if event.matches(QKeySequence.StandardKey.Undo):
                    self.undo()
                    return True
                if event.matches(QKeySequence.StandardKey.Redo) or self._is_ctrl_shift_z(event):
                    self.redo()
                    return True
            elif isinstance(event, QWheelEvent) and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                delta = event.angleDelta().y()
                if delta:
                    self._change_editor_zoom(1 if delta > 0 else -1)
                    return True
        table = getattr(self, "table", None)
        if isinstance(table, QTableView) and watched is table.viewport():
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.RightButton:
                index = self.table.indexAt(event.position().toPoint())
                if index.isValid():
                    # QTableView normally clears the selection during the
                    # press.  Consume it and defer the menu until release.
                    self._table_context_click = (index, event.position().toPoint())
                    return True
            elif event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.RightButton:
                if self._table_context_click is not None:
                    index, point = self._table_context_click
                    self._table_context_click = None
                    self._suppress_table_context_event = True
                    QTimer.singleShot(0, lambda: self._show_table_menu_for_index(index, self.table.viewport().mapToGlobal(point)))
                    QTimer.singleShot(0, self._clear_table_context_suppression)
                    return True
            elif event.type() == QEvent.Type.ContextMenu and self._suppress_table_context_event:
                self._suppress_table_context_event = False
                return True
        return super().eventFilter(watched, event)

    def _clear_table_context_suppression(self) -> None:
        self._suppress_table_context_event = False

    @staticmethod
    def _is_ctrl_shift_z(event: QKeyEvent) -> bool:
        return event.key() == Qt.Key.Key_Z and event.modifiers() == (
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
        )

    def _focused_editor(self) -> QPlainTextEdit | None:
        focus = QApplication.focusWidget()
        for editor in self._editor_widgets():
            if focus is editor or (focus is not None and editor.isAncestorOf(focus)):
                return editor
        return None

    def _watched_editor(self, watched: QObject) -> QPlainTextEdit | None:
        for editor in self._editor_widgets():
            if watched is editor or watched is editor.viewport():
                return editor
        return None

    def _editor_widgets(self) -> tuple[QPlainTextEdit, ...]:
        editors: list[QPlainTextEdit] = []
        source = getattr(self, "source_edit", None)
        translation = getattr(self, "translation_edit", None)
        if isinstance(source, QPlainTextEdit):
            editors.append(source)
        if isinstance(translation, QPlainTextEdit):
            editors.append(translation)
        return tuple(editors)

    def _try_editor_undo(self) -> bool:
        editor = self._focused_editor()
        if editor is None:
            return False
        if editor is not self.translation_edit:
            return False
        if not editor.document().isUndoAvailable():
            return False
        self._cancel_pending_typing_operation()
        self._replaying_editor_history = True
        try:
            editor.undo()
        finally:
            self._replaying_editor_history = False
        return True

    def _try_editor_redo(self) -> bool:
        editor = self._focused_editor()
        if editor is None:
            return False
        if editor is not self.translation_edit:
            return False
        if not editor.document().isRedoAvailable():
            return False
        self._cancel_pending_typing_operation()
        self._replaying_editor_history = True
        try:
            editor.redo()
        finally:
            self._replaying_editor_history = False
        return True

    def _apply_editor_zoom(self) -> None:
        for editor in self._editor_widgets():
            current = int(editor.property("zoomSteps") or 0)
            delta = self.editor_zoom_steps - current
            if delta > 0:
                editor.zoomIn(delta)
            elif delta < 0:
                editor.zoomOut(-delta)
            editor.setProperty("zoomSteps", self.editor_zoom_steps)

    def _change_editor_zoom(self, delta: int) -> None:
        new_steps = max(-8, min(24, self.editor_zoom_steps + delta))
        if new_steps == self.editor_zoom_steps:
            return
        self.editor_zoom_steps = new_steps
        self._apply_editor_zoom()
        self.settings = replace(self.settings, editor_zoom_steps=self.editor_zoom_steps)
        save_settings(self.settings)
        percent = 100 + self.editor_zoom_steps * 10
        self.statusBar().showMessage(translate("status.editor_zoom", percent=percent), 2500)

    def _reset_editor_zoom(self) -> None:
        if self.editor_zoom_steps == 0:
            return
        self.editor_zoom_steps = 0
        self._apply_editor_zoom()
        self.settings = replace(self.settings, editor_zoom_steps=0)
        save_settings(self.settings)
        self.statusBar().showMessage(translate("status.editor_zoom", percent=100), 2500)

    def _cancel_pending_typing_operation(self) -> None:
        self.typing_timer.stop()
        self.typing_uid = ""
        self.typing_before = ""
        self.typing_before_deleted = False

    def _editor_group(self, read_only: bool) -> tuple[QGroupBox, QPlainTextEdit]:
        box = QGroupBox()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 12, 8, 8)
        editor = QPlainTextEdit()
        editor.setReadOnly(read_only)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        layout.addWidget(editor)
        return box, editor

    @staticmethod
    def _local_project_problem(root: Path) -> str | None:
        root = root.expanduser()
        languages_root = root / "languages"
        if not languages_root.is_dir():
            return translate("folder_problem.no_languages")
        if not has_vanilla_source_entries(languages_root):
            return translate("folder_problem.no_source_files")
        return None

    def _available_local_project_roots(self) -> list[Path]:
        return local_project_roots(APP_ROOT)

    @staticmethod
    def _project_folder_problem(root: Path) -> str | None:
        root = root.expanduser()
        if not root.is_dir():
            return translate("game_folder_problem.not_dir")
        languages_root = game_languages_root(root)
        if not languages_root.is_dir():
            return translate("game_folder_problem.no_languages")
        if not has_vanilla_source_entries(languages_root):
            return translate("game_folder_problem.no_source_files")
        return None

    def _startup_project_root(self) -> Path | None:
        candidates = [self.settings.last_project_root, *self.settings.recent_project_roots]
        seen: set[str] = set()
        for raw_path in candidates:
            if not raw_path:
                continue
            try:
                root = Path(raw_path).expanduser().resolve()
            except OSError:
                continue
            key = str(root).casefold()
            if key in seen:
                continue
            seen.add(key)
            if self._local_project_problem(root) is None:
                return root
        local_roots = self._available_local_project_roots()
        return local_roots[0] if local_roots else None

    def _startup_game_root(self) -> Path | None:
        raw_path = self.settings.last_game_root
        if not raw_path:
            return None
        try:
            root = Path(raw_path).expanduser().resolve()
        except OSError:
            return None
        return root if self._project_folder_problem(root) is None else None

    def _clear_loaded_project(self) -> None:
        self.project = None
        self.git = None
        self.git_pending = False
        self.history.clear()
        self.typing_uid = ""
        self.typing_before = ""
        self.typing_before_deleted = False
        self.current_uid = ""
        self.model.clear()
        self.table.clearSelection()
        self._update_file_choices()
        self._sync_document_layout()
        self._set_editor_unit(None)
        self._update_counts()
        self._update_pending_state()
        self._update_project_button()
        self._update_window_title()

    def _update_language_input_prompt(self) -> None:
        self.language_combo.setToolTip(translate("toolbar.language_tooltip"))
        self.language_combo.setPlaceholderText(translate("toolbar.language_placeholder"))

    @staticmethod
    def _normalized_language_name(raw: str) -> str:
        return raw.strip()

    @staticmethod
    def _language_name_problem(language: str) -> str | None:
        if not language:
            return translate("language_problem.empty")
        if not language.startswith("#"):
            return translate("language_problem.must_start_hash", example=DEFAULT_TRANSLATION_LANGUAGE)
        if language == "#":
            return translate("language_problem.too_short", example=DEFAULT_TRANSLATION_LANGUAGE)
        if any(char in language for char in '<>:"/\\|?*'):
            return translate("language_problem.invalid_chars")
        return None

    def _show_language_setup_hint(self) -> None:
        self.statusBar().showMessage(translate("status.language_needed", example=DEFAULT_TRANSLATION_LANGUAGE))

    def _load_language_choices(self, preferred: str | None = None) -> list[str]:
        choices = Project.language_dirs(self.project_root) if self.project_root is not None else []
        blocker = QSignalBlocker(self.language_combo)
        self.language_combo.clear()
        for choice in choices:
            self.language_combo.addItem(choice)
        if choices:
            self.language_combo.addItem("", LANGUAGE_ACTION_SEPARATOR)
        self.language_combo.addItem(translate("toolbar.language_create"), LANGUAGE_ACTION_NEW)
        model = self.language_combo.model()
        if isinstance(model, QStandardItemModel):
            separator_index = self.language_combo.findData(LANGUAGE_ACTION_SEPARATOR)
            if separator_index >= 0:
                item = model.item(separator_index)
                if item is not None:
                    item.setEnabled(False)
        selected = self._normalized_language_name(preferred or "")
        if selected in choices:
            self.language_combo.setCurrentText(selected)
        elif choices:
            self.language_combo.setCurrentText(choices[0])
        else:
            self.language_combo.setCurrentIndex(-1)
        del blocker
        self._update_language_input_prompt()
        return choices

    def _restore_language_selection(self) -> None:
        preferred = self.project.language if self.project is not None else ""
        self._load_language_choices(preferred)

    def _confirm_language_switch(self) -> bool:
        if self.project is None:
            return True
        self._commit_typing_operation()
        if not self.project.has_dirty_units():
            return True
        answer = QMessageBox.question(self, translate("dialog.reload_title"), translate("dialog.reload_discard"))
        return answer == QMessageBox.StandardButton.Yes

    def _apply_language_selection(self, language: str, *, create: bool = False) -> None:
        if not create and self.project is not None and language == self.project.language:
            self._restore_language_selection()
            return
        problem = self._language_name_problem(language)
        if problem is not None:
            QMessageBox.warning(self, translate("dialog.invalid_language_title"), problem)
            self._restore_language_selection()
            return
        if not self._confirm_language_switch():
            self._restore_language_selection()
            return
        if create and self.project_root is not None:
            ensure_translation_dir(self.project_root, language)
        self._load_language_choices(language)
        self.load_project(discard_changes=True)

    def _create_new_language(self) -> None:
        if self.project_root is None:
            self._restore_language_selection()
            return
        dialog = NewLanguageDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._restore_language_selection()
            return
        self._apply_language_selection(dialog.result_language(), create=True)

    def _on_language_combo_activated(self, index: int) -> None:
        data = self.language_combo.itemData(index)
        if data == LANGUAGE_ACTION_SEPARATOR or not self.language_combo.itemText(index).strip() and data != LANGUAGE_ACTION_NEW:
            self._restore_language_selection()
            return
        if data == LANGUAGE_ACTION_NEW:
            self._create_new_language()
            return
        language = self._normalized_language_name(str(data or self.language_combo.itemText(index)))
        if not language:
            self._restore_language_selection()
            return
        self._apply_language_selection(language)

    def _git_matches_current_project(self, language: str) -> bool:
        if self.git is None or self.project_root is None:
            return False
        try:
            return self.git.language == language and self.git.project_root == self.project_root.resolve()
        except OSError:
            return False

    def switch_local_project(self, root: Path) -> None:
        try:
            root = root.expanduser().resolve()
        except OSError:
            QMessageBox.warning(self, translate("dialog.load_error"), translate("folder_problem.not_dir"))
            return
        problem = self._local_project_problem(root)
        if problem is not None:
            QMessageBox.warning(
                self,
                translate("dialog.invalid_source_title"),
                translate("dialog.invalid_source_detail", problem=problem),
            )
            return
        if self.ai_worker is not None:
            QMessageBox.information(self, translate("dialog.translating_title"), translate("dialog.translating_detail"))
            return
        if self.project is not None:
            self._commit_typing_operation()
            if self.project.has_dirty_units():
                answer = QMessageBox.question(self, translate("dialog.switch_project_title"), translate("dialog.switch_project_discard"))
                if answer != QMessageBox.StandardButton.Yes:
                    return
        preferred = self._normalized_language_name(self.language_combo.currentText())
        self.project_root = root
        self._remember_project_root(root)
        choices = self._load_language_choices(preferred)
        if not choices:
            self._clear_loaded_project()
            self._show_language_setup_hint()
            return
        self.load_project(discard_changes=True)

    def load_project(self, discard_changes: bool = False) -> None:
        if self.project_root is None:
            if self.game_root is not None:
                self.switch_project_folder(self.game_root)
            else:
                self.choose_project_folder()
            return
        language = self._normalized_language_name(self.language_combo.currentText())
        problem = self._language_name_problem(language)
        if problem is not None:
            if not language:
                self._show_language_setup_hint()
                return
            QMessageBox.warning(self, translate("dialog.invalid_language_title"), problem)
            return
        if self.project is not None and not discard_changes:
            self._commit_typing_operation()
            if self.project.has_dirty_units():
                answer = QMessageBox.question(self, translate("dialog.reload_title"), translate("dialog.reload_discard"))
                if answer != QMessageBox.StandardButton.Yes:
                    return
        created_language_dir = False
        try:
            language_root = self.project_root / "languages" / language
            if not language_root.exists():
                ensure_translation_dir(self.project_root, language)
                created_language_dir = True
                self._load_language_choices(language)
            if not self._git_matches_current_project(language):
                self.git = LanguageGit(self.project_root, language, codec_root=DEFAULT_PROJECT_ROOT)
            self.git.ensure_repository(self.settings)
            project = Project.load(
                self.project_root,
                language,
                codec_root=DEFAULT_PROJECT_ROOT,
            )
        except (ProjectError, GitError, OSError, ValueError) as exc:
            QMessageBox.critical(self, translate("dialog.load_error"), str(exc))
            return
        self._activate_project(project)
        if created_language_dir:
            self.statusBar().showMessage(
                translate("status.language_created_loaded", language=language, count=len(project.units)),
                5000,
            )
        if self.project_root is not None:
            self._remember_project_root(self.project_root)

    def _activate_project(self, project: Project) -> None:
        self.project = project
        self.history.clear()
        self.typing_uid = ""
        self.typing_before = ""
        self.typing_before_deleted = False
        self.current_uid = ""
        self.model.set_project(self.project)
        self.translation_highlighter.set_glyph_codec(self.project.codec if ENABLE_FONT_GLYPH_VALIDATION else None)
        self._update_file_choices()
        self._apply_filters()
        if not self._is_document_file_selected():
            self._set_editor_unit(None)
        self._update_counts()
        self._update_pending_state()
        self._update_project_button()
        self.statusBar().showMessage(translate("status.project_loaded", count=len(self.project.units)), 4500)

    def choose_project_folder(self) -> None:
        current = self.game_root or APP_ROOT
        start_dir = current if current.is_dir() else current.parent
        folder = QFileDialog.getExistingDirectory(self, translate("dialog.choose_project"), str(start_dir))
        if folder:
            self.switch_project_folder(Path(folder))

    def switch_project_folder(self, root: Path) -> None:
        try:
            root = root.expanduser().resolve()
        except OSError:
            QMessageBox.warning(self, translate("dialog.open_project_error"), translate("folder_problem.not_dir"))
            return
        problem = self._project_folder_problem(root)
        if problem is not None:
            QMessageBox.warning(
                self,
                translate("dialog.invalid_project_title"),
                translate("dialog.invalid_project_detail", problem=problem),
            )
            return
        if self.ai_worker is not None:
            QMessageBox.information(self, translate("dialog.translating_title"), translate("dialog.translating_detail"))
            return
        if self.project is not None:
            self._commit_typing_operation()
            if self.project.has_dirty_units():
                answer = QMessageBox.question(self, translate("dialog.switch_project_title"), translate("dialog.switch_project_discard"))
                if answer != QMessageBox.StandardButton.Yes:
                    return
        preferred = self._normalized_language_name(self.language_combo.currentText())
        try:
            sync_vanilla_sources(root, MANAGED_PROJECT_ROOT)
            self.project_root = MANAGED_PROJECT_ROOT
        except (ProjectError, GitError, OSError, ValueError) as exc:
            QMessageBox.critical(self, translate("dialog.load_error"), str(exc))
            return
        self.game_root = root
        self._remember_game_root(root)
        self.project_root = MANAGED_PROJECT_ROOT
        # Selecting a game root feeds the managed Vanilla project, but the
        # active project identity should remain Vanilla rather than the game
        # install path itself.
        choices = self._load_language_choices(preferred)
        if not choices:
            self._clear_loaded_project()
            self._show_language_setup_hint()
            return
        self.load_project(discard_changes=True)

    def _choose_management_game_root(self) -> Path | None:
        current = self.game_root or APP_ROOT
        start_dir = current if current.is_dir() else current.parent
        folder = QFileDialog.getExistingDirectory(self, translate("dialog.choose_project"), str(start_dir))
        if not folder:
            return None
        try:
            root = Path(folder).expanduser().resolve()
        except OSError:
            QMessageBox.warning(self, translate("dialog.open_project_error"), translate("folder_problem.not_dir"))
            return None
        problem = self._project_folder_problem(root)
        if problem is not None:
            QMessageBox.warning(
                self,
                translate("dialog.invalid_project_title"),
                translate("dialog.invalid_project_detail", problem=problem),
            )
            return None
        self.game_root = root
        self._remember_game_root(root)
        self._update_project_button()
        return root

    def show_project_manager(self) -> None:
        game_root = self.game_root
        if game_root is None or self._project_folder_problem(game_root) is not None:
            game_root = self._choose_management_game_root()
        if game_root is None:
            return
        ProjectManagerDialog(game_root, APP_ROOT, self._sync_scanned_project, self).exec()

    def _sync_scanned_project(self, spec: SourceProjectSpec) -> str:
        if self.ai_worker is not None:
            raise RuntimeError(translate("dialog.translating_detail"))
        active_project = False
        if self.project_root is not None:
            try:
                active_project = self.project_root.resolve() == spec.project_root.resolve()
            except OSError:
                active_project = False
        if active_project and self.project is not None:
            self._commit_typing_operation()
            if self.project.has_dirty_units():
                raise RuntimeError(translate("dialog.project_manager_unsaved_detail", name=spec.name))

        result = sync_source_project(spec.source_root, spec.project_root)

        if active_project:
            preferred = self.project.language if self.project is not None else self._normalized_language_name(self.language_combo.currentText())
            choices = self._load_language_choices(preferred)
            if not choices:
                self._clear_loaded_project()
                self._show_language_setup_hint()
            else:
                self.load_project(discard_changes=True)

        message = translate(
            "status.project_manager_synced",
            name=spec.name,
            synced=len(result.synced_source_files),
            removed=len(result.removed_source_files),
            invalidated=result.invalidated_units,
        )
        self.statusBar().showMessage(message, 7000)
        return message

    def _remember_project_root(self, root: Path) -> None:
        value = str(root)
        recent = [value]
        recent.extend(path for path in self.settings.recent_project_roots if path.casefold() != value.casefold())
        self.settings = replace(self.settings, last_project_root=value, recent_project_roots=recent[:8])
        save_settings(self.settings)

    def _remember_game_root(self, root: Path) -> None:
        value = str(root)
        if self.settings.last_game_root == value:
            return
        self.settings = replace(self.settings, last_game_root=value)
        save_settings(self.settings)

    def _populate_status_choices(self) -> None:
        current = self.status_combo.currentData() or STATUS_FILTER_ALL
        choices = [
            (translate("filter.all_statuses"), STATUS_FILTER_ALL),
            (translate("filter.needs_translation"), STATUS_FILTER_TODO),
            (status_text(STATUS_TRANSLATED), STATUS_TRANSLATED),
            (status_text(STATUS_EXTRA), STATUS_EXTRA),
            (status_text(STATUS_IGNORED), STATUS_IGNORED),
        ]
        blocker = QSignalBlocker(self.status_combo)
        self.status_combo.clear()
        for label, value in choices:
            self.status_combo.addItem(label, value)
        index = self.status_combo.findData(current)
        self.status_combo.setCurrentIndex(index if index >= 0 else 0)
        del blocker

    def _retranslate_ui(self) -> None:
        self.workspace_subtitle.setText(translate("workspace.subtitle"))
        self.language_label.setText(translate("toolbar.language"))
        current_language = self.project.language if self.project is not None else self._normalized_language_name(str(self.language_combo.currentData() or ""))
        self._load_language_choices(current_language)
        self.status_label.setText(translate("toolbar.status"))
        self.file_label.setText(translate("toolbar.file"))
        self.search_label.setText(translate("toolbar.search"))
        self.search_edit.setPlaceholderText(translate("toolbar.search_placeholder"))
        self.only_missing.setText(translate("toolbar.only_missing"))
        self.only_format_warnings.setText(translate("toolbar.only_format_warnings"))
        for button in self.top_buttons:
            button.setText(translate(str(button.property("text_key") or "")))
        self.retry_button.setText(translate("button.retry_commit"))
        self.retry_button.setToolTip(translate("button.retry_commit_tooltip"))
        self.source_box.setTitle(translate("editor.source_title"))
        self.translation_box.setTitle(translate("editor.translation_title"))
        self.source_edit.setPlaceholderText(translate("editor.placeholder"))
        self.translation_edit.setPlaceholderText(translate("editor.placeholder"))
        self.batch_ai_button._update_presentation()
        self._populate_status_choices()
        self._update_project_button()
        self._update_file_choices()
        self.model.retranslate()
        self.table.viewport().update()
        self._update_counts()
        self._update_issue_detail(self._current_unit())
        self._update_window_title()

    def _update_project_button(self) -> None:
        self.project_manager_button.setText(translate("project.button.manage"))
        if self.game_root is None:
            self.project_manager_button.setToolTip(translate("project.button.manage_choose_tooltip"))
        else:
            self.project_manager_button.setToolTip(
                translate("project.button.manage_tooltip", path=str(self.game_root))
            )
        if self.project_root is not None:
            self.project_button.setText(translate("project.button.current_project", name=self.project_root.name))
            self.project_button.setToolTip(str(self.project_root))
            return
        if self.game_root is None:
            self.project_button.setText(translate("project.button.open"))
            self.project_button.setToolTip(translate("project.button.open_tooltip"))
            return
        self.project_button.setText(translate("project.button.current", name=self.game_root.name))
        self.project_button.setToolTip(str(self.game_root))

    def _populate_project_menu(self) -> None:
        self.project_menu.clear()
        self.project_menu.addAction(translate("project.choose_folder"), self.choose_project_folder)
        self.project_menu.addSeparator()
        self.project_menu.addSection(translate("project.menu.local"))
        local_available = 0
        local_roots = self._available_local_project_roots()
        for project_root in local_roots:
            action = self.project_menu.addAction(project_root.name or str(project_root))
            action.setToolTip(str(project_root))
            action.triggered.connect(lambda _checked=False, local_root=project_root: self.switch_local_project(local_root))
            local_available += 1
        if not local_available:
            action = self.project_menu.addAction(translate("project.menu.none_local"))
            action.setEnabled(False)
        # Recent game-root entries are intentionally hidden for now.
        # The game install path will come back later through a dedicated
        # update workflow instead of appearing as a separate project item.

    def _update_file_choices(self) -> None:
        files = sorted({unit.file_rel for unit in self.model.units})
        previous = self.file_combo.currentData() or FILE_FILTER_ALL
        default_file = "Text.dbt" if "Text.dbt" in files else FILE_FILTER_ALL
        blocker = QSignalBlocker(self.file_combo)
        self.file_combo.clear()
        self.file_combo.addItem(translate("filter.all_files"), FILE_FILTER_ALL)
        for file_rel in files:
            self.file_combo.addItem(file_rel, file_rel)
        desired = previous if previous in files or previous == FILE_FILTER_ALL else default_file
        index = self.file_combo.findData(desired)
        self.file_combo.setCurrentIndex(index if index >= 0 else 0)
        del blocker

    def _is_document_file_selected(self) -> bool:
        selected = str(self.file_combo.currentData() or "")
        return selected != FILE_FILTER_ALL and selected.lower().endswith(".txt")

    def _current_document_unit(self) -> TranslationUnit | None:
        if self.project is None or not self._is_document_file_selected():
            return None
        file_rel = str(self.file_combo.currentData() or "")
        return next((unit for unit in self.project.units if unit.file_rel == file_rel and unit.ref.kind == "text"), None)

    def _sync_document_layout(self) -> bool:
        document_mode = self._is_document_file_selected()
        if document_mode:
            if self.table_frame.isVisible():
                sizes = self.main_splitter.sizes()
                if len(sizes) == 2 and sizes[0] > 0:
                    self._table_visible_splitter_sizes = sizes
            self.table_frame.setVisible(False)
            self.main_splitter.setSizes([0, max(sum(self._table_visible_splitter_sizes), 1)])
            unit = self._current_document_unit()
            self.current_uid = unit.uid if unit is not None else ""
            self._set_editor_unit(unit)
            self._update_window_title()
            return True
        if not self.table_frame.isVisible():
            self.table_frame.setVisible(True)
            self.main_splitter.setSizes(self._table_visible_splitter_sizes)
        return False

    def _apply_filters(self) -> None:
        query = self.search_edit.text()
        clearing_search = bool(self.last_applied_query) and not query.strip()
        previous_document_mode = not self.table_frame.isVisible()
        selected_uid = self.current_uid
        self.proxy.set_filters(
            file_filter=str(self.file_combo.currentData() or FILE_FILTER_ALL),
            status_filter=str(self.status_combo.currentData() or STATUS_FILTER_TODO),
            only_missing=self.only_missing.isChecked(),
            only_format_warnings=self.only_format_warnings.isChecked(),
            query=query,
        )
        self.last_applied_query = query.strip()
        self._update_counts()
        if self._sync_document_layout():
            return
        if previous_document_mode:
            self.current_uid = ""
            self._set_editor_unit(None)
            self._update_window_title()
            return
        if clearing_search and selected_uid:
            self._restore_selected_row(selected_uid)

    def _on_search_changed(self, text: str) -> None:
        # Do not apply an empty intermediate value synchronously. Replacing a
        # Ctrl+A selection can briefly emit "" before the first new character;
        # restoring the table selection at that point steals focus from search.
        self._refresh_editor_highlights()
        self.search_debounce.start()

    def _restore_selected_row(self, uid: str) -> None:
        if self._is_document_file_selected():
            return
        source_row = self.model.row_for_uid(uid)
        if source_row is None:
            return
        proxy_index = self.proxy.mapFromSource(self.model.index(source_row, 0))
        if not proxy_index.isValid():
            return
        self.table.setCurrentIndex(proxy_index)
        self.table.selectRow(proxy_index.row())
        self.table.scrollTo(proxy_index, QAbstractItemView.ScrollHint.PositionAtCenter)

    def _update_counts(self) -> None:
        if self.project is None:
            self.counts_label.setText("")
            return
        effective: Counter[str] = Counter()
        todo = 0
        for unit in self.project.units:
            status = unit.filter_status()
            effective[status] += 1
            todo += status in MISSING_WORK_STATUSES
        recent = self.model.recently_translated_count
        self.counts_label.setText(
            translate(
                "counts.summary",
                visible=self.proxy.rowCount(),
                total=len(self.project.units),
                todo=todo,
                translated=effective[STATUS_TRANSLATED],
                recent=recent,
                ignored=effective[STATUS_IGNORED],
            )
        )

    def _update_window_title(self) -> None:
        if self.project is None:
            self.setWindowTitle(translate("window.title.unloaded"))
            return
        unit = self._current_unit()
        if unit is None:
            location = self.project.language
        elif unit.record_id:
            location = f"{unit.file_rel} · #{unit.record_id}"
        else:
            location = unit.file_rel
        dirty_count = self.project.dirty_count()
        save_state = (
            translate("window.save_state.unsaved", count=dirty_count)
            if dirty_count
            else translate("window.save_state.saved")
        )
        git_state = translate("window.git_pending") if self.git_pending else ""
        project_name = (
            self.project_root.name
            if self.project_root is not None
            else (self.game_root.name if self.game_root is not None else translate("window.project_unloaded"))
        )
        self.setWindowTitle(translate("window.title.loaded", project=project_name, location=location, save_state=save_state, git_state=git_state))

    def _on_row_selected(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if self._is_document_file_selected():
            return
        self._commit_typing_operation()
        unit = self._unit_from_proxy_index(current)
        self.current_uid = unit.uid if unit else ""
        self._set_editor_unit(unit)
        self._update_window_title()

    def _set_editor_unit(self, unit: TranslationUnit | None) -> None:
        self.loading_editor = True
        source_blocker = QSignalBlocker(self.source_edit)
        translation_blocker = QSignalBlocker(self.translation_edit)
        self.source_edit.setPlainText(unit.source_text if unit else "")
        self.translation_edit.setPlainText(unit.current_text if unit else "")
        self.translation_edit.document().clearUndoRedoStacks()
        del source_blocker, translation_blocker
        self.loading_editor = False
        self._cancel_pending_typing_operation()
        self._update_issue_detail(unit)
        self._refresh_editor_highlights()

    def _search_ranges(self, text: str) -> list[tuple[int, int]]:
        query = self.search_edit.text()
        if not query:
            return []
        needle = query.casefold()
        haystack = text.casefold()
        ranges: list[tuple[int, int]] = []
        start = 0
        while True:
            index = haystack.find(needle, start)
            if index < 0:
                break
            ranges.append((index, index + len(query)))
            start = index + max(len(query), 1)
        return ranges

    def _refresh_editor_highlights(self) -> None:
        unit = self._current_unit()
        source_selections: list[QTextEdit.ExtraSelection] = []
        translation_selections: list[QTextEdit.ExtraSelection] = []

        for start, end in self._search_ranges(self.source_edit.toPlainText()):
            source_selections.append(_make_editor_selection(self.source_edit, start, end, background="#f6e58d"))
        for start, end in self._search_ranges(self.translation_edit.toPlainText()):
            translation_selections.append(_make_editor_selection(self.translation_edit, start, end, background="#f6e58d"))

        if unit is not None:
            for start, end in _missing_source_token_ranges(unit.source_text, unit.current_text):
                source_selections.append(
                    _make_editor_selection(self.source_edit, start, end, background="#f5c2c7", foreground="#7f1d1d")
                )

        self.source_edit.setExtraSelections(source_selections)
        self.translation_edit.setExtraSelections(translation_selections)

    def _on_editor_changed(self) -> None:
        if self.loading_editor:
            return
        unit = self._current_unit()
        if unit is None:
            return
        text = self.translation_edit.toPlainText()
        if not self._replaying_editor_history:
            if not self.typing_uid:
                self.typing_uid = unit.uid
                self.typing_before = unit.current_text
                self.typing_before_deleted = unit.pending_delete
            elif self.typing_uid != unit.uid:
                self._commit_typing_operation()
                self.typing_uid = unit.uid
                self.typing_before = unit.current_text
                self.typing_before_deleted = unit.pending_delete
        before_status = unit.filter_status()
        unit.set_text(text)
        self.model.refresh_unit(unit)
        self._update_recent_translation_marker(unit, before_status)
        self._update_issue_detail(unit)
        self._refresh_editor_highlights()
        self._update_counts()
        self._update_window_title()
        if not self._replaying_editor_history:
            self.typing_timer.start()

    def _commit_typing_operation(self) -> None:
        self.typing_timer.stop()
        if not self.typing_uid:
            return
        unit = self.model.unit_for_uid(self.typing_uid)
        before, self.typing_uid = self.typing_before, ""
        self.typing_before = ""
        before_deleted, self.typing_before_deleted = self.typing_before_deleted, False
        if unit is not None and (unit.current_text != before or unit.pending_delete != before_deleted):
            self.history.push(
                TranslationOperation(
                    translate("operation.continuous_edit"),
                    (UnitChange(unit.uid, before, unit.current_text, before_deleted, unit.pending_delete),),
                )
            )

    def _apply_operation_state(self, uid: str, text: str, pending_delete: bool) -> None:
        unit = self.model.unit_for_uid(uid)
        if unit is None:
            return
        before_status = unit.filter_status()
        unit.set_text(text)
        unit.set_pending_delete(pending_delete)
        self.model.refresh_unit(unit)
        self._update_recent_translation_marker(unit, before_status)
        if uid == self.current_uid:
            self._set_editor_unit(unit)
        self._update_counts()
        self._update_window_title()

    def _update_recent_translation_marker(self, unit: TranslationUnit, before_status: str) -> None:
        current_status = unit.filter_status()
        changed_existing_translation = unit.is_dirty and unit.status == STATUS_TRANSLATED
        if current_status == STATUS_TRANSLATED and (before_status in MISSING_WORK_STATUSES or changed_existing_translation):
            self.model.set_recently_translated(unit, True)
        elif current_status != STATUS_TRANSLATED or not unit.is_dirty:
            self.model.set_recently_translated(unit, False)

    def _replace_current_text(self, text: str, label: str) -> None:
        unit = self._current_unit()
        if unit is not None:
            self._replace_unit_text(unit, text, label)

    def _replace_unit_text(self, unit: TranslationUnit, text: str, label: str) -> None:
        self._replace_units_state((unit,), {unit.uid: text}, False, label)

    def _replace_units_state(
        self, units: Iterable[TranslationUnit], texts: dict[str, str], pending_delete: bool | None, label: str
    ) -> None:
        self._commit_typing_operation()
        changes = tuple(
            UnitChange(
                unit.uid,
                unit.current_text,
                texts.get(unit.uid, unit.current_text),
                unit.pending_delete,
                unit.pending_delete if pending_delete is None else pending_delete,
            )
            for unit in units
            if (
                unit.uid in texts or pending_delete is not None
            )
            and (
                unit.current_text != texts.get(unit.uid, unit.current_text)
                or unit.pending_delete != (unit.pending_delete if pending_delete is None else pending_delete)
            )
        )
        if not changes:
            return
        for change in changes:
            self._apply_operation_state(change.uid, change.after, change.after_deleted)
        self.proxy.refresh_rows()
        current = self._current_unit()
        if current is not None and any(change.uid == current.uid for change in changes):
            self._set_editor_unit(current)
        self._update_counts()
        self._update_window_title()
        self.history.push(TranslationOperation(label, changes))

    def _set_units_pending_delete(self, units: Iterable[TranslationUnit], pending_delete: bool) -> None:
        self._replace_units_state(
            tuple(units),
            {},
            pending_delete,
            translate("operation.mark_delete") if pending_delete else translate("operation.unmark_delete"),
        )
        self.statusBar().showMessage(
            translate("status.mark_delete") if pending_delete else translate("status.unmark_delete"),
            3000,
        )

    def undo(self) -> None:
        if self._try_editor_undo():
            return
        self._commit_typing_operation()
        operation = self.history.undo(self._apply_operation_state)
        if operation:
            self.statusBar().showMessage(translate("status.undo", label=operation.label), 2500)

    def redo(self) -> None:
        if self._try_editor_redo():
            return
        self._commit_typing_operation()
        operation = self.history.redo(self._apply_operation_state)
        if operation:
            self.statusBar().showMessage(translate("status.redo", label=operation.label), 2500)

    def _show_table_menu(self, point: QPoint) -> None:
        index = self.table.indexAt(point)
        self._show_table_menu_for_index(index, self.table.viewport().mapToGlobal(point))

    def _show_table_menu_for_index(self, index: QModelIndex, global_point: QPoint) -> None:
        unit = self._unit_from_proxy_index(index)
        if unit is None:
            return
        self._select_context_row(index)
        if index.column() == UnitTableModel.AI:
            self._show_ai_provider_menu(global_point)
            return
        units = self._selected_units()
        count = len(units)
        suffix = f" · {count}" if count > 1 else ""
        can_delete_all = bool(units) and all(item.can_delete_translation() for item in units)
        can_mark_delete = can_delete_all and any(not item.pending_delete for item in units)
        can_unmark_delete = any(item.pending_delete for item in units)
        menu = QMenu(self)
        menu.addSection(translate("menu.selected_entries"))
        copy_translation = menu.addAction(translate("menu.copy_selected_translation", suffix=suffix))
        menu.addSection(translate("menu.translation_edit"))
        restore = menu.addAction(translate("menu.restore_loaded", suffix=suffix))
        source = menu.addAction(translate("menu.restore_source", suffix=suffix))
        clear = menu.addAction(translate("menu.clear_translation", suffix=suffix))
        menu.addSection(translate("menu.ai_service"))
        ai_translate = menu.addAction(translate("menu.ai_translate_selected", suffix=suffix))
        llm_suggestion = menu.addAction(translate("menu.llm_suggestion"))
        menu.addSection(translate("menu.delete_cleanup"))
        mark_delete = menu.addAction(translate("menu.mark_delete", suffix=suffix))
        mark_delete.setEnabled(can_mark_delete)
        unmark_delete = menu.addAction(translate("menu.unmark_delete", suffix=suffix))
        unmark_delete.setEnabled(can_unmark_delete)
        menu.addSection(translate("menu.entry_status"))
        confirm_current_translation = menu.addAction(translate("menu.confirm_current_translation", suffix=suffix))
        confirm_current_translation.setEnabled(any(item.review_reason == TODO_REASON_SOURCE_CHANGED for item in units))
        ignored = menu.addAction(
            translate("menu.unmark_ignored", suffix=suffix)
            if all(item.ignored for item in units)
            else translate("menu.mark_ignored", suffix=suffix)
        )
        action = menu.exec(global_point)
        if action == copy_translation:
            self._copy_unit_translations(units)
        elif action == restore:
            self._replace_units_state(
                units,
                {item.uid: item.translate_text for item in units},
                False,
                translate("operation.restore_loaded"),
            )
        elif action == source:
            self._replace_units_state(units, {item.uid: item.source_text for item in units}, False, translate("operation.restore_source"))
        elif action == clear:
            self._replace_units_state(units, {item.uid: "" for item in units}, False, translate("operation.clear_translation"))
        elif action == ai_translate:
            self.translate_selected_units(units)
        elif action == llm_suggestion:
            self.request_llm_suggestion(unit.uid)
        elif action == mark_delete:
            self._set_units_pending_delete(units, True)
        elif action == unmark_delete:
            self._set_units_pending_delete(units, False)
        elif action == confirm_current_translation:
            self._set_units_source_review(units, False)
        elif action == ignored:
            self._set_units_ignored(units, not all(item.ignored for item in units))

    def _select_context_row(self, index: QModelIndex) -> None:
        """Keep an existing multi-selection intact when opening its context menu."""
        selection = self.table.selectionModel()
        if any(selected.row() == index.row() for selected in selection.selectedRows()):
            return
        self.table.setCurrentIndex(index)
        self.table.selectRow(index.row())

    def _selected_units(self) -> list[TranslationUnit]:
        units: list[TranslationUnit] = []
        for index in self.table.selectionModel().selectedRows():
            unit = self._unit_from_proxy_index(index)
            if unit is not None:
                units.append(unit)
        return units

    def _copy_unit_translations(self, units: Iterable[TranslationUnit]) -> None:
        texts = [unit.current_text for unit in units if unit.current_text]
        if not texts:
            self.statusBar().showMessage(translate("status.copy_none"), 2500)
            return
        QApplication.clipboard().setText("\n".join(texts))
        self.statusBar().showMessage(translate("status.copy_done", count=len(texts)), 2500)

    def _set_ignored(self, unit: TranslationUnit, ignored: bool) -> None:
        self._set_units_ignored((unit,), ignored)

    def _set_units_ignored(self, units: Iterable[TranslationUnit], ignored: bool) -> None:
        if self.project is None:
            return
        selected = tuple(units)
        self.project.set_units_ignored(selected, ignored)
        for unit in selected:
            self.model.refresh_unit(unit)
            self.model.set_recently_translated(unit, False)
        self._apply_filters()
        self._update_issue_detail(self._current_unit())
        self._update_window_title()

    def _set_units_source_review(self, units: Iterable[TranslationUnit], source_changed: bool) -> None:
        if self.project is None:
            return
        selected = tuple(units)
        self.project.set_units_source_review(selected, source_changed)
        for unit in selected:
            self.model.refresh_unit(unit)
        self._apply_filters()
        self._update_counts()
        self._update_issue_detail(self._current_unit())
        self._update_window_title()
        if not source_changed and selected:
            self.statusBar().showMessage(translate("status.review_confirmed", count=len(selected)), 3500)

    def _show_ai_provider_menu(self, global_point: QPoint) -> None:
        menu = QMenu(self)
        menu.setTitle(translate("dialog.ai_service_title"))
        google = menu.addAction(translate("dialog.ai_service_google"))
        google.setCheckable(True)
        google.setChecked(self.settings.provider != "openai")
        openai = menu.addAction(translate("dialog.ai_service_openai"))
        openai.setCheckable(True)
        openai.setChecked(self.settings.provider == "openai")
        menu.addSeparator()
        settings_action = menu.addAction(translate("dialog.ai_service_settings"))
        action = menu.exec(global_point)
        if action == google:
            self._set_ai_provider("google")
        elif action == openai:
            self._set_ai_provider("openai")
        elif action == settings_action:
            self.show_settings()

    def _set_ai_provider(self, provider: str) -> None:
        if self.settings.provider == provider:
            return
        self.settings = replace(self.settings, provider=provider)
        save_settings(self.settings)
        self.ai_delegate.set_provider(provider)
        name = "Google Translate" if provider == "google" else translate("dialog.ai_service_openai").replace("✦ ", "")
        self.statusBar().showMessage(translate("status.ai_provider_changed", name=name), 3500)

    def translate_one_unit(self, uid: str) -> None:
        self._commit_typing_operation()
        unit = self.model.unit_for_uid(uid)
        if unit is None or not unit.source_text:
            return
        if unit.current_text and (unit.filter_status() not in MISSING_WORK_STATUSES or unit.requires_manual_review):
            answer = QMessageBox.question(self, translate("dialog.retranslate_title"), translate("dialog.retranslate_detail"))
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._start_ai([unit], translate("operation.ai_single"))

    def translate_selected_units(self, selected: Iterable[TranslationUnit]) -> None:
        self._commit_typing_operation()
        units = [
            unit
            for unit in selected
            if not unit.ignored and not unit.requires_manual_review and unit.source_text and unit.filter_status() in MISSING_WORK_STATUSES
        ]
        if not units:
            QMessageBox.information(
                self,
                translate("dialog.ai_no_translatable_selected_title"),
                translate("dialog.ai_no_translatable_selected_detail"),
            )
            return
        self._start_ai(units, translate("operation.ai_selected", count=len(units)), is_batch=True)

    def request_llm_suggestion(self, uid: str | None = None) -> None:
        self._commit_typing_operation()
        unit = self.model.unit_for_uid(uid) if uid else self._current_unit()
        if unit is None or not unit.source_text:
            return
        if self.suggestion_worker is not None:
            if self.suggestion_dialog is not None:
                self.suggestion_dialog.show()
                self.suggestion_dialog.raise_()
            self.statusBar().showMessage(translate("status.llm_generating"), 2500)
            return
        provider = llm_provider_from_settings(self.settings)
        if not provider.api_key:
            QMessageBox.information(
                self,
                translate("dialog.llm_settings_required_title"),
                translate("dialog.llm_settings_required_detail"),
            )
            self.show_settings()
            return
        self.suggestion_cancel_event = threading.Event()
        self.suggestion_uid = unit.uid
        dialog = SuggestionDialog(self)
        dialog.apply_translation.connect(self._apply_suggested_translation)
        dialog.dismissed.connect(self._close_suggestion_dialog)
        self.suggestion_dialog = dialog
        dialog.move(self.mapToGlobal(QPoint(max(24, self.width() - dialog.width() - 36), 72)))
        dialog.show()
        dialog.raise_()
        worker = LlmSuggestionWorker(provider, unit.source_text, unit.current_text, self.suggestion_cancel_event)
        worker.signals.chunk.connect(self._append_suggestion_chunk)
        worker.signals.failed.connect(self._show_suggestion_failure)
        worker.signals.finished.connect(self._finish_suggestion)
        self.suggestion_worker = worker
        self.thread_pool.start(worker)

    def _append_suggestion_chunk(self, chunk: str) -> None:
        if self.suggestion_cancel_event is None or self.suggestion_cancel_event.is_set():
            return
        if self.suggestion_dialog is not None:
            self.suggestion_dialog.append_chunk(chunk)

    def _show_suggestion_failure(self, message: str) -> None:
        if self.suggestion_dialog is not None:
            self.suggestion_dialog.show_failure(message)

    def _finish_suggestion(self) -> None:
        cancelled = bool(self.suggestion_cancel_event and self.suggestion_cancel_event.is_set())
        self.suggestion_worker = None
        self.suggestion_cancel_event = None
        if not cancelled and self.suggestion_dialog is not None:
            self.suggestion_dialog.complete()

    def _apply_suggested_translation(self, text: str) -> None:
        unit = self.model.unit_for_uid(self.suggestion_uid)
        if unit is None or (unit.current_text == text and not unit.pending_delete):
            return
        before = unit.current_text
        before_deleted = unit.pending_delete
        self._apply_operation_state(unit.uid, text, False)
        self.history.push(
            TranslationOperation(translate("operation.apply_llm"), (UnitChange(unit.uid, before, text, before_deleted, False),))
        )
        self.statusBar().showMessage(translate("status.llm_applied"), 3500)

    def _close_suggestion_dialog(self) -> None:
        if self.suggestion_cancel_event is not None:
            self.suggestion_cancel_event.set()
        self.suggestion_dialog = None

    def translate_visible_units(self) -> None:
        self._commit_typing_operation()
        units: list[TranslationUnit] = []
        for row in range(self.proxy.rowCount()):
            unit = self._unit_from_proxy_index(self.proxy.index(row, 0))
            if unit and not unit.requires_manual_review and unit.source_text and unit.filter_status() in MISSING_WORK_STATUSES:
                units.append(unit)
        if not units:
            QMessageBox.information(self, translate("dialog.batch_ai_title"), translate("dialog.batch_ai_empty"))
            return
        answer = QMessageBox.question(
            self,
            translate("dialog.batch_ai_title"),
            translate("dialog.batch_ai_confirm", count=len(units)),
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._start_ai(units, translate("operation.batch_ai", count=len(units)), is_batch=True)

    def _on_batch_ai_button_clicked(self) -> None:
        if self.batch_ai_button.busy:
            self.cancel_batch_translation()
        else:
            self.translate_visible_units()

    def cancel_batch_translation(self) -> None:
        if not self.ai_is_batch or self.ai_cancel_event is None or self.ai_cancelled:
            return
        self.ai_cancelled = True
        self.ai_cancel_event.set()
        self.batch_ai_button.set_cancelling()
        self.statusBar().showMessage(translate("status.batch_ai_cancelling"), 4000)

    def _start_ai(self, units: list[TranslationUnit], label: str, *, is_batch: bool = False) -> None:
        if self.ai_worker is not None:
            self.statusBar().showMessage(translate("status.ai_already_running"), 3500)
            return
        try:
            provider = provider_from_settings(self.settings)
        except Exception as exc:
            QMessageBox.critical(self, translate("dialog.ai_settings_error"), str(exc))
            return
        self.ai_results = {}
        self.ai_changes = []
        self.ai_failures = []
        self.ai_cancel_event = threading.Event()
        self.ai_is_batch = is_batch
        self.ai_cancelled = False
        if is_batch:
            self.batch_ai_button.set_busy(True, len(units))
        worker = AiWorker(provider, units, self.ai_cancel_event)
        worker.signals.translated.connect(self._collect_ai_result)
        worker.signals.failed.connect(self._collect_ai_failure)
        worker.signals.progress.connect(self._update_ai_progress)
        worker.signals.finished.connect(lambda: self._finish_ai(label))
        self.ai_worker = worker
        self.thread_pool.start(worker)

    def _collect_ai_result(self, uid: str, translated: str) -> None:
        self.ai_results[uid] = translated
        unit = self.model.unit_for_uid(uid)
        if unit is None or (unit.current_text == translated and not unit.pending_delete):
            return
        self.ai_changes.append(UnitChange(uid, unit.current_text, translated, unit.pending_delete, False))
        # AI signals are delivered on the GUI thread. Apply each completed
        # result immediately, while retaining one combined undo operation.
        self._apply_operation_state(uid, translated, False)
        if self.ai_is_batch:
            self._schedule_ai_filter_refresh()

    def _collect_ai_failure(self, uid: str, message: str) -> None:
        self.ai_failures.append(f"{uid}: {message}")

    def _update_ai_progress(self, current: int, total: int) -> None:
        if self.ai_is_batch:
            self.batch_ai_button.set_progress(current, total)
        self.statusBar().showMessage(translate("status.ai_progress", current=current, total=total))

    def _schedule_ai_filter_refresh(self) -> None:
        self.ai_filter_refresh_pending = True
        if not self.ai_filter_refresh_timer.isActive():
            self.ai_filter_refresh_timer.start()

    def _refresh_ai_filter(self) -> None:
        if not self.ai_filter_refresh_pending:
            return
        self.ai_filter_refresh_pending = False
        self.proxy.refresh_rows()
        self._update_counts()

    def _finish_ai(self, label: str) -> None:
        was_batch = self.ai_is_batch
        was_cancelled = self.ai_cancelled
        if was_batch:
            self.batch_ai_button.set_busy(False)
        self.ai_cancel_event = None
        self.ai_worker = None
        self.ai_is_batch = False
        self.ai_cancelled = False
        self.ai_filter_refresh_timer.stop()
        self._refresh_ai_filter()
        changes = tuple(self.ai_changes)
        if changes:
            # A cancelled batch still preserves all completed translations as
            # one application-level operation, so Ctrl+Z remains predictable.
            self.history.push(TranslationOperation(label, changes))
        summary = translate("status.ai_summary", count=len(changes))
        if was_cancelled:
            summary = translate("status.ai_summary_cancelled", count=len(changes))
        elif was_batch:
            summary = translate("status.ai_summary_finished", count=len(changes))
        if self.ai_failures:
            summary += translate("status.ai_summary_failures", count=len(self.ai_failures))
            QMessageBox.warning(self, translate("dialog.ai_finished_title"), summary + "\n\n" + "\n".join(self.ai_failures[:8]))
        else:
            self.statusBar().showMessage(translate("status.ai_review_save", summary=summary), 5000)
        if was_batch:
            anchor = self.batch_ai_button.mapToGlobal(self.batch_ai_button.rect().bottomLeft())
            QToolTip.showText(
                anchor,
                translate("status.ai_review_save", summary=summary),
                self.batch_ai_button,
                self.batch_ai_button.rect(),
                4500,
            )

    def save_all(self) -> None:
        self._commit_typing_operation()
        if self.project is None:
            return
        try:
            result = self.project.save(
                auto_space_before_color_tokens=self.settings.auto_space_before_color_tokens_on_save
            )
        except SaveValidationError as exc:
            QMessageBox.warning(self, translate("dialog.save_blocked"), "\n".join(exc.messages[:20]))
            return
        reviewed = tuple(
            unit for unit in (*result.saved_units, *result.deleted_units) if unit.review_reason == TODO_REASON_SOURCE_CHANGED
        )
        if reviewed:
            self.project.set_units_source_review(reviewed, False)
        format_warning_count = sum(
            1
            for unit in result.saved_units
            for issue in unit.issues()
            if not issue.blocks_save
        )
        if not result.changed_files:
            if result.deleted_units:
                self.load_project(discard_changes=True)
                self.statusBar().showMessage(translate("status.deleted_entries", count=len(result.deleted_units)), 4000)
                return
            self.statusBar().showMessage(translate("status.no_changes_to_save"), 3000)
            return
        commit_note = ""
        try:
            commit = (
                self.git.commit_saved(result.changed_files, result.saved_units, result.deleted_units)
                if self.git is not None
                else None
            )
            commit_note = translate("status.saved_commit", hash=commit.short_hash) if commit else ""
        except GitError as exc:
            commit_note = translate("status.saved_git_failed", error=exc)
        self.load_project(discard_changes=True)
        delete_note = translate("status.saved_delete_note", count=len(result.deleted_units)) if result.deleted_units else ""
        warning_note = translate("status.saved_warning_note", count=format_warning_count) if format_warning_count else ""
        self.statusBar().showMessage(
            translate(
                "status.saved_files",
                count=len(result.changed_files),
                delete_note=delete_note,
                warning_note=warning_note,
                commit_note=commit_note,
            ),
            7000,
        )

    def retry_commit(self) -> None:
        if self.git is None:
            return
        try:
            commit = self.git.commit_pending()
        except GitError as exc:
            QMessageBox.warning(self, translate("dialog.git_commit_failed"), str(exc))
            return
        self._update_pending_state()
        self.statusBar().showMessage(
            translate("status.retry_commit_done", hash=commit.short_hash) if commit else translate("status.retry_commit_none"),
            5000,
        )

    def _update_pending_state(self) -> None:
        if self.git is None:
            self.git_pending = False
            self.retry_button.setVisible(False)
            self._update_window_title()
            return
        try:
            pending = self.git.has_pending_changes()
        except GitError:
            pending = True
        self.git_pending = pending
        self.retry_button.setVisible(pending)
        if pending:
            self.statusBar().showMessage(translate("status.git_pending"))
        self._update_window_title()

    def show_history(self) -> None:
        if self.git is None:
            QMessageBox.information(self, translate("dialog.history_title"), translate("dialog.history_requires_project"))
            return
        HistoryDialog(self.git, self).exec()

    def show_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        previous_language = self.settings.ui_language
        self.settings = dialog.result_settings()
        save_settings(self.settings)
        if self.settings.ui_language != previous_language:
            set_language(self.settings.ui_language)
            self._retranslate_ui()
        self.ai_delegate.set_provider(self.settings.provider)
        if self.git is None:
            return
        try:
            self.git.ensure_repository(self.settings)
        except GitError as exc:
            QMessageBox.warning(self, translate("dialog.git_settings_error"), str(exc))

    def _current_unit(self) -> TranslationUnit | None:
        return self.model.unit_for_uid(self.current_uid) if self.current_uid else None

    def _unit_from_proxy_index(self, index: QModelIndex) -> TranslationUnit | None:
        if not index.isValid():
            return None
        source_index = self.proxy.mapToSource(index)
        return self.model.unit_at(source_index.row())

    def _update_issue_detail(self, unit: TranslationUnit | None) -> None:
        if unit is None:
            self.issue_label.setText(translate("issue.empty"))
            return
        if unit.pending_delete:
            self.issue_label.setText(translate("issue.pending_delete"))
            return
        issues = unit.issues()
        errors = [issue.message for issue in issues if issue.blocks_save]
        warnings = [issue.message for issue in issues if not issue.blocks_save]
        parts = []
        if unit.ref.kind == "text" and issues:
            parts.append(translate("issue.document_scope"))
        summary = _format_diff_text(unit)
        if summary != translate("issue.format_ok"):
            parts.append(translate("issue.summary_prefix", text=summary))
        if unit.filter_status() == STATUS_TODO and unit.todo_reason:
            parts.append(translate("issue.todo_reason_prefix", text=todo_reason_text(unit.todo_reason)))
        if errors:
            parts.append(translate("issue.error_prefix", text=_localized_detail_join(errors)))
        if warnings:
            parts.append(translate("issue.warning_prefix", text=_localized_detail_join(warnings)))
        if unit.is_dirty:
            parts.append(translate("issue.unsaved"))
        self.issue_label.setText("   ·   ".join(parts) if parts else translate("issue.format_ok"))

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._commit_typing_operation()
        if self.project is None:
            event.accept()
            return
        dirty_count = self.project.dirty_count()
        if not dirty_count:
            event.accept()
            return
        choice = QMessageBox.warning(
            self,
            translate("dialog.unsaved_title"),
            translate("dialog.unsaved_detail", count=dirty_count),
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if choice == QMessageBox.StandardButton.Save:
            self.save_all()
            if self.project is None or not self.project.has_dirty_units():
                event.accept()
            else:
                event.ignore()
            return
        if choice == QMessageBox.StandardButton.Discard:
            event.accept()
            return
        event.ignore()


def _search_blob(unit: TranslationUnit) -> str:
    todo_reason = todo_reason_text(unit.todo_reason) if unit.todo_reason else ""
    return "\n".join(
        (
            unit.file_rel,
            unit.record_id,
            unit.label,
            unit.field_name,
            unit.source_text,
            unit.current_text,
            unit.status,
            unit.filter_status(),
            status_text(unit.display_status()),
            todo_reason,
        )
    ).lower()


def _clip(text: str, limit: int) -> str:
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _localized_list_join(items: Iterable[str]) -> str:
    return ("、" if current_language().startswith("zh") else ", ").join(items)


def _localized_detail_join(items: Iterable[str]) -> str:
    return ("；" if current_language().startswith("zh") else "; ").join(items)


def _diff_token_key(token: str) -> str:
    return re.sub(r"\s+", "", token) if COLOR_TOKEN_RE.fullmatch(token) else token


def _format_token_occurrences(text: str) -> list[tuple[str, int, int]]:
    occurrences: list[tuple[str, int, int]] = []
    for match in TOKEN_RE.finditer(text):
        token = match.group(0)
        if token == "$N" or token.startswith("$["):
            continue
        occurrences.append((_diff_token_key(token), match.start(), match.end()))
    return occurrences


def _missing_source_token_ranges(source_text: str, target_text: str) -> list[tuple[int, int]]:
    source_occurrences = _format_token_occurrences(source_text)
    target_occurrences = _format_token_occurrences(target_text)
    source_keys = [key for key, _start, _end in source_occurrences]
    target_keys = [key for key, _start, _end in target_occurrences]
    ranges: list[tuple[int, int]] = []
    for tag, i1, i2, _j1, _j2 in SequenceMatcher(None, source_keys, target_keys, autojunk=False).get_opcodes():
        if tag in {"delete", "replace"}:
            ranges.extend((start, end) for _key, start, end in source_occurrences[i1:i2])
    return ranges


def _make_editor_selection(
    editor: QPlainTextEdit,
    start: int,
    end: int,
    *,
    background: str,
    foreground: str | None = None,
) -> QTextEdit.ExtraSelection:
    selection = QTextEdit.ExtraSelection()
    cursor = QTextCursor(editor.document())
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    selection.cursor = cursor
    selection.format.setBackground(QColor(background))
    if foreground is not None:
        selection.format.setForeground(QColor(foreground))
    return selection


def _history_state_html(title: str, detail: str, *, kind: str = "info") -> str:
    return f"""
    <html>
      <head>
        <style>
          body.history-root {{
            background: #fbf1c7;
            color: #3c3836;
            font-family: "Segoe UI", "Microsoft YaHei UI";
            margin: 0;
          }}
          .history-state {{
            background: #f2e5bc;
            border: 2px solid #bdae93;
            border-radius: 10px;
            padding: 14px 16px;
          }}
          .history-state--error {{
            background: #f2d8d8;
            border-color: #cc241d;
          }}
          .history-state__title {{
            font-size: 16px;
            font-weight: 900;
          }}
          .history-state__detail {{
            margin-top: 6px;
            color: #665c54;
            font-weight: 600;
            white-space: pre-wrap;
          }}
        </style>
      </head>
      <body class="history-root">
        <div class="history-state history-state--{kind}">
          <div class="history-state__title">{html.escape(title)}</div>
          <div class="history-state__detail">{html.escape(detail)}</div>
        </div>
      </body>
    </html>
    """


def _history_text(text: str) -> str:
    return html.escape(text.replace("\r", ""))


def _history_inline_diff_html(before: str, after: str) -> str:
    parts: list[str] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        left = _history_text(before[i1:i2])
        right = _history_text(after[j1:j2])
        if tag == "equal":
            parts.append(right)
        elif tag == "delete":
            if left:
                parts.append(f'<span class="diff-del">{left}</span>')
        elif tag == "insert":
            if right:
                parts.append(f'<span class="diff-add">{right}</span>')
        else:
            if left:
                parts.append(f'<span class="diff-del">{left}</span>')
            if right:
                parts.append(f'<span class="diff-add">{right}</span>')
    return "".join(parts) or f'<span class="diff-empty">{html.escape(translate("history.empty_value"))}</span>'


def _history_files_phrase(file_counts: Counter[str]) -> str:
    if not file_counts:
        return translate("history.zero_files")
    top_files = [Path(file_rel).name for file_rel, _count in file_counts.most_common(2)]
    if len(file_counts) <= 2:
        return _localized_list_join(top_files)
    return translate("history.files_many", names=_localized_list_join(top_files), count=len(file_counts))


def _history_change_phrase(add_count: int, update_count: int, delete_count: int) -> str:
    parts: list[str] = []
    if add_count:
        parts.append(translate("history.change.add", count=add_count))
    if update_count:
        parts.append(translate("history.change.update", count=update_count))
    if delete_count:
        parts.append(translate("history.change.delete", count=delete_count))
    return _localized_list_join(parts) if parts else translate("history.change.none")


def _history_entry_sort_key(entry: TranslationLogEntry) -> tuple[int, int | str, str, str]:
    if entry.record_id.isdigit():
        return (0, int(entry.record_id), entry.label, entry.field_name)
    return (1, entry.record_id, entry.label, entry.field_name)


def _history_entry_title(entry: TranslationLogEntry) -> str:
    title = entry.label if entry.label and entry.label != entry.file_rel else ""
    if not title:
        title = f"ID {entry.record_id}" if entry.record_id else Path(entry.file_rel).name
    hidden_fields = {"body", "text", "translation", "translated", "translator"}
    if entry.field_name and entry.field_name.lower() not in hidden_fields:
        title = f"{title} · {entry.field_name}"
    return title


def _history_entry_meta(entry: TranslationLogEntry) -> str:
    parts = [entry.file_rel]
    if entry.record_id:
        parts.append(f"ID {entry.record_id}")
    return " · ".join(parts)


def _render_history_entry(entry: TranslationLogEntry) -> str:
    if entry.kind == "新增":
        badge_class = "history-badge--add"
    elif entry.kind == "删除":
        badge_class = "history-badge--delete"
    else:
        badge_class = "history-badge--update"
    if entry.kind == "新增":
        diff_html = f'<span class="diff-add">{_history_text(entry.translated_text)}</span>'
        source_note = f'<div class="history-entry__source">{html.escape(translate("history.entry.source", text=entry.source_text))}</div>'
    elif entry.kind == "删除":
        diff_html = _history_inline_diff_html(entry.before_text, "")
        source_note = f'<div class="history-entry__source">{html.escape(translate("history.entry.source", text=entry.source_text))}</div>'
    else:
        diff_html = _history_inline_diff_html(entry.before_text, entry.translated_text)
        source_note = ""
    return f"""
    <div class="history-entry">
      <div class="history-entry__head">
        <span class="history-badge {badge_class}">{html.escape(history_kind_text(entry.kind))}</span>
        <span class="history-entry__title">{html.escape(_history_entry_title(entry))}</span>
      </div>
      <div class="history-entry__meta">{html.escape(_history_entry_meta(entry))}</div>
      <div class="history-entry__diff">{diff_html}</div>
      {source_note}
    </div>
    """


def _render_history_html(commits_oldest_first: tuple[GitCommit, ...], entries: list[TranslationLogEntry]) -> str:
    if not commits_oldest_first:
        return _history_state_html(translate("history.state.none_selected_title"), translate("history.state.none_selected_detail"))
    if not entries:
        detail = translate("history.state.no_final_changes_detail", count=len(commits_oldest_first))
        return _history_state_html(translate("history.state.no_final_changes_title"), detail)

    file_counts: Counter[str] = Counter(entry.file_rel for entry in entries)
    add_count = sum(1 for entry in entries if entry.kind == "新增")
    delete_count = sum(1 for entry in entries if entry.kind == "删除")
    update_count = len(entries) - add_count - delete_count
    top_files = _localized_list_join(f"{Path(file_rel).name} {count}" for file_rel, count in file_counts.most_common(3))
    note = translate("history.note", summary=_history_change_phrase(add_count, update_count, delete_count), files=_history_files_phrase(file_counts))
    if len(commits_oldest_first) == 1:
        scope = commits_oldest_first[0].short_hash
    else:
        scope = f"{commits_oldest_first[0].short_hash} → {commits_oldest_first[-1].short_hash}"

    grouped: dict[str, list[TranslationLogEntry]] = {}
    for entry in entries:
        grouped.setdefault(entry.file_rel, []).append(entry)
    sections: list[str] = []
    for file_rel, file_entries in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0].lower())):
        entry_html = "".join(_render_history_entry(entry) for entry in sorted(file_entries, key=_history_entry_sort_key))
        sections.append(
            f"""
            <section class="history-file">
              <div class="history-file__name">{html.escape(file_rel)}</div>
              {entry_html}
            </section>
            """
        )

    title = translate(
        "history.title.single" if len(commits_oldest_first) == 1 else "history.title.multi",
        commits=len(commits_oldest_first),
        entries=len(entries),
        add=add_count,
        update=update_count,
        delete=delete_count,
        files=len(file_counts),
    )
    return f"""
    <html>
      <head>
        <style>
          body.history-root {{
            background: #fbf1c7;
            color: #3c3836;
            font-family: "Segoe UI", "Microsoft YaHei UI";
            margin: 0;
          }}
          .history-summary, .history-state {{
            background: #f2e5bc;
            border: 2px solid #bdae93;
            border-radius: 10px;
            padding: 14px 16px;
            margin-bottom: 16px;
          }}
          .history-state--error {{
            background: #f2d8d8;
            border-color: #cc241d;
          }}
          .history-state__title, .history-summary__title {{
            font-size: 16px;
            font-weight: 900;
          }}
          .history-state__detail, .history-summary__meta, .history-summary__note {{
            margin-top: 6px;
            color: #665c54;
            font-weight: 600;
          }}
          .history-summary__note {{
            color: #3c3836;
          }}
          .history-file {{
            margin-top: 14px;
          }}
          .history-file__name {{
            background: #d5c4a1;
            border: 2px solid #3c3836;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 900;
            padding: 5px 9px;
            margin-bottom: 8px;
          }}
          .history-entry {{
            background: #f9efc9;
            border: 1px solid #d5c4a1;
            border-radius: 8px;
            padding: 8px 10px;
            margin-bottom: 8px;
          }}
          .history-entry__head {{
            display: block;
            margin-bottom: 2px;
          }}
          .history-entry__title {{
            font-weight: 900;
            font-size: 13px;
          }}
          .history-entry__meta {{
            color: #7c6f64;
            font-size: 11px;
            font-weight: 700;
            margin-bottom: 6px;
          }}
          .history-entry__diff {{
            background: #fbf1c7;
            border: 1px solid #d5c4a1;
            border-radius: 6px;
            padding: 6px 8px;
            white-space: pre-wrap;
            word-break: break-word;
            line-height: 1.5;
            font-size: 13px;
          }}
          .history-entry__source {{
            margin-top: 5px;
            color: #7c6f64;
            font-size: 11px;
            font-weight: 600;
            white-space: pre-wrap;
            word-break: break-word;
          }}
          .history-badge {{
            display: inline-block;
            border-radius: 999px;
            padding: 1px 7px;
            margin-right: 7px;
            font-size: 11px;
            font-weight: 900;
          }}
          .history-badge--add {{
            background: #d8f0d2;
            color: #076678;
          }}
          .history-badge--update {{
            background: #f5d6d6;
            color: #9d0006;
          }}
          .history-badge--delete {{
            background: #f5d6d6;
            color: #9d0006;
          }}
          .diff-del {{
            background: #f5d6d6;
            color: #9d0006;
            border-radius: 3px;
            padding: 0 1px;
            text-decoration: line-through;
            text-decoration-thickness: 2px;
          }}
          .diff-add {{
            background: #b8bb26;
            color: #1d2021;
            border-radius: 3px;
            padding: 0 1px;
          }}
          .diff-empty {{
            color: #928374;
            font-style: italic;
          }}
        </style>
      </head>
      <body class="history-root">
        <section class="history-summary">
          <div class="history-summary__title">{html.escape(title)}</div>
          <div class="history-summary__note">{html.escape(note)}</div>
          <div class="history-summary__meta">{html.escape(translate("history.scope", scope=scope))}</div>
          <div class="history-summary__meta">{html.escape(translate("history.top_files", files=top_files))}</div>
        </section>
        {''.join(sections)}
      </body>
    </html>
    """


def _issue_badge(unit: TranslationUnit) -> str:
    issues = unit.issues()
    errors = sum(issue.blocks_save for issue in issues)
    warnings = len(issues) - errors
    if errors:
        return f"✕{errors}" + (f" !{warnings}" if warnings else "")
    return f"!{warnings}" if warnings else "—"


def _format_token_deltas(unit: TranslationUnit) -> tuple[Counter[str], Counter[str], Counter[str], Counter[str]]:
    source_hard, source_color = split_soft_color_tokens(_format_tokens_for_diff(unit.source_text))
    target_hard, target_color = split_soft_color_tokens(_format_tokens_for_diff(unit.current_text))
    return (
        source_hard - target_hard,
        target_hard - source_hard,
        source_color - target_color,
        target_color - source_color,
    )


def _format_diff_parts(unit: TranslationUnit) -> list[tuple[str, str]]:
    """Return full token-level differences for tooltips and detail views."""
    missing, extra, missing_color, extra_color = _format_token_deltas(unit)
    parts: list[tuple[str, str]] = []
    parts.extend(("!", token) for token in _counter_tokens(missing))
    parts.extend(("-", token) for token in _counter_tokens(missing_color))
    parts.extend(("+", token) for token in _counter_tokens(extra))
    parts.extend(("+", token) for token in _counter_tokens(extra_color))
    return parts or [("✓", "")]


def _counter_tokens(counter: Counter[str]) -> list[str]:
    values: list[str] = []
    for token, count in sorted(counter.items()):
        values.append(token if count == 1 else f"{token}×{count}")
    return values


FORMAT_INFO_CODES = {"source-format-suspect", "format-fallback"}
FORMAT_ERROR_CODES = {"unknown-format", "dbt-quote"}


def _format_indicator(unit: TranslationUnit) -> tuple[str, str]:
    issues = unit.issues()
    if not issues:
        return "✓", translate("format.summary.ok")
    if any(issue.blocks_save for issue in issues):
        return "!", translate("format.summary.blocking")

    codes = {issue.code for issue in issues}
    if (
        any(code in FORMAT_ERROR_CODES or code.startswith("argument-") for code in codes)
        or any(issue.code == "font-glyph" for issue in issues)
    ):
        return "!", translate("format.summary.high")
    if codes and codes.issubset(FORMAT_INFO_CODES):
        return "~", translate("format.summary.source_suspect")
    return "?", translate("format.summary.warning")


def _format_diff_text(unit: TranslationUnit) -> str:
    return _format_indicator(unit)[1]


def _format_diff_tooltip(unit: TranslationUnit) -> str:
    source_tokens = format_counter_items(_format_tokens_for_diff(unit.source_text)) or translate("format.tooltip.source_tokens_empty")
    summary = _format_diff_text(unit)
    parts = _format_diff_parts(unit)
    lines = [
        translate("format.tooltip.summary", text=summary),
        translate("format.tooltip.source_tokens", text=source_tokens),
    ]
    if parts == [("✓", "")]:
        lines.append(translate("format.tooltip.diff_none"))
    else:
        difference = " ".join(marker + content for marker, content in parts)
        lines.append(translate("format.tooltip.diff", text=difference))
    issue_lines = [
        issue.message
        for issue in unit.issues()
        if issue.code not in {"format-missing", "format-extra", "format-color-missing", "format-color-extra"}
    ]
    if issue_lines:
        lines.append(translate("format.tooltip.notes", text=_localized_detail_join(issue_lines)))
    return "\n".join(lines)


def _format_tokens_for_diff(text: str) -> Counter[str]:
    tokens = format_tokens(text)
    tokens.pop("$N", None)
    return tokens


def _extract_recommended_translation(markdown: str) -> str:
    match = re.search(r"```(?:[A-Za-z0-9_-]+)?[ \t]*\r?\n(.*?)```", markdown, flags=re.DOTALL)
    return match.group(1).strip() if match else ""


def _text_format(color: str, underline: bool = False) -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.setForeground(QColor(color))
    fmt.setFontUnderline(underline)
    return fmt


def apply_modern_style(app: QApplication) -> None:
    app.setStyle("Fusion")
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#ebdbb2"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#fbf1c7"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#ebdbb2"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#3c3836"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#d79921"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#b8bb26"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#3c3836"))
    app.setPalette(palette)
    app.setStyleSheet(
        """
        QWidget { color: #3c3836; font-family: "Segoe UI", "Microsoft YaHei UI"; font-size: 13px; }
        QMainWindow, #root { background: #ebdbb2; }
        #titlebar { background: #3c3836; border: 3px solid #282828; border-radius: 10px; }
        #workspaceTitle { color: #fbf1c7; font-size: 18px; font-weight: 900; letter-spacing: 1px; }
        #workspaceSubtitle { color: #d5c4a1; font-size: 10px; font-weight: 800; letter-spacing: 2px; }
        #toolbar { background: #d5c4a1; border: 3px solid #3c3836; border-radius: 10px; }
        #toolbar QLabel { font-weight: 800; }
        #counts { background: #fbf1c7; border: 2px solid #3c3836; border-radius: 6px; color: #3c3836; font-weight: 800; padding: 5px 8px; }
        #issues { background: #d3869b; border: 3px solid #3c3836; border-radius: 7px; padding: 8px 10px; color: #3c3836; font-weight: 600; }
        #hint { color: #3c3836; padding: 4px 0; font-weight: 600; }
        #projectManagerDialog { background: #ebdbb2; }
        #projectManagerSummary { background: #fbf1c7; border: 2px solid #3c3836; border-radius: 8px; padding: 8px 10px; font-weight: 800; }
        #projectManagerGameRoot { background: #f2e5bc; border: 2px solid #bdae93; border-radius: 8px; padding: 7px 10px; font-weight: 700; }
        #projectManagerRow { background: #fbf1c7; border: 3px solid #3c3836; border-radius: 10px; }
        #projectManagerName { font-size: 15px; font-weight: 900; }
        #projectKindBadge, #projectStateBadge { border-radius: 9px; padding: 3px 9px; font-weight: 900; }
        #projectKindBadge[kind="vanilla"] { background: #458588; color: #fbf1c7; }
        #projectKindBadge[kind="mod"] { background: #689d6a; color: #fbf1c7; }
        #projectStateBadge[state="added"] { background: #b8bb26; color: #3c3836; }
        #projectStateBadge[state="missing"] { background: #d79921; color: #3c3836; }
        #projectManagerPath { color: #665c54; font-weight: 600; }
        #projectManagerFeedback { background: #dce5b5; border: 2px solid #3c3836; border-radius: 8px; padding: 8px 10px; font-weight: 700; }
        #projectAddButton { font-size: 18px; min-width: 36px; }
        QGroupBox { background: #fbf1c7; border: 3px solid #3c3836; border-radius: 8px; margin-top: 14px; padding-top: 8px; font-weight: 900; color: #3c3836; }
        QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 12px; padding: 0 6px; background: #fbf1c7; }
        QTableView { background: #fbf1c7; border: 3px solid #3c3836; border-radius: 8px; gridline-color: #928374; selection-background-color: #b8bb26; selection-color: #3c3836; }
        QTableView::item { background: transparent; border-bottom: 1px solid #d5c4a1; padding: 2px 4px; }
        QTableView::item:selected { background: #b8bb26; color: #3c3836; }
        QHeaderView::section { background: #d79921; color: #3c3836; border: 0; border-right: 2px solid #3c3836; border-bottom: 3px solid #3c3836; padding: 8px; font-weight: 900; }
        QPlainTextEdit, QTextBrowser { background: #fbf1c7; border: 0; padding: 8px; selection-background-color: #b8bb26; selection-color: #3c3836; }
        QListWidget { background: #fbf1c7; border: 3px solid #3c3836; border-radius: 8px; padding: 3px; font-size: 12px; }
        QListWidget::item { padding: 4px 7px; border-radius: 4px; }
        QListWidget::item:selected { background: #b8bb26; color: #3c3836; }
        QLineEdit, QComboBox { background: #f2e5bc; border: 2px solid #3c3836; border-radius: 5px; padding: 5px 7px; min-height: 20px; font-weight: 600; }
        QLineEdit:focus, QComboBox:focus { border: 3px solid #458588; }
        QComboBox QAbstractItemView { background: #f2e5bc; border: 2px solid #3c3836; selection-background-color: #b8bb26; selection-color: #3c3836; }
        QPushButton, QToolButton { background: #d79921; color: #3c3836; border: 2px solid #3c3836; border-bottom: 5px solid #3c3836; border-radius: 5px; padding: 5px 10px 3px 10px; font-weight: 900; }
        QPushButton:hover, QToolButton:hover { background: #e8b75d; }
        QPushButton:pressed, QToolButton:pressed { border-top: 5px solid #3c3836; border-bottom: 2px solid #3c3836; padding: 8px 8px 2px 12px; }
        QPushButton#primary { background: #458588; color: #fbf1c7; }
        QPushButton#primary:hover { background: #689d6a; }
        QPushButton#batchAi[mode="busy"] { background: #689d6a; color: #fbf1c7; }
        QPushButton#batchAi[mode="cancel"] { background: #cc241d; color: #fbf1c7; }
        QPushButton#batchAi[mode="cancelling"] { background: #d65d0e; color: #fbf1c7; }
        QMenu { background: #fbf1c7; border: 3px solid #3c3836; padding: 4px; }
        QMenu::item { padding: 7px 22px 7px 10px; font-weight: 700; }
        QMenu::item:selected { background: #b8bb26; color: #3c3836; }
        QMenu::separator { height: 1px; background: #bdae93; margin: 6px 8px; }
        QDialog#suggestionDialog { background: #ebdbb2; border: 3px solid #3c3836; }
        QDialog#historyDialog { background: #ebdbb2; }
        #historyHint { color: #665c54; font-weight: 700; padding-bottom: 4px; }
        #historyContent { border: 3px solid #3c3836; border-radius: 8px; }
        #suggestionStatus { color: #665c54; font-weight: 700; }
        QToolTip { background: #3c3836; color: #fbf1c7; border: 2px solid #d79921; padding: 5px; font-weight: 700; }
        QStatusBar { background: #ebdbb2; color: #3c3836; font-weight: 700; }
        """
    )


def main() -> None:
    app = QApplication([])
    app.setApplicationName("The Guild 2 Translator")
    apply_modern_style(app)
    window = TranslatorWindow()
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
