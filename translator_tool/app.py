from __future__ import annotations

from collections import Counter
from dataclasses import replace
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
    QMimeData,
    QModelIndex,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRunnable,
    QSignalBlocker,
    QSortFilterProxyModel,
    Qt,
    QThreadPool,
    QTimer,
    QRectF,
    QUrl,
    Signal,
)
from PySide6.QtGui import QAction, QCloseEvent, QColor, QCursor, QFont, QFontMetrics, QIcon, QImage, QKeyEvent, QKeySequence, QPainter, QPalette, QPen, QStandardItemModel, QSyntaxHighlighter, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument, QTextImageFormat, QWheelEvent
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
    QListWidgetItem,
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
    LlmSuggestionContext,
    OpenAICompatibleProvider,
    TranslationProvider,
    TranslationProviderError,
    build_llm_contexts,
    llm_provider_from_settings,
    provider_from_settings,
)
from .code_index import CodeReference, CodeReferenceIndex, CodeReferenceSet, label_group_key, normalize_label
from .code_index_lazy import LazyCodeIndexBuilder, LazyIndexProgress
from .code_window_context import (
    PreviewWindowContext,
    engine_window_context,
    engine_pair_preview_surface,
    surface_window_context,
)
from .code_open import open_code_reference
from .codec_adapter import CodecError, Guild2Codec, load_codec_for_language, language_uses_codec
from .diagnostics import configure_diagnostics, log_exception, log_failure, log_metrics, shutdown_diagnostics
from .entry_clipboard import ENTRY_CLIPBOARD_MIME, decode_translations, encode_entries
from .git_history import GitCommit, GitError, LanguageGit, TranslationLogEntry
from .game_theme import GameAssetSet, GameHeaderFrame, GamePanelFrame, install_game_theme_style
from .history import OperationHistory, TranslationOperation, UnitChange
from .history_index import (
    MAX_INDEXED_COMMITS,
    HistoryIndexCapacityError,
    HistoryIndexStore,
)
from .history_render import (
    commit_search_blob,
    entry_meta as _history_entry_meta,
    entry_search_blob,
    entry_title as _history_entry_title,
    history_text as _history_text,
    inline_diff_html as _history_inline_diff_html,
    render_entry_timeline_html,
)
from .i18n import current_language, history_kind_text, set_language, status_text, todo_reason_text, translate, ui_language_options
from .project import (
    ENABLE_FONT_GLYPH_VALIDATION,
    MISSING_WORK_STATUSES,
    Project,
    ProjectError,
    TODO_REASON_MANUAL_REVIEW,
    TODO_REASON_SOURCE_CHANGED,
    STATUS_EXTRA,
    STATUS_IGNORED,
    STATUS_PENDING_DELETE,
    STATUS_REVIEW,
    STATUS_TODO,
    STATUS_TRANSLATED,
    SaveValidationError,
    TranslationUnit,
)
from .preview import GLYPH_MARK, PREVIEW_MARK, PreviewAtom, PreviewDocument, PreviewService
from .preview_coverage import preview_placeholder_coverage, preview_reference_coverage
from .preview_context_selection import rank_preview_references, select_preview_context
from .recovery import apply_recovery_draft, clear_recovery_draft, load_recovery_draft, save_recovery_draft
from .search import SearchClause, parse_search_query, search_blob as _search_blob, search_field_values as _search_field_values
from .settings import AppSettings, load_settings, protect_secret, reveal_secret, save_settings
from .source_sync import (
    DEFAULT_TRANSLATION_LANGUAGE,
    SourceProjectSpec,
    VANILLA_PROJECT_NAME,
    discover_game_source_projects,
    ensure_translation_dir,
    game_languages_root,
    has_vanilla_source_entries,
    local_project_roots,
    managed_vanilla_project_root,
    sync_source_project,
    sync_vanilla_sources,
)
from .text_import import (
    IMPORT_MODE_KEYED,
    IMPORT_MODE_TRANSLATIONS,
    IMPORT_POLICY_EMPTY,
    IMPORT_POLICY_OVERWRITE,
    OUTCOME_AMBIGUOUS,
    OUTCOME_DUPLICATE,
    OUTCOME_EMPTY,
    OUTCOME_EXISTING,
    OUTCOME_NOT_FOUND,
    OUTCOME_SAME,
    OUTCOME_SOURCE_MISMATCH,
    OUTCOME_UPDATE,
    TextImportPlan,
    build_import_plan,
    parse_import_text,
)
from .validation import (
    COLOR_TOKEN_RE,
    FORMAT_GUILD2,
    format_counter_items,
    format_dialect,
    format_tokens,
    highlight_re_for,
    split_soft_color_tokens,
    token_re_for,
)


BUNDLED_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])).resolve()
APP_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else BUNDLED_ROOT
DEFAULT_PROJECT_ROOT = BUNDLED_ROOT
APP_ICON_PATH = BUNDLED_ROOT / "assets" / "app-icon.ico"
MANAGED_PROJECT_ROOT = managed_vanilla_project_root(APP_ROOT)
TYPING_GROUP_DELAY_MS = 750
HISTORY_ENTRY_RESULT_LIMIT = 500
FILE_FILTER_ALL = "__all_files__"
STATUS_FILTER_ALL = "__all_statuses__"
STATUS_FILTER_TODO = "__needs_translation__"
STATUS_FILTER_REVIEW = STATUS_REVIEW
LANGUAGE_ACTION_NEW = "__new_language__"
LANGUAGE_ACTION_SEPARATOR = "__language_separator__"
class SearchLineEdit(QLineEdit):
    case_sensitive_toggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.case_button = QToolButton(self)
        self.case_button.setObjectName("searchCaseButton")
        self.case_button.setText("Aa")
        self.case_button.setCheckable(True)
        self.case_button.setAutoRaise(True)
        self.case_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.case_button.setFixedSize(28, 22)
        self.case_button.toggled.connect(self.case_sensitive_toggled)
        self.case_button.toggled.connect(lambda checked: self.case_button.setProperty("active", checked))
        self.setTextMargins(0, 0, 52, 0)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.case_button.move(max(0, self.width() - 52), max(0, (self.height() - self.case_button.height()) // 2))


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
        self._units_by_file: dict[str, tuple[TranslationUnit, ...]] = {}
        self._search: dict[str, str] = {}
        self._search_case_sensitive: dict[str, str] = {}
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
        self._search_case_sensitive.clear()
        self._row_by_uid.clear()
        self._units_by_file.clear()
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
                label = translate("status.review") if unit.requires_manual_review else status_text(unit.display_status())
                return label + suffix + detail
        if role == Qt.ItemDataRole.BackgroundRole:
            if unit.pending_delete:
                return QColor(_theme_row_tint("delete", "#f2d6d3"))
            if unit.requires_manual_review:
                return QColor(_theme_row_tint("review", "#f4b66f"))
            if self.has_glyph_warning(index.row()):
                return QColor(_theme_row_tint("glyph", "#f3d9a4"))
            return QColor(_theme_row_tint("recent", "#dce5b5")) if unit.uid in self._recently_translated else None
        if role == Qt.ItemDataRole.ForegroundRole and unit.pending_delete:
            return QColor(_theme_color("bad_token", "#9d0006"))
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

    def search_blob(self, row: int, *, case_sensitive: bool = False) -> str:
        unit = self.units[row]
        index = self._search_case_sensitive if case_sensitive else self._search
        return index.get(unit.uid, "")

    def matches_search(
        self,
        row: int,
        clauses: tuple[SearchClause, ...],
        *,
        case_sensitive: bool,
    ) -> bool:
        unit = self.unit_at(row)
        if unit is None:
            return False
        blob = self.search_blob(row, case_sensitive=case_sensitive)
        for clause in clauses:
            values = (blob,) if not clause.field else _search_field_values(unit, clause.field)
            if not case_sensitive and clause.field:
                values = tuple(value.casefold() for value in values)
            matched = any(clause.needle in value for value in values)
            if matched == clause.excluded:
                return False
        return True

    def refresh_unit(self, unit: TranslationUnit) -> None:
        row = self._row_by_uid.get(unit.uid)
        if row is None:
            return
        raw_search = _search_blob(unit)
        self._search_case_sensitive[unit.uid] = raw_search
        self._search[unit.uid] = raw_search.casefold()
        self._format_warning.pop(unit.uid, None)
        self._glyph_warning.pop(unit.uid, None)
        self.dataChanged.emit(self.index(row, 0), self.index(row, self.columnCount() - 1))

    def refresh_units(self, units: Iterable[TranslationUnit]) -> None:
        rows: list[int] = []
        for unit in units:
            row = self._row_by_uid.get(unit.uid)
            if row is None:
                continue
            raw_search = _search_blob(unit)
            self._search_case_sensitive[unit.uid] = raw_search
            self._search[unit.uid] = raw_search.casefold()
            self._format_warning.pop(unit.uid, None)
            self._glyph_warning.pop(unit.uid, None)
            rows.append(row)
        if rows:
            self.dataChanged.emit(
                self.index(min(rows), 0),
                self.index(max(rows), self.columnCount() - 1),
            )

    def set_recently_translated(self, unit: TranslationUnit, recent: bool, *, notify: bool = True) -> None:
        if recent:
            self._recently_translated.add(unit.uid)
        else:
            self._recently_translated.discard(unit.uid)
        if not notify:
            return
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

    def units_for_file(self, file_rel: str) -> tuple[TranslationUnit, ...]:
        return self._units_by_file.get(file_rel, ())

    def _rebuild_indexes(self) -> None:
        self._row_by_uid = {unit.uid: index for index, unit in enumerate(self.units)}
        self._search_case_sensitive = {unit.uid: _search_blob(unit) for unit in self.units}
        self._search = {uid: text.casefold() for uid, text in self._search_case_sensitive.items()}
        units_by_file: dict[str, list[TranslationUnit]] = {}
        for unit in self.units:
            units_by_file.setdefault(unit.file_rel, []).append(unit)
        self._units_by_file = {file_rel: tuple(units) for file_rel, units in units_by_file.items()}

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
        self.case_sensitive = False
        self.search_clauses: tuple[SearchClause, ...] = ()
        self._sort_rank_by_uid: dict[str, int] = {}
        # Edits and filter changes must not continuously re-sort a large project.
        # Sorting is only performed when the user explicitly clicks a column.
        self.setDynamicSortFilter(False)

    def setSourceModel(self, source_model) -> None:  # noqa: N802
        super().setSourceModel(source_model)
        if source_model is not None:
            source_model.modelReset.connect(self._resort_after_model_reset)

    def _resort_after_model_reset(self) -> None:
        column = self.sortColumn()
        if column >= 0:
            self.sort(column, self.sortOrder())

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        self._sort_rank_by_uid.clear()
        source = self.sourceModel()
        if column >= 0 and isinstance(source, UnitTableModel):
            ranked = sorted(
                range(source.rowCount()),
                key=lambda row: self._sort_key(source, source.unit_at(row), row, column),
            )
            self._sort_rank_by_uid = {
                unit.uid: rank
                for rank, row in enumerate(ranked)
                if (unit := source.unit_at(row)) is not None
            }
        super().sort(column, order)

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:  # noqa: N802
        source = self.sourceModel()
        if not isinstance(source, UnitTableModel):
            return super().lessThan(left, right)
        left_unit = source.unit_at(left.row())
        right_unit = source.unit_at(right.row())
        if left_unit is None or right_unit is None:
            return left.row() < right.row()
        left_rank = self._sort_rank_by_uid.get(left_unit.uid)
        right_rank = self._sort_rank_by_uid.get(right_unit.uid)
        if left_rank is None or right_rank is None:
            return left.row() < right.row()
        return left_rank < right_rank

    def _sort_key(
        self,
        source: UnitTableModel,
        unit: TranslationUnit | None,
        source_row: int,
        column: int,
    ) -> tuple[object, ...]:
        if unit is None:
            return (source_row,)
        text_tie = (unit.file_rel.casefold(), unit.label.casefold(), source_row)
        if column == UnitTableModel.FILE:
            key: tuple[object, ...] = (unit.file_rel.casefold(), *text_tie)
        elif column == UnitTableModel.ID:
            record_id = unit.record_id.strip()
            key = ((0, int(record_id)) if record_id.isdecimal() else (1, record_id.casefold()), *text_tie)
        elif column == UnitTableModel.LABEL:
            key = (unit.label.casefold(), *text_tie)
        elif column == UnitTableModel.SOURCE:
            key = (unit.source_text.casefold(), *text_tie)
        elif column == UnitTableModel.TRANSLATION:
            key = (unit.current_text.casefold(), *text_tie)
        elif column == UnitTableModel.STATUS:
            status_rank = {
                STATUS_TODO: 0,
                STATUS_REVIEW: 1,
                STATUS_TRANSLATED: 2,
                STATUS_IGNORED: 3,
                STATUS_EXTRA: 4,
                STATUS_PENDING_DELETE: 5,
            }
            status = unit.display_status()
            key = (status_rank.get(status, 99), status.casefold(), *text_tie)
        elif column == UnitTableModel.FORMAT:
            key = (0 if source.has_format_warning(source_row) else 1, *text_tie)
        elif column == UnitTableModel.AI:
            can_translate = bool(
                unit.source_text
                and not unit.is_ignored
                and not unit.requires_manual_review
                and unit.filter_status() in MISSING_WORK_STATUSES
            )
            key = (0 if unit.pending_delete else 1 if can_translate else 2, *text_tie)
        else:
            key = (*text_tie,)
        return key

    def set_filters(
        self,
        *,
        file_filter: str,
        status_filter: str,
        only_missing: bool,
        only_format_warnings: bool,
        query: str,
        case_sensitive: bool = False,
    ) -> None:
        self.file_filter = file_filter
        self.status_filter = status_filter
        self.only_missing = only_missing
        self.only_format_warnings = only_format_warnings
        self.query = query.strip()
        self.case_sensitive = case_sensitive
        self.search_clauses = parse_search_query(self.query, case_sensitive=case_sensitive)
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
        needs_review = unit.requires_manual_review
        needs_translation = effective_status in MISSING_WORK_STATUSES and not needs_review
        keep_visible = source.is_recently_translated(unit)
        if unit.pending_delete:
            keep_visible = True
        if self.status_filter == STATUS_FILTER_REVIEW and not needs_review:
            return False
        if self.status_filter == STATUS_FILTER_TODO and not needs_translation and not keep_visible:
            return False
        if self.status_filter == STATUS_FILTER_ALL and self.only_missing and not needs_translation and not keep_visible:
            return False
        if self.status_filter not in {STATUS_FILTER_ALL, STATUS_FILTER_TODO, STATUS_FILTER_REVIEW} and effective_status != self.status_filter:
            return False
        if self.only_format_warnings and not source.has_format_warning(source_row):
            return False
        return not self.search_clauses or source.matches_search(
            source_row,
            self.search_clauses,
            case_sensitive=self.case_sensitive,
        )


class RowTintDelegate(QStyledItemDelegate):
    """Force model-provided review colors through the stylesheet paint path."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        if _paint_review_background(painter, option, index):
            painter.save()
            font = index.data(Qt.ItemDataRole.FontRole)
            painter.setFont(font if isinstance(font, QFont) else option.font)
            foreground = index.data(Qt.ItemDataRole.ForegroundRole)
            painter.setPen(
                foreground
                if isinstance(foreground, QColor)
                else QColor(_theme_color("text", "#3c3836"))
            )
            text_rect = option.rect.adjusted(5, 0, -5, 0)
            text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
            text = painter.fontMetrics().elidedText(text, option.textElideMode, text_rect.width())
            painter.drawText(text_rect, option.displayAlignment or (Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), text)
            painter.setPen(QColor(_theme_color("muted_text", "#d5c4a1")))
            painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
            painter.restore()
            return
        super().paint(painter, option, index)


class DelayedToolTipFilter(QObject):
    """Give one widget its own tooltip delay instead of Qt's shared wake-up state."""

    def __init__(
        self,
        widget: QWidget,
        delay_ms: int,
        content_at: Callable[[QPoint], tuple[object, str, object] | None],
    ) -> None:
        super().__init__(widget)
        self.widget = widget
        self.content_at = content_at
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(delay_ms)
        self.timer.timeout.connect(self._show)
        self.pending_key: object | None = None
        self.pending_text = ""
        self.pending_rect = widget.rect()
        widget.setMouseTracking(True)
        widget.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is not self.widget:
            return False
        event_type = event.type()
        if event_type == QEvent.Type.ToolTip:
            return True
        if event_type in {QEvent.Type.Enter, QEvent.Type.MouseMove}:
            if event_type == QEvent.Type.MouseMove:
                point = event.position().toPoint()
            else:
                point = self.widget.mapFromGlobal(QCursor.pos())
            self._schedule(point)
        elif event_type in {
            QEvent.Type.Leave,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.Wheel,
            QEvent.Type.Hide,
        }:
            self.cancel()
        return False

    def _schedule(self, point: QPoint) -> None:
        content = self.content_at(point)
        if content is None or not content[1]:
            self.cancel()
            return
        key, text, rect = content
        if key == self.pending_key and (self.timer.isActive() or QToolTip.isVisible()):
            return
        QToolTip.hideText()
        self.timer.stop()
        self.pending_key = key
        self.pending_text = text
        self.pending_rect = rect
        self.timer.start()

    def _show(self) -> None:
        if not self.pending_text or not self.widget.underMouse():
            return
        point = self.widget.mapFromGlobal(QCursor.pos())
        content = self.content_at(point)
        if content is None or content[0] != self.pending_key or content[1] != self.pending_text:
            self.cancel()
            return
        QToolTip.showText(QCursor.pos(), self.pending_text, self.widget, self.pending_rect)

    def cancel(self) -> None:
        self.timer.stop()
        self.pending_key = None
        self.pending_text = ""
        QToolTip.hideText()


class GamePreviewPopup(QWidget):
    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self._image = QImage()
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

    def show_image(self, image: QImage, anchor: QPoint, avoid: QRect) -> None:
        self._image = image
        self.setFixedSize(image.size())
        screen = QApplication.screenAt(anchor) or QApplication.primaryScreen()
        position = QPoint(avoid.right() - self.width(), avoid.top() - self.height() - 8)
        if screen is not None:
            available = screen.availableGeometry()
            if position.y() < available.top():
                position.setY(avoid.bottom() + 8)
            if position.y() + self.height() > available.bottom():
                position.setX(avoid.left() - self.width() - 8)
                position.setY(anchor.y() - self.height() // 2)
            if position.x() < available.left():
                position.setX(avoid.right() + 8)
            position.setX(max(available.left(), min(position.x(), available.right() - self.width())))
            position.setY(max(available.top(), min(position.y(), available.bottom() - self.height())))
        self.move(position)
        self.show()
        self.raise_()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.drawImage(self.rect(), self._image)


class GamePreviewHoverFilter(QObject):
    def __init__(
        self,
        button: QToolButton,
        popup: GamePreviewPopup,
        image_provider: Callable[[], QImage | None],
        delay_ms: int = 250,
    ) -> None:
        super().__init__(button)
        self.button = button
        self.popup = popup
        self.image_provider = image_provider
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(delay_ms)
        self.timer.timeout.connect(self._show)
        self.hovered = False
        button.setMouseTracking(True)
        button.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        try:
            if watched is not self.button:
                return False
            event_type = event.type()
        except RuntimeError:
            return False
        if event_type == QEvent.Type.ToolTip:
            return True
        if event_type in {QEvent.Type.Enter, QEvent.Type.MouseMove}:
            self.hovered = True
            if not self.button.isChecked() and not self.timer.isActive() and not self.popup.isVisible():
                self.timer.start()
        elif event_type in {
            QEvent.Type.Leave,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.Hide,
        }:
            self.cancel()
        return False

    def _show(self) -> None:
        try:
            if self.button.isChecked() or not self.hovered:
                return
        except RuntimeError:
            return
        image = self.image_provider()
        if image is None or image.isNull():
            return
        top_left = self.button.mapToGlobal(self.button.rect().topLeft())
        avoid = QRect(top_left, self.button.size())
        self.popup.show_image(image, QCursor.pos(), avoid)

    def cancel(self) -> None:
        self.hovered = False
        self.timer.stop()
        try:
            self.popup.hide()
        except RuntimeError:
            pass


class EditorGroupBox(QGroupBox):
    """Place the preview toggle in the title line without consuming editor space."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("editorPanel")
        self.game_assets = GameAssetSet()
        self.code_button = QToolButton(self)
        self.code_button.setObjectName("codeReferenceButton")
        self.code_button.setAutoRaise(False)
        self.code_button.hide()
        self.reference_label = QLabel(self)
        self.reference_label.setObjectName("codeReferenceCount")
        self.reference_label.hide()
        self.preview_button = QToolButton(self)
        self.preview_button.setObjectName("previewToggle")
        self.preview_button.setCheckable(True)
        self.preview_button.setAutoRaise(False)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        app = QApplication.instance()
        if app is None or app.property("guild2Theme") is not True:
            return
        painter = QPainter(self)
        self.game_assets.nine_slice(painter, self.rect(), "B_3DWindow_01")
        painter.end()

    def resizeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.position_preview_button()

    def position_preview_button(self) -> None:
        height = self.fontMetrics().height() + 6
        width = max(36, self.preview_button.fontMetrics().horizontalAdvance(self.preview_button.text()) + 16)
        self.preview_button.setFixedSize(width, height)
        self.preview_button.move(max(8, self.width() - width - 12), 1)
        if self.code_button.isVisible():
            title_width = self.fontMetrics().horizontalAdvance(self.title())
            code_width = max(52, self.code_button.fontMetrics().horizontalAdvance(self.code_button.text()) + 24)
            code_x = 16 + title_width + 12
            self.code_button.setFixedSize(code_width, height)
            self.code_button.move(code_x, 0)
            label_width = max(74, self.reference_label.fontMetrics().horizontalAdvance(self.reference_label.text()) + 14)
            max_label_width = max(0, self.preview_button.x() - code_x - code_width - 12)
            if max_label_width:
                label_width = min(label_width, max_label_width)
            self.reference_label.setFixedSize(label_width, height)
            self.reference_label.move(code_x + code_width + 6, 0)


def _single_line_preview_text(text: str) -> str:
    return (
        text.replace(PREVIEW_MARK, "")
        .replace("\r\n", "↵")
        .replace("\r", "↵")
        .replace("\n", "↵")
        .replace("\t", "⇥")
    )


class PreviewTextDelegate(RowTintDelegate):
    """Render a non-editable table cell from the shared format preview model."""

    def __init__(
        self,
        parent: QTableView,
        *,
        target: bool,
        enabled: Callable[[], bool],
        render_preview: Callable[[TranslationUnit, bool], PreviewDocument],
        glyph_image: Callable[[int, bool], object | None],
    ) -> None:
        super().__init__(parent)
        self.target = target
        self.enabled = enabled
        self.render_preview = render_preview
        self.glyph_image = glyph_image

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        unit = _unit_from_model_index(index)
        if not self.enabled() or not isinstance(unit, TranslationUnit):
            super().paint(painter, option, index)
            return

        if not _paint_review_background(painter, option, index):
            background = QStyleOptionViewItem(option)
            background.text = ""
            style = option.widget.style() if option.widget else QApplication.style()
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, background, painter, option.widget)

        document = self.render_preview(unit, self.target)
        painter.save()
        painter.setClipRect(option.rect)
        font = option.font
        if unit.pending_delete:
            font.setStrikeOut(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        x = option.rect.left() + 5
        baseline = option.rect.center().y() + (metrics.ascent() - metrics.descent()) // 2
        right = option.rect.right() - 5
        default_color = QColor("#d4775d") if unit.pending_delete else QColor(_theme_color("text", "#3c3836"))

        for atom in document.atoms:
            if x >= right:
                break
            text = _single_line_preview_text(atom.text)
            if atom.glyph_id is not None:
                image = self.glyph_image(atom.glyph_id, self.target)
                if image is not None and hasattr(image, "isNull") and not image.isNull():
                    height = max(8.0, min(float(option.rect.height() - 6), float(metrics.height())))
                    width = max(1.0, image.width() * height / max(image.height(), 1))
                    if x + width > right:
                        break
                    marker_rect = QRectF(
                        float(x),
                        option.rect.center().y() - height / 2.0,
                        width,
                        height,
                    )
                    painter.drawImage(marker_rect, image)
                    x += math.ceil(width) + 1
                    continue
                text = f"□{atom.glyph_id}"
            if not text:
                continue
            remaining = right - x
            rendered = metrics.elidedText(text, Qt.TextElideMode.ElideRight, remaining)
            width = metrics.horizontalAdvance(rendered)
            underline_y = float(baseline + 2)
            text_color = QColor(*atom.color) if atom.final_style and atom.color is not None else default_color
            if atom.replacement and not atom.final_style and atom.text not in {"\n", "\t", PREVIEW_MARK}:
                marker_rect = QRectF(
                    float(x),
                    float(baseline - metrics.ascent()),
                    float(width),
                    float(metrics.height()),
                )
                style = Qt.PenStyle.DashLine if atom.color is not None else Qt.PenStyle.SolidLine
                underline_color = QColor(*atom.color) if atom.color is not None else QColor(_theme_color("markup_token", "#79740e"))
                painter.setPen(QPen(underline_color, 2, style))
                painter.drawLine(marker_rect.bottomLeft(), marker_rect.bottomRight())
                underline_y = float(marker_rect.bottom())
            painter.setPen(text_color)
            painter.drawText(x, baseline, rendered)
            if atom.color is not None and not atom.final_style and not (
                atom.replacement and atom.text not in {"\n", "\t", PREVIEW_MARK}
            ):
                painter.setPen(QPen(QColor(*atom.color), 2, Qt.PenStyle.SolidLine))
                painter.drawLine(
                    QPointF(float(x), underline_y),
                    QPointF(float(x + width), underline_y),
                )
            x += width
            if rendered != text:
                break

        painter.setPen(QColor(_theme_color("muted_text", "#d5c4a1")))
        painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
        painter.restore()


class PopupHighlightDelegate(QStyledItemDelegate):
    """Keep the combo's current value visibly marked inside the popup."""

    def __init__(self, combo: QComboBox) -> None:
        super().__init__(combo.view())
        self.combo = combo

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        data = index.data(Qt.ItemDataRole.UserRole)
        palette = QApplication.palette()
        if data == LANGUAGE_ACTION_SEPARATOR:
            painter.save()
            painter.setPen(QPen(palette.color(QPalette.ColorRole.Mid), 1))
            y = option.rect.center().y()
            painter.drawLine(option.rect.left() + 8, y, option.rect.right() - 8, y)
            painter.restore()
            return
        is_current_value = index.row() == self.combo.currentIndex()
        is_hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)

        if is_current_value:
            painter.save()
            painter.fillRect(option.rect, palette.color(QPalette.ColorRole.Highlight))
            painter.restore()
            option.palette.setColor(QPalette.ColorRole.Highlight, palette.color(QPalette.ColorRole.Highlight))
            option.palette.setColor(QPalette.ColorRole.HighlightedText, palette.color(QPalette.ColorRole.HighlightedText))
            option.palette.setColor(QPalette.ColorRole.Text, palette.color(QPalette.ColorRole.Text))
        elif is_hovered or is_selected:
            painter.save()
            painter.fillRect(option.rect, palette.color(QPalette.ColorRole.Midlight))
            painter.restore()
            option.palette.setColor(QPalette.ColorRole.Highlight, palette.color(QPalette.ColorRole.Midlight))
            option.palette.setColor(QPalette.ColorRole.HighlightedText, palette.color(QPalette.ColorRole.Text))
            option.palette.setColor(QPalette.ColorRole.Text, palette.color(QPalette.ColorRole.Text))

        super().paint(painter, option, index)


class PopupSelectionComboBox(QComboBox):
    """Keep the popup view aligned with the combo's current item."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.view().setItemDelegate(PopupHighlightDelegate(self))

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
        if bool(QApplication.instance().property("guild2Theme")):
            game_button = QImage(_game_theme_asset("button_start_pressed.png" if pressed else "button_start.png"))
            if not game_button.isNull():
                painter.drawImage(rect, game_button)
                painter.setPen(QColor("#f4e8ae"))
                font = painter.font()
                font.setBold(True)
                painter.setFont(font)
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, translate("table.ai_action"))
                painter.restore()
                return
        if not pressed:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#3c3836"))
            shadow_offset = 4 if hovered else 3
            painter.drawRoundedRect(rect.translated(3, shadow_offset), 4, 4)
        if self.provider == "google":
            fill = QColor("#d79921")
        elif self.provider == "deepl":
            fill = QColor("#287fc4")
        else:
            fill = QColor("#b16286")
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
        try:
            table = self.parent()
        except RuntimeError:
            return False
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
        STATUS_REVIEW: ("status.review", "#d65d0e", "#fbf1c7"),
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


class PreviewPlainTextEdit(QTextEdit):
    """Editable raw text with a reversible, localized preview presentation."""

    previewRendered = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._raw_text = ""
        self._preview_enabled = False
        self._editing_raw = False
        self._uses_application_undo_history = False
        self._preview_builder: Callable[[str], PreviewDocument] | None = None
        self._glyph_provider: Callable[[int], object | None] | None = None
        self._text_glyph_provider: Callable[[str, tuple[int, int, int, int] | None], object | None] | None = None
        self._text_font_family_provider: Callable[[], str] | None = None
        self._game_font_enabled = False
        self._preview_document = PreviewDocument.from_atoms("", [])
        self._base_zoom_point_size: float | None = None

    @property
    def preview_enabled(self) -> bool:
        return self._preview_enabled

    @property
    def rendered_preview(self) -> PreviewDocument:
        return self._preview_document

    def use_application_undo_history(self) -> None:
        self._uses_application_undo_history = True
        self._sync_native_undo()

    def _sync_native_undo(self) -> None:
        enabled = not self._preview_enabled and not self.isReadOnly() and not self._uses_application_undo_history
        self.setUndoRedoEnabled(enabled)
        if not enabled:
            self.document().clearUndoRedoStacks()

    def set_preview_builder(
        self,
        builder: Callable[[str], PreviewDocument],
        glyph_provider: Callable[[int], object | None],
    ) -> None:
        self._preview_builder = builder
        self._glyph_provider = glyph_provider
        if self._preview_enabled:
            self.refresh_preview()

    def set_game_font_builder(
        self,
        enabled: bool,
        provider: Callable[[str, tuple[int, int, int, int] | None], object | None],
        font_family_provider: Callable[[], str] | None = None,
    ) -> None:
        self._game_font_enabled = enabled
        self._text_glyph_provider = provider
        self._text_font_family_provider = font_family_provider
        if self._preview_enabled:
            self.refresh_preview()

    def set_preview_enabled(self, enabled: bool) -> None:
        if enabled == self._preview_enabled:
            return
        if self._editing_raw:
            self._finish_raw_edit()
        if enabled:
            self._raw_text = QTextEdit.toPlainText(self)
            raw_position = self.textCursor().position()
            self._preview_enabled = True
            self._sync_native_undo()
            self._render_preview(raw_position)
        else:
            raw_position = self._preview_document.raw_position(self.textCursor().position())
            self._preview_enabled = False
            blocker = QSignalBlocker(self)
            self._set_unformatted_plain_text(self._raw_text)
            cursor = self.textCursor()
            cursor.setPosition(min(raw_position, len(self._raw_text)))
            self.setTextCursor(cursor)
            del blocker
            self._sync_native_undo()
        self.previewRendered.emit()

    def refresh_preview(self) -> None:
        if not self._preview_enabled or self._editing_raw:
            return
        raw_position = self._preview_document.raw_position(self.textCursor().position())
        self._render_preview(raw_position)
        self.previewRendered.emit()

    def setPlainText(self, text: str) -> None:  # noqa: N802
        self._raw_text = text
        if self._preview_enabled and not self._editing_raw:
            self._render_preview(0)
            return
        self._set_unformatted_plain_text(text)

    def toPlainText(self) -> str:  # noqa: N802
        if self._preview_enabled and not self._editing_raw:
            return self._raw_text
        return QTextEdit.toPlainText(self)

    def map_raw_range(self, start: int, end: int) -> tuple[int, int]:
        if not self._preview_enabled or self._editing_raw:
            return start, end
        return self._preview_document.display_range(start, end)

    def set_zoom_factor(self, factor: float) -> None:
        if self._base_zoom_point_size is None:
            font = self.font()
            if font.pointSizeF() > 0:
                self._base_zoom_point_size = font.pointSizeF()
            else:
                self._base_zoom_point_size = font.pixelSize() * 72.0 / max(1, self.logicalDpiY())
        font = QFont(self.font())
        font.setPointSizeF(max(1.0, self._base_zoom_point_size * factor))
        self.document().setDefaultFont(font)
        cursor = QTextCursor(self.document())
        cursor.select(QTextCursor.SelectionType.Document)
        char_format = QTextCharFormat()
        char_format.setFont(font)
        cursor.mergeCharFormat(char_format)
        self.setCurrentCharFormat(char_format)

    @staticmethod
    def _is_edit_key(event: QKeyEvent) -> bool:
        if event.key() in {
            Qt.Key.Key_Backspace,
            Qt.Key.Key_Delete,
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Tab,
        }:
            return True
        if event.matches(QKeySequence.StandardKey.Paste) or event.matches(QKeySequence.StandardKey.Cut):
            return True
        return bool(event.text()) and not (
            event.modifiers() & Qt.KeyboardModifier.ControlModifier and not event.modifiers() & Qt.KeyboardModifier.AltModifier
        )

    def _begin_raw_edit(self) -> None:
        if not self._preview_enabled or self._editing_raw:
            return
        display_cursor = self.textCursor()
        raw_anchor = self._preview_document.raw_position(display_cursor.anchor())
        raw_position = self._preview_document.raw_position(display_cursor.position())
        self._editing_raw = True
        blocker = QSignalBlocker(self)
        self._set_unformatted_plain_text(self._raw_text)
        raw_cursor = self.textCursor()
        raw_cursor.setPosition(raw_anchor)
        raw_cursor.setPosition(raw_position, QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(raw_cursor)
        del blocker

    def _finish_raw_edit(self) -> None:
        if not self._editing_raw:
            return
        raw_cursor = self.textCursor()
        raw_anchor = raw_cursor.anchor()
        raw_position = raw_cursor.position()
        self._raw_text = QTextEdit.toPlainText(self)
        self._editing_raw = False
        self._render_preview(raw_position, raw_anchor)
        self.previewRendered.emit()

    def _render_preview(self, raw_position: int, raw_anchor: int | None = None) -> None:
        builder = self._preview_builder
        document = builder(self._raw_text) if builder is not None else PreviewDocument.from_atoms(
            self._raw_text,
            [PreviewAtom(self._raw_text, 0, len(self._raw_text))] if self._raw_text else [],
        )
        self._preview_document = document
        blocker = QSignalBlocker(self)
        self._set_unformatted_plain_text(document.display_text)
        self._apply_preview_line_height(document.line_height_percent)
        text_font_family = (
            self._text_font_family_provider()
            if self._game_font_enabled and self._text_font_family_provider is not None
            else ""
        )
        for span in document.spans:
            atom = span.atom
            if (
                self._game_font_enabled
                and self._text_glyph_provider is not None
                and not text_font_family
                and atom.glyph_id is None
            ):
                for offset, char in enumerate(atom.text):
                    if char in {"\n", "\r", "\t", PREVIEW_MARK}:
                        continue
                    image = self._text_glyph_provider(
                        char,
                        atom.color or _theme_rgba("text", (55, 38, 24, 255)),
                    )
                    if image is None or not hasattr(image, "isNull") or image.isNull():
                        continue
                    glyph_cursor = QTextCursor(self.document())
                    glyph_cursor.setPosition(span.display_start + offset)
                    glyph_cursor.movePosition(
                        QTextCursor.MoveOperation.NextCharacter,
                        QTextCursor.MoveMode.KeepAnchor,
                    )
                    height = max(8, QFontMetrics(self.document().defaultFont()).height() - 2)
                    width = max(1.0, image.width() * height / max(image.height(), 1))
                    resource_url = QUrl(
                        f"preview-font-{ord(char)}-{span.display_start + offset}-{height}.png"
                    )
                    self.document().addResource(
                        QTextDocument.ResourceType.ImageResource,
                        resource_url,
                        image,
                    )
                    image_format = QTextImageFormat()
                    image_format.setName(resource_url.toString())
                    image_format.setWidth(width)
                    image_format.setHeight(height)
                    image_format.setVerticalAlignment(
                        QTextCharFormat.VerticalAlignment.AlignMiddle
                    )
                    glyph_cursor.insertImage(image_format)
                continue
            cursor = QTextCursor(self.document())
            cursor.setPosition(span.display_start)
            cursor.setPosition(span.display_end, QTextCursor.MoveMode.KeepAnchor)
            char_format = QTextCharFormat()
            if text_font_family and atom.glyph_id is None:
                font = QFont(self.document().defaultFont())
                font.setFamily(text_font_family)
                char_format.setFont(font)
            has_visible_replacement = atom.replacement and atom.text not in {"\n", "\t", PREVIEW_MARK}
            if atom.final_style and atom.color is not None:
                char_format.setForeground(QColor(*atom.color))
            elif atom.color is not None:
                char_format.setUnderlineColor(QColor(*atom.color))
                char_format.setUnderlineStyle(
                    QTextCharFormat.UnderlineStyle.DashUnderline
                    if has_visible_replacement
                    else QTextCharFormat.UnderlineStyle.SingleUnderline
                )
            elif has_visible_replacement and not atom.final_style:
                char_format.setUnderlineStyle(QTextCharFormat.UnderlineStyle.DashUnderline)
                char_format.setUnderlineColor(QColor(_theme_color("markup_token", "#79740e")))
            if atom.replacement and atom.text not in {"\n", "\t", PREVIEW_MARK}:
                char_format.setFontWeight(QFont.Weight.Normal)
            if atom.glyph_id is not None and self._glyph_provider is not None:
                image = self._glyph_provider(atom.glyph_id)
                if image is not None and hasattr(image, "isNull") and not image.isNull():
                    height = max(8, QFontMetrics(self.document().defaultFont()).height() - 2)
                    width = max(1.0, image.width() * height / max(image.height(), 1))
                    resource_url = QUrl(
                        f"preview-glyph-{atom.glyph_id}-{span.display_start}-{height}.png"
                    )
                    self.document().addResource(
                        QTextDocument.ResourceType.ImageResource,
                        resource_url,
                        image,
                    )
                    glyph_format = QTextImageFormat()
                    glyph_format.setName(resource_url.toString())
                    glyph_format.setWidth(width)
                    glyph_format.setHeight(height)
                    glyph_format.setVerticalAlignment(
                        QTextCharFormat.VerticalAlignment.AlignMiddle
                    )
                    cursor.insertImage(glyph_format)
                    continue
            cursor.mergeCharFormat(char_format)
        display_anchor = document.display_position(raw_anchor if raw_anchor is not None else raw_position)
        display_position = document.display_position(raw_position)
        cursor = self.textCursor()
        cursor.setPosition(display_anchor)
        cursor.setPosition(display_position, QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(cursor)
        del blocker
        self.viewport().update()

    def _set_unformatted_plain_text(self, text: str) -> None:
        self.setCurrentCharFormat(QTextCharFormat())
        QTextEdit.setPlainText(self, text)
        self.setCurrentCharFormat(QTextCharFormat())

    def _apply_preview_line_height(self, percent: int) -> None:
        cursor = QTextCursor(self.document())
        cursor.select(QTextCursor.SelectionType.Document)
        block_format = QTextBlockFormat()
        block_format.setLineHeight(percent, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        block_format.setTopMargin(0)
        block_format.setBottomMargin(0)
        cursor.mergeBlockFormat(block_format)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if self._preview_enabled and self._is_edit_key(event):
            self._begin_raw_edit()
            QTextEdit.keyPressEvent(self, event)
            self._finish_raw_edit()
            return
        QTextEdit.keyPressEvent(self, event)

    def inputMethodEvent(self, event) -> None:  # noqa: N802
        if self._preview_enabled:
            self._begin_raw_edit()
            QTextEdit.inputMethodEvent(self, event)
            if not event.preeditString():
                self._finish_raw_edit()
            return
        QTextEdit.inputMethodEvent(self, event)

    def insertPlainText(self, text: str) -> None:  # noqa: N802
        if self._preview_enabled and not self._editing_raw:
            self._begin_raw_edit()
            QTextEdit.insertPlainText(self, text)
            self._finish_raw_edit()
            return
        QTextEdit.insertPlainText(self, text)

    def insertFromMimeData(self, source) -> None:  # noqa: N802
        if self._preview_enabled and not self._editing_raw:
            self._begin_raw_edit()
            QTextEdit.insertFromMimeData(self, source)
            self._finish_raw_edit()
            return
        QTextEdit.insertFromMimeData(self, source)

    def cut(self) -> None:
        if self._preview_enabled and not self._editing_raw:
            self._begin_raw_edit()
            QTextEdit.cut(self)
            self._finish_raw_edit()
            return
        QTextEdit.cut(self)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        if self._editing_raw:
            self._finish_raw_edit()
        super().focusOutEvent(event)


class TokenHighlighter(QSyntaxHighlighter):
    def __init__(
        self,
        document,
        glyph_codec: Guild2Codec | None = None,
        dialect: str = FORMAT_GUILD2,
    ) -> None:
        super().__init__(document)
        self.glyph_codec = glyph_codec
        self.dialect = dialect
        self.format_token = _text_format(_theme_color("format_token", "#075a9c"))
        self.color_token = _text_format(_theme_color("color_token", "#7a3e9d"))
        self.markup_token = _text_format(_theme_color("markup_token", "#6b6b00"))
        self.quote_token = _text_format(_theme_color("quote_token", "#107c10"))
        self.bad_token = _text_format(_theme_color("bad_token", "#b00020"), underline=True)
        self.glyph_token = _text_format(_theme_color("glyph_token", "#cc241d"), underline=True)

    def set_glyph_codec(self, glyph_codec: Guild2Codec | None) -> None:
        self.glyph_codec = glyph_codec
        self.rehighlight()

    def set_dialect(self, dialect: str) -> None:
        if dialect == self.dialect:
            return
        self.dialect = dialect
        self.rehighlight()

    def refresh_theme(self) -> None:
        self.format_token.setForeground(QColor(_theme_color("format_token", "#075a9c")))
        self.color_token.setForeground(QColor(_theme_color("color_token", "#7a3e9d")))
        self.markup_token.setForeground(QColor(_theme_color("markup_token", "#6b6b00")))
        self.quote_token.setForeground(QColor(_theme_color("quote_token", "#107c10")))
        self.bad_token.setForeground(QColor(_theme_color("bad_token", "#b00020")))
        self.glyph_token.setForeground(QColor(_theme_color("glyph_token", "#cc241d")))
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        for match in highlight_re_for(self.dialect).finditer(text):
            token = match.group(0)
            fmt = self.format_token
            if token.startswith("$C") or token.startswith("$S") or token == "$N":
                fmt = self.color_token
            elif token.startswith(("<", "[", "{")):
                fmt = self.markup_token
            elif token.startswith(">") and token.endswith("<"):
                fmt = self.quote_token
            self.setFormat(match.start(), match.end() - match.start(), fmt)
        if self.glyph_codec is not None:
            position = 0
            for char in text:
                if char != GLYPH_MARK and self.glyph_codec.unsupported_characters(char):
                    self.setFormat(position, 2 if ord(char) > 0xFFFF else 1, self.glyph_token)
                position += 2 if ord(char) > 0xFFFF else 1


class AiWorkerSignals(QObject):
    translated = Signal(str, str)
    failed = Signal(str, str)
    progress = Signal(int, int)
    finished = Signal()


class AiWorker(QRunnable):
    def __init__(
        self,
        provider: TranslationProvider,
        units: Iterable[TranslationUnit],
        cancel_event: threading.Event,
        contexts: dict[str, LlmSuggestionContext] | None = None,
    ) -> None:
        super().__init__()
        self.provider = provider
        self.units = tuple(units)
        self.cancel_event = cancel_event
        self.contexts = contexts or {}
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
                translated = self.provider.translate(
                    unit.source_text,
                    dbt_field=unit.ref.kind == "dbt",
                    context=self.contexts.get(unit.uid),
                )
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


class CodeIndexWorkerSignals(QObject):
    partial = Signal(int, object, object)
    finished = Signal(int)
    failed = Signal(int, str)


class CodeIndexWorker(QRunnable):
    def __init__(self, token: int, game_root: Path | None, project_root: Path | None) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.token = token
        self.game_root = game_root
        self.project_root = project_root
        self.signals = CodeIndexWorkerSignals()
        self.cancel_event = threading.Event()
        self._request_lock = threading.Lock()
        self._requested: dict[str, int] = {}

    def request_labels(self, labels: Iterable[str], priority: int) -> None:
        with self._request_lock:
            for label in labels:
                normalized = normalize_label(label)
                if not normalized:
                    continue
                current = self._requested.get(normalized)
                if current is None or priority < current:
                    self._requested[normalized] = priority

    def cancel(self) -> None:
        self.cancel_event.set()

    def _take_requested(self) -> tuple[str, ...]:
        with self._request_lock:
            if not self._requested:
                return ()
            priority = min(self._requested.values())
            labels = tuple(
                label
                for label, requested_priority in self._requested.items()
                if requested_priority == priority
            )
            for label in labels:
                self._requested.pop(label, None)
            return labels

    def _has_requested(self) -> bool:
        with self._request_lock:
            return bool(self._requested)

    def run(self) -> None:
        if self.game_root is None or self.project_root is None:
            self.signals.finished.emit(self.token)
            return
        builder = LazyCodeIndexBuilder(
            self.game_root,
            self.project_root,
            vanilla_project_name=VANILLA_PROJECT_NAME,
        )
        try:
            while not self.cancel_event.is_set():
                labels = self._take_requested()
                if labels:
                    index = builder.analyze_labels(
                        labels,
                        cancelled=self.cancel_event.is_set,
                    )
                else:
                    index = builder.analyze_next_batch(
                        12,
                        cancelled=lambda: self.cancel_event.is_set() or self._has_requested(),
                    )
                progress = builder.progress
                if not index.is_empty:
                    self.signals.partial.emit(self.token, index, progress)
                if progress.complete:
                    break
        except Exception as exc:
            try:
                self.signals.failed.emit(self.token, str(exc))
            except RuntimeError:
                pass
            return
        finally:
            builder.close()
        if self.cancel_event.is_set():
            return
        try:
            self.signals.finished.emit(self.token)
        except RuntimeError:
            pass


class GitInitWorkerSignals(QObject):
    ready = Signal(int, bool)
    failed = Signal(int, str)


class GitInitWorker(QRunnable):
    """Prepare Git without making project loading wait for subprocesses."""

    def __init__(self, token: int, git: LanguageGit, settings: AppSettings) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.token = token
        self.git = git
        self.settings = settings
        self.signals = GitInitWorkerSignals()

    def run(self) -> None:
        try:
            self.git.ensure_repository(self.settings)
            pending = self.git.has_pending_changes()
        except (GitError, OSError, ValueError) as exc:
            try:
                self.signals.failed.emit(self.token, str(exc))
            except RuntimeError:
                pass
            return
        except Exception as exc:
            try:
                self.signals.failed.emit(self.token, translate("error.unexpected", error=exc))
            except RuntimeError:
                pass
            return
        try:
            self.signals.ready.emit(self.token, pending)
        except RuntimeError:
            pass


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
        context: LlmSuggestionContext | None,
        cancel_event: threading.Event,
    ) -> None:
        super().__init__()
        self.provider = provider
        self.source_text = source_text
        self.current_translation = current_translation
        self.context = context
        self.cancel_event = cancel_event
        self.signals = SuggestionWorkerSignals()

    def run(self) -> None:
        try:
            for chunk in self.provider.stream_suggestion_with_context(
                self.source_text, self.current_translation, self.context
            ):
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
            by_commit = dict(self.git.iter_entries_for_commits(reversed(hashes)))
            entries = [entry for commit in hashes for entry in by_commit.get(commit, ())]
            rendered = _render_history_html(self.commits_oldest_first, entries)
            self.signals.rendered.emit(self.request_id, rendered)
        except (GitError, OSError, UnicodeError) as exc:
            self.signals.failed.emit(self.request_id, str(exc))
        except Exception as exc:
            self.signals.failed.emit(self.request_id, translate("error.unexpected", error=exc))


class HistoryIndexWorkerSignals(QObject):
    ready = Signal(int, object)
    progress = Signal(int, int)
    failed = Signal(int, str)


class HistoryIndexWorker(QRunnable):
    def __init__(
        self,
        request_id: int,
        git: LanguageGit,
        commits_newest_first: tuple[GitCommit, ...],
        cancel_event: threading.Event,
    ) -> None:
        super().__init__()
        self.request_id = request_id
        self.git = git
        self.commits_newest_first = commits_newest_first
        self.cancel_event = cancel_event
        self.signals = HistoryIndexWorkerSignals()

    def run(self) -> None:
        events: list[tuple[GitCommit, TranslationLogEntry]] = []
        commits = self.commits_newest_first[:MAX_INDEXED_COMMITS]
        total = len(commits)
        limited = len(commits) < len(self.commits_newest_first)
        try:
            store = HistoryIndexStore.for_repository(
                self.git.repo,
                self.git.language,
                codec_fingerprint=self.git.history_cache_fingerprint,
            )
            hashes = tuple(commit.full_hash for commit in commits)
            store.retain_commits(hashes)
            indexed = store.indexed_hashes(hashes)
            cached_entries = store.entries_for_commits(
                commit.full_hash for commit in commits if commit.full_hash in indexed
            )
            loaded_entries: dict[str, list[TranslationLogEntry]] = dict(cached_entries)
            missing = tuple(commit.full_hash for commit in commits if commit.full_hash not in indexed)
            completed = len(indexed)
            if completed:
                self.signals.progress.emit(completed, total)
            if missing:
                with store.writer() as writer:
                    for commit_hash, entries in self.git.iter_entries_for_commits(missing, self.cancel_event):
                        if self.cancel_event.is_set():
                            return
                        try:
                            writer.store_commit(commit_hash, entries)
                        except HistoryIndexCapacityError:
                            limited = True
                            break
                        loaded_entries[commit_hash] = entries
                        completed += 1
                        self.signals.progress.emit(completed, total)
            for commit in commits:
                entries = loaded_entries.get(commit.full_hash)
                if entries is None:
                    continue
                events.extend((commit, entry) for entry in entries)
        except (GitError, OSError, UnicodeError) as exc:
            if not self.cancel_event.is_set():
                self.signals.failed.emit(self.request_id, str(exc))
            return
        except Exception as exc:
            if not self.cancel_event.is_set():
                self.signals.failed.emit(self.request_id, translate("error.unexpected", error=exc))
            return
        if not self.cancel_event.is_set():
            self.signals.ready.emit(self.request_id, {"events": events, "limited": limited})


class TextImportDialog(QDialog):
    DETAIL_LIMIT = 200

    def __init__(
        self,
        units: Iterable[TranslationUnit],
        selected_units: Iterable[TranslationUnit],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.units = tuple(units)
        self.selected_units = tuple(selected_units)
        self._units_by_uid = {unit.uid: unit for unit in self.units}
        self._plan = TextImportPlan((), 0, ())
        self.setWindowTitle(translate("text_import.title"))
        self.resize(900, 680)

        layout = QVBoxLayout(self)
        intro = QLabel(translate("text_import.intro"))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        controls = QFormLayout()
        self.mode_combo = QComboBox()
        self.mode_combo.addItem(translate("text_import.mode.keyed"), IMPORT_MODE_KEYED)
        self.mode_combo.addItem(
            translate("text_import.mode.translations", count=len(self.selected_units)),
            IMPORT_MODE_TRANSLATIONS,
        )
        controls.addRow(translate("text_import.mode.label"), self.mode_combo)
        self.policy_combo = QComboBox()
        self.policy_combo.addItem(translate("text_import.policy.empty"), IMPORT_POLICY_EMPTY)
        self.policy_combo.addItem(translate("text_import.policy.overwrite"), IMPORT_POLICY_OVERWRITE)
        controls.addRow(translate("text_import.policy.label"), self.policy_combo)
        self.allow_empty = QCheckBox(translate("text_import.allow_empty"))
        controls.addRow("", self.allow_empty)
        layout.addLayout(controls)

        self.input_edit = QPlainTextEdit()
        self.input_edit.setPlaceholderText(translate("text_import.placeholder"))
        layout.addWidget(self.input_edit, 1)

        self.summary = QLabel(translate("text_import.summary.empty"))
        self.summary.setObjectName("textImportSummary")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.details_button = QToolButton()
        self.details_button.setCheckable(True)
        self.details_button.setText(translate("text_import.details.show"))
        self.details_button.toggled.connect(self._toggle_details)
        layout.addWidget(self.details_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(210)
        self.details.document().setMaximumBlockCount(self.DETAIL_LIMIT + 30)
        self.details.hide()
        layout.addWidget(self.details)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.import_button = self.buttons.addButton(
            translate("text_import.import"),
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self.import_button.setEnabled(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.setInterval(100)
        self.refresh_timer.timeout.connect(self._refresh_plan)
        self.input_edit.textChanged.connect(self.refresh_timer.start)
        self.mode_combo.currentIndexChanged.connect(self._schedule_refresh)
        self.policy_combo.currentIndexChanged.connect(self._schedule_refresh)
        self.allow_empty.toggled.connect(self._schedule_refresh)

    def _schedule_refresh(self, _value: object = None) -> None:
        self.refresh_timer.start()

    def _toggle_details(self, shown: bool) -> None:
        self.details.setVisible(shown)
        self.details_button.setText(
            translate("text_import.details.hide" if shown else "text_import.details.show")
        )

    def _refresh_plan(self) -> None:
        mode = str(self.mode_combo.currentData() or IMPORT_MODE_KEYED)
        policy = str(self.policy_combo.currentData() or IMPORT_POLICY_EMPTY)
        parsed = parse_import_text(self.input_edit.toPlainText(), mode)
        self._plan = build_import_plan(
            parsed,
            self.units,
            self.selected_units,
            mode=mode,
            policy=policy,
            allow_empty=self.allow_empty.isChecked(),
        )
        if not self.input_edit.toPlainText():
            self.summary.setText(translate("text_import.summary.empty"))
        else:
            self.summary.setText(
                translate(
                    "text_import.summary",
                    updates=len(self._plan.updates),
                    skipped=self._plan.skipped_count,
                    problems=self._plan.problem_count,
                )
            )
        self.import_button.setEnabled(bool(self._plan.updates))
        self.details.setPlainText(self._detail_text())

    def _detail_text(self) -> str:
        counts = self._plan.outcome_counts()
        detail_lines = [
            translate("text_import.count.update", count=counts[OUTCOME_UPDATE]),
            translate("text_import.count.same", count=counts[OUTCOME_SAME]),
            translate("text_import.count.existing", count=counts[OUTCOME_EXISTING]),
            translate("text_import.count.empty", count=counts[OUTCOME_EMPTY]),
            translate("text_import.count.blank", count=self._plan.blank_lines),
            translate("text_import.count.not_found", count=counts[OUTCOME_NOT_FOUND]),
            translate("text_import.count.source_mismatch", count=counts[OUTCOME_SOURCE_MISMATCH]),
            translate("text_import.count.duplicate", count=counts[OUTCOME_DUPLICATE]),
            translate("text_import.count.ambiguous", count=counts[OUTCOME_AMBIGUOUS]),
            translate("text_import.count.issues", count=len(self._plan.issues)),
        ]
        rows: list[str] = []
        detail_total = sum(row.outcome != OUTCOME_SAME for row in self._plan.rows) + len(self._plan.issues)
        for planned in self._plan.rows:
            if planned.outcome in {OUTCOME_UPDATE, OUTCOME_SAME}:
                continue
            unit = self._units_by_uid.get(planned.unit_uid)
            key = planned.row.key or (unit.label if unit is not None else "")
            rows.append(
                translate(
                    "text_import.detail.row",
                    line=planned.row.line_number,
                    entry_key=key or translate("text_import.detail.no_key"),
                    result=translate(f"text_import.outcome.{planned.outcome}"),
                )
            )
            if len(rows) >= self.DETAIL_LIMIT:
                break
        for issue in self._plan.issues:
            if len(rows) >= self.DETAIL_LIMIT:
                break
            if issue.code == "selection_count":
                imported, selected = (issue.preview.split("\t", 1) + [""])[:2]
                result = translate(
                    "text_import.issue.selection_count",
                    imported=imported,
                    selected=selected,
                )
            else:
                result = translate(f"text_import.issue.{issue.code}")
            line = str(issue.line_number) if issue.line_number else "-"
            rows.append(
                translate(
                    "text_import.detail.row",
                    line=line,
                    entry_key=issue.preview or translate("text_import.detail.no_key"),
                    result=result,
                )
            )
        if len(rows) < self.DETAIL_LIMIT:
            for planned in self._plan.updates:
                unit = self._units_by_uid.get(planned.unit_uid)
                key = planned.row.key or (unit.label if unit is not None else "")
                before = (planned.current_text or translate("text_import.detail.empty_value")).replace("\n", " ↵ ")
                after = (planned.row.translation or translate("text_import.detail.empty_value")).replace("\n", " ↵ ")
                rows.append(
                    translate(
                        "text_import.detail.change",
                        line=planned.row.line_number,
                        entry_key=key or translate("text_import.detail.no_key"),
                        before=before[:80],
                        after=after[:80],
                    )
                )
                if len(rows) >= self.DETAIL_LIMIT:
                    break
        if rows:
            detail_lines.extend(("", translate("text_import.detail.preview")))
            detail_lines.extend(rows)
            if detail_total > len(rows):
                detail_lines.append(
                    translate("text_import.detail.limited", count=detail_total - len(rows))
                )
        return "\n".join(detail_lines)

    def import_plan(self) -> TextImportPlan:
        if self.refresh_timer.isActive():
            self.refresh_timer.stop()
            self._refresh_plan()
        return self._plan


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
        self.ui_theme = QComboBox()
        self.ui_theme_label = QLabel()
        interface_form.addRow(self.ui_theme_label, self.ui_theme)
        self.preview_scope = QComboBox()
        self.preview_scope_label = QLabel()
        interface_form.addRow(self.preview_scope_label, self.preview_scope)
        self.preview_scope_hint = QLabel()
        self.preview_scope_hint.setObjectName("hint")
        self.preview_scope_hint.setWordWrap(True)
        interface_form.addRow(self.preview_scope_hint)
        layout.addWidget(self.interface_group)

        self.preview_assets_group = QGroupBox()
        preview_assets_form = QFormLayout(self.preview_assets_group)
        self.preview_translation_font_dir = QLineEdit(self.settings.preview_translation_font_dir)
        self.preview_ui_assets_dir = QLineEdit(self.settings.preview_ui_assets_dir)
        self.preview_translation_font_label = QLabel()
        self.preview_ui_assets_label = QLabel()
        self.preview_path_buttons: list[QToolButton] = []
        for label, line_edit in (
            (self.preview_translation_font_label, self.preview_translation_font_dir),
            (self.preview_ui_assets_label, self.preview_ui_assets_dir),
        ):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(line_edit, 1)
            browse = QToolButton()
            browse.clicked.connect(lambda _checked=False, field=line_edit: self._choose_preview_path(field))
            row_layout.addWidget(browse)
            self.preview_path_buttons.append(browse)
            preview_assets_form.addRow(label, row)
        self.preview_game_font_in_editors = QCheckBox()
        self.preview_game_font_in_editors.setChecked(self.settings.preview_game_font_in_editors)
        preview_assets_form.addRow(self.preview_game_font_in_editors)
        self.preview_use_code_context = QCheckBox()
        self.preview_use_code_context.setChecked(self.settings.preview_use_code_context)
        preview_assets_form.addRow(self.preview_use_code_context)
        self.preview_window_scale = QComboBox()
        for percent in (100, 125, 150, 175, 200):
            self.preview_window_scale.addItem(f"{percent}%", percent)
        scale_index = self.preview_window_scale.findData(
            self.settings.preview_window_scale_percent
        )
        self.preview_window_scale.setCurrentIndex(scale_index if scale_index >= 0 else 0)
        self.preview_window_scale_label = QLabel()
        preview_assets_form.addRow(
            self.preview_window_scale_label,
            self.preview_window_scale,
        )
        self.preview_assets_hint = QLabel()
        self.preview_assets_hint.setObjectName("hint")
        self.preview_assets_hint.setWordWrap(True)
        preview_assets_form.addRow(self.preview_assets_hint)
        layout.addWidget(self.preview_assets_group)

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

        self.translation_languages_group = QGroupBox()
        languages_form = QFormLayout(self.translation_languages_group)
        self.source_language = QLineEdit(self.settings.source_language)
        self.target_language = QLineEdit(self.settings.target_language)
        self.source_language_label = QLabel()
        self.target_language_label = QLabel()
        languages_form.addRow(self.source_language_label, self.source_language)
        languages_form.addRow(self.target_language_label, self.target_language)
        layout.addWidget(self.translation_languages_group)

        self.google_group = QGroupBox()
        google_form = QFormLayout(self.google_group)
        self.google_endpoint = QLineEdit(self.settings.google_endpoint)
        self.google_endpoint_label = QLabel()
        google_form.addRow(self.google_endpoint_label, self.google_endpoint)
        layout.addWidget(self.google_group)

        self.deepl_group = QGroupBox()
        deepl_form = QFormLayout(self.deepl_group)
        self.deepl_plan = QComboBox()
        self.deepl_key = QLineEdit(reveal_secret(self.settings.deepl_api_key_protected))
        self.deepl_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.deepl_plan_label = QLabel()
        self.deepl_key_label = QLabel()
        deepl_form.addRow(self.deepl_plan_label, self.deepl_plan)
        deepl_form.addRow(self.deepl_key_label, self.deepl_key)
        layout.addWidget(self.deepl_group)

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
        self.enable_chinese_codec = QCheckBox()
        self.enable_chinese_codec.setChecked(self.settings.enable_chinese_codec)
        save_layout.addWidget(self.enable_chinese_codec)
        self.codec_hint = QLabel()
        self.codec_hint.setObjectName("hint")
        self.codec_hint.setWordWrap(True)
        save_layout.addWidget(self.codec_hint)
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
        self.provider.addItem(translate("settings.group.deepl", locale=self._preview_language), "deepl")
        self.provider.addItem(translate("dialog.ai_service_openai", locale=self._preview_language).lstrip("✦ ").strip(), "openai")
        index = self.provider.findData(current)
        self.provider.setCurrentIndex(index if index >= 0 else 0)
        del blocker

    def _populate_deepl_plan_combo(self) -> None:
        current = str(self.deepl_plan.currentData() or self.settings.deepl_plan or "free")
        blocker = QSignalBlocker(self.deepl_plan)
        self.deepl_plan.clear()
        for value in ("free", "pro"):
            self.deepl_plan.addItem(
                translate(f"settings.deepl_plan.{value}", locale=self._preview_language),
                value,
            )
        index = self.deepl_plan.findData(current)
        self.deepl_plan.setCurrentIndex(index if index >= 0 else 0)
        del blocker

    def _populate_preview_scope_combo(self) -> None:
        current = str(self.preview_scope.currentData() or self.settings.preview_scope or "off")
        blocker = QSignalBlocker(self.preview_scope)
        self.preview_scope.clear()
        for value in ("off", "source", "translation", "all"):
            self.preview_scope.addItem(
                translate(f"settings.preview.{value}", locale=self._preview_language),
                value,
            )
        index = self.preview_scope.findData(current)
        self.preview_scope.setCurrentIndex(index if index >= 0 else 0)
        del blocker

    def _populate_theme_combo(self) -> None:
        current = str(self.ui_theme.currentData() or self.settings.ui_theme or "modern")
        blocker = QSignalBlocker(self.ui_theme)
        self.ui_theme.clear()
        for value in ("modern", "dark", "guild2"):
            self.ui_theme.addItem(
                translate(f"settings.theme.{value}", locale=self._preview_language),
                value,
            )
        index = self.ui_theme.findData(current)
        self.ui_theme.setCurrentIndex(index if index >= 0 else 0)
        del blocker

    def _on_language_changed(self) -> None:
        self._preview_language = str(self.ui_language.currentData() or self._preview_language)
        self._retranslate_ui()

    def _retranslate_ui(self) -> None:
        locale = self._preview_language
        self.setWindowTitle(translate("settings.title", locale=locale))
        self._populate_ui_language_combo()
        self._populate_theme_combo()
        self._populate_provider_combo()
        self._populate_deepl_plan_combo()
        self._populate_preview_scope_combo()

        self.tabs.setTabText(0, translate("settings.tab.general", locale=locale))
        self.tabs.setTabText(1, translate("settings.tab.translation", locale=locale))
        self.tabs.setTabText(2, translate("settings.tab.git", locale=locale))
        self.tabs.setTabText(3, translate("settings.tab.save", locale=locale))

        self.interface_group.setTitle(translate("settings.group.ui", locale=locale))
        self.service_group.setTitle(translate("settings.group.service", locale=locale))
        self.translation_languages_group.setTitle(translate("settings.group.languages", locale=locale))
        self.google_group.setTitle(translate("settings.group.google", locale=locale))
        self.deepl_group.setTitle(translate("settings.group.deepl", locale=locale))
        self.openai_group.setTitle(translate("settings.group.openai", locale=locale))
        self.git_group.setTitle(translate("settings.group.git", locale=locale))
        self.save_group.setTitle(translate("settings.group.save", locale=locale))
        self.preview_assets_group.setTitle(translate("settings.preview_assets_group", locale=locale))

        self.ui_language_label.setText(translate("settings.ui_language", locale=locale))
        self.ui_theme_label.setText(translate("settings.ui_theme", locale=locale))
        self.preview_scope_label.setText(translate("settings.preview_scope", locale=locale))
        self.preview_scope_hint.setText(translate("settings.preview_scope_hint", locale=locale))
        self.preview_translation_font_label.setText(translate("settings.preview_translation_font_dir", locale=locale))
        self.preview_ui_assets_label.setText(translate("settings.preview_ui_assets_dir", locale=locale))
        self.preview_game_font_in_editors.setText(
            translate("settings.preview_game_font_in_editors", locale=locale)
        )
        self.preview_use_code_context.setText(
            translate("settings.preview_use_code_context", locale=locale)
        )
        self.preview_window_scale_label.setText(
            translate("settings.preview_window_scale", locale=locale)
        )
        self.preview_assets_hint.setText(translate("settings.preview_assets_hint", locale=locale))
        for field in (
            self.preview_translation_font_dir,
            self.preview_ui_assets_dir,
        ):
            field.setPlaceholderText(translate("settings.preview_path_auto", locale=locale))
        for button in self.preview_path_buttons:
            button.setText(translate("settings.preview_path_browse", locale=locale))
        self.provider_label.setText(translate("settings.provider", locale=locale))
        self.google_endpoint_label.setText(translate("settings.endpoint", locale=locale))
        self.source_language_label.setText(translate("settings.source_language", locale=locale))
        self.target_language_label.setText(translate("settings.target_language", locale=locale))
        self.deepl_plan_label.setText(translate("settings.deepl_plan", locale=locale))
        self.deepl_key_label.setText(translate("settings.api_key", locale=locale))
        self.openai_base_url_label.setText(translate("settings.base_url", locale=locale))
        self.openai_model_label.setText(translate("settings.model", locale=locale))
        self.openai_key_label.setText(translate("settings.api_key", locale=locale))
        self.git_name_label.setText(translate("settings.author_name", locale=locale))
        self.git_email_label.setText(translate("settings.email", locale=locale))
        self.enable_chinese_codec.setText(translate("settings.enable_chinese_codec", locale=locale))
        self.codec_hint.setText(translate("settings.codec_hint", locale=locale))
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
        if self.provider.currentData() == "deepl":
            self.provider_note.setText(translate("settings.provider_note.deepl", locale=locale))
        elif self.provider.currentData() == "openai":
            self.provider_note.setText(translate("settings.provider_note.openai", locale=locale))
        else:
            self.provider_note.setText(translate("settings.provider_note.google", locale=locale))

    def _choose_preview_path(self, field: QLineEdit) -> None:
        current = Path(field.text().strip()).expanduser() if field.text().strip() else Path.home()
        if current.is_file():
            current = current.parent
        selected = QFileDialog.getExistingDirectory(
            self,
            translate("settings.preview_assets_group", locale=self._preview_language),
            str(current),
        )
        if selected:
            field.setText(selected)

    def result_settings(self) -> AppSettings:
        return replace(
            self.settings,
            ui_language=str(self.ui_language.currentData() or current_language()),
            ui_theme=str(self.ui_theme.currentData() or "modern"),
            provider=str(self.provider.currentData()),
            google_endpoint=self.google_endpoint.text().strip(),
            source_language=self.source_language.text().strip() or "en",
            target_language=self.target_language.text().strip() or "zh-CN",
            deepl_plan=str(self.deepl_plan.currentData() or "free"),
            deepl_api_key_protected=protect_secret(self.deepl_key.text().strip()),
            openai_base_url=self.openai_base_url.text().strip(),
            openai_model=self.openai_model.text().strip(),
            openai_api_key_protected=protect_secret(self.openai_key.text().strip()),
            git_author_name=self.git_name.text().strip() or "The Guild 2 Translator",
            git_author_email=self.git_email.text().strip() or "translator@local",
            enable_chinese_codec=self.enable_chinese_codec.isChecked(),
            auto_space_before_color_tokens_on_save=self.auto_space_before_color_tokens.isChecked(),
            preview_scope=str(self.preview_scope.currentData() or "off"),
            preview_translation_font_dir=self.preview_translation_font_dir.text().strip(),
            preview_ui_assets_dir=self.preview_ui_assets_dir.text().strip(),
            preview_game_font_in_editors=self.preview_game_font_in_editors.isChecked(),
            preview_use_code_context=self.preview_use_code_context.isChecked(),
            preview_window_scale_percent=int(
                self.preview_window_scale.currentData() or 100
            ),
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
        self._pending_chunks: list[str] = []
        self._recommended_translation = ""
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(40)
        self._render_timer.timeout.connect(self._flush_chunks)

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
        self._pending_chunks.append(chunk)
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _flush_chunks(self) -> None:
        if not self._pending_chunks:
            return
        self._markdown += "".join(self._pending_chunks)
        self._pending_chunks.clear()
        self.content.setMarkdown(self._markdown)
        self.content.verticalScrollBar().setValue(self.content.verticalScrollBar().maximum())

    def show_failure(self, message: str) -> None:
        self.loading_label.setText(translate("suggestion.error"))
        self.content.setPlainText(message)

    def complete(self) -> None:
        self._render_timer.stop()
        self._flush_chunks()
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
    def __init__(
        self,
        git: LanguageGit,
        parent: QWidget | None = None,
        *,
        focus_key: tuple[str, str, str, str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.git = git
        self._focus_key = focus_key
        self.setObjectName("historyDialog")
        self.setWindowTitle(translate("history.dialog.title", project=git.project_root.name, language=git.language))
        self.resize(1180, 720)
        layout = QHBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.left_tabs = QTabWidget()

        commit_page = QWidget()
        commit_column = QVBoxLayout(commit_page)
        commit_column.setContentsMargins(4, 4, 4, 4)
        self.commit_search = QLineEdit()
        self.commit_search.setClearButtonEnabled(True)
        self.commit_search.setPlaceholderText(translate("history.search.commits"))
        commit_column.addWidget(self.commit_search)
        selection_hint = QLabel(translate("history.selection_hint"))
        selection_hint.setObjectName("historyHint")
        selection_hint.setWordWrap(True)
        commit_column.addWidget(selection_hint)
        self.commits = QListWidget()
        self.commits.setObjectName("historyList")
        self.commits.setMinimumWidth(370)
        self.commits.setUniformItemSizes(True)
        self.commits.setSpacing(1)
        self.commits.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        commit_column.addWidget(self.commits, 1)
        self.left_tabs.addTab(commit_page, translate("history.tab.commits"))

        entry_page = QWidget()
        entry_column = QVBoxLayout(entry_page)
        entry_column.setContentsMargins(4, 4, 4, 4)
        self.entry_search = QLineEdit()
        self.entry_search.setClearButtonEnabled(True)
        self.entry_search.setPlaceholderText(translate("history.search.entries"))
        entry_column.addWidget(self.entry_search)
        self.entry_status = QLabel(translate("history.entry_index.idle"))
        self.entry_status.setWordWrap(True)
        entry_column.addWidget(self.entry_status)
        self.entries = QListWidget()
        self.entries.setObjectName("historyEntryList")
        self.entries.setMinimumWidth(370)
        self.entries.setSpacing(2)
        entry_column.addWidget(self.entries, 1)
        self.left_tabs.addTab(entry_page, translate("history.tab.entries"))
        splitter.addWidget(self.left_tabs)

        self.content = QTextBrowser()
        self.content.setObjectName("historyContent")
        self.content.setOpenExternalLinks(False)
        self.content.document().setDocumentMargin(14)
        splitter.addWidget(self.content)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)

        self._items: list[GitCommit] = []
        self._commit_blobs: dict[str, str] = {}
        self._events_by_key: dict[tuple[str, str, str, str], list[tuple[GitCommit, TranslationLogEntry]]] = {}
        self._entry_keys: list[tuple[str, str, str, str]] = []
        self._visible_entry_keys: list[tuple[str, str, str, str]] = []
        self._entry_blobs: dict[tuple[str, str, str, str], str] = {}
        self._indexed_change_count = 0
        self._index_limited = False
        self._request_id = 0
        self._selected_rows: tuple[int, ...] = ()
        self._rendered_rows: tuple[int, ...] = ()
        self._history_workers: set[HistoryRenderWorker] = set()
        self._index_request_id = 1
        self._index_cancel_event = threading.Event()
        self._index_worker: HistoryIndexWorker | None = None
        self._index_started = False
        self._selection_timer = QTimer(self)
        self._selection_timer.setSingleShot(True)
        self._selection_timer.setInterval(110)
        self._selection_timer.timeout.connect(self._load_selected_commits)
        try:
            self._items = git.list_all_commits()
            self.commits.addItems([commit.display for commit in self._items])
            self._commit_blobs = {commit.full_hash: commit_search_blob(commit) for commit in self._items}
        except GitError as exc:
            self.content.setHtml(_history_state_html(translate("history.read_error_title"), str(exc), kind="error"))
            self.entry_status.setText(str(exc))
        else:
            self.content.setHtml(
                _history_state_html(translate("history.initial_title"), translate("history.initial_detail"))
            )
        self.commits.itemSelectionChanged.connect(self._show_selected_commits)
        self.commit_search.textChanged.connect(self._on_commit_search_changed)
        self.entry_search.textChanged.connect(self._filter_entries)
        self.entries.itemSelectionChanged.connect(self._show_selected_entry)
        self.left_tabs.currentChanged.connect(self._on_history_tab_changed)
        if self._items:
            QTimer.singleShot(0, self._select_latest_commit)
        if focus_key is not None:
            self.left_tabs.setCurrentIndex(1)
            self._ensure_entry_index()

    @staticmethod
    def _matches_query(blob: str, query: str) -> bool:
        return all(part in blob for part in query.casefold().split())

    def _filter_commits(self, query: str) -> None:
        for row, commit in enumerate(self._items):
            item = self.commits.item(row)
            if item is not None:
                item.setHidden(not self._matches_query(self._commit_blobs.get(commit.full_hash, ""), query))

    def _on_commit_search_changed(self, query: str) -> None:
        self._filter_commits(query)
        if query.strip():
            self._ensure_entry_index()

    def _on_history_tab_changed(self, index: int) -> None:
        if index == 1:
            self._ensure_entry_index()

    def _filter_entries(self, query: str) -> None:
        matches = [
            key
            for key in self._entry_keys
            if self._matches_query(self._entry_blobs.get(key, ""), query)
        ]
        self._populate_entry_items(matches[:HISTORY_ENTRY_RESULT_LIMIT])
        if self._index_worker is None and self._index_started:
            if len(matches) > HISTORY_ENTRY_RESULT_LIMIT:
                self.entry_status.setText(
                    translate(
                        "history.entry_index.results_limited",
                        shown=HISTORY_ENTRY_RESULT_LIMIT,
                        total=len(matches),
                    )
                )
            else:
                status_key = "history.entry_index.limited" if self._index_limited else "history.entry_index.ready"
                self.entry_status.setText(
                    translate(status_key, entries=len(self._entry_keys), changes=self._indexed_change_count)
                )

    def _populate_entry_items(self, keys: Iterable[tuple[str, str, str, str]]) -> None:
        selected = list(keys)
        blocker = QSignalBlocker(self.entries)
        self.entries.clear()
        self._visible_entry_keys = selected
        for key in selected:
            events = self._events_by_key[key]
            entry = events[0][1]
            self.entries.addItem(
                translate(
                    "history.entry_list.item",
                    title=_history_entry_title(entry),
                    count=len(events),
                    meta=_history_entry_meta(entry),
                )
            )
        del blocker

    def _ensure_entry_index(self) -> None:
        if self._index_started or not self._items:
            return
        self._index_started = True
        self.entry_status.setText(translate("history.entry_index.starting"))
        worker = HistoryIndexWorker(
            self._index_request_id,
            self.git,
            tuple(self._items),
            self._index_cancel_event,
        )
        self._index_worker = worker
        worker.signals.progress.connect(self._apply_index_progress)
        worker.signals.ready.connect(self._apply_history_index)
        worker.signals.failed.connect(self._apply_index_error)
        QThreadPool.globalInstance().start(worker)

    def _apply_index_progress(self, current: int, total: int) -> None:
        self.entry_status.setText(translate("history.entry_index.progress", current=current, total=total))

    def _apply_history_index(self, request_id: int, result: object) -> None:
        if request_id != self._index_request_id or not isinstance(result, dict):
            return
        raw_events = result.get("events")
        if not isinstance(raw_events, list):
            return
        limited = bool(result.get("limited"))
        self._index_worker = None
        events_by_commit: dict[str, list[TranslationLogEntry]] = {}
        for event in raw_events:
            if not isinstance(event, tuple) or len(event) != 2:
                continue
            commit, entry = event
            if not isinstance(commit, GitCommit) or not isinstance(entry, TranslationLogEntry):
                continue
            self._events_by_key.setdefault(entry.change_key, []).append((commit, entry))
            events_by_commit.setdefault(commit.full_hash, []).append(entry)
        for commit in self._items:
            self._commit_blobs[commit.full_hash] = commit_search_blob(
                commit,
                events_by_commit.get(commit.full_hash, ()),
            )
        self._entry_keys = sorted(
            self._events_by_key,
            key=lambda key: (
                _history_entry_title(self._events_by_key[key][0][1]).casefold(),
                _history_entry_meta(self._events_by_key[key][0][1]).casefold(),
            ),
        )
        self._entry_blobs.clear()
        for key in self._entry_keys:
            events = self._events_by_key[key]
            self._entry_blobs[key] = "\n".join(
                [entry_search_blob(change) for _commit, change in events]
                + [commit_search_blob(commit) for commit, _change in events]
            )
        self._indexed_change_count = len(raw_events)
        self._index_limited = limited
        self._filter_commits(self.commit_search.text())
        self._filter_entries(self.entry_search.text())
        if self._focus_key is not None:
            self._select_entry_key(self._focus_key)

    def _apply_index_error(self, request_id: int, message: str) -> None:
        if request_id != self._index_request_id:
            return
        self._index_worker = None
        self._index_started = False
        self.entry_status.setText(translate("history.entry_index.failed", error=message))

    def _select_entry_key(self, key: tuple[str, str, str, str]) -> None:
        try:
            selected_key = self._entry_keys[self._entry_keys.index(key)]
        except ValueError:
            candidates = [
                index
                for index, candidate in enumerate(self._entry_keys)
                if candidate[:3] == key[:3]
            ]
            if not candidates:
                self.entry_status.setText(translate("history.entry_index.not_found"))
                return
            selected_key = self._entry_keys[candidates[0]]
        if selected_key not in self._visible_entry_keys:
            keys = [selected_key]
            keys.extend(candidate for candidate in self._entry_keys if candidate != selected_key)
            self._populate_entry_items(keys[:HISTORY_ENTRY_RESULT_LIMIT])
        row = self._visible_entry_keys.index(selected_key)
        self.entries.setCurrentRow(row)
        item = self.entries.item(row)
        if item is not None:
            item.setSelected(True)
            self.entries.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)

    def _show_selected_entry(self) -> None:
        row = self.entries.currentRow()
        if row < 0 or row >= len(self._visible_entry_keys):
            return
        self.content.setHtml(render_entry_timeline_html(self._events_by_key[self._visible_entry_keys[row]]))

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

    def closeEvent(self, event) -> None:  # noqa: N802
        self._selection_timer.stop()
        self._index_cancel_event.set()
        self._index_request_id += 1
        super().closeEvent(event)


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
        if self.game_root is not None:
            discovered_translation = self.game_root / "Textures" / "Hud" / "chinese"
            discovered_ui = self.game_root / "Textures" / "Hud"
            updated = self.settings
            if (
                not updated.preview_translation_font_dir
                and (discovered_translation / "Sets.dat").is_file()
            ):
                updated = replace(
                    updated,
                    preview_translation_font_dir=str(discovered_translation),
                )
            if not updated.preview_ui_assets_dir and (discovered_ui / "Sets.dat").is_file():
                updated = replace(updated, preview_ui_assets_dir=str(discovered_ui))
            if updated != self.settings:
                self.settings = updated
                save_settings(self.settings)
        self.preview_service = PreviewService(
            self.game_root,
            translation_font_dir=self.settings.preview_translation_font_dir,
            ui_assets_dir=self.settings.preview_ui_assets_dir,
        )
        self.git: LanguageGit | None = None
        self.git_ready = False
        self.git_pending = False
        self._git_pending_forced = False
        self._git_init_failed = False
        self._git_init_token = 0
        self._git_init_workers: list[GitInitWorker] = []
        self.project: Project | None = None
        self.model = UnitTableModel()
        self.proxy = UnitFilterProxyModel()
        self.proxy.setSourceModel(self.model)
        self.history = OperationHistory()
        self.current_uid = ""
        self._filter_anchor_uid = ""
        self._game_preview_cache: dict[tuple[object, ...], QImage] = {}
        self.last_applied_query = ""
        self.loading_editor = False
        self.typing_uid = ""
        self.typing_before = ""
        self.typing_before_deleted = False
        self.editor_zoom_steps = self.settings.editor_zoom_steps
        self.typing_timer = QTimer(self)
        self.typing_timer.setSingleShot(True)
        self.typing_timer.setInterval(TYPING_GROUP_DELAY_MS)
        self.typing_timer.timeout.connect(self._commit_typing_operation)
        self.recovery_timer = QTimer(self)
        self.recovery_timer.setSingleShot(True)
        self.recovery_timer.setInterval(800)
        self.recovery_timer.timeout.connect(self._write_recovery_snapshot)
        self._recovery_warning_shown = False
        self.counts_refresh_timer = QTimer(self)
        self.counts_refresh_timer.setSingleShot(True)
        self.counts_refresh_timer.setInterval(50)
        self.counts_refresh_timer.timeout.connect(self._update_counts)
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
        app = QApplication.instance()
        theme_qss = app.property("gameThemeQss") if app is not None else None
        if isinstance(theme_qss, str):
            self.setStyleSheet(theme_qss)
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(9)

        titlebar = GameHeaderFrame()
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
        self.reset_sort_button = QToolButton()
        self.reset_sort_button.clicked.connect(self._reset_table_sort)
        self.reset_sort_button.hide()
        toolbar_layout.addWidget(self.reset_sort_button)
        toolbar_layout.addStretch(1)
        self.search_label = QLabel()
        toolbar_layout.addWidget(self.search_label)
        self.search_edit = SearchLineEdit()
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumWidth(240)
        self.search_edit.case_sensitive_toggled.connect(self._on_search_case_toggled)
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
        for key, slot, primary in (("button.save", self.save_all, True),):
            button = QPushButton()
            button.setProperty("text_key", key)
            if primary:
                button.setObjectName("primary")
            button.clicked.connect(slot)
            title_layout.addWidget(button)
            self.top_buttons.append(button)
        self.more_button = QToolButton()
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_menu = QMenu(self.more_button)
        self.more_actions: list[QAction] = []
        for key, slot in (
            ("button.import_text", self.show_text_import),
            ("button.history", self.show_history),
            ("button.settings", self.show_settings),
        ):
            action = self.more_menu.addAction("")
            action.setProperty("text_key", key)
            action.triggered.connect(slot)
            self.more_actions.append(action)
        self.more_button.setMenu(self.more_menu)
        title_layout.addWidget(self.more_button)
        self.retry_button = QToolButton()
        self.retry_button.clicked.connect(self.retry_commit)
        self.retry_button.setVisible(False)
        title_layout.addWidget(self.retry_button)

        counts_row = QHBoxLayout()
        counts_row.setContentsMargins(0, 0, 0, 0)
        counts_row.setSpacing(8)
        self.counts_label = QLabel()
        self.counts_label.setObjectName("counts")
        counts_row.addWidget(self.counts_label, 1)
        self.review_attention_button = QPushButton()
        self.review_attention_button.setObjectName("reviewAttention")
        self.review_attention_button.clicked.connect(self._show_review_attention)
        self.review_attention_button.hide()
        counts_row.addWidget(self.review_attention_button)
        layout.addLayout(counts_row)

        self.main_splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(self.main_splitter, 1)
        self.table_frame = GamePanelFrame()
        self.table_frame.setObjectName("tablePanel")
        table_layout = QVBoxLayout(self.table_frame)
        self.table_layout = table_layout
        table_layout.setContentsMargins(0, 0, 0, 0)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(False)
        self.table.setSortingEnabled(True)
        self.proxy.sort(-1)
        self.table.horizontalHeader().setSortIndicatorShown(False)
        self.table.horizontalHeader().sortIndicatorChanged.connect(self._on_table_sort_changed)
        self.table.setWordWrap(False)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_table_menu)
        self.table.installEventFilter(self)
        self.table.viewport().installEventFilter(self)
        self.table_tooltip_filter = DelayedToolTipFilter(
            self.table.viewport(),
            700,
            self._table_tooltip_content,
        )
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
        self.source_preview_delegate = PreviewTextDelegate(
            self.table,
            target=False,
            enabled=lambda: self._table_preview_enabled(False),
            render_preview=self._render_unit_preview,
            glyph_image=self.preview_service.glyph_image,
        )
        self.translation_preview_delegate = PreviewTextDelegate(
            self.table,
            target=True,
            enabled=lambda: self._table_preview_enabled(True),
            render_preview=self._render_unit_preview,
            glyph_image=self.preview_service.glyph_image,
        )
        self.table.setItemDelegateForColumn(UnitTableModel.SOURCE, self.source_preview_delegate)
        self.table.setItemDelegateForColumn(UnitTableModel.TRANSLATION, self.translation_preview_delegate)
        table_layout.addWidget(self.table)
        self.main_splitter.addWidget(self.table_frame)

        self.editors_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.source_box, self.source_edit, self.source_preview_button = self._editor_group(True)
        self.translation_box, self.translation_edit, self.translation_preview_button = self._editor_group(False)
        self.source_box.code_button.show()
        self.source_box.reference_label.show()
        self.source_code_button = self.source_box.code_button
        self.code_reference_label = self.source_box.reference_label
        self.code_reference_popup = QListWidget()
        self.code_reference_popup.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.code_reference_popup.setMouseTracking(True)
        self.code_reference_popup.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.code_reference_popup.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.code_reference_popup.installEventFilter(self)
        self.code_reference_popup.viewport().installEventFilter(self)
        self.code_reference_popup.itemClicked.connect(self._open_code_reference_item)
        self.source_code_button.installEventFilter(self)
        self.code_button_hold_timer = QTimer(self)
        self.code_button_hold_timer.setSingleShot(True)
        self.code_button_hold_timer.setInterval(260)
        self.code_button_hold_timer.timeout.connect(self._show_code_reference_popup)
        self.code_reference_index: CodeReferenceIndex | None = None
        self.code_reference_index_complete = False
        self.code_reference_index_token = 0
        self.code_reference_workers: list[CodeIndexWorker] = []
        self.code_index_visible_timer = QTimer(self)
        self.code_index_visible_timer.setSingleShot(True)
        self.code_index_visible_timer.setInterval(90)
        self.code_index_visible_timer.timeout.connect(self._request_visible_code_contexts)
        self.table.verticalScrollBar().valueChanged.connect(
            lambda _value: self.code_index_visible_timer.start()
        )
        self.source_preview_button.toggled.connect(
            lambda checked: self._on_editor_preview_toggled(False, checked)
        )
        self.translation_preview_button.toggled.connect(
            lambda checked: self._on_editor_preview_toggled(True, checked)
        )
        self.game_preview_popup = GamePreviewPopup()
        self.source_preview_tooltip_filter = GamePreviewHoverFilter(
            self.source_preview_button,
            self.game_preview_popup,
            lambda: self._game_preview_image(False),
        )
        self.translation_preview_tooltip_filter = GamePreviewHoverFilter(
            self.translation_preview_button,
            self.game_preview_popup,
            lambda: self._game_preview_image(True),
        )
        self.source_edit.set_preview_builder(
            lambda text: self._render_editor_preview(text, False),
            lambda glyph_id: self.preview_service.glyph_image(glyph_id, False),
        )
        self.translation_edit.set_preview_builder(
            lambda text: self._render_editor_preview(text, True),
            lambda glyph_id: self.preview_service.glyph_image(glyph_id, True),
        )
        self.source_edit.set_game_font_builder(
            self.settings.preview_game_font_in_editors,
            lambda char, color: self.preview_service.text_glyph_image(char, False, color),
            lambda: self.preview_service.text_font_family(False),
        )
        self.translation_edit.set_game_font_builder(
            self.settings.preview_game_font_in_editors,
            lambda char, color: self.preview_service.text_glyph_image(char, True, color),
            lambda: self.preview_service.text_font_family(True),
        )
        self.translation_edit.use_application_undo_history()
        self.source_edit.installEventFilter(self)
        self.source_edit.viewport().installEventFilter(self)
        self.translation_edit.installEventFilter(self)
        self.translation_edit.viewport().installEventFilter(self)
        self.translation_edit.textChanged.connect(self._on_editor_changed)
        self.source_edit.previewRendered.connect(self._refresh_editor_highlights)
        self.translation_edit.previewRendered.connect(self._refresh_editor_highlights)
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
        self._apply_theme_layout()
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
        if watched is getattr(self, "source_code_button", None):
            return self._handle_code_button_event(event)
        code_popup = getattr(self, "code_reference_popup", None)
        try:
            code_popup_viewport = code_popup.viewport() if code_popup is not None else None
        except RuntimeError:
            # Qt can delete the popup's C++ object before the last shutdown
            # events reach this Python event filter.
            code_popup = None
            code_popup_viewport = None
        if watched is code_popup or watched is code_popup_viewport:
            return self._handle_code_popup_event(event)
        editor = self._watched_editor(watched)
        if isinstance(editor, PreviewPlainTextEdit):
            if event.type() == QEvent.Type.ShortcutOverride and isinstance(event, QKeyEvent):
                if (
                    editor is self.translation_edit
                    and event.matches(QKeySequence.StandardKey.Paste)
                    and QApplication.clipboard().mimeData().hasFormat(ENTRY_CLIPBOARD_MIME)
                ):
                    event.accept()
                    return True
                if event.matches(QKeySequence.StandardKey.Undo) or event.matches(QKeySequence.StandardKey.Redo) or self._is_ctrl_shift_z(event):
                    event.accept()
                    return True
            if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
                if (
                    editor is self.translation_edit
                    and event.matches(QKeySequence.StandardKey.Paste)
                    and QApplication.clipboard().mimeData().hasFormat(ENTRY_CLIPBOARD_MIME)
                ):
                    self._paste_unit_translations()
                    return True
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
        if isinstance(table, QTableView) and (watched is table or watched is table.viewport()):
            if watched is table.viewport() and event.type() == QEvent.Type.Resize:
                visible_timer = getattr(self, "code_index_visible_timer", None)
                if isinstance(visible_timer, QTimer):
                    visible_timer.start()
            if event.type() == QEvent.Type.ShortcutOverride and isinstance(event, QKeyEvent):
                if event.matches(QKeySequence.StandardKey.Copy) or event.matches(QKeySequence.StandardKey.Paste):
                    event.accept()
                    return True
            if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
                if event.matches(QKeySequence.StandardKey.Copy):
                    self._copy_unit_entries(self._selected_units())
                    return True
                if event.matches(QKeySequence.StandardKey.Paste):
                    self._paste_unit_translations()
                    return True
            if watched is not table.viewport():
                return super().eventFilter(watched, event)
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

    def _watched_editor(self, watched: QObject) -> PreviewPlainTextEdit | None:
        for editor in self._editor_widgets():
            if watched is editor or watched is editor.viewport():
                return editor
        return None

    def _editor_widgets(self) -> tuple[PreviewPlainTextEdit, ...]:
        editors: list[PreviewPlainTextEdit] = []
        source = getattr(self, "source_edit", None)
        translation = getattr(self, "translation_edit", None)
        if isinstance(source, PreviewPlainTextEdit):
            editors.append(source)
        if isinstance(translation, PreviewPlainTextEdit):
            editors.append(translation)
        return tuple(editors)

    def _apply_editor_zoom(self) -> None:
        factor = max(0.2, 1.0 + self.editor_zoom_steps * 0.1)
        for editor in self._editor_widgets():
            editor.set_zoom_factor(factor)
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

    def _editor_group(self, read_only: bool) -> tuple[QGroupBox, PreviewPlainTextEdit, QToolButton]:
        box = EditorGroupBox()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 12, 8, 8)
        editor = PreviewPlainTextEdit()
        editor.setReadOnly(read_only)
        editor.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        layout.addWidget(editor)
        return box, editor, box.preview_button

    def _apply_theme_layout(self) -> None:
        app = QApplication.instance()
        game_theme = app is not None and app.property("guild2Theme") is True
        margin = 7 if game_theme else 0
        self.table_layout.setContentsMargins(margin, margin, margin, margin)

    def _start_code_reference_index(self) -> None:
        for stale_worker in self.code_reference_workers:
            stale_worker.cancel()
        self.code_reference_workers.clear()
        self.code_reference_index = CodeReferenceIndex()
        self.code_reference_index_complete = False
        self.code_reference_index_token += 1
        token = self.code_reference_index_token
        self._update_code_reference_display()
        worker = CodeIndexWorker(token, self.game_root, self.project_root)
        worker.signals.partial.connect(self._code_reference_index_partial)
        worker.signals.finished.connect(self._code_reference_index_finished)
        worker.signals.failed.connect(self._code_reference_index_failed)
        self.code_reference_workers.append(worker)
        self.thread_pool.start(worker)
        unit = self._current_unit()
        if unit is not None:
            self._request_code_context_for_unit(unit)
        self.code_index_visible_timer.start()

    def _code_reference_index_partial(
        self,
        token: int,
        index: object,
        progress: object,
    ) -> None:
        if token != self.code_reference_index_token or not isinstance(index, CodeReferenceIndex):
            return
        if self.code_reference_index is None:
            self.code_reference_index = CodeReferenceIndex()
        self.code_reference_index.merge(index)
        if isinstance(progress, LazyIndexProgress):
            self.code_reference_index_complete = progress.complete
        self._update_code_reference_display()
        self._refresh_preview_presentations()

    def _code_reference_index_finished(self, token: int) -> None:
        self.code_reference_workers = [
            worker for worker in self.code_reference_workers if worker.token != token
        ]
        if token != self.code_reference_index_token:
            return
        self.code_reference_index_complete = True
        if isinstance(self.code_reference_index, CodeReferenceIndex):
            log_metrics(
                "preview_reference_coverage",
                **preview_reference_coverage(self.code_reference_index).metrics(),
            )
            log_metrics(
                "preview_placeholder_coverage",
                **preview_placeholder_coverage(
                    self.code_reference_index,
                    (
                        (unit.label, unit.source_text)
                        for unit in self.model.units
                        if unit.label and unit.source_text
                    ),
                ).metrics(),
            )
        self._update_code_reference_display()
        self._refresh_preview_presentations()

    def _code_reference_index_ready(self, token: int, index: object) -> None:
        """Compatibility entry point for stale-worker cleanup tests and old signals."""
        if isinstance(index, CodeReferenceIndex):
            TranslatorWindow._code_reference_index_partial(
                self,
                token,
                index,
                LazyIndexProgress(0, 0, True),
            )
        TranslatorWindow._code_reference_index_finished(self, token)

    def _code_reference_index_failed(self, token: int, message: str) -> None:
        self.code_reference_workers = [
            worker for worker in self.code_reference_workers if worker.token != token
        ]
        if token != self.code_reference_index_token:
            return
        self.code_reference_index = CodeReferenceIndex()
        self.code_reference_index_complete = True
        self._update_code_reference_display()
        self.statusBar().showMessage(translate("status.code_index_failed", error=message), 4000)

    def _request_code_context_for_unit(self, unit: TranslationUnit) -> None:
        if unit.ref.kind != "dbt" or not unit.label or not self.code_reference_workers:
            return
        self.code_reference_workers[-1].request_labels((unit.label,), 0)

    def _request_visible_code_contexts(self) -> None:
        if (
            not self.code_reference_workers
            or not self.table_frame.isVisible()
            or self.proxy.rowCount() <= 0
        ):
            return
        viewport = self.table.viewport()
        first = self.table.rowAt(0)
        last = self.table.rowAt(max(0, viewport.height() - 1))
        if first < 0:
            first = 0
        if last < first:
            visible_count = max(1, viewport.height() // max(1, self.table.verticalHeader().defaultSectionSize()))
            last = min(self.proxy.rowCount() - 1, first + visible_count - 1)
        margin = max(1, last - first + 1)
        start = max(0, first - margin)
        stop = min(self.proxy.rowCount(), last + margin + 1)
        labels: list[str] = []
        for row in range(start, stop):
            unit = self._unit_from_proxy_index(self.proxy.index(row, 0))
            if unit is not None and unit.ref.kind == "dbt" and unit.label:
                labels.append(unit.label)
        self.code_reference_workers[-1].request_labels(labels, 1)

    def _current_code_reference_set(self) -> CodeReferenceSet:
        unit = self._current_unit()
        if unit is None or not unit.label or unit.ref.kind != "dbt" or self.code_reference_index is None:
            return CodeReferenceSet()
        return self.code_reference_index.references_for(unit.label)

    def _code_references_for_unit(self, unit: TranslationUnit) -> tuple[CodeReference, ...]:
        if not self.settings.preview_use_code_context:
            return ()
        if not unit.label or unit.ref.kind != "dbt" or self.code_reference_index is None:
            return ()
        return rank_preview_references(
            unit.source_text,
            self.code_reference_index.references_for(unit.label).active,
            unit.label,
        )

    def _current_ranked_code_references(self) -> tuple[CodeReference, ...]:
        unit = self._current_unit()
        if unit is None:
            return ()
        references = self._current_code_reference_set().active
        return rank_preview_references(unit.source_text, references, unit.label)

    def _project_is_mod(self) -> bool:
        return self.project_root is not None and self.project_root.name.casefold() != VANILLA_PROJECT_NAME.casefold()

    def _update_code_reference_display(self) -> None:
        if not hasattr(self, "code_reference_label"):
            return
        unit = self._current_unit()
        if unit is None or unit.ref.kind != "dbt":
            self.code_reference_label.setText("")
            self.source_code_button.setEnabled(False)
            if isinstance(self.source_box, EditorGroupBox):
                self.source_box.position_preview_button()
            return
        if self.code_reference_index is None:
            self.code_reference_label.setText(translate("code.references.loading"))
            self.source_code_button.setEnabled(False)
        else:
            references = self._current_code_reference_set()
            if references.project_count:
                self.code_reference_label.setText(translate("code.references.count", count=references.project_count))
                self.source_code_button.setEnabled(True)
            elif self._project_is_mod() and references.vanilla_count:
                self.code_reference_label.setText(translate("code.references.vanilla_count", count=references.vanilla_count))
                self.source_code_button.setEnabled(True)
            elif self.code_reference_index_complete:
                self.code_reference_label.setText(translate("code.references.zero"))
                self.source_code_button.setEnabled(False)
            else:
                self.code_reference_label.setText(translate("code.references.loading"))
                self.source_code_button.setEnabled(False)
        self.source_code_button.setToolTip(self.code_reference_label.text())
        if isinstance(self.source_box, EditorGroupBox):
            self.source_box.position_preview_button()

    def _handle_code_button_event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            references = self._current_ranked_code_references()
            if not references:
                return True
            self.code_button_hold_timer.stop()
            if len(references) > 1:
                self.code_button_hold_timer.start()
            return True
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            if self.code_button_hold_timer.isActive():
                self.code_button_hold_timer.stop()
                self._open_first_code_reference()
            elif self.code_reference_popup.isVisible():
                self._open_code_reference_under_cursor()
                self.code_reference_popup.hide()
            else:
                self._open_first_code_reference()
            return True
        return False

    def _handle_code_popup_event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.MouseMove:
            item = self.code_reference_popup.itemAt(event.position().toPoint())
            self.code_reference_popup.setCurrentItem(item)
            return False
        if event.type() == QEvent.Type.Leave:
            self.code_reference_popup.setCurrentItem(None)
            return False
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            item = self.code_reference_popup.itemAt(event.position().toPoint())
            self.code_reference_popup.hide()
            if item is not None:
                self._open_code_reference_item(item)
            return True
        return False

    def _open_code_reference_under_cursor(self) -> None:
        popup = self.code_reference_popup
        point = popup.viewport().mapFromGlobal(QCursor.pos())
        item = popup.itemAt(point)
        if item is not None:
            self._open_code_reference_item(item)

    def _open_code_reference_item(self, item: QListWidgetItem) -> None:
        reference = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(reference, CodeReference):
            self._open_code_reference(reference)

    def _show_code_reference_popup(self) -> None:
        references = self._current_ranked_code_references()
        if len(references) <= 1:
            return
        self.code_reference_popup.clear()
        for reference in references:
            item = QListWidgetItem(reference.display_name)
            item.setToolTip(str(reference.path))
            item.setData(Qt.ItemDataRole.UserRole, reference)
            self.code_reference_popup.addItem(item)
        screen = self.source_code_button.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else self.geometry()
        content_width = max(
            self.code_reference_popup.fontMetrics().horizontalAdvance(self.code_reference_popup.item(row).text())
            for row in range(self.code_reference_popup.count())
        )
        width = min(max(220, content_width + 34), max(220, available.width() - 36))
        row_heights = [
            max(24, self.code_reference_popup.sizeHintForRow(row))
            for row in range(self.code_reference_popup.count())
        ]
        margins = self.code_reference_popup.contentsMargins()
        desired_height = (
            sum(row_heights)
            + max(0, self.code_reference_popup.count() - 1) * self.code_reference_popup.spacing()
            + margins.top()
            + margins.bottom()
            + self.code_reference_popup.frameWidth() * 2
            + 18
        )
        button_top = self.source_code_button.mapToGlobal(QPoint(0, 0)).y()
        space_above = max(0, button_top - available.top() - 8)
        space_below = max(0, available.bottom() - button_top - self.source_code_button.height() - 8)
        popup_above = space_above >= min(desired_height, 180) or space_above >= space_below
        max_height = max(80, space_above if popup_above else space_below)
        height = min(desired_height, max_height)
        self.code_reference_popup.setFixedSize(width, height)
        point = self.source_code_button.mapToGlobal(
            QPoint(0, -height - 4) if popup_above else QPoint(0, self.source_code_button.height() + 4)
        )
        point.setX(min(max(available.left() + 8, point.x()), available.right() - width - 8))
        self.code_reference_popup.move(point)
        self.code_reference_popup.show()
        self.code_reference_popup.raise_()

    def _open_first_code_reference(self) -> None:
        references = self._current_ranked_code_references()
        if references:
            self._open_code_reference(references[0])

    def _open_code_reference(self, reference: CodeReference) -> None:
        if open_code_reference(reference):
            self.statusBar().showMessage(
                translate("status.code_reference_opened", file=reference.path.name, line=reference.line),
                2500,
            )
        else:
            self.statusBar().showMessage(
                translate("status.code_reference_open_failed", file=str(reference.path)),
                4000,
            )

    def _table_tooltip_content(self, point: QPoint) -> tuple[object, str, object] | None:
        index = self.table.indexAt(point)
        if not index.isValid():
            return None
        if index.column() in {UnitTableModel.SOURCE, UnitTableModel.TRANSLATION}:
            unit = self._unit_from_proxy_index(index)
            if unit is None:
                return None
            target = index.column() == UnitTableModel.TRANSLATION
            text = self.preview_service.tooltip_html(
                self._render_unit_preview(unit, target),
                target=target,
            )
        else:
            text = str(index.data(Qt.ItemDataRole.ToolTipRole) or "")
        if not text:
            return None
        return (index.row(), index.column()), text, self.table.visualRect(index)

    def _table_preview_enabled(self, target: bool) -> bool:
        scope = self.settings.preview_scope
        return scope == "all" or scope == ("translation" if target else "source")

    def _render_unit_preview(self, unit: TranslationUnit, target: bool) -> PreviewDocument:
        return self.preview_service.render(
            unit.current_text if target else unit.source_text,
            unit_key=unit.uid,
            label=unit.label,
            file_rel=unit.file_rel,
            kind=unit.ref.kind,
            target=target,
            references=self._code_references_for_unit(unit),
        )

    def _render_editor_preview(self, text: str, target: bool) -> PreviewDocument:
        unit = self._current_unit()
        if unit is None:
            atoms = [PreviewAtom(text, 0, len(text))] if text else []
            return PreviewDocument.from_atoms(text, atoms)
        return self.preview_service.render(
            text,
            unit_key=unit.uid,
            label=unit.label,
            file_rel=unit.file_rel,
            kind=unit.ref.kind,
            target=target,
            references=self._code_references_for_unit(unit),
        )

    def _on_editor_preview_toggled(self, target: bool, checked: bool) -> None:
        if target:
            self._commit_typing_operation()
            editor = self.translation_edit
        else:
            editor = self.source_edit
        editor.set_preview_enabled(checked)
        self._update_preview_tooltips()
        self._refresh_editor_highlights()

    def _update_preview_tooltips(self) -> None:
        unit = self._current_unit()
        for target, button in (
            (False, self.source_preview_button),
            (True, self.translation_preview_button),
        ):
            if button.isChecked():
                button.setToolTip("")
                continue
            if unit is None:
                button.setToolTip(translate("editor.preview_empty"))
                continue
            document = self._render_unit_preview(unit, target)
            button.setToolTip(self.preview_service.tooltip_html(document, target=target))

    def _game_preview_image(self, target: bool) -> QImage | None:
        unit = self._current_unit()
        if unit is None:
            return None
        context, header_unit, body_unit, button_units, context_references = self._game_preview_parts(unit)
        cache_key = (
            target,
            unit.uid,
            context,
            context_references,
            header_unit.uid if header_unit is not None else "",
            header_unit.current_text if target and header_unit is not None else (
                header_unit.source_text if header_unit is not None else ""
            ),
            body_unit.uid if body_unit is not None else "",
            body_unit.current_text if target and body_unit is not None else (
                body_unit.source_text if body_unit is not None else ""
            ),
            tuple(
                (
                    button.uid if isinstance(button, TranslationUnit) else "",
                    button.current_text if target and isinstance(button, TranslationUnit) else (
                        button.source_text if isinstance(button, TranslationUnit) else str(button)
                    ),
                )
                for button in button_units
            ),
            self.settings.preview_window_scale_percent,
        )
        cached = self._game_preview_cache.get(cache_key)
        if cached is not None:
            return cached

        def render(candidate: TranslationUnit | str | None) -> PreviewDocument | None:
            if candidate is None:
                return None
            if isinstance(candidate, str):
                return self.preview_service.render(
                    candidate,
                    unit_key=f"{unit.uid}:button:{candidate}",
                    label=unit.label,
                    file_rel=unit.file_rel,
                    kind=unit.ref.kind,
                    target=target,
                    references=context_references or self._code_references_for_unit(unit),
                )
            text = candidate.current_text if target else candidate.source_text
            if target and not text:
                text = candidate.source_text
            return self.preview_service.render(
                text,
                unit_key=candidate.uid,
                label=candidate.label,
                file_rel=candidate.file_rel,
                kind=candidate.ref.kind,
                target=target,
                references=context_references or self._code_references_for_unit(candidate),
            )

        image = self.preview_service.game_window_image(
            render(header_unit),
            render(body_unit),
            target=target,
            context=context,
            buttons=tuple(document for button in button_units if (document := render(button)) is not None),
            button_assets=(
                tuple(button.icon_asset for button in context.buttons)
                if context is not None
                else ()
            ),
            output_scale=self.settings.preview_window_scale_percent / 100.0,
        )
        if len(self._game_preview_cache) >= 24:
            self._game_preview_cache.clear()
        self._game_preview_cache[cache_key] = image
        return image

    def _game_preview_parts(
        self,
        unit: TranslationUnit,
    ) -> tuple[
        PreviewWindowContext | None,
        TranslationUnit | None,
        TranslationUnit | None,
        tuple[TranslationUnit | str, ...],
        tuple[CodeReference, ...],
    ]:
        selection = select_preview_context(
            str(getattr(unit, "source_text", "") or ""),
            self._code_references_for_unit(unit),
            unit.label,
        )
        context = selection.window
        if context is None:
            header_unit, body_unit = self._paired_preview_units(unit)
            context = engine_window_context(unit.label)
            if context is not None:
                context = replace(
                    context,
                    header_label=(
                        normalize_label(header_unit.label)
                        if header_unit is not None
                        else context.header_label
                    ),
                    body_label=(
                        normalize_label(body_unit.label)
                        if body_unit is not None
                        else context.body_label
                    ),
                )
            elif TranslatorWindow._is_name_tooltip_pair(header_unit, body_unit):
                context = surface_window_context(
                    engine_pair_preview_surface(unit.label),
                    header_label=normalize_label(header_unit.label) if header_unit is not None else "",
                    body_label=normalize_label(body_unit.label) if body_unit is not None else "",
                )
            return context, header_unit, body_unit, (), selection.references
        header_unit = self._unit_for_context_label(unit, context.header_label)
        body_unit = self._unit_for_context_label(unit, context.body_label)
        button_units: list[TranslationUnit | str] = []
        for button in context.buttons:
            candidate = self._unit_for_context_label(unit, button.label)
            if candidate is not None:
                button_units.append(candidate)
            elif button.text:
                button_units.append(button.text)
            elif button.identifier:
                button_units.append(button.identifier)
        if header_unit is None and body_unit is None and not button_units:
            header_unit, body_unit = self._paired_preview_units(unit)
        if header_unit is None and body_unit is None:
            body_unit = unit
        return context, header_unit, body_unit, tuple(button_units), selection.references

    def _unit_for_context_label(
        self,
        current_unit: TranslationUnit,
        label: str,
    ) -> TranslationUnit | None:
        normalized_current = normalize_label(current_unit.label).lstrip("_")
        normalized_label = normalize_label(label).lstrip("_")
        if normalized_current == normalized_label:
            return current_unit
        if "*" in normalized_label:
            pattern = "^" + re.escape(normalized_label).replace("\\*", "[a-z0-9_]+") + "$"
            if re.match(pattern, normalized_current):
                return current_unit
        return self._unit_for_normalized_label(current_unit.file_rel, label)

    def _unit_for_normalized_label(self, file_rel: str, label: str) -> TranslationUnit | None:
        if not label:
            return None
        labels = [label]
        if label.startswith("_"):
            labels.append(label[1:])
        else:
            labels.append("_" + label)
        wildcard_patterns = [
            re.compile("^" + re.escape(candidate).replace("\\*", "[a-z0-9_]+") + "$")
            for candidate in labels
            if "*" in candidate
        ]
        label_groups = {group for candidate in labels if (group := label_group_key(candidate)) is not None}
        fallback: TranslationUnit | None = None
        for candidate in TranslatorWindow._preview_units_for_file(self, file_rel):
            normalized = normalize_label(candidate.label)
            if normalized in labels:
                return candidate
            if any(pattern.match(normalized) for pattern in wildcard_patterns):
                return candidate
            if fallback is None and label_group_key(normalized) in label_groups:
                fallback = candidate
        if fallback is not None:
            return fallback
        for candidate in self.model.units:
            normalized = normalize_label(candidate.label)
            if normalized in labels:
                return candidate
            if any(pattern.match(normalized) for pattern in wildcard_patterns):
                return candidate
            if fallback is None and label_group_key(normalized) in label_groups:
                fallback = candidate
        if fallback is not None:
            return fallback
        return None

    def _paired_preview_units(
        self,
        unit: TranslationUnit,
    ) -> tuple[TranslationUnit | None, TranslationUnit | None]:
        onscreen = re.match(r"^(.*?)(NAME|DESCRIPTION|TOOLTIP)(_[+]\d+)?$", unit.label, re.IGNORECASE)
        if onscreen is not None and "ONSCREENHELP" in unit.label.upper():
            prefix, kind, suffix = onscreen.groups()
            suffix = suffix or ""
            labels = {
                "name": f"{prefix}NAME{suffix}".casefold(),
                "description": f"{prefix}DESCRIPTION{suffix}".casefold(),
            }
            paired: dict[str, TranslationUnit | None] = {"name": None, "description": None}
            for candidate in TranslatorWindow._preview_units_for_file(self, unit.file_rel):
                normalized = candidate.label.casefold()
                for role, label in labels.items():
                    if normalized == label:
                        paired[role] = candidate
            role = kind.casefold()
            if role in paired:
                paired[role] = unit
            if role == "tooltip":
                return None, unit
            return paired["name"], paired["description"]
        name_tooltip = re.match(r"^(.*?)(NAME|TOOLTIP)(_[+]\d+)?$", unit.label, re.IGNORECASE)
        if name_tooltip is not None:
            prefix, kind, suffix = name_tooltip.groups()
            suffix = suffix or ""
            tooltip = TranslatorWindow._preview_unit_for_labels(
                TranslatorWindow._preview_units_for_file(self, unit.file_rel),
                unit.file_rel,
                (f"{prefix}TOOLTIP_+0", f"{prefix}TOOLTIP{suffix}"),
            )
            name = TranslatorWindow._preview_unit_for_labels(
                TranslatorWindow._preview_units_for_file(self, unit.file_rel),
                unit.file_rel,
                (f"{prefix}NAME{suffix}", f"{prefix}NAME_+0", f"{prefix}NAME_+1"),
            )
            if name is None:
                name = TranslatorWindow._preview_unit_for_role(
                    TranslatorWindow._preview_units_for_file(self, unit.file_rel), unit.file_rel, prefix, "NAME"
                )
            role = kind.casefold()
            if role == "name":
                name = unit
            elif role == "tooltip":
                tooltip = unit
            if name is not None and tooltip is not None:
                return name, tooltip
        match = re.match(r"^(.*?)(HEAD|BODY)(_[+]\d+)?$", unit.label, re.IGNORECASE)
        if match is None:
            return None, unit
        prefix, kind, suffix = match.groups()
        head_suffix = "_+0" if suffix else ""
        body_suffix = suffix if kind.casefold() == "body" else head_suffix
        labels = {
            "head": f"{prefix}HEAD{head_suffix}".casefold(),
            "body": f"{prefix}BODY{body_suffix}".casefold(),
        }
        paired: dict[str, TranslationUnit | None] = {"head": None, "body": None}
        for candidate in TranslatorWindow._preview_units_for_file(self, unit.file_rel):
            normalized = candidate.label.casefold()
            for role, label in labels.items():
                if normalized == label:
                    paired[role] = candidate
        paired[kind.casefold()] = unit
        return paired["head"], paired["body"]

    def _preview_units_for_file(self, file_rel: str) -> Iterable[TranslationUnit]:
        indexed_lookup = getattr(self.model, "units_for_file", None)
        if callable(indexed_lookup):
            return indexed_lookup(file_rel)
        return (candidate for candidate in self.model.units if candidate.file_rel == file_rel)

    @staticmethod
    def _preview_unit_for_labels(
        units: Iterable[TranslationUnit],
        file_rel: str,
        labels: tuple[str, ...],
    ) -> TranslationUnit | None:
        keys = {TranslatorWindow._preview_label_key(label) for label in labels if label}
        for candidate in units:
            if candidate.file_rel == file_rel and TranslatorWindow._preview_label_key(candidate.label) in keys:
                return candidate
        return None

    @staticmethod
    def _preview_unit_for_role(
        units: Iterable[TranslationUnit],
        file_rel: str,
        prefix: str,
        role: str,
    ) -> TranslationUnit | None:
        prefix_key = TranslatorWindow._preview_label_key(prefix)
        role_value = role.casefold()
        for candidate in units:
            if candidate.file_rel != file_rel:
                continue
            match = re.match(r"^(.*?)(NAME|TOOLTIP)(_[+]\d+)?$", candidate.label, re.IGNORECASE)
            if match is None:
                continue
            candidate_prefix, candidate_role, _ = match.groups()
            if candidate_role.casefold() == role_value and TranslatorWindow._preview_label_key(candidate_prefix) == prefix_key:
                return candidate
        return None

    @staticmethod
    def _preview_label_key(label: str) -> str:
        return normalize_label(label).lstrip("_")

    @staticmethod
    def _is_name_tooltip_pair(header_unit: TranslationUnit | None, body_unit: TranslationUnit | None) -> bool:
        if header_unit is None or body_unit is None:
            return False
        return bool(
            re.match(r"^.*NAME(_[+]\d+)?$", header_unit.label, re.IGNORECASE)
            and re.match(r"^.*TOOLTIP(_[+]\d+)?$", body_unit.label, re.IGNORECASE)
        )

    def _refresh_preview_presentations(self) -> None:
        self._game_preview_cache.clear()
        self.source_edit.refresh_preview()
        self.translation_edit.refresh_preview()
        self._update_preview_tooltips()
        self.table.viewport().update()

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
        self.git_ready = False
        self.git_pending = False
        self._git_pending_forced = False
        self._git_init_failed = False
        self._git_init_token += 1
        self.history.clear()
        self.typing_uid = ""
        self.typing_before = ""
        self.typing_before_deleted = False
        self.current_uid = ""
        self._filter_anchor_uid = ""
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
        if answer == QMessageBox.StandardButton.Yes:
            self._clear_current_recovery()
            return True
        return False

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
            return (
                self.git.language == language
                and self.git.project_root == self.project_root.resolve()
                and self.git.enable_codec == self.settings.enable_chinese_codec
            )
        except OSError:
            return False

    def _set_git_binding(self, git: LanguageGit) -> None:
        self._git_init_token += 1
        self.git = git
        self.git_ready = False
        self.git_pending = False
        self._git_pending_forced = False
        self._git_init_failed = False

    def _start_git_initialization(self) -> None:
        git = self.git
        # Serialize repository preparation across rapid project/language switches.
        # Different LanguageGit instances still share the same languages repository.
        if git is None or self.git_ready or self._git_init_workers:
            return
        token = self._git_init_token
        self.git_ready = False
        self._git_init_failed = False
        self._update_pending_state()
        worker = GitInitWorker(token, git, self.settings)
        worker.signals.ready.connect(self._git_initialization_ready)
        worker.signals.failed.connect(self._git_initialization_failed)
        self._git_init_workers.append(worker)
        self.thread_pool.start(worker)

    def _finish_git_initialization_worker(self, token: int) -> None:
        self._git_init_workers = [
            worker for worker in self._git_init_workers if worker.token != token
        ]

    def _git_initialization_ready(self, token: int, pending: bool) -> None:
        self._finish_git_initialization_worker(token)
        if token != self._git_init_token or self.git is None:
            self._start_git_initialization()
            return
        self.git_ready = True
        self._git_init_failed = False
        self.git_pending = bool(pending or self._git_pending_forced)
        self.retry_button.setVisible(self.git_pending)
        self._update_window_title()

    def _git_initialization_failed(self, token: int, message: str) -> None:
        self._finish_git_initialization_worker(token)
        if token != self._git_init_token or self.git is None:
            self._start_git_initialization()
            return
        self.git_ready = False
        self._git_init_failed = True
        self.git_pending = self._git_pending_forced
        self.retry_button.setVisible(True)
        self._update_window_title()
        self.statusBar().showMessage(translate("status.git_prepare_failed", error=message), 7000)

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
                self._clear_current_recovery()
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
                self._clear_current_recovery()
        created_language_dir = False
        try:
            language_root = self.project_root / "languages" / language
            if not language_root.exists():
                ensure_translation_dir(self.project_root, language)
                created_language_dir = True
                self._load_language_choices(language)
            if not self._git_matches_current_project(language):
                self._set_git_binding(LanguageGit(
                    self.project_root,
                    language,
                    codec_root=DEFAULT_PROJECT_ROOT,
                    enable_codec=self.settings.enable_chinese_codec,
                ))
            # A first-time baseline must finish before editing can begin. Once
            # repository metadata exists, routine validation is safe to defer.
            if self.git is not None and not self.git.has_repository_metadata():
                self.git.ensure_repository(self.settings)
                self.git_ready = True
            project = Project.load(
                self.project_root,
                language,
                codec_root=DEFAULT_PROJECT_ROOT,
                enable_codec=self.settings.enable_chinese_codec,
            )
        except (ProjectError, GitError, OSError, ValueError) as exc:
            QMessageBox.critical(self, translate("dialog.load_error"), str(exc))
            return
        self._activate_project(project)
        self._start_git_initialization()
        if created_language_dir:
            self.statusBar().showMessage(
                translate("status.language_created_loaded", language=language, count=len(project.units)),
                5000,
            )
        if self.project_root is not None:
            self._remember_project_root(self.project_root)

    def _activate_project(self, project: Project) -> None:
        self.recovery_timer.stop()
        recovered, skipped = self._offer_recovery_draft(project)
        self._game_preview_cache.clear()
        self.project = project
        self.preview_service.configure(
            self.game_root,
            project.language,
            self.settings.preview_translation_font_dir,
            self.settings.preview_ui_assets_dir,
        )
        self.history.clear()
        self.typing_uid = ""
        self.typing_before = ""
        self.typing_before_deleted = False
        self.current_uid = ""
        self._filter_anchor_uid = ""
        self.model.set_project(self.project)
        self.preview_service.set_project_localization(
            {
                unit.label: unit.source_text
                for unit in project.units
                if unit.label
            },
            {
                unit.label: unit.current_text
                for unit in project.units
                if unit.label
            },
        )
        self.translation_highlighter.set_glyph_codec(self.project.codec if ENABLE_FONT_GLYPH_VALIDATION else None)
        self._start_code_reference_index()
        self._update_file_choices()
        self._apply_filters()
        if not self._is_document_file_selected():
            self._set_editor_unit(None)
        self._update_counts()
        self._update_pending_state()
        self._update_project_button()
        if recovered or skipped:
            self.statusBar().showMessage(
                translate("status.recovery_restored", count=recovered, skipped=skipped),
                7000,
            )
            self._schedule_recovery_snapshot()
        else:
            self.statusBar().showMessage(translate("status.project_loaded", count=len(self.project.units)), 4500)

    def _offer_recovery_draft(self, project: Project) -> tuple[int, int]:
        draft = load_recovery_draft(project.root, project.language)
        if draft is None or not draft.units:
            return 0, 0
        answer = QMessageBox.question(
            self,
            translate("dialog.recovery_title"),
            translate("dialog.recovery_detail", count=len(draft.units)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            clear_recovery_draft(project.root, project.language)
            return 0, 0
        restored, skipped = apply_recovery_draft(project, draft)
        if not restored:
            clear_recovery_draft(project.root, project.language)
        return restored, skipped

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
        if self.project is not None:
            self.preview_service.configure(
                root,
                self.project.language,
                self.settings.preview_translation_font_dir,
                self.settings.preview_ui_assets_dir,
            )
            self._refresh_preview_presentations()
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
            (translate("filter.needs_review"), STATUS_FILTER_REVIEW),
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
        self.search_edit.case_button.setToolTip(translate("toolbar.search_case_sensitive"))
        self.only_missing.setText(translate("toolbar.only_missing"))
        self.only_format_warnings.setText(translate("toolbar.only_format_warnings"))
        self.reset_sort_button.setText(translate("toolbar.reset_sort"))
        self.reset_sort_button.setToolTip(translate("toolbar.reset_sort_tooltip"))
        self.review_attention_button.setToolTip(translate("review_attention.tooltip"))
        for button in self.top_buttons:
            button.setText(translate(str(button.property("text_key") or "")))
        self.more_button.setText(translate("button.more"))
        for action in self.more_actions:
            action.setText(translate(str(action.property("text_key") or "")))
        self.retry_button.setText(translate("button.retry_commit"))
        self.retry_button.setToolTip(translate("button.retry_commit_tooltip"))
        self.source_box.setTitle(translate("editor.source_title"))
        self.translation_box.setTitle(translate("editor.translation_title"))
        self.source_code_button.setText(translate("editor.code_button"))
        self.source_preview_button.setText(translate("editor.preview_toggle"))
        self.translation_preview_button.setText(translate("editor.preview_toggle"))
        self._update_code_reference_display()
        if isinstance(self.source_box, EditorGroupBox):
            self.source_box.position_preview_button()
        if isinstance(self.translation_box, EditorGroupBox):
            self.translation_box.position_preview_button()
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
        self._refresh_preview_presentations()

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
        previous_document_mode = not self.table_frame.isVisible()
        selected_uid = self._filter_anchor_uid or self.current_uid
        selected_visible = self._change_proxy_rows(
            lambda: self.proxy.set_filters(
                file_filter=str(self.file_combo.currentData() or FILE_FILTER_ALL),
                status_filter=str(self.status_combo.currentData() or STATUS_FILTER_TODO),
                only_missing=self.only_missing.isChecked(),
                only_format_warnings=self.only_format_warnings.isChecked(),
                query=query,
                case_sensitive=self.search_edit.case_button.isChecked(),
            ),
            selected_uid,
        )
        self.last_applied_query = query.strip()
        self._update_counts()
        self.code_index_visible_timer.start()
        if self._sync_document_layout():
            return
        if previous_document_mode:
            if selected_visible and self._restore_selected_row(selected_uid):
                return
            self.current_uid = ""
            self._set_editor_unit(None)
            self._update_window_title()
            return
        if selected_visible:
            self._restore_selected_row(selected_uid)

    def _change_proxy_rows(self, change: Callable[[], None], selected_uid: str) -> bool:
        """Apply a proxy change without letting Qt invent a new current row."""
        selection_model = self.table.selectionModel()
        selection_blocker = QSignalBlocker(selection_model)
        try:
            change()
            selected_index = QModelIndex()
            if selected_uid:
                source_row = self.model.row_for_uid(selected_uid)
                selected_index = (
                    self.proxy.mapFromSource(self.model.index(source_row, 0))
                    if source_row is not None
                    else QModelIndex()
                )
            if not selected_index.isValid():
                self.table.clearSelection()
                selection_model.clearCurrentIndex()
        finally:
            del selection_blocker
        return selected_index.isValid()

    def _on_table_sort_changed(self, column: int, _order: Qt.SortOrder) -> None:
        sorted_view = column >= 0
        self.table.horizontalHeader().setSortIndicatorShown(sorted_view)
        self.reset_sort_button.setVisible(sorted_view)
        if self._filter_anchor_uid:
            self._restore_selected_row(self._filter_anchor_uid)
        self.code_index_visible_timer.start()

    def _reset_table_sort(self) -> None:
        self.proxy.sort(-1)
        self.table.horizontalHeader().setSortIndicatorShown(False)
        self.reset_sort_button.hide()
        if self._filter_anchor_uid:
            self._restore_selected_row(self._filter_anchor_uid)
        self.code_index_visible_timer.start()

    def _on_search_changed(self, text: str) -> None:
        # Do not apply an empty intermediate value synchronously. Replacing a
        # Ctrl+A selection can briefly emit "" before the first new character;
        # restoring the table selection at that point steals focus from search.
        self._refresh_editor_highlights()
        self.search_debounce.start()

    def _on_search_case_toggled(self, _checked: bool) -> None:
        self._refresh_editor_highlights()
        self._apply_filters()

    def _restore_selected_row(self, uid: str) -> bool:
        if self._is_document_file_selected():
            return False
        source_row = self.model.row_for_uid(uid)
        if source_row is None:
            return False
        proxy_index = self.proxy.mapFromSource(self.model.index(source_row, 0))
        if not proxy_index.isValid():
            return False
        if self.table.currentIndex().data(Qt.ItemDataRole.UserRole) != uid:
            self.table.setCurrentIndex(proxy_index)
            self.table.selectRow(proxy_index.row())
        self.table.scrollTo(proxy_index, QAbstractItemView.ScrollHint.PositionAtCenter)
        QTimer.singleShot(0, lambda selected_uid=uid: self._scroll_selected_row_into_view(selected_uid))
        if self.current_uid != uid:
            self._on_row_selected(proxy_index, QModelIndex())
        return True

    def _scroll_selected_row_into_view(self, uid: str) -> None:
        if uid != self._filter_anchor_uid or self._is_document_file_selected():
            return
        source_row = self.model.row_for_uid(uid)
        if source_row is None:
            return
        proxy_index = self.proxy.mapFromSource(self.model.index(source_row, 0))
        if proxy_index.isValid():
            self.table.scrollTo(proxy_index, QAbstractItemView.ScrollHint.PositionAtCenter)

    def _update_counts(self) -> None:
        if self.project is None:
            self.counts_label.setText("")
            self.review_attention_button.hide()
            return
        effective: Counter[str] = Counter()
        todo = 0
        review = 0
        for unit in self.project.units:
            status = unit.filter_status()
            effective[status] += 1
            if unit.requires_manual_review:
                review += 1
            else:
                todo += status in MISSING_WORK_STATUSES
        recent = self.model.recently_translated_count
        self.counts_label.setText(
            translate(
                "counts.summary",
                visible=self.proxy.rowCount(),
                total=len(self.project.units),
                todo=todo,
                review=review,
                translated=effective[STATUS_TRANSLATED],
                recent=recent,
                ignored=effective[STATUS_IGNORED],
            )
        )
        self.review_attention_button.setText(translate("review_attention.button", count=review))
        self.review_attention_button.setVisible(review > 0)

    def _show_review_attention(self) -> None:
        status_index = self.status_combo.findData(STATUS_FILTER_REVIEW)
        if status_index < 0:
            return
        status_blocker = QSignalBlocker(self.status_combo)
        missing_blocker = QSignalBlocker(self.only_missing)
        self.status_combo.setCurrentIndex(status_index)
        self.only_missing.setChecked(False)
        del status_blocker, missing_blocker
        self._apply_filters()

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
        if unit is not None:
            self._filter_anchor_uid = unit.uid
            self._request_code_context_for_unit(unit)
            self.code_index_visible_timer.start()
        self._set_editor_unit(unit)
        self._update_window_title()

    def _set_editor_unit(self, unit: TranslationUnit | None) -> None:
        self.loading_editor = True
        dialect = format_dialect(unit.file_rel, unit.ref.kind) if unit is not None else FORMAT_GUILD2
        self.source_highlighter.set_dialect(dialect)
        self.translation_highlighter.set_dialect(dialect)
        source_blocker = QSignalBlocker(self.source_edit)
        translation_blocker = QSignalBlocker(self.translation_edit)
        self.source_edit.setPlainText(unit.source_text if unit else "")
        self.translation_edit.setPlainText(unit.current_text if unit else "")
        self.translation_edit.document().clearUndoRedoStacks()
        del source_blocker, translation_blocker
        self.loading_editor = False
        self._cancel_pending_typing_operation()
        self._update_issue_detail(unit)
        self._update_preview_tooltips()
        self._refresh_editor_highlights()
        self._update_code_reference_display()

    def _search_ranges(self, text: str, field: str) -> list[tuple[int, int]]:
        case_sensitive = self.search_edit.case_button.isChecked()
        clauses = parse_search_query(self.search_edit.text(), case_sensitive=case_sensitive)
        needles = tuple(
            clause.needle
            for clause in clauses
            if not clause.excluded and clause.field in {"", field}
        )
        if not needles:
            return []
        haystack = text if case_sensitive else text.casefold()
        ranges: list[tuple[int, int]] = []
        for needle in needles:
            start = 0
            while True:
                index = haystack.find(needle, start)
                if index < 0:
                    break
                ranges.append((index, index + len(needle)))
                start = index + max(len(needle), 1)
        return sorted(set(ranges))

    def _refresh_editor_highlights(self) -> None:
        unit = self._current_unit()
        source_selections: list[QTextEdit.ExtraSelection] = []
        translation_selections: list[QTextEdit.ExtraSelection] = []

        for start, end in self._search_ranges(self.source_edit.toPlainText(), "source"):
            display_start, display_end = self.source_edit.map_raw_range(start, end)
            source_selections.append(
                _make_editor_selection(self.source_edit, display_start, display_end, background="#f6e58d")
            )
        for start, end in self._search_ranges(self.translation_edit.toPlainText(), "translation"):
            display_start, display_end = self.translation_edit.map_raw_range(start, end)
            translation_selections.append(
                _make_editor_selection(self.translation_edit, display_start, display_end, background="#f6e58d")
            )

        if unit is not None:
            dialect = format_dialect(unit.file_rel, unit.ref.kind)
            for start, end in _missing_source_token_ranges(unit.source_text, unit.current_text, dialect=dialect):
                display_start, display_end = self.source_edit.map_raw_range(start, end)
                source_selections.append(
                    _make_editor_selection(
                        self.source_edit,
                        display_start,
                        display_end,
                        background="#f5c2c7",
                        foreground="#7f1d1d",
                    )
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
        self._set_unit_text(unit, text)
        self.model.refresh_unit(unit)
        self._update_recent_translation_marker(unit, before_status)
        self._update_issue_detail(unit)
        self._update_preview_tooltips()
        self._refresh_editor_highlights()
        self._schedule_counts_update()
        self._update_window_title()
        self._schedule_recovery_snapshot()
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

    def _schedule_recovery_snapshot(self) -> None:
        if self.project is not None:
            self.recovery_timer.start()

    def _write_recovery_snapshot(self) -> None:
        if self.project is None:
            return
        try:
            save_recovery_draft(self.project)
            self._recovery_warning_shown = False
        except OSError as exc:
            if not self._recovery_warning_shown:
                self.statusBar().showMessage(translate("status.recovery_failed", error=exc), 7000)
                self._recovery_warning_shown = True

    def _schedule_counts_update(self) -> None:
        if not self.counts_refresh_timer.isActive():
            self.counts_refresh_timer.start()

    def _clear_current_recovery(self) -> None:
        self.recovery_timer.stop()
        if self.project is None:
            return
        try:
            clear_recovery_draft(self.project.root, self.project.language)
        except OSError as exc:
            self.statusBar().showMessage(translate("status.recovery_failed", error=exc), 7000)

    def _apply_operation_state(self, uid: str, text: str, pending_delete: bool) -> None:
        unit = self.model.unit_for_uid(uid)
        if unit is None:
            return
        before_status = unit.filter_status()
        if self.project is not None:
            self.project.apply_unit_edits(((unit, text, pending_delete),))
        else:
            unit.set_text(text)
            unit.set_pending_delete(pending_delete)
        self._update_preview_localization((unit,))
        self.model.refresh_unit(unit)
        self._update_recent_translation_marker(unit, before_status)
        if uid == self.current_uid:
            self._set_editor_unit(unit)
        self._update_counts()
        self._update_window_title()
        self._schedule_recovery_snapshot()

    def _apply_operation_changes(self, changes: tuple[UnitChange, ...], *, use_after: bool) -> None:
        cursor = self.translation_edit.textCursor()
        cursor_position = cursor.position()
        old_display_length = max(0, self.translation_edit.document().characterCount() - 1)
        cursor_was_at_end = cursor_position >= old_display_length
        changed: list[tuple[TranslationUnit, str, bool]] = []
        edit_states: list[tuple[TranslationUnit, str, bool | None]] = []
        changed_uids: set[str] = set()
        for change in changes:
            unit = self.model.unit_for_uid(change.uid)
            if unit is None:
                continue
            text = change.after if use_after else change.before
            pending_delete = change.after_deleted if use_after else change.before_deleted
            before_status = unit.filter_status()
            changed.append((unit, before_status, pending_delete))
            edit_states.append((unit, text, pending_delete))
            changed_uids.add(unit.uid)

        if not changed:
            return
        if self.project is not None:
            self.project.apply_unit_edits(edit_states)
        else:
            for unit, text, pending_delete in edit_states:
                unit.set_text(text)
                if pending_delete is not None:
                    unit.set_pending_delete(pending_delete)
        self._update_preview_localization(
            unit
            for unit, _before_status, _pending_delete in changed
        )
        for unit, before_status, _pending_delete in changed:
            self._update_recent_translation_marker(unit, before_status, notify=False)
        changed_units = tuple(unit for unit, _before_status, _pending_delete in changed)
        self.model.refresh_units(changed_units)
        selected_uid = self.current_uid or self._filter_anchor_uid
        if self._change_proxy_rows(self.proxy.refresh_rows, selected_uid):
            self._restore_selected_row(selected_uid)
        current = self.model.unit_for_uid(self.current_uid) if self.current_uid else None
        if current is not None and current.uid in changed_uids:
            self._set_editor_unit(current)
            new_display_length = max(0, self.translation_edit.document().characterCount() - 1)
            restored_cursor = self.translation_edit.textCursor()
            restored_cursor.setPosition(
                new_display_length if cursor_was_at_end else min(cursor_position, new_display_length)
            )
            self.translation_edit.setTextCursor(restored_cursor)
        self._update_counts()
        self._update_window_title()
        self._schedule_recovery_snapshot()

    def _set_unit_text(self, unit: TranslationUnit, text: str) -> None:
        if self.project is None:
            unit.set_text(text)
            self._update_preview_localization((unit,))
            return
        self.project.apply_unit_edits(((unit, text, None),))
        self._update_preview_localization((unit,))

    def _update_preview_localization(
        self,
        units: Iterable[TranslationUnit],
    ) -> None:
        changed = tuple(units)
        if not changed:
            return
        for unit in changed:
            if unit.label:
                self.preview_service.update_project_localization(
                    unit.label,
                    unit.source_text,
                    unit.current_text,
                )
        self._game_preview_cache.clear()

    def _update_recent_translation_marker(self, unit: TranslationUnit, before_status: str, *, notify: bool = True) -> None:
        current_status = unit.filter_status()
        changed_existing_translation = unit.is_dirty and unit.status == STATUS_TRANSLATED
        if current_status == STATUS_TRANSLATED and unit.is_dirty and (
            before_status in MISSING_WORK_STATUSES or changed_existing_translation
        ):
            self.model.set_recently_translated(unit, True, notify=notify)
        elif current_status != STATUS_TRANSLATED or not unit.is_dirty:
            self.model.set_recently_translated(unit, False, notify=notify)

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
        self._apply_operation_changes(changes, use_after=True)
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
        self._commit_typing_operation()
        operation = self.history.take_undo()
        if operation:
            self._apply_operation_changes(operation.changes, use_after=False)
            self.statusBar().showMessage(translate("status.undo", label=operation.label), 2500)

    def redo(self) -> None:
        self._commit_typing_operation()
        operation = self.history.take_redo()
        if operation:
            self._apply_operation_changes(operation.changes, use_after=True)
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
        suffix = translate("menu.selection_suffix", count=count) if count > 1 else ""
        can_delete_all = bool(units) and all(item.can_delete_translation() for item in units)
        all_pending_delete = bool(units) and all(item.pending_delete for item in units)
        can_toggle_delete = all_pending_delete or can_delete_all
        all_need_work = bool(units) and all(item.review_reason == TODO_REASON_MANUAL_REVIEW for item in units)
        all_ignored = bool(units) and all(item.ignored for item in units)
        can_mark_review = bool(units) and all(not item.is_extra for item in units)
        can_confirm = can_mark_review and all(item.current_text for item in units)
        menu = QMenu(self)
        menu.addSection(translate("menu.entry_status"))
        confirm_translated = menu.addAction(translate("menu.confirm_translated", suffix=suffix))
        confirm_translated.setEnabled(can_confirm)
        need_work = menu.addAction(
            translate("menu.unmark_need_work", suffix=suffix)
            if all_need_work
            else translate("menu.mark_need_work", suffix=suffix)
        )
        need_work.setEnabled(can_mark_review)
        ignored = menu.addAction(
            translate("menu.unmark_ignored", suffix=suffix)
            if all_ignored
            else translate("menu.mark_ignored", suffix=suffix)
        )
        ignored.setEnabled(can_mark_review)
        menu.addSection(translate("menu.translation_edit"))
        entry_history = menu.addAction(translate("menu.entry_history"))
        entry_history.setEnabled(count == 1 and self.git is not None and self.git_ready)
        copy_translation = menu.addAction(translate("menu.copy_selected_translation", suffix=suffix))
        restore = menu.addAction(translate("menu.restore_loaded", suffix=suffix))
        source = menu.addAction(translate("menu.restore_source", suffix=suffix))
        clear = menu.addAction(translate("menu.clear_translation", suffix=suffix))
        menu.addSection(translate("menu.ai_service"))
        ai_translate = menu.addAction(translate("menu.ai_translate_selected", suffix=suffix))
        llm_suggestion = menu.addAction(translate("menu.llm_suggestion"))
        llm_suggestion.setEnabled(count == 1)
        menu.addSection(translate("menu.delete_cleanup"))
        delete_mark = menu.addAction(
            translate("menu.unmark_delete", suffix=suffix)
            if all_pending_delete
            else translate("menu.mark_delete", suffix=suffix)
        )
        delete_mark.setEnabled(can_toggle_delete)
        action = menu.exec(global_point)
        if action == entry_history:
            self.show_entry_history(unit)
        elif action == confirm_translated:
            self._set_units_confirmed(units)
        elif action == need_work:
            self._set_units_need_work(units, not all_need_work)
        elif action == ignored:
            self._set_units_ignored(units, not all_ignored)
        elif action == copy_translation:
            self._copy_unit_entries(units)
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
        elif action == delete_mark:
            self._set_units_pending_delete(units, not all_pending_delete)

    def _select_context_row(self, index: QModelIndex) -> None:
        """Keep an existing multi-selection intact when opening its context menu."""
        selection = self.table.selectionModel()
        if any(selected.row() == index.row() for selected in selection.selectedRows()):
            return
        self.table.setCurrentIndex(index)
        self.table.selectRow(index.row())

    def _selected_units(self) -> list[TranslationUnit]:
        units: list[TranslationUnit] = []
        for index in sorted(self.table.selectionModel().selectedRows(), key=lambda item: item.row()):
            unit = self._unit_from_proxy_index(index)
            if unit is not None:
                units.append(unit)
        return units

    def _copy_unit_entries(self, units: Iterable[TranslationUnit]) -> None:
        selected = tuple(units)
        if not selected:
            self.statusBar().showMessage(translate("status.copy_none"), 2500)
            return
        raw, plain_text = encode_entries(selected)
        mime = QMimeData()
        mime.setData(ENTRY_CLIPBOARD_MIME, raw)
        mime.setText(plain_text)
        QApplication.clipboard().setMimeData(mime)
        self.statusBar().showMessage(translate("status.copy_done", count=len(selected)), 2500)

    def _clipboard_translations(self) -> list[str] | None:
        mime = QApplication.clipboard().mimeData()
        if not mime.hasFormat(ENTRY_CLIPBOARD_MIME):
            return None
        return decode_translations(bytes(mime.data(ENTRY_CLIPBOARD_MIME)))

    def _paste_unit_translations(self) -> None:
        translations = self._clipboard_translations()
        if translations is None:
            self.statusBar().showMessage(translate("status.paste_unavailable"), 3000)
            return
        indexes = sorted(self.table.selectionModel().selectedRows(), key=lambda item: item.row())
        copied_count = len(translations)
        if copied_count == 1 and indexes:
            targets = [self._unit_from_proxy_index(index) for index in indexes]
            target_translations = translations * len(indexes)
        elif len(indexes) == copied_count:
            targets = [self._unit_from_proxy_index(index) for index in indexes]
            target_translations = translations
        elif len(indexes) == 1:
            start = indexes[0].row()
            if start + copied_count > self.proxy.rowCount():
                self.statusBar().showMessage(translate("status.paste_range_short"), 3500)
                return
            targets = [self._unit_from_proxy_index(self.proxy.index(row, 0)) for row in range(start, start + copied_count)]
            target_translations = translations
        else:
            self.statusBar().showMessage(
                translate("status.paste_count_mismatch", copied=copied_count, selected=len(indexes)),
                4000,
            )
            return
        selected = tuple(unit for unit in targets if unit is not None)
        if len(selected) != len(target_translations):
            self.statusBar().showMessage(translate("status.paste_range_short"), 3500)
            return
        texts = {unit.uid: text for unit, text in zip(selected, target_translations)}
        changed_count = sum(
            unit.current_text != texts[unit.uid] or unit.pending_delete
            for unit in selected
        )
        if not changed_count:
            self.statusBar().showMessage(translate("status.paste_no_changes"), 2500)
            return
        self._replace_units_state(
            selected,
            texts,
            False,
            translate("operation.paste_translations", count=changed_count),
        )
        self.statusBar().showMessage(translate("status.paste_done", count=changed_count), 3000)

    def _set_ignored(self, unit: TranslationUnit, ignored: bool) -> None:
        self._set_units_ignored((unit,), ignored)

    def _refresh_unit_metadata(self, units: Iterable[TranslationUnit]) -> tuple[TranslationUnit, ...]:
        selected = tuple(units)
        for unit in selected:
            self.model.refresh_unit(unit)
            self.model.set_recently_translated(unit, False)
        self._apply_filters()
        self._update_counts()
        self._update_issue_detail(self._current_unit())
        self._update_window_title()
        return selected

    def _set_units_confirmed(self, units: Iterable[TranslationUnit]) -> None:
        if self.project is None:
            return
        selected = tuple(unit for unit in units if unit.current_text and not unit.is_extra)
        if not selected:
            return
        self.project.set_units_confirmed(selected, True)
        self._refresh_unit_metadata(selected)
        self.statusBar().showMessage(translate("status.confirmed_translated", count=len(selected)), 3000)

    def _set_units_need_work(self, units: Iterable[TranslationUnit], need_work: bool) -> None:
        if self.project is None:
            return
        selected = tuple(unit for unit in units if not unit.is_extra)
        if not selected:
            return
        self.project.set_units_need_work(selected, need_work)
        self._refresh_unit_metadata(selected)
        self.statusBar().showMessage(
            translate("status.need_work_marked" if need_work else "status.need_work_cleared", count=len(selected)),
            3000,
        )

    def _set_units_ignored(self, units: Iterable[TranslationUnit], ignored: bool) -> None:
        if self.project is None:
            return
        selected = tuple(unit for unit in units if not unit.is_extra)
        if not selected:
            return
        self.project.set_units_ignored(selected, ignored)
        self._refresh_unit_metadata(selected)
        self.statusBar().showMessage(
            translate("status.ignored_marked" if ignored else "status.ignored_cleared", count=len(selected)),
            3000,
        )

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
        google.setChecked(self.settings.provider == "google")
        deepl = menu.addAction(translate("dialog.ai_service_deepl"))
        deepl.setCheckable(True)
        deepl.setChecked(self.settings.provider == "deepl")
        openai = menu.addAction(translate("dialog.ai_service_openai"))
        openai.setCheckable(True)
        openai.setChecked(self.settings.provider == "openai")
        menu.addSeparator()
        settings_action = menu.addAction(translate("dialog.ai_service_settings"))
        action = menu.exec(global_point)
        if action == google:
            self._set_ai_provider("google")
        elif action == deepl:
            self._set_ai_provider("deepl")
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
        if provider == "google":
            name = "Google Translate"
        elif provider == "deepl":
            name = "DeepL"
        else:
            name = translate("dialog.ai_service_openai").replace("✦ ", "")
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
        worker = LlmSuggestionWorker(
            provider,
            unit.source_text,
            unit.current_text,
            self._build_llm_suggestion_context(unit),
            self.suggestion_cancel_event,
        )
        worker.signals.chunk.connect(self._append_suggestion_chunk)
        worker.signals.failed.connect(self._show_suggestion_failure)
        worker.signals.finished.connect(self._finish_suggestion)
        self.suggestion_worker = worker
        self.thread_pool.start(worker)

    def _build_llm_suggestion_context(self, unit: TranslationUnit) -> LlmSuggestionContext:
        project = self.model.project
        if project is None:
            return LlmSuggestionContext(
                unit.file_rel, unit.record_id, unit.label, field_name=unit.field_name
            )
        contexts = build_llm_contexts(project.units, (unit.uid,))
        return contexts.get(
            unit.uid,
            LlmSuggestionContext(unit.file_rel, unit.record_id, unit.label, field_name=unit.field_name),
        )

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
        contexts: dict[str, LlmSuggestionContext] = {}
        if isinstance(provider, OpenAICompatibleProvider):
            context_units = self.model.project.units if self.model.project is not None else units
            contexts = build_llm_contexts(context_units, (unit.uid for unit in units))
        worker = AiWorker(provider, units, self.ai_cancel_event, contexts)
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
        selected_uid = self._filter_anchor_uid or self.current_uid
        # AI results can arrive repeatedly while the user is reviewing the
        # table. Refresh filtering without re-centering the selected row, or
        # every completed translation will fight the user's manual scrolling.
        self._change_proxy_rows(self.proxy.refresh_rows, selected_uid)
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
        save_started = time.perf_counter()
        try:
            result = self.project.save(
                auto_space_before_color_tokens=self.settings.auto_space_before_color_tokens_on_save
            )
        except SaveValidationError as exc:
            log_metrics(
                "save_blocked",
                total_ms=(time.perf_counter() - save_started) * 1000,
                issue_count=len(exc.messages),
            )
            QMessageBox.warning(self, translate("dialog.save_blocked"), "\n".join(exc.messages[:20]))
            return
        except (ProjectError, OSError) as exc:
            log_failure(
                "save_file_failed",
                exc,
                total_ms=(time.perf_counter() - save_started) * 1000,
            )
            QMessageBox.critical(
                self,
                translate("dialog.save_failed"),
                translate("dialog.save_failed_detail", error=exc),
            )
            return
        project_save_ms = (time.perf_counter() - save_started) * 1000
        if result.changed_files or result.deleted_units:
            self._clear_current_recovery()
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
                log_metrics(
                    "save_delete_only",
                    total_ms=(time.perf_counter() - save_started) * 1000,
                    deleted_units=len(result.deleted_units),
                )
                self.statusBar().showMessage(translate("status.deleted_entries", count=len(result.deleted_units)), 4000)
                return
            log_metrics("save_no_changes", total_ms=(time.perf_counter() - save_started) * 1000)
            self.statusBar().showMessage(translate("status.no_changes_to_save"), 3000)
            return
        commit_note = ""
        git_started = time.perf_counter()
        git_failed = False
        try:
            commit = (
                self.git.commit_saved(result.changed_files, result.saved_units, result.deleted_units)
                if self.git is not None and self.git_ready
                else None
            )
            if commit is not None:
                commit_note = translate("status.saved_commit", hash=commit.short_hash)
            elif self.git is not None and not self.git_ready:
                self._git_pending_forced = True
                commit_note = translate("status.saved_git_deferred")
        except GitError as exc:
            git_failed = True
            self._git_pending_forced = True
            commit_note = translate("status.saved_git_failed", error=exc)
            log_failure("save_git_failed", exc)
        git_ms = (time.perf_counter() - git_started) * 1000
        refresh_started = time.perf_counter()
        refresh_fallback = False
        try:
            self._refresh_saved_project(result.changed_files)
        except (ProjectError, OSError, ValueError) as exc:
            refresh_fallback = True
            log_failure("save_refresh_failed", exc)
            # The files are already durable. Fall back to the established full
            # reload instead of leaving a partially refreshed in-memory model.
            self.load_project(discard_changes=True)
        refresh_ms = (time.perf_counter() - refresh_started) * 1000
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
        log_metrics(
            "save_complete",
            total_ms=(time.perf_counter() - save_started) * 1000,
            project_save_ms=project_save_ms,
            git_ms=git_ms,
            refresh_ms=refresh_ms,
            changed_files=len(result.changed_files),
            saved_units=len(result.saved_units),
            deleted_units=len(result.deleted_units),
            warning_count=format_warning_count,
            git_failed=git_failed,
            git_deferred=self.git is not None and not self.git_ready,
            refresh_fallback=refresh_fallback,
        )

    def _refresh_saved_project(self, changed_files: Iterable[Path]) -> None:
        if self.project is None:
            return
        selected_uid = self._filter_anchor_uid or self.current_uid
        self.project.reload_saved_files(changed_files)
        self._game_preview_cache.clear()
        self.current_uid = ""
        self._filter_anchor_uid = selected_uid if self.project.unit_by_uid(selected_uid) is not None else ""
        self._set_editor_unit(None)
        self.model.set_project(self.project)
        self._update_file_choices()
        self._apply_filters()
        self._update_pending_state()
        self._update_window_title()

    def retry_commit(self) -> None:
        if self.git is None:
            return
        if not self.git_ready:
            self._start_git_initialization()
            self.statusBar().showMessage(translate("status.git_preparing"), 4000)
            return
        try:
            commit = self.git.commit_pending()
        except GitError as exc:
            QMessageBox.warning(self, translate("dialog.git_commit_failed"), str(exc))
            return
        self._git_pending_forced = False
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
        if not self.git_ready:
            self.git_pending = self._git_pending_forced
            self.retry_button.setVisible(self._git_init_failed or self.git_pending)
            self._update_window_title()
            return
        try:
            pending = self.git.has_pending_changes()
        except GitError:
            pending = True
        self.git_pending = bool(pending or self._git_pending_forced)
        self.retry_button.setVisible(self.git_pending)
        if self.git_pending:
            self.statusBar().showMessage(translate("status.git_pending"))
        self._update_window_title()

    def show_text_import(self) -> None:
        if self.project is None:
            QMessageBox.information(
                self,
                translate("text_import.title"),
                translate("text_import.requires_project"),
            )
            return
        if self.ai_worker is not None or self.suggestion_worker is not None:
            self.statusBar().showMessage(translate("status.ai_already_running"), 4000)
            return
        dialog = TextImportDialog(self.project.units, self._selected_units(), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            dialog.deleteLater()
            return
        plan = dialog.import_plan()
        dialog.deleteLater()
        texts: dict[str, str] = {}
        units: list[TranslationUnit] = []
        for planned in plan.updates:
            unit = self.project.unit_by_uid(planned.unit_uid)
            if unit is None:
                continue
            units.append(unit)
            texts[unit.uid] = planned.row.translation
        if not units:
            return
        self._replace_units_state(
            units,
            texts,
            False,
            translate("operation.import_text", count=len(units)),
        )
        self.statusBar().showMessage(
            translate(
                "status.import_text_done",
                count=len(units),
                skipped=plan.skipped_count,
                problems=plan.problem_count,
            ),
            5000,
        )

    def show_history(self) -> None:
        if self.git is None:
            QMessageBox.information(self, translate("dialog.history_title"), translate("dialog.history_requires_project"))
            return
        if not self.git_ready:
            QMessageBox.information(self, translate("dialog.history_title"), translate("dialog.history_git_preparing"))
            return
        dialog = HistoryDialog(self.git, self)
        dialog.exec()
        dialog.deleteLater()

    def show_entry_history(self, unit: TranslationUnit) -> None:
        if self.git is None:
            QMessageBox.information(self, translate("dialog.history_title"), translate("dialog.history_requires_project"))
            return
        if not self.git_ready:
            QMessageBox.information(self, translate("dialog.history_title"), translate("dialog.history_git_preparing"))
            return
        focus_key = (unit.file_rel, unit.record_id or "", unit.label, unit.ref.target_field)
        dialog = HistoryDialog(self.git, self, focus_key=focus_key)
        dialog.exec()
        dialog.deleteLater()

    def show_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            dialog.deleteLater()
            return
        previous_language = self.settings.ui_language
        previous_theme = self.settings.ui_theme
        previous_codec = self.settings.enable_chinese_codec
        previous_preview_scope = self.settings.preview_scope
        previous_game_font = self.settings.preview_game_font_in_editors
        previous_use_code_context = self.settings.preview_use_code_context
        previous_preview_window_scale = self.settings.preview_window_scale_percent
        previous_preview_resources = (
            self.settings.preview_translation_font_dir,
            self.settings.preview_ui_assets_dir,
        )
        self.settings = dialog.result_settings()
        save_settings(self.settings)
        if self.settings.ui_theme != previous_theme:
            next_theme = self.settings.ui_theme
            dialog.destroyed.connect(
                lambda _object=None, theme=next_theme: QTimer.singleShot(
                    0, lambda: self._apply_runtime_theme(theme)
                )
            )
        dialog.deleteLater()
        if self.settings.ui_language != previous_language:
            set_language(self.settings.ui_language)
            self._retranslate_ui()
        self.ai_delegate.set_provider(self.settings.provider)
        if self.settings.preview_scope != previous_preview_scope:
            self.table.viewport().update()
        if self.settings.preview_use_code_context != previous_use_code_context:
            self._refresh_preview_presentations()
        if self.settings.preview_window_scale_percent != previous_preview_window_scale:
            self._refresh_preview_presentations()
        current_preview_resources = (
            self.settings.preview_translation_font_dir,
            self.settings.preview_ui_assets_dir,
        )
        if current_preview_resources != previous_preview_resources:
            self.preview_service.configure(
                self.game_root,
                self.project.language if self.project is not None else "#chinese",
                *current_preview_resources,
            )
            self._refresh_preview_presentations()
        if self.settings.preview_game_font_in_editors != previous_game_font:
            self.source_edit.set_game_font_builder(
                self.settings.preview_game_font_in_editors,
                lambda char, color: self.preview_service.text_glyph_image(char, False, color),
                lambda: self.preview_service.text_font_family(False),
            )
            self.translation_edit.set_game_font_builder(
                self.settings.preview_game_font_in_editors,
                lambda char, color: self.preview_service.text_glyph_image(char, True, color),
                lambda: self.preview_service.text_font_family(True),
            )
        if (
            self.settings.enable_chinese_codec != previous_codec
            and self.project is not None
            and language_uses_codec(self.project.language)
        ):
            self.load_project(discard_changes=False)
        if self.git is None or not self.git_ready or self._git_init_workers:
            return
        self._git_init_token += 1
        self.git_ready = False
        self._start_git_initialization()

    def _apply_runtime_theme(self, theme: str) -> None:
        app = QApplication.instance()
        if app is None:
            return
        self.setUpdatesEnabled(False)
        try:
            # Clear the window-level game QSS before replacing the proxy style.
            # Otherwise Qt repolishes the large table and both editor trees twice.
            self.setStyleSheet("")
            apply_theme(app, theme)
            if theme == "guild2":
                self.setStyleSheet(str(app.property("gameThemeQss") or ""))
            if hasattr(self, "source_highlighter"):
                self.source_highlighter.refresh_theme()
            if hasattr(self, "translation_highlighter"):
                self.translation_highlighter.refresh_theme()
            self._apply_theme_layout()
        finally:
            self.setUpdatesEnabled(True)
        self.update()

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
        popup = getattr(self, "game_preview_popup", None)
        if isinstance(popup, GamePreviewPopup):
            popup.hide()
        self._commit_typing_operation()
        if self.project is None:
            self._prepare_shutdown()
            event.accept()
            return
        self._write_recovery_snapshot()
        dirty_count = self.project.dirty_count()
        if not dirty_count:
            self._clear_current_recovery()
            self._prepare_shutdown()
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
                self._prepare_shutdown()
                event.accept()
            else:
                event.ignore()
            return
        if choice == QMessageBox.StandardButton.Discard:
            self._clear_current_recovery()
            self._prepare_shutdown()
            event.accept()
            return
        event.ignore()

    def _prepare_shutdown(self) -> None:
        self.search_debounce.stop()
        self.typing_timer.stop()
        self.recovery_timer.stop()
        self.counts_refresh_timer.stop()
        self.ai_filter_refresh_timer.stop()
        self.code_button_hold_timer.stop()
        self.code_index_visible_timer.stop()
        self.source_preview_tooltip_filter.cancel()
        self.translation_preview_tooltip_filter.cancel()
        if self.ai_cancel_event is not None:
            self.ai_cancel_event.set()
        if self.suggestion_cancel_event is not None:
            self.suggestion_cancel_event.set()
        for worker in self.code_reference_workers:
            worker.cancel()
        self.code_reference_index_token += 1


def _clip(text: str, limit: int) -> str:
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _localized_list_join(items: Iterable[str]) -> str:
    return ("、" if current_language().startswith("zh") else ", ").join(items)


def _localized_detail_join(items: Iterable[str]) -> str:
    return ("；" if current_language().startswith("zh") else "; ").join(items)


def _diff_token_key(token: str) -> str:
    return re.sub(r"\s+", "", token) if COLOR_TOKEN_RE.fullmatch(token) else token


def _format_token_occurrences(text: str, dialect: str = FORMAT_GUILD2) -> list[tuple[str, int, int]]:
    occurrences: list[tuple[str, int, int]] = []
    for match in token_re_for(dialect).finditer(text):
        token = match.group(0)
        if dialect == FORMAT_GUILD2 and (token == "$N" or token.startswith("$[")):
            continue
        occurrences.append((_diff_token_key(token), match.start(), match.end()))
    return occurrences


def _missing_source_token_ranges(
    source_text: str,
    target_text: str,
    *,
    dialect: str = FORMAT_GUILD2,
) -> list[tuple[int, int]]:
    source_occurrences = _format_token_occurrences(source_text, dialect)
    target_counts = Counter(key for key, _start, _end in _format_token_occurrences(target_text, dialect))
    ranges: list[tuple[int, int]] = []
    for key, start, end in source_occurrences:
        if target_counts[key]:
            target_counts[key] -= 1
        else:
            ranges.append((start, end))
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


def _history_colors() -> dict[str, str]:
    app = QApplication.instance()
    if app is not None and bool(app.property("guild2Theme")):
        return {
            "base": "#211a12", "text": "#eadca7", "panel": "#2b2419", "border": "#806537",
            "error_bg": "#4b241d", "error_border": "#a84f36", "muted": "#b8a274",
            "header": "#4a321d", "entry": "#30271a", "diff": "#211a12",
            "add_bg": "#32462d", "add_text": "#b9d59e", "danger_bg": "#542822",
            "danger_text": "#f0a18d", "diff_add_bg": "#735727", "diff_add_text": "#fff0b8",
            "empty": "#a99667",
        }
    if app is not None and bool(app.property("darkTheme")):
        return {
            "base": "#111318", "text": "#e3e7ee", "panel": "#20252d", "border": "#3a424e",
            "error_bg": "#45262b", "error_border": "#8f4d58", "muted": "#9aa4b2",
            "header": "#252a33", "entry": "#1b1f26", "diff": "#111318",
            "add_bg": "#20392d", "add_text": "#89d3a7", "danger_bg": "#45262b",
            "danger_text": "#ff9b96", "diff_add_bg": "#315f8a", "diff_add_text": "#ffffff",
            "empty": "#747d8b",
        }
    return {
        "base": "#fbf1c7", "text": "#3c3836", "panel": "#f2e5bc", "border": "#bdae93",
        "error_bg": "#f2d8d8", "error_border": "#cc241d", "muted": "#665c54",
        "header": "#d5c4a1", "entry": "#f9efc9", "diff": "#fbf1c7",
        "add_bg": "#d8f0d2", "add_text": "#076678", "danger_bg": "#f5d6d6",
        "danger_text": "#9d0006", "diff_add_bg": "#c6a15b", "diff_add_text": "#1d2021",
        "empty": "#928374",
    }


def _history_state_html(title: str, detail: str, *, kind: str = "info") -> str:
    colors = _history_colors()
    return f"""
    <html>
      <head>
        <style>
          body.history-root {{
            background: {colors["base"]};
            color: {colors["text"]};
            font-family: "Segoe UI", "Microsoft YaHei UI";
            margin: 0;
          }}
          .history-state {{
            background: {colors["panel"]};
            border: 2px solid {colors["border"]};
            border-radius: 10px;
            padding: 14px 16px;
          }}
          .history-state--error {{
            background: {colors["error_bg"]};
            border-color: {colors["error_border"]};
          }}
          .history-state__title {{
            font-size: 16px;
            font-weight: 900;
          }}
          .history-state__detail {{
            margin-top: 6px;
            color: {colors["muted"]};
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
    colors = _history_colors()
    return f"""
    <html>
      <head>
        <style>
          body.history-root {{
            background: {colors["base"]};
            color: {colors["text"]};
            font-family: "Segoe UI", "Microsoft YaHei UI";
            margin: 0;
          }}
          .history-summary, .history-state {{
            background: {colors["panel"]};
            border: 2px solid {colors["border"]};
            border-radius: 10px;
            padding: 14px 16px;
            margin-bottom: 16px;
          }}
          .history-state--error {{
            background: {colors["error_bg"]};
            border-color: {colors["error_border"]};
          }}
          .history-state__title, .history-summary__title {{
            font-size: 16px;
            font-weight: 900;
          }}
          .history-state__detail, .history-summary__meta, .history-summary__note {{
            margin-top: 6px;
            color: {colors["muted"]};
            font-weight: 600;
          }}
          .history-summary__note {{
            color: {colors["text"]};
          }}
          .history-file {{
            margin-top: 14px;
          }}
          .history-file__name {{
            background: {colors["header"]};
            border: 2px solid {colors["border"]};
            border-radius: 8px;
            font-size: 14px;
            font-weight: 900;
            padding: 5px 9px;
            margin-bottom: 8px;
          }}
          .history-entry {{
            background: {colors["entry"]};
            border: 1px solid {colors["border"]};
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
            color: {colors["muted"]};
            font-size: 11px;
            font-weight: 700;
            margin-bottom: 6px;
          }}
          .history-entry__diff {{
            background: {colors["diff"]};
            border: 1px solid {colors["border"]};
            border-radius: 6px;
            padding: 6px 8px;
            white-space: pre-wrap;
            word-break: break-word;
            line-height: 1.5;
            font-size: 13px;
          }}
          .history-entry__source {{
            margin-top: 5px;
            color: {colors["muted"]};
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
            background: {colors["add_bg"]};
            color: {colors["add_text"]};
          }}
          .history-badge--update {{
            background: {colors["danger_bg"]};
            color: {colors["danger_text"]};
          }}
          .history-badge--delete {{
            background: {colors["danger_bg"]};
            color: {colors["danger_text"]};
          }}
          .diff-del {{
            background: {colors["danger_bg"]};
            color: {colors["danger_text"]};
            border-radius: 3px;
            padding: 0 1px;
            text-decoration: line-through;
            text-decoration-thickness: 2px;
          }}
          .diff-add {{
            background: {colors["diff_add_bg"]};
            color: {colors["diff_add_text"]};
            border-radius: 3px;
            padding: 0 1px;
          }}
          .diff-empty {{
            color: {colors["empty"]};
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
    dialect = format_dialect(unit.file_rel, unit.ref.kind)
    source_hard, source_color = split_soft_color_tokens(_format_tokens_for_diff(unit.source_text, dialect))
    target_hard, target_color = split_soft_color_tokens(_format_tokens_for_diff(unit.current_text, dialect))
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
    dialect = format_dialect(unit.file_rel, unit.ref.kind)
    source_tokens = format_counter_items(
        _format_tokens_for_diff(unit.source_text, dialect)
    ) or translate("format.tooltip.source_tokens_empty")
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


def _format_tokens_for_diff(text: str, dialect: str = FORMAT_GUILD2) -> Counter[str]:
    tokens = format_tokens(text, dialect=dialect)
    if dialect == FORMAT_GUILD2:
        tokens.pop("$N", None)
        for token in [value for value in tokens if value.startswith("$[")]:
            del tokens[token]
    return tokens


def _extract_recommended_translation(markdown: str) -> str:
    match = re.search(r"```(?:[A-Za-z0-9_-]+)?[ \t]*\r?\n(.*?)```", markdown, flags=re.DOTALL)
    return match.group(1).strip() if match else ""


def _theme_color(name: str, fallback: str) -> str:
    app = QApplication.instance()
    if app is not None and bool(app.property("guild2Theme")):
        return {
            "text": "#d8c68f",
            "muted_text": "#a99667",
            "format_token": "#9bb69b",
            "color_token": "#c49a82",
            "markup_token": "#d0ad61",
            "quote_token": "#9bb276",
            "bad_token": "#d4775d",
            "glyph_token": "#d4775d",
        }.get(name, fallback)
    if app is not None and bool(app.property("darkTheme")):
        return {
            "text": "#e3e7ee",
            "muted_text": "#747d8b",
            "format_token": "#79b8ff",
            "color_token": "#d2a8ff",
            "markup_token": "#e3b341",
            "quote_token": "#7ee787",
            "bad_token": "#ff7b72",
            "glyph_token": "#ff9b8f",
        }.get(name, fallback)
    return fallback


def _theme_row_tint(name: str, fallback: str) -> str:
    app = QApplication.instance()
    if app is not None and bool(app.property("darkTheme")):
        return {
            "delete": "#45262b",
            "review": "#4b3823",
            "glyph": "#433b25",
            "recent": "#20392d",
        }.get(name, fallback)
    if app is not None and bool(app.property("guild2Theme")):
        return {
            "delete": "#4b241d",
            "review": "#57401f",
            "glyph": "#4b4025",
            "recent": "#283c28",
        }.get(name, fallback)
    return fallback


def _theme_rgba(name: str, fallback: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    value = _theme_color(name, "")
    if not value.startswith("#") or len(value) != 7:
        return fallback
    return tuple(int(value[index : index + 2], 16) for index in (1, 3, 5)) + (fallback[3],)


def _text_format(color: str, underline: bool = False) -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.setForeground(QColor(color))
    fmt.setFontUnderline(underline)
    return fmt


def apply_modern_style(app: QApplication) -> None:
    app.setProperty("guild2Theme", False)
    app.setProperty("darkTheme", False)
    app.setProperty("gameThemeQss", "")
    app.setStyleSheet("")
    app.setStyle("Fusion")
    app._game_theme_style = None
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#ebdbb2"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#3c3836"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#fbf1c7"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#ebdbb2"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#3c3836"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#fbf1c7"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#3c3836"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#d79921"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#3c3836"))
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#cc241d"))
    palette.setColor(QPalette.ColorRole.Link, QColor("#076678"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#c6a15b"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#3c3836"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#7c6f64"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#928374"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#928374"))
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
        QPushButton#reviewAttention { background: #cc241d; color: #fbf1c7; min-height: 25px; }
        QPushButton#reviewAttention:hover { background: #d65d0e; }
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
        #projectStateBadge[state="added"] { background: #c6a15b; color: #3c3836; }
        #projectStateBadge[state="missing"] { background: #d79921; color: #3c3836; }
        #projectManagerPath { color: #665c54; font-weight: 600; }
        #projectManagerFeedback { background: #dce5b5; border: 2px solid #3c3836; border-radius: 8px; padding: 8px 10px; font-weight: 700; }
        #projectAddButton { font-size: 18px; min-width: 36px; }
        QGroupBox { background: #fbf1c7; border: 3px solid #3c3836; border-radius: 8px; margin-top: 14px; padding-top: 8px; font-weight: 900; color: #3c3836; }
        QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 12px; padding: 0 6px; background: #fbf1c7; }
        QTableView { background: #fbf1c7; border: 3px solid #3c3836; border-radius: 8px; gridline-color: #928374; selection-background-color: #c6a15b; selection-color: #3c3836; }
        QTableView::item { background: transparent; border-bottom: 1px solid #d5c4a1; padding: 2px 4px; }
        QTableView::item:selected { background: #c6a15b; color: #3c3836; }
        QHeaderView::section { background: #d79921; color: #3c3836; border: 0; border-right: 2px solid #3c3836; border-bottom: 3px solid #3c3836; padding: 8px; font-weight: 900; }
        QPlainTextEdit, QTextEdit, QTextBrowser { background: #fbf1c7; border: 0; padding: 8px; selection-background-color: #c6a15b; selection-color: #3c3836; }
        QListWidget { background: #fbf1c7; border: 3px solid #3c3836; border-radius: 8px; padding: 3px; font-size: 12px; }
        QListWidget::item { padding: 4px 7px; border-radius: 4px; }
        QListWidget::item:selected { background: #c6a15b; color: #3c3836; }
        QLineEdit, QComboBox { background: #f2e5bc; border: 2px solid #3c3836; border-radius: 5px; padding: 5px 7px; min-height: 20px; font-weight: 600; }
        QLineEdit:focus, QComboBox:focus { border: 3px solid #458588; }
        QComboBox QAbstractItemView { background: #f2e5bc; border: 2px solid #3c3836; selection-background-color: #c6a15b; selection-color: #3c3836; }
        QPushButton, QToolButton { background: #d79921; color: #3c3836; border: 2px solid #3c3836; border-bottom: 5px solid #3c3836; border-radius: 5px; padding: 5px 10px 3px 10px; font-weight: 900; }
        QPushButton:hover, QToolButton:hover { background: #e8b75d; }
        QToolButton#codeReferenceButton { background: #d79921; border: 2px solid #3c3836; border-radius: 4px; padding: 0 8px; }
        QToolButton#codeReferenceButton:disabled { background: #d5c4a1; color: #7c6f64; }
        QLabel#codeReferenceCount { background: #fbf1c7; color: #665c54; font-weight: 800; padding: 0 6px; }
        QListWidget { background: #fbf1c7; border: 3px solid #3c3836; border-radius: 8px; padding: 3px; font-size: 12px; }
        QListWidget::item { padding: 4px 7px; border-radius: 4px; }
        QListWidget::item:selected { background: #c6a15b; color: #3c3836; }
        QToolButton#previewToggle { border: 2px solid #3c3836; border-radius: 4px; padding: 0 6px; }
        QToolButton#previewToggle:pressed { border: 2px solid #3c3836; padding: 1px 5px 0 7px; }
        QToolButton#previewToggle:checked { background: #689d6a; color: #fbf1c7; }
        QToolButton#searchCaseButton { color: #665c54; font-weight: 800; border-radius: 4px; padding: 0; }
        QToolButton#searchCaseButton:hover { background: #e5d6aa; }
        QToolButton#searchCaseButton:checked { color: #fbf1c7; background: #458588; }
        QPushButton:pressed, QToolButton:pressed { border-top: 5px solid #3c3836; border-bottom: 2px solid #3c3836; padding: 8px 8px 2px 12px; }
        QPushButton#primary { background: #458588; color: #fbf1c7; }
        QPushButton#primary:hover { background: #689d6a; }
        QPushButton#batchAi[mode="busy"] { background: #689d6a; color: #fbf1c7; }
        QPushButton#batchAi[mode="cancel"] { background: #cc241d; color: #fbf1c7; }
        QPushButton#batchAi[mode="cancelling"] { background: #d65d0e; color: #fbf1c7; }
        QMenu { background: #fbf1c7; border: 3px solid #3c3836; padding: 4px; }
        QMenu::item { padding: 7px 22px 7px 10px; font-weight: 700; }
        QMenu::item:selected { background: #c6a15b; color: #3c3836; }
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


def apply_dark_style(app: QApplication) -> None:
    app.setProperty("guild2Theme", False)
    app.setProperty("darkTheme", True)
    app.setProperty("gameThemeQss", "")
    app.setStyleSheet("")
    app.setStyle("Fusion")
    app._game_theme_style = None
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#171a1f"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#e3e7ee"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#111318"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#1d2128"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#252a33"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#f2f4f8"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#e3e7ee"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#252a33"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#e3e7ee"))
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#ff7b72"))
    palette.setColor(QPalette.ColorRole.Link, QColor("#79b8ff"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#3f6693"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#747d8b"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#68717d"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#68717d"))
    app.setPalette(palette)
    app.setStyleSheet(
        """
        QWidget { color: #e3e7ee; font-family: "Segoe UI", "Microsoft YaHei UI"; font-size: 13px; }
        QMainWindow, #root, QDialog { background: #171a1f; }
        #titlebar { background: #20252d; border: 1px solid #343b46; border-radius: 8px; }
        #workspaceTitle { color: #f2f4f8; font-size: 18px; font-weight: 900; letter-spacing: 1px; }
        #workspaceSubtitle { color: #8c96a5; font-size: 10px; font-weight: 800; letter-spacing: 2px; }
        #toolbar { background: #20252d; border: 1px solid #343b46; border-radius: 8px; }
        #toolbar QLabel { font-weight: 800; }
        #counts { background: #111318; border: 1px solid #343b46; border-radius: 6px; color: #cdd3dc; font-weight: 800; padding: 5px 8px; }
        #issues { background: #38252c; border: 1px solid #75414c; border-radius: 7px; padding: 8px 10px; color: #ffd7dc; font-weight: 600; }
        #hint, #historyHint, #suggestionStatus { color: #9aa4b2; font-weight: 600; }
        #projectManagerDialog { background: #171a1f; }
        #projectManagerSummary, #projectManagerRow { background: #20252d; border: 1px solid #3a424e; border-radius: 8px; padding: 8px 10px; }
        #projectManagerGameRoot { background: #111318; border: 1px solid #343b46; border-radius: 8px; padding: 7px 10px; }
        #projectManagerPath { color: #9aa4b2; font-weight: 600; }
        #projectManagerFeedback { background: #1f382d; border: 1px solid #3f765b; border-radius: 8px; padding: 8px 10px; }
        #projectKindBadge[kind="vanilla"] { background: #315f78; color: #f2f4f8; }
        #projectKindBadge[kind="mod"] { background: #376b4b; color: #f2f4f8; }
        #projectStateBadge[state="added"] { background: #806b31; color: #ffffff; }
        #projectStateBadge[state="missing"] { background: #8b5628; color: #ffffff; }
        QGroupBox { background: #20252d; border: 1px solid #3a424e; border-radius: 8px; margin-top: 14px; padding-top: 8px; font-weight: 900; }
        QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 12px; padding: 0 6px; background: #20252d; color: #dce1e8; }
        QTableView, QListWidget, QPlainTextEdit, QTextEdit, QTextBrowser { background: #111318; color: #e3e7ee; border: 1px solid #343b46; border-radius: 7px; selection-background-color: #3f6693; selection-color: #ffffff; }
        QTableView { gridline-color: #303641; }
        QTableView::item { background: transparent; border-bottom: 1px solid #292f38; padding: 2px 4px; }
        QTableView::item:selected, QListWidget::item:selected { background: #3f6693; color: #ffffff; }
        QHeaderView::section { background: #252a33; color: #dce1e8; border: 0; border-right: 1px solid #3a424e; border-bottom: 1px solid #4b5563; padding: 8px; font-weight: 900; }
        QLineEdit, QComboBox, QSpinBox { background: #111318; color: #e3e7ee; border: 1px solid #3a424e; border-radius: 5px; padding: 5px 7px; min-height: 20px; }
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border: 1px solid #6ea8e0; }
        QComboBox QAbstractItemView { background: #20252d; color: #e3e7ee; border: 1px solid #4b5563; selection-background-color: #3f6693; selection-color: #ffffff; }
        QPushButton, QToolButton { background: #2b313b; color: #e3e7ee; border: 1px solid #4b5563; border-radius: 5px; padding: 6px 10px; font-weight: 800; }
        QPushButton:hover, QToolButton:hover { background: #353d49; border-color: #687483; }
        QPushButton:pressed, QToolButton:pressed { background: #20252d; }
        QPushButton:disabled, QToolButton:disabled { background: #20242b; color: #68717d; border-color: #303641; }
        QPushButton#primary { background: #315f8a; color: #ffffff; border-color: #578bc0; }
        QPushButton#primary:hover { background: #3b70a1; }
        QPushButton#reviewAttention, QPushButton#batchAi[mode="cancel"] { background: #793b43; color: #ffffff; border-color: #a85a64; }
        QPushButton#batchAi[mode="busy"] { background: #326848; color: #ffffff; }
        QPushButton#batchAi[mode="cancelling"] { background: #875626; color: #ffffff; }
        QToolButton#previewToggle:checked, QToolButton#searchCaseButton:checked { background: #315f8a; color: #ffffff; border-color: #578bc0; }
        QToolButton#searchCaseButton { color: #9aa4b2; padding: 0; }
        QLabel#codeReferenceCount { background: #111318; color: #9aa4b2; padding: 0 6px; }
        QTabWidget::pane { background: #171a1f; border: 1px solid #3a424e; top: -1px; }
        QTabBar::tab { background: #20252d; color: #aeb7c4; border: 1px solid #343b46; padding: 7px 12px; }
        QTabBar::tab:selected { background: #2b313b; color: #ffffff; border-bottom-color: #2b313b; }
        QMenu { background: #20252d; color: #e3e7ee; border: 1px solid #4b5563; padding: 4px; }
        QMenu::item { padding: 7px 22px 7px 10px; }
        QMenu::item:selected { background: #3f6693; color: #ffffff; }
        QMenu::separator { height: 1px; background: #3a424e; margin: 6px 8px; }
        QToolTip { background: #252a33; color: #f2f4f8; border: 1px solid #687483; padding: 5px; }
        QStatusBar { background: #171a1f; color: #9aa4b2; }
        QScrollBar:vertical { background: #111318; width: 14px; margin: 0; }
        QScrollBar:horizontal { background: #111318; height: 14px; margin: 0; }
        QScrollBar::handle { background: #3a424e; border-radius: 5px; min-height: 28px; min-width: 28px; }
        QScrollBar::handle:hover { background: #4b5563; }
        QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
        QSplitter::handle { background: #343b46; }
        """
    )


def _game_theme_asset(name: str) -> str:
    return (Path(__file__).resolve().parents[1] / "assets" / "game_theme" / name).as_posix()


def apply_game_style(app: QApplication) -> None:
    asset = _game_theme_asset
    app.setProperty("guild2Theme", True)
    app.setProperty("darkTheme", False)
    app.setStyleSheet("")
    app.setStyle("Fusion")
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#2b2419"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#eadca7"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#2b2419"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#33291c"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#2b2419"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#eadca7"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#eadca7"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#4a321d"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#eadca7"))
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#d4775d"))
    palette.setColor(QPalette.ColorRole.Link, QColor("#d0ad61"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#876a2f"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#fff0b8"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#a99667"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#75684a"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#75684a"))
    app.setPalette(palette)
    install_game_theme_style(app)
    qss = f"""
        QMainWindow, #root {{ background-color: #2b2419; background-image: url({asset("dark_panel_background_2048.png")}); background-repeat: no-repeat; background-position: top left; border: 4px solid transparent; border-image: url({asset("Border_4px_4.png")}) 4 4 4 4 stretch stretch; }}
        #titlebar {{ background-color: #4a1e12; min-height: 42px; }}
        #workspaceTitle {{ color: #e1c777; font-size: 18px; font-weight: 900; letter-spacing: 1px; }}
        #workspaceSubtitle {{ color: #b89a57; font-size: 10px; font-weight: 800; letter-spacing: 2px; }}
        #toolbar {{ background-color: #2b2419; background-image: url({asset("dark_panel_background_2048.png")}); background-repeat: no-repeat; border: 4px solid transparent; border-image: url({asset("Border_4px_4.png")}) 4 4 4 4 stretch stretch; padding: 2px; }}
        #toolbar QLabel {{ font-weight: 800; }}
        #counts, #issues, #hint {{ background: transparent; color: #eadca7; }}
        #counts {{ border-image: url({asset("Border_4px_4.png")}) 4 4 4 4 stretch stretch; padding: 5px 8px; font-weight: 800; }}
        QPushButton#reviewAttention {{ background: #8f2f20; color: #f3dfa0; border: 2px solid #c49b55; padding: 5px 10px; font-weight: 900; }}
        QPushButton#reviewAttention:hover {{ background: #b0442c; }}
        #issues {{ border-image: url({asset("Border_4px_4.png")}) 4 4 4 4 stretch stretch; padding: 8px 10px; font-weight: 600; }}
        #tablePanel, #editorPanel {{ background-color: #2b2419; background-image: url({asset("dark_panel_background_2048.png")}); background-repeat: no-repeat; border: 0px; }}
        #editorPanel {{ margin-top: 10px; }}
        #editorPanel QPlainTextEdit {{ background-color: #211a12; background-image: url({asset("dark_panel_background_2048.png")}); background-repeat: no-repeat; color: #d8c68f; selection-background-color: #6c4d25; selection-color: #ead79a; padding: 10px; }}
        QSplitter::handle {{ background: #806537; }}
        QDialog {{ background-color: #2b2419; background-image: url({asset("dark_panel_background_2048.png")}); background-repeat: no-repeat; }}
        QGroupBox {{ background: transparent; border: 0px; margin-top: 14px; padding-top: 8px; font-weight: 900; color: #eadca7; }}
        QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; left: 12px; padding: 0 6px; background: #33291c; color: #d8c68f; }}
        QAbstractScrollArea {{ background: transparent; }}
        QTableView, QListWidget, QPlainTextEdit, QTextEdit, QTextBrowser {{ background: transparent; border: 0px; selection-background-color: #735727; selection-color: #fff0b8; }}
        QTableView::viewport, QListWidget::viewport, QAbstractScrollArea > QWidget {{ background: transparent; }}
        QTableView::item {{ background: transparent; border-bottom: 1px solid #5a4526; padding: 2px 4px; }}
        QTableView::item:selected, QListWidget::item:selected {{ background: #735727; color: #fff0b8; }}
        QHeaderView::section {{ background-color: #4a1e12; background-image: url({asset("header_middle.png")}); background-repeat: repeat-x; color: #d8c68f; border-right: 1px solid #9d7a36; border-bottom: 2px solid #c4a258; padding: 8px; font-weight: 900; }}
        QLineEdit {{ background: #0d0b08; color: #d8c68f; padding: 5px 7px; min-height: 20px; font-weight: 600; }}
        QComboBox QAbstractItemView {{ background: #2b2419; color: #d8c68f; selection-background-color: #735727; selection-color: #ead79a; }}
        QTabWidget, QTabWidget > QWidget {{ background: transparent; }}
        QTabWidget::pane {{ background: transparent; border: 2px solid #806537; top: -1px; }}
        QScrollBar:vertical {{ background: #1b1510; width: 18px; margin: 18px 0 18px 0; }}
        QScrollBar:horizontal {{ background: #1b1510; height: 18px; margin: 0 18px 0 18px; }}
        QStatusBar {{ background: #2b2419; color: #eadca7; font-weight: 700; }}
        QMenu, QToolTip {{ background-color: #2b2419; color: #eadca7; border: 2px solid #806537; padding: 4px; }}
        QMenu::item {{ padding: 7px 22px 7px 10px; font-weight: 700; }}
        QMenu::item:selected {{ background: #735727; color: #fff0b8; }}
        """
    app.setProperty("gameThemeQss", qss)
    app.setStyleSheet("")


def apply_theme(app: QApplication | None, theme: str) -> None:
    if app is None:
        return
    if theme == "guild2":
        apply_game_style(app)
    elif theme == "dark":
        apply_dark_style(app)
    else:
        apply_modern_style(app)


def main() -> None:
    configure_diagnostics()
    startup_started = time.perf_counter()
    try:
        app = QApplication([])
        app.setApplicationName("The Guild 2 Translator")
        if APP_ICON_PATH.exists():
            app.setWindowIcon(QIcon(str(APP_ICON_PATH)))
        apply_theme(app, load_settings().ui_theme)
        window = TranslatorWindow()
        log_metrics("startup_window_ready", total_ms=(time.perf_counter() - startup_started) * 1000)
        if APP_ICON_PATH.exists():
            window.setWindowIcon(QIcon(str(APP_ICON_PATH)))
        window.show()
        app.exec()
    except BaseException:
        error_type, _error, tb = sys.exc_info()
        if error_type is not None:
            log_exception("main_failed", error_type, tb)
        raise
    finally:
        log_metrics("session_end")
        shutdown_diagnostics()


if __name__ == "__main__":
    main()
