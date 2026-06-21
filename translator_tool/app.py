from __future__ import annotations

from collections import Counter
from dataclasses import replace
import math
from pathlib import Path
import re
import sys
import threading
import time
from typing import Iterable

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
from PySide6.QtGui import QAction, QCloseEvent, QColor, QFont, QKeySequence, QPainter, QPalette, QPen, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
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
    QSplitter,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionViewItem,
    QTableView,
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
from .git_history import GitCommit, GitError, LanguageGit, format_entries
from .history import OperationHistory, TranslationOperation, UnitChange
from .project import (
    MISSING_WORK_STATUSES,
    Project,
    ProjectError,
    STATUS_EMPTY,
    STATUS_EXTRA,
    STATUS_IGNORED,
    STATUS_MISSING_ROW,
    STATUS_SAME,
    STATUS_TRANSLATED,
    STATUS_TRANSLATION_ONLY,
    SaveValidationError,
    TranslationUnit,
)
from .settings import AppSettings, load_settings, protect_secret, reveal_secret, save_settings
from .validation import (
    CHINESE_QUOTE_RE,
    FULLWIDTH_SYNTAX_RE,
    HIGHLIGHT_RE,
    format_counter_items,
    format_tokens,
    split_soft_color_tokens,
)


PROJECT_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])).resolve()
TYPING_GROUP_DELAY_MS = 750


class UnitTableModel(QAbstractTableModel):
    FILE, ID, LABEL, FIELD, SOURCE, TRANSLATION, STATUS, FORMAT, AI = range(9)
    HEADERS = ("文件", "ID", "标签 / Key", "字段", "原文", "译文", "状态", "格式", "AI 翻译")
    WIDTHS = (145, 68, 210, 90, 280, 280, 88, 76, 78)

    def __init__(self, project: Project | None = None) -> None:
        super().__init__()
        self.project = project
        self.units: list[TranslationUnit] = list(project.units) if project else []
        self._search: dict[str, str] = {}
        self._format_warning: dict[str, bool] = {}
        self._rebuild_search()

    def set_project(self, project: Project) -> None:
        self.beginResetModel()
        self.project = project
        self.units = list(project.units)
        self._format_warning.clear()
        self._rebuild_search()
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.units)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
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
                return "左键：翻译当前条目\n右键：切换 Google Translate / OpenAI 兼容 LLM"
            if index.column() == self.STATUS:
                return unit.display_status()
        if role == Qt.ItemDataRole.BackgroundRole:
            return None
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        column = index.column()
        values = {
            self.FILE: unit.file_rel,
            self.ID: unit.record_id,
            self.LABEL: _clip(unit.label, 72),
            self.FIELD: unit.field_name,
            self.SOURCE: _clip(unit.source_text, 130),
            self.TRANSLATION: _clip(unit.current_text, 130),
            self.STATUS: unit.display_status(),
            self.FORMAT: _format_diff_text(unit),
            self.AI: "翻译",
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
        try:
            row = self.units.index(unit)
        except ValueError:
            return
        self._search[unit.uid] = _search_blob(unit)
        self._format_warning.pop(unit.uid, None)
        self.dataChanged.emit(self.index(row, 0), self.index(row, self.columnCount() - 1))

    def has_format_warning(self, row: int) -> bool:
        unit = self.unit_at(row)
        if unit is None:
            return False
        if unit.uid not in self._format_warning:
            self._format_warning[unit.uid] = bool(unit.issues())
        return self._format_warning[unit.uid]

    def _rebuild_search(self) -> None:
        self._search = {unit.uid: _search_blob(unit) for unit in self.units}


class UnitFilterProxyModel(QSortFilterProxyModel):
    def __init__(self) -> None:
        super().__init__()
        self.file_filter = "全部文件"
        self.status_filter = "全部"
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
        if self.file_filter != "全部文件" and unit.file_rel != self.file_filter:
            return False
        effective_status = unit.filter_status()
        if self.status_filter == "待翻译" and effective_status not in MISSING_WORK_STATUSES:
            return False
        if self.status_filter == "全部" and self.only_missing and effective_status not in MISSING_WORK_STATUSES:
            return False
        if self.status_filter not in {"全部", "待翻译"} and effective_status != self.status_filter:
            return False
        if self.only_format_warnings and not source.has_format_warning(source_row):
            return False
        return not self.query or self.query in source.search_blob(source_row)


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
        uid = str(index.data(Qt.ItemDataRole.UserRole) or "")
        pressed = uid == self._pressed_uid
        hovered = uid == self._hover_uid
        painter.save()
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
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "翻译")
        painter.restore()

    def editorEvent(self, event, model, option: QStyleOptionViewItem, index: QModelIndex) -> bool:  # noqa: N802
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
        "+": QColor("#98971a"),
        "-": QColor("#cc241d"),
        "!": QColor("#cc241d"),
        "~": QColor("#d79921"),
        "✓": QColor("#689d6a"),
    }

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        unit = index.model().data(index, Qt.ItemDataRole.UserRole)
        table_model = index.model()
        if isinstance(table_model, QSortFilterProxyModel):
            source_index = table_model.mapToSource(index)
            source_model = table_model.sourceModel()
            unit = source_model.unit_at(source_index.row()) if isinstance(source_model, UnitTableModel) else None
        if not isinstance(unit, TranslationUnit):
            super().paint(painter, option, index)
            return

        background = QStyleOptionViewItem(option)
        background.text = ""
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, background, painter, option.widget)

        parts = _format_diff_parts(unit)
        painter.save()
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        left = option.rect.left() + 5
        right = option.rect.right() - 5
        baseline = option.rect.top() + (option.rect.height() + metrics.ascent() - metrics.descent()) // 2
        for marker, content in parts:
            visible = marker + _compact_token(content)
            width = metrics.horizontalAdvance(visible)
            if left + width > right:
                painter.setPen(QColor("#928374"))
                painter.drawText(left, baseline, "…")
                break
            painter.setPen(self.COLORS.get(marker, QColor("#3c3836")))
            painter.drawText(left, baseline, visible)
            left += width + metrics.horizontalAdvance(" ")
        painter.restore()


class StatusBadgeDelegate(QStyledItemDelegate):
    STYLES = {
        STATUS_TRANSLATED: ("已翻译", "#98971a", "#fbf1c7"),
        "已修改": ("已修改", "#458588", "#fbf1c7"),
        "待新增": ("待新增", "#d65d0e", "#fbf1c7"),
        STATUS_MISSING_ROW: ("待新增", "#d65d0e", "#fbf1c7"),
        STATUS_EMPTY: ("空译文", "#cc241d", "#fbf1c7"),
        STATUS_SAME: ("未翻译", "#d79921", "#3c3836"),
        STATUS_IGNORED: ("已忽略", "#928374", "#fbf1c7"),
        STATUS_EXTRA: ("多余", "#b16286", "#fbf1c7"),
        STATUS_TRANSLATION_ONLY: ("仅译文", "#689d6a", "#fbf1c7"),
    }

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        status = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        label, fill, text = self.STYLES.get(status, (status or "未知", "#928374", "#fbf1c7"))
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
        super().__init__("AI 批量翻译", parent)
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
            self.setText("AI 批量翻译")
            self.setToolTip("翻译当前筛选的未译条目")
            mode = "idle"
        elif self._cancelling:
            self.setText("正在取消…")
            self.setToolTip("正在停止剩余翻译请求")
            mode = "cancelling"
        elif self._hovering:
            self.setText("取消  ×")
            self.setToolTip("点击取消当前批量翻译")
            mode = "cancel"
        else:
            progress = f" {self._current}/{self._total}" if self._total else ""
            self.setText(f"翻译中…{progress}")
            self.setToolTip("批量翻译进行中；悬浮后点击可取消")
            mode = "busy"
        if self.property("mode") != mode:
            self.setProperty("mode", mode)
            self.style().unpolish(self)
            self.style().polish(self)
        self.update()


class TokenHighlighter(QSyntaxHighlighter):
    def __init__(self, document) -> None:
        super().__init__(document)
        self.format_token = _text_format("#075a9c")
        self.color_token = _text_format("#7a3e9d")
        self.markup_token = _text_format("#6b6b00")
        self.quote_token = _text_format("#107c10")
        self.bad_token = _text_format("#b00020", underline=True)
        self.warn_token = _text_format("#c45f00", underline=True)

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
        for match in FULLWIDTH_SYNTAX_RE.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), self.bad_token)
        for match in CHINESE_QUOTE_RE.finditer(text):
            self.setFormat(match.start(), match.end() - match.start(), self.warn_token)


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
                self.signals.failed.emit(unit.uid, f"意外错误：{exc}")
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
                self.signals.failed.emit(f"意外错误：{exc}")
        finally:
            self.signals.finished.emit()


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AI 与 Git 设置")
        self.setMinimumWidth(680)
        self.settings = settings
        layout = QVBoxLayout(self)

        service_group = QGroupBox("默认 AI 翻译服务")
        service_form = QFormLayout(service_group)
        self.provider = QComboBox()
        self.provider.addItem("Google Translate（公共免费端点）", "google")
        self.provider.addItem("OpenAI 兼容接口", "openai")
        self.provider.setCurrentIndex(0 if settings.provider != "openai" else 1)
        service_form.addRow("单条 / 批量翻译", self.provider)
        self.provider_note = QLabel()
        self.provider_note.setObjectName("hint")
        self.provider_note.setWordWrap(True)
        service_form.addRow(self.provider_note)
        layout.addWidget(service_group)

        google_group = QGroupBox("Google Translate（公共免费端点）")
        google_form = QFormLayout(google_group)
        self.google_endpoint = QLineEdit(settings.google_endpoint)
        self.source_language = QLineEdit(settings.source_language)
        self.target_language = QLineEdit(settings.target_language)
        google_form.addRow("端点", self.google_endpoint)
        google_form.addRow("源语言", self.source_language)
        google_form.addRow("目标语言", self.target_language)
        layout.addWidget(google_group)

        openai_group = QGroupBox("OpenAI 兼容 LLM（也用于右键翻译建议）")
        openai_form = QFormLayout(openai_group)
        self.openai_base_url = QLineEdit(settings.openai_base_url)
        self.openai_model = QLineEdit(settings.openai_model)
        self.openai_key = QLineEdit(reveal_secret(settings.openai_api_key_protected))
        self.openai_key.setEchoMode(QLineEdit.EchoMode.Password)
        openai_form.addRow("Base URL", self.openai_base_url)
        openai_form.addRow("模型", self.openai_model)
        openai_form.addRow("API Key", self.openai_key)
        layout.addWidget(openai_group)

        git_group = QGroupBox("Git 自动提交身份")
        git_form = QFormLayout(git_group)
        self.git_name = QLineEdit(settings.git_author_name)
        self.git_email = QLineEdit(settings.git_author_email)
        git_form.addRow("作者名", self.git_name)
        git_form.addRow("邮箱", self.git_email)
        layout.addWidget(git_group)

        note = QLabel("Google 公共端点不需要 Key，但可能受限速或上游变更影响。API Key 仅保存到当前 Windows 用户的本地设置。")
        note.setWordWrap(True)
        note.setObjectName("hint")
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.provider.currentIndexChanged.connect(self._update_enabled)
        self._update_enabled()

    def _update_enabled(self) -> None:
        if self.provider.currentData() == "openai":
            self.provider_note.setText("单条和批量翻译将使用 OpenAI 兼容接口。右键“LLM 翻译建议”也使用此配置。")
        else:
            self.provider_note.setText("单条和批量翻译将使用 Google Translate。右键“LLM 翻译建议”始终使用下方 OpenAI 兼容配置。")

    def result_settings(self) -> AppSettings:
        return replace(
            self.settings,
            provider=str(self.provider.currentData()),
            google_endpoint=self.google_endpoint.text().strip(),
            source_language=self.source_language.text().strip() or "en",
            target_language=self.target_language.text().strip() or "zh-CN",
            openai_base_url=self.openai_base_url.text().strip(),
            openai_model=self.openai_model.text().strip(),
            openai_api_key_protected=protect_secret(self.openai_key.text().strip()),
            git_author_name=self.git_name.text().strip() or "The Guild 2 Translator",
            git_author_email=self.git_email.text().strip() or "translator@local",
        )


class SuggestionDialog(QDialog):
    apply_translation = Signal(str)
    dismissed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("suggestionDialog")
        self.setWindowTitle("LLM 翻译建议")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setModal(False)
        self.setMinimumSize(350, 200)
        self.resize(350, 250)
        self._markdown = ""
        self._recommended_translation = ""

        layout = QVBoxLayout(self)
        self.loading_label = QLabel("正在生成建议…")
        self.loading_label.setObjectName("suggestionStatus")
        layout.addWidget(self.loading_label)
        self.content = QTextBrowser()
        self.content.setOpenExternalLinks(False)
        self.content.setPlaceholderText("LLM 会先解释原文，再在代码块中给出一条推荐译文。")
        layout.addWidget(self.content, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.apply_button = buttons.addButton("应用推荐译文", QDialogButtonBox.ButtonRole.AcceptRole)
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self._apply)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

    def append_chunk(self, chunk: str) -> None:
        self._markdown += chunk
        self.content.setMarkdown(self._markdown)
        self.content.verticalScrollBar().setValue(self.content.verticalScrollBar().maximum())

    def show_failure(self, message: str) -> None:
        self.loading_label.setText("错误")
        self.content.setPlainText(message)

    def complete(self) -> None:
        self.loading_label.setText("建议")
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
        self.setWindowTitle("Git 更新日志")
        self.resize(1080, 680)
        layout = QHBoxLayout(self)
        self.commits = QListWidget()
        self.commits.setMinimumWidth(370)
        self.commits.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        history_column = QVBoxLayout()
        selection_hint = QLabel("Shift 选择范围 · Ctrl 添加提交")
        selection_hint.setObjectName("historyHint")
        selection_hint.setWordWrap(True)
        history_column.addWidget(selection_hint)
        history_column.addWidget(self.commits, 1)
        self.content = QPlainTextEdit()
        self.content.setReadOnly(True)
        self.content.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        layout.addLayout(history_column, 1)
        layout.addWidget(self.content, 2)
        self._items: list[GitCommit] = []
        try:
            self._items = git.list_commits()
            self.commits.addItems([commit.display for commit in self._items])
        except GitError as exc:
            self.content.setPlainText(str(exc))
        self.commits.itemSelectionChanged.connect(self._show_selected_commits)
        if self._items:
            self.commits.setCurrentRow(0)
            self.commits.item(0).setSelected(True)

    def _show_selected_commits(self) -> None:
        # Git lists newest first, while combining must apply the oldest change
        # first so a later revision wins when the same entry appears twice.
        rows = sorted((self.commits.row(item) for item in self.commits.selectedItems()), reverse=True)
        if not rows:
            self.content.clear()
            return
        try:
            commits = [self._items[row].full_hash for row in rows]
            entries = self.git.entries_for_commits(commits)
            summary = f"已合并 {len(rows)} 次提交 · {len(entries)} 条最终变更"
            self.content.setPlainText(f"{summary}\n\n{format_entries(entries)}")
        except (GitError, OSError, UnicodeError) as exc:
            self.content.setPlainText(f"无法读取所选提交：{exc}")


class TranslatorWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("The Guild 2 · 中文翻译工作台")
        self.resize(1480, 920)
        self.settings = load_settings()
        self.git = LanguageGit(PROJECT_ROOT)
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
        self.thread_pool = QThreadPool.globalInstance()

        self._build_ui()
        self._load_language_choices()
        self.load_project(discard_changes=True)

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(9)

        toolbar = QFrame()
        toolbar.setObjectName("toolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(10, 8, 10, 8)
        toolbar_layout.setSpacing(8)
        layout.addWidget(toolbar)

        toolbar_layout.addWidget(QLabel("语言"))
        self.language_combo = QComboBox()
        self.language_combo.setMinimumWidth(128)
        self.language_combo.currentTextChanged.connect(lambda _value: self.load_project())
        toolbar_layout.addWidget(self.language_combo)
        toolbar_layout.addWidget(QLabel("状态"))
        self.status_combo = QComboBox()
        self.status_combo.addItems(
            ["全部", "待翻译", STATUS_MISSING_ROW, STATUS_EMPTY, STATUS_SAME, STATUS_TRANSLATED, STATUS_IGNORED, STATUS_EXTRA, STATUS_TRANSLATION_ONLY]
        )
        self.status_combo.setCurrentText("全部")
        self.status_combo.currentTextChanged.connect(self._apply_filters)
        toolbar_layout.addWidget(self.status_combo)
        toolbar_layout.addWidget(QLabel("文件"))
        self.file_combo = QComboBox()
        self.file_combo.setMinimumWidth(190)
        self.file_combo.currentTextChanged.connect(self._apply_filters)
        toolbar_layout.addWidget(self.file_combo)
        self.only_missing = QCheckBox("只显示待翻译")
        self.only_missing.setChecked(True)
        self.only_missing.toggled.connect(self._apply_filters)
        toolbar_layout.addWidget(self.only_missing)
        self.only_format_warnings = QCheckBox("仅格式警告")
        self.only_format_warnings.toggled.connect(self._apply_filters)
        toolbar_layout.addWidget(self.only_format_warnings)
        toolbar_layout.addStretch(1)
        toolbar_layout.addWidget(QLabel("搜索"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("原文、译文、标签、ID…")
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
        for text, slot, primary in (
            ("保存", self.save_all, True),
            ("更新日志", self.show_history, False),
            ("设置", self.show_settings, False),
        ):
            button = QPushButton(text)
            if primary:
                button.setObjectName("primary")
            button.clicked.connect(slot)
            toolbar_layout.addWidget(button)
        self.retry_button = QToolButton()
        self.retry_button.setText("重试提交")
        self.retry_button.setToolTip("提交尚未进入 Git 的语言修改")
        self.retry_button.clicked.connect(self.retry_commit)
        self.retry_button.setVisible(False)
        toolbar_layout.addWidget(self.retry_button)

        self.counts_label = QLabel()
        self.counts_label.setObjectName("counts")
        layout.addWidget(self.counts_label)

        splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(splitter, 1)
        table_frame = QFrame()
        table_layout = QVBoxLayout(table_frame)
        table_layout.setContentsMargins(0, 0, 0, 0)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(False)
        # Keeping source order is both clearer for translators and dramatically faster
        # when switching the filter from pending entries to the full project.
        self.table.setSortingEnabled(False)
        self.table.setWordWrap(False)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_table_menu)
        self.table.selectionModel().currentRowChanged.connect(self._on_row_selected)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.horizontalHeader().setStretchLastSection(False)
        for column, width in enumerate(UnitTableModel.WIDTHS):
            self.table.setColumnWidth(column, width)
        self.table.horizontalHeader().setSectionResizeMode(UnitTableModel.SOURCE, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(UnitTableModel.TRANSLATION, QHeaderView.ResizeMode.Stretch)
        self.ai_delegate = AiButtonDelegate(self.table, self.settings.provider)
        self.ai_delegate.translate_requested.connect(self.translate_one_unit)
        self.table.setItemDelegateForColumn(UnitTableModel.AI, self.ai_delegate)
        self.format_delegate = FormatDiffDelegate(self.table)
        self.table.setItemDelegateForColumn(UnitTableModel.FORMAT, self.format_delegate)
        self.status_delegate = StatusBadgeDelegate(self.table)
        self.table.setItemDelegateForColumn(UnitTableModel.STATUS, self.status_delegate)
        table_layout.addWidget(self.table)
        splitter.addWidget(table_frame)

        editors = QSplitter(Qt.Orientation.Horizontal)
        source_box, self.source_edit = self._editor_group("原文 / English", True)
        translated_box, self.translation_edit = self._editor_group("译文", False)
        self.translation_edit.setUndoRedoEnabled(False)
        self.translation_edit.textChanged.connect(self._on_editor_changed)
        self.source_highlighter = TokenHighlighter(self.source_edit.document())
        self.translation_highlighter = TokenHighlighter(self.translation_edit.document())
        editors.addWidget(source_box)
        editors.addWidget(translated_box)
        editors.setSizes([620, 620])
        splitter.addWidget(editors)
        splitter.setSizes([560, 270])

        self.issue_label = QLabel("选择一个条目开始翻译。")
        self.issue_label.setObjectName("issues")
        self.issue_label.setWordWrap(True)
        layout.addWidget(self.issue_label)
        self.statusBar().showMessage("准备就绪")

        for shortcut, slot in (
            (QKeySequence.StandardKey.Save, self.save_all),
            (QKeySequence.StandardKey.Undo, self.undo),
            (QKeySequence.StandardKey.Redo, self.redo),
            (QKeySequence("Ctrl+Shift+Z"), self.redo),
        ):
            action = QAction(self)
            action.setShortcut(shortcut)
            action.triggered.connect(slot)
            self.addAction(action)

    def _editor_group(self, title: str, read_only: bool) -> tuple[QGroupBox, QPlainTextEdit]:
        box = QGroupBox(title)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 12, 8, 8)
        editor = QPlainTextEdit()
        editor.setReadOnly(read_only)
        editor.setPlaceholderText("从上方列表选择条目")
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        layout.addWidget(editor)
        return box, editor

    def _load_language_choices(self) -> None:
        choices = Project.language_dirs(PROJECT_ROOT)
        blocker = QSignalBlocker(self.language_combo)
        self.language_combo.clear()
        self.language_combo.addItems(choices)
        if "#chinese" in choices:
            self.language_combo.setCurrentText("#chinese")
        del blocker

    def load_project(self, discard_changes: bool = False) -> None:
        if self.project is not None and not discard_changes:
            self._commit_typing_operation()
            if self.project.dirty_units():
                answer = QMessageBox.question(self, "重新加载", "有未保存译文，是否放弃并重新加载？")
                if answer != QMessageBox.StandardButton.Yes:
                    return
        try:
            self.git.ensure_repository(self.settings)
            self.project = Project.load(PROJECT_ROOT, self.language_combo.currentText() or "#chinese")
        except (ProjectError, GitError) as exc:
            QMessageBox.critical(self, "无法加载项目", str(exc))
            return
        self.history.clear()
        self.typing_uid = ""
        self.current_uid = ""
        self.model.set_project(self.project)
        self._update_file_choices()
        self._apply_filters()
        self._set_editor_unit(None)
        self._update_counts()
        self._update_pending_state()
        self.statusBar().showMessage(f"已加载 {len(self.project.units)} 条翻译条目", 4500)

    def _update_file_choices(self) -> None:
        files = ["全部文件", *sorted({unit.file_rel for unit in self.model.units})]
        previous = self.file_combo.currentText()
        blocker = QSignalBlocker(self.file_combo)
        self.file_combo.clear()
        self.file_combo.addItems(files)
        self.file_combo.setCurrentText(previous if previous in files else "全部文件")
        del blocker

    def _apply_filters(self) -> None:
        query = self.search_edit.text()
        clearing_search = bool(self.last_applied_query) and not query.strip()
        selected_uid = self.current_uid
        self.proxy.set_filters(
            file_filter=self.file_combo.currentText() or "全部文件",
            status_filter=self.status_combo.currentText() or "待翻译",
            only_missing=self.only_missing.isChecked(),
            only_format_warnings=self.only_format_warnings.isChecked(),
            query=query,
        )
        self.last_applied_query = query.strip()
        self._update_counts()
        if clearing_search and selected_uid:
            self._restore_selected_row(selected_uid)

    def _on_search_changed(self, text: str) -> None:
        if not text.strip() and self.last_applied_query:
            self.search_debounce.stop()
            self._apply_filters()
            return
        self.search_debounce.start()

    def _restore_selected_row(self, uid: str) -> None:
        unit = self.model.unit_for_uid(uid)
        if unit is None:
            return
        try:
            source_row = self.model.units.index(unit)
        except ValueError:
            return
        proxy_index = self.proxy.mapFromSource(self.model.index(source_row, 0))
        if not proxy_index.isValid():
            return
        self.table.setCurrentIndex(proxy_index)
        self.table.selectRow(proxy_index.row())
        self.table.scrollTo(proxy_index, QAbstractItemView.ScrollHint.PositionAtCenter)
        self.table.setFocus()

    def _update_counts(self) -> None:
        if self.project is None:
            self.counts_label.setText("")
            return
        effective = Counter(unit.filter_status() for unit in self.project.units)
        todo = sum(unit.filter_status() in MISSING_WORK_STATUSES for unit in self.project.units)
        self.counts_label.setText(
            f"当前显示 {self.proxy.rowCount():,} / 总计 {len(self.project.units):,}   ·   "
            f"待翻译 {todo:,}   ·   已翻译 {effective[STATUS_TRANSLATED]:,}   ·   "
            f"无需翻译 {effective[STATUS_IGNORED]:,}"
        )

    def _update_window_title(self) -> None:
        if self.project is None:
            self.setWindowTitle("The Guild 2 · 中文翻译工作台 · 未加载")
            return
        unit = self._current_unit()
        if unit is None:
            location = self.project.language
        elif unit.record_id:
            location = f"{unit.file_rel} · #{unit.record_id}"
        else:
            location = unit.file_rel
        dirty_count = len(self.project.dirty_units())
        save_state = f"未保存 {dirty_count} 条" if dirty_count else "已保存"
        git_state = " · Git 待提交" if self.git_pending else ""
        self.setWindowTitle(f"The Guild 2 · {location} · {save_state}{git_state}")

    def _on_row_selected(self, current: QModelIndex, _previous: QModelIndex) -> None:
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
        self._update_issue_detail(unit)

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
        elif self.typing_uid != unit.uid:
            self._commit_typing_operation()
            self.typing_uid = unit.uid
            self.typing_before = unit.current_text
        unit.set_text(text)
        self.model.refresh_unit(unit)
        self._update_issue_detail(unit)
        self._update_counts()
        self._update_window_title()
        self.typing_timer.start()

    def _commit_typing_operation(self) -> None:
        self.typing_timer.stop()
        if not self.typing_uid:
            return
        unit = self.model.unit_for_uid(self.typing_uid)
        before, self.typing_uid = self.typing_before, ""
        self.typing_before = ""
        if unit is not None and unit.current_text != before:
            self.history.push(TranslationOperation("连续编辑", (UnitChange(unit.uid, before, unit.current_text),)))

    def _apply_operation_text(self, uid: str, text: str) -> None:
        unit = self.model.unit_for_uid(uid)
        if unit is None:
            return
        unit.set_text(text)
        self.model.refresh_unit(unit)
        if uid == self.current_uid:
            self._set_editor_unit(unit)
        self._update_counts()
        self._update_window_title()

    def _replace_current_text(self, text: str, label: str) -> None:
        self._commit_typing_operation()
        unit = self._current_unit()
        if unit is None or unit.current_text == text:
            return
        before = unit.current_text
        self._apply_operation_text(unit.uid, text)
        self.history.push(TranslationOperation(label, (UnitChange(unit.uid, before, text),)))

    def undo(self) -> None:
        self._commit_typing_operation()
        operation = self.history.undo(self._apply_operation_text)
        if operation:
            self.statusBar().showMessage(f"已撤回：{operation.label}", 2500)

    def redo(self) -> None:
        self._commit_typing_operation()
        operation = self.history.redo(self._apply_operation_text)
        if operation:
            self.statusBar().showMessage(f"已重做：{operation.label}", 2500)

    def _show_table_menu(self, point: QPoint) -> None:
        index = self.table.indexAt(point)
        unit = self._unit_from_proxy_index(index)
        if unit is None:
            return
        self.table.setCurrentIndex(index)
        if index.column() == UnitTableModel.AI:
            self._show_ai_provider_menu(self.table.viewport().mapToGlobal(point))
            return
        menu = QMenu(self)
        menu.addSection("译文编辑")
        restore = menu.addAction("恢复载入时的译文")
        source = menu.addAction("还原为原文")
        clear = menu.addAction("清空译文")
        menu.addSection("AI 服务")
        ai_translate = menu.addAction("AI 翻译并填入")
        llm_suggestion = menu.addAction("LLM 翻译建议…")
        menu.addSection("条目状态")
        ignored = menu.addAction("取消无需翻译" if unit.ignored else "标记为无需翻译")
        action = menu.exec(self.table.viewport().mapToGlobal(point))
        if action == restore:
            self._replace_current_text(unit.translate_text, "恢复载入译文")
        elif action == source:
            self._replace_current_text(unit.source_text, "还原为原文")
        elif action == clear:
            self._replace_current_text("", "清空译文")
        elif action == ai_translate:
            self.translate_one_unit(unit.uid)
        elif action == llm_suggestion:
            self.request_llm_suggestion()
        elif action == ignored:
            self._set_ignored(unit, not unit.ignored)

    def _set_ignored(self, unit: TranslationUnit, ignored: bool) -> None:
        if self.project is None:
            return
        self.project.set_unit_ignored(unit, ignored)
        self.model.refresh_unit(unit)
        self._apply_filters()
        self._update_issue_detail(unit)
        self._update_window_title()

    def _show_ai_provider_menu(self, global_point: QPoint) -> None:
        menu = QMenu(self)
        menu.setTitle("AI 翻译服务")
        google = menu.addAction("⚡ Google Translate（公共免费端点）")
        google.setCheckable(True)
        google.setChecked(self.settings.provider != "openai")
        openai = menu.addAction("✦ OpenAI 兼容 LLM")
        openai.setCheckable(True)
        openai.setChecked(self.settings.provider == "openai")
        menu.addSeparator()
        settings_action = menu.addAction("打开 AI 设置…")
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
        name = "Google Translate" if provider == "google" else "OpenAI 兼容 LLM"
        self.statusBar().showMessage(f"AI 翻译服务已切换为：{name}", 3500)

    def translate_one_unit(self, uid: str) -> None:
        self._commit_typing_operation()
        unit = self.model.unit_for_uid(uid)
        if unit is None or not unit.source_text:
            return
        if unit.filter_status() not in MISSING_WORK_STATUSES and unit.current_text:
            answer = QMessageBox.question(self, "重新 AI 翻译", "此条已有译文，是否用 AI 建议替换当前未保存内容？")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._start_ai([unit], "AI 单条翻译")

    def request_llm_suggestion(self) -> None:
        self._commit_typing_operation()
        unit = self._current_unit()
        if unit is None or not unit.source_text:
            return
        if self.suggestion_worker is not None:
            if self.suggestion_dialog is not None:
                self.suggestion_dialog.show()
                self.suggestion_dialog.raise_()
            self.statusBar().showMessage("LLM 建议正在生成中。", 2500)
            return
        provider = llm_provider_from_settings(self.settings)
        if not provider.api_key:
            QMessageBox.information(self, "需要 LLM 设置", "LLM 翻译建议需要 OpenAI 兼容接口。请先在设置中填写 Base URL、模型和 API Key。")
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
        if unit is None or unit.current_text == text:
            return
        before = unit.current_text
        self._apply_operation_text(unit.uid, text)
        self.history.push(TranslationOperation("应用 LLM 建议", (UnitChange(unit.uid, before, text),)))
        self.statusBar().showMessage("已应用 LLM 推荐译文，尚未保存。", 3500)

    def _close_suggestion_dialog(self) -> None:
        if self.suggestion_cancel_event is not None:
            self.suggestion_cancel_event.set()
        self.suggestion_dialog = None

    def translate_visible_units(self) -> None:
        self._commit_typing_operation()
        units: list[TranslationUnit] = []
        for row in range(self.proxy.rowCount()):
            unit = self._unit_from_proxy_index(self.proxy.index(row, 0))
            if unit and unit.source_text and unit.filter_status() in MISSING_WORK_STATUSES:
                units.append(unit)
        if not units:
            QMessageBox.information(self, "AI 批量翻译", "当前筛选中没有可翻译的未译条目。")
            return
        answer = QMessageBox.question(
            self,
            "AI 批量翻译",
            f"将翻译当前筛选的 {len(units)} 条未译条目。已有译文不会被覆盖，结果需保存后才写入文件。继续吗？",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._start_ai(units, f"AI 批量翻译（{len(units)} 条）", is_batch=True)

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
        self.statusBar().showMessage("正在取消批量翻译；已完成的结果仍会保留供审阅。", 4000)

    def _start_ai(self, units: list[TranslationUnit], label: str, *, is_batch: bool = False) -> None:
        if self.ai_worker is not None:
            self.statusBar().showMessage("已有 AI 翻译任务正在运行，请先完成或取消它。", 3500)
            return
        try:
            provider = provider_from_settings(self.settings)
        except Exception as exc:
            QMessageBox.critical(self, "AI 设置错误", str(exc))
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
        if unit is None or unit.current_text == translated:
            return
        self.ai_changes.append(UnitChange(uid, unit.current_text, translated))
        # AI signals are delivered on the GUI thread. Apply each completed
        # result immediately, while retaining one combined undo operation.
        self._apply_operation_text(uid, translated)
        if self.ai_is_batch:
            self._schedule_ai_filter_refresh()

    def _collect_ai_failure(self, uid: str, message: str) -> None:
        self.ai_failures.append(f"{uid}: {message}")

    def _update_ai_progress(self, current: int, total: int) -> None:
        if self.ai_is_batch:
            self.batch_ai_button.set_progress(current, total)
        self.statusBar().showMessage(f"正在请求 AI 翻译… {current}/{total}")

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
        summary = f"AI 已生成 {len(changes)} 条建议"
        if was_cancelled:
            summary = f"批量翻译已取消，已生成 {len(changes)} 条建议"
        elif was_batch:
            summary = f"批量翻译完成：已生成 {len(changes)} 条建议"
        if self.ai_failures:
            summary += f"；{len(self.ai_failures)} 条失败"
            QMessageBox.warning(self, "AI 翻译完成", summary + "\n\n" + "\n".join(self.ai_failures[:8]))
        else:
            self.statusBar().showMessage(summary + "，请审阅后保存。", 5000)
        if was_batch:
            anchor = self.batch_ai_button.mapToGlobal(self.batch_ai_button.rect().bottomLeft())
            QToolTip.showText(anchor, summary + "，请审阅后保存。", self.batch_ai_button, self.batch_ai_button.rect(), 4500)

    def save_all(self) -> None:
        self._commit_typing_operation()
        if self.project is None:
            return
        try:
            result = self.project.save()
        except SaveValidationError as exc:
            QMessageBox.warning(self, "保存被阻止", "\n".join(exc.messages[:20]))
            return
        if not result.changed_files:
            self.statusBar().showMessage("没有需要保存的变更。", 3000)
            return
        commit_note = ""
        try:
            commit = self.git.commit_saved(result.changed_files, result.saved_units)
            commit_note = f"，已创建 Git 提交 {commit.short_hash}" if commit else ""
        except GitError as exc:
            commit_note = f"；文件已保存，但 Git 提交失败：{exc}"
        self.load_project(discard_changes=True)
        self.statusBar().showMessage(f"已保存 {len(result.changed_files)} 个文件{commit_note}", 7000)

    def retry_commit(self) -> None:
        try:
            commit = self.git.commit_pending()
        except GitError as exc:
            QMessageBox.warning(self, "Git 提交失败", str(exc))
            return
        self._update_pending_state()
        self.statusBar().showMessage(f"已提交待处理修改：{commit.short_hash}" if commit else "没有待提交的修改。", 5000)

    def _update_pending_state(self) -> None:
        try:
            pending = self.git.has_pending_changes()
        except GitError:
            pending = True
        self.git_pending = pending
        self.retry_button.setVisible(pending)
        if pending:
            self.statusBar().showMessage("语言仓库有待提交修改。")
        self._update_window_title()

    def show_history(self) -> None:
        HistoryDialog(self.git, self).exec()

    def show_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.settings = dialog.result_settings()
        save_settings(self.settings)
        self.ai_delegate.set_provider(self.settings.provider)
        try:
            self.git.ensure_repository(self.settings)
        except GitError as exc:
            QMessageBox.warning(self, "Git 设置", str(exc))

    def _current_unit(self) -> TranslationUnit | None:
        return self.model.unit_for_uid(self.current_uid) if self.current_uid else None

    def _unit_from_proxy_index(self, index: QModelIndex) -> TranslationUnit | None:
        if not index.isValid():
            return None
        source_index = self.proxy.mapToSource(index)
        return self.model.unit_at(source_index.row())

    def _update_issue_detail(self, unit: TranslationUnit | None) -> None:
        if unit is None:
            self.issue_label.setText("选择一个条目开始翻译。")
            return
        issues = unit.issues()
        errors = [issue.message for issue in issues if issue.blocks_save]
        warnings = [issue.message for issue in issues if not issue.blocks_save]
        parts = []
        if errors:
            parts.append("错误：" + "；".join(errors))
        if warnings:
            parts.append("提示：" + "；".join(warnings))
        if unit.is_dirty:
            parts.append("未保存")
        self.issue_label.setText("   ·   ".join(parts) if parts else "格式正常")

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._commit_typing_operation()
        if self.project is None:
            event.accept()
            return
        dirty_count = len(self.project.dirty_units())
        if not dirty_count:
            event.accept()
            return
        choice = QMessageBox.warning(
            self,
            "未保存的翻译",
            f"当前有 {dirty_count} 条译文尚未保存。退出前要如何处理？",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if choice == QMessageBox.StandardButton.Save:
            self.save_all()
            if self.project is None or not self.project.dirty_units():
                event.accept()
            else:
                event.ignore()
            return
        if choice == QMessageBox.StandardButton.Discard:
            event.accept()
            return
        event.ignore()


def _search_blob(unit: TranslationUnit) -> str:
    return "\n".join(
        (unit.file_rel, unit.record_id, unit.label, unit.field_name, unit.source_text, unit.current_text, unit.status)
    ).lower()


def _clip(text: str, limit: int) -> str:
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _issue_badge(unit: TranslationUnit) -> str:
    issues = unit.issues()
    errors = sum(issue.blocks_save for issue in issues)
    warnings = len(issues) - errors
    if errors:
        return f"✕{errors}" + (f" !{warnings}" if warnings else "")
    return f"!{warnings}" if warnings else "—"


def _format_diff_parts(unit: TranslationUnit) -> list[tuple[str, str]]:
    """Return source-relative format changes in the same visual language as Git."""
    source_hard, source_color = split_soft_color_tokens(_format_tokens_for_diff(unit.source_text))
    target_hard, target_color = split_soft_color_tokens(_format_tokens_for_diff(unit.current_text))
    parts: list[tuple[str, str]] = []
    parts.extend(("!", token) for token in _counter_tokens(source_hard - target_hard))
    parts.extend(("-", token) for token in _counter_tokens(source_color - target_color))
    parts.extend(("+", token) for token in _counter_tokens(target_hard - source_hard))
    parts.extend(("+", token) for token in _counter_tokens(target_color - source_color))

    known_prefixes = ("缺少格式标记", "新增格式标记", "颜色标记不一致")
    for issue in unit.issues():
        if issue.message.startswith(known_prefixes):
            continue
        marker = "!" if issue.blocks_save else "~"
        parts.append((marker, _short_issue_name(issue.message)))
    return parts or [("✓", "")]


def _counter_tokens(counter: Counter[str]) -> list[str]:
    values: list[str] = []
    for token, count in sorted(counter.items()):
        values.append(token if count == 1 else f"{token}×{count}")
    return values


def _short_issue_name(message: str) -> str:
    if "全角" in message:
        return "FW"
    if "双引号" in message:
        return '"'
    if "中文引号" in message:
        return '"'
    if "单个 %" in message:
        return "%"
    if "编码" in message:
        return "ENC"
    return "ERR" if "error" in message.lower() else "WARN"


def _compact_token(token: str) -> str:
    return token if len(token) <= 11 else token[:10] + "…"


def _format_diff_text(unit: TranslationUnit) -> str:
    return " ".join(marker + content for marker, content in _format_diff_parts(unit))


def _format_diff_tooltip(unit: TranslationUnit) -> str:
    source_tokens = format_counter_items(_format_tokens_for_diff(unit.source_text)) or "（原文无格式标记）"
    parts = _format_diff_parts(unit)
    if parts == [("✓", "")]:
        return f"原文格式：{source_tokens}\n译文与原文格式一致"
    difference = " ".join(marker + content for marker, content in parts)
    return f"按原文格式比较：{source_tokens}\n差异：{difference}"


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
        #toolbar { background: #d5c4a1; border: 3px solid #3c3836; border-radius: 10px; }
        #toolbar QLabel { font-weight: 800; }
        #counts { background: #fbf1c7; border: 2px solid #3c3836; border-radius: 6px; color: #3c3836; font-weight: 800; padding: 5px 8px; }
        #issues { background: #d3869b; border: 3px solid #3c3836; border-radius: 7px; padding: 8px 10px; color: #3c3836; font-weight: 600; }
        #hint { color: #3c3836; padding: 4px 0; font-weight: 600; }
        QGroupBox { background: #fbf1c7; border: 3px solid #3c3836; border-radius: 8px; margin-top: 14px; padding-top: 8px; font-weight: 900; color: #3c3836; }
        QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 12px; padding: 0 6px; background: #fbf1c7; }
        QTableView { background: #fbf1c7; border: 3px solid #3c3836; border-radius: 8px; gridline-color: #928374; selection-background-color: #b8bb26; selection-color: #3c3836; }
        QTableView::item { border-bottom: 1px solid #d5c4a1; padding: 2px 4px; }
        QTableView::item:selected { background: #b8bb26; color: #3c3836; }
        QHeaderView::section { background: #d79921; color: #3c3836; border: 0; border-right: 2px solid #3c3836; border-bottom: 3px solid #3c3836; padding: 8px; font-weight: 900; }
        QPlainTextEdit { background: #fbf1c7; border: 0; padding: 8px; selection-background-color: #b8bb26; selection-color: #3c3836; }
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
