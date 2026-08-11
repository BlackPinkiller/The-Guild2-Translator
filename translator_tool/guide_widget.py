from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QRect, QSize, Qt, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextImageFormat,
)
from PySide6.QtWidgets import (
    QAbstractSlider,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollBar,
    QSplitter,
    QStyle,
    QTextBrowser,
    QToolButton,
    QToolTip,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .guide_model import GuideTocEntry, parse_guide_toc, render_guide_html
from .i18n import translate


class _GuideHeader(QFrame):
    def __init__(self, texture_path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._texture = QPixmap(str(texture_path))

    def paintEvent(self, _event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if self._texture.isNull():
            painter.fillRect(self.rect(), QColor("#5b2112"))
        else:
            painter.drawPixmap(self.rect(), self._texture, self._texture.rect())
        painter.setPen(QPen(QColor("#75662f"), 2))
        painter.drawRect(self.rect().adjusted(1, 1, -2, -2))
        painter.setPen(QPen(QColor("#c8ad5b"), 1))
        painter.drawRect(self.rect().adjusted(4, 4, -5, -5))


class _GuideCloseButton(QToolButton):
    def paintEvent(self, _event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.fillRect(rect, QColor("#3f3d1d") if self.underMouse() else QColor("#252818"))
        painter.setPen(QPen(QColor("#c8ad5b"), 1.0))
        painter.drawRect(rect)
        inset = max(4, round(min(rect.width(), rect.height()) * 0.28))
        cross = rect.adjusted(inset, inset, -inset, -inset)
        cross_pen = QPen(QColor("#e0c665"), 1.4)
        cross_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(cross_pen)
        painter.drawLine(cross.topLeft(), cross.bottomRight())
        painter.drawLine(cross.topRight(), cross.bottomLeft())


class _GuideParchment(QWidget):
    """Resizable Guide page frame whose decorated corners never stretch."""

    _SOURCE_INSETS = (110, 96, 110, 104)
    _TARGET_INSETS = (64, 58, 64, 62)

    def __init__(self, texture_path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._texture = QPixmap(str(texture_path))

    def paintEvent(self, _event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if self._texture.isNull():
            painter.fillRect(self.rect(), QColor("#d8bd83"))
            return

        source_width = self._texture.width()
        source_height = self._texture.height()
        left, top, right, bottom = self._SOURCE_INSETS
        target_left, target_top, target_right, target_bottom = self._TARGET_INSETS
        target_left = min(target_left, self.width() // 2)
        target_right = min(target_right, self.width() - target_left)
        target_top = min(target_top, self.height() // 2)
        target_bottom = min(target_bottom, self.height() - target_top)

        source_x = (0, left, source_width - right, source_width)
        source_y = (0, top, source_height - bottom, source_height)
        target_x = (0, target_left, self.width() - target_right, self.width())
        target_y = (0, target_top, self.height() - target_bottom, self.height())
        for row in range(3):
            for column in range(3):
                source = QRect(
                    source_x[column],
                    source_y[row],
                    source_x[column + 1] - source_x[column],
                    source_y[row + 1] - source_y[row],
                )
                target = QRect(
                    target_x[column],
                    target_y[row],
                    target_x[column + 1] - target_x[column],
                    target_y[row + 1] - target_y[row],
                )
                if target.width() > 0 and target.height() > 0:
                    painter.drawPixmap(target, self._texture, source)


class _GuideScrollBar(QScrollBar):
    """Game scrollbar with a movable, fixed-aspect 17 x 55 thumb."""

    _EXTENT = 17
    _BUTTON_HEIGHT = 18
    _THUMB_HEIGHT = 55

    def __init__(self, assets: Path, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Vertical, parent)
        self._thumb = QPixmap(str(assets / "guide_scrollbar.png"))
        self._up = QPixmap(str(assets / "scrollup.png"))
        self._down = QPixmap(str(assets / "scrolldown.png"))
        self._drag_offset: int | None = None
        self.setFixedWidth(self._EXTENT)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._EXTENT, 120)

    def _geometry(self) -> tuple[QRect, QRect, QRect, QRect]:
        button_height = min(self._BUTTON_HEIGHT, self.height() // 2)
        sub_line = QRect(0, 0, self.width(), button_height)
        add_line = QRect(0, self.height() - button_height, self.width(), button_height)
        groove = QRect(
            0,
            button_height,
            self.width(),
            max(0, self.height() - button_height * 2),
        )
        thumb_height = min(self._THUMB_HEIGHT, groove.height())
        available = max(0, groove.height() - thumb_height)
        offset = QStyle.sliderPositionFromValue(
            self.minimum(),
            self.maximum(),
            self.sliderPosition(),
            available,
            self.invertedAppearance(),
        )
        slider = QRect(0, groove.top() + offset, self.width(), thumb_height)
        return sub_line, add_line, groove, slider

    def slider_rect(self) -> QRect:
        return self._geometry()[3]

    def paintEvent(self, _event: object) -> None:
        sub_line, add_line, groove, slider = self._geometry()
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#080a06"))
        painter.setPen(QPen(QColor("#62582c"), 1))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.fillRect(sub_line, QColor("#16190d"))
        painter.fillRect(add_line, QColor("#16190d"))
        for rect, image in ((sub_line, self._up), (add_line, self._down)):
            if image.isNull():
                continue
            point = rect.center() - image.rect().center()
            painter.drawPixmap(point, image)
        if self.maximum() <= self.minimum() or slider.isEmpty() or self._thumb.isNull():
            return
        if slider.size() == self._thumb.size():
            painter.drawPixmap(slider.topLeft(), self._thumb)
        else:
            scaled = self._thumb.scaled(
                slider.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            point = slider.center() - scaled.rect().center()
            painter.drawPixmap(point, scaled)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        sub_line, add_line, _groove, slider = self._geometry()
        position = event.position().toPoint()
        if slider.contains(position):
            self._drag_offset = position.y() - slider.top()
            self.setSliderDown(True)
            event.accept()
            return
        action = QAbstractSlider.SliderAction.SliderNoAction
        if sub_line.contains(position):
            action = QAbstractSlider.SliderAction.SliderSingleStepSub
        elif add_line.contains(position):
            action = QAbstractSlider.SliderAction.SliderSingleStepAdd
        elif position.y() < slider.top():
            action = QAbstractSlider.SliderAction.SliderPageStepSub
        elif position.y() > slider.bottom():
            action = QAbstractSlider.SliderAction.SliderPageStepAdd
        if action != QAbstractSlider.SliderAction.SliderNoAction:
            self.triggerAction(action)
            self.setRepeatAction(action, 500, 55)
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_offset is None:
            super().mouseMoveEvent(event)
            return
        _sub_line, _add_line, groove, slider = self._geometry()
        available = max(0, groove.height() - slider.height())
        position = max(
            0,
            min(available, round(event.position().y()) - groove.top() - self._drag_offset),
        )
        self.setSliderPosition(
            QStyle.sliderValueFromPosition(
                self.minimum(),
                self.maximum(),
                position,
                available,
                self.invertedAppearance(),
            )
        )
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.setRepeatAction(QAbstractSlider.SliderAction.SliderNoAction)
            self._drag_offset = None
            self.setSliderDown(False)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class GuidePreviewPane(QWidget):
    closeRequested = Signal()
    pageRequested = Signal(str)
    targetRequested = Signal(bool)

    PAGE_ROLE = Qt.ItemDataRole.UserRole

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("guidePreviewPane")
        assets = Path(__file__).resolve().parents[1] / "assets" / "game_theme"
        self._assets = assets
        self._surface_texture = QPixmap(str(assets / "guide_surface_hires.png"))
        self._target = False
        self._page_id = ""
        self._toc_text = ""
        self._page_text = ""
        self._tip_resolver: Callable[[str, bool], str] | None = None
        self._dynamic_resolver: Callable[[str, bool], tuple[str, ...]] | None = None
        self._text_glyph_provider: Callable[
            [str, bool, tuple[int, int, int, int] | None], object | None
        ] | None = None
        self._text_font_family_provider: Callable[[bool], str] | None = None
        self._preview_font_enabled = True
        self._zoom_factor = 1.0
        self._tree_items: list[QTreeWidgetItem] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(5, 5, 5, 5)
        root.setSpacing(4)

        header = _GuideHeader(assets / "guide_header_hires.png")
        self.header = header
        header.setObjectName("guidePreviewHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(8, 2, 4, 2)
        self.font_button = QToolButton()
        self.font_button.setObjectName("guidePreviewFont")
        self.font_button.setCheckable(True)
        self.font_button.setChecked(True)
        self.font_button.clicked.connect(self.set_preview_font_enabled)
        header_layout.addWidget(self.font_button)
        header_layout.addStretch(1)
        self.title_label = QLabel()
        self.title_label.setObjectName("guidePreviewTitle")
        header_layout.addWidget(self.title_label)
        header_layout.addStretch(1)
        self.close_button = _GuideCloseButton()
        self.close_button.setObjectName("guidePreviewClose")
        self.close_button.setFixedSize(18, 18)
        self.close_button.clicked.connect(self.closeRequested)
        right_controls = QWidget()
        right_controls.setObjectName("guidePreviewRightControls")
        right_controls.setFixedWidth(92)
        right_layout = QHBoxLayout(right_controls)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addStretch(1)
        right_layout.addWidget(self.close_button)
        header_layout.addWidget(right_controls)
        root.addWidget(header)

        controls = QHBoxLayout()
        controls.setSpacing(6)
        self.home_button = QPushButton()
        self.home_button.setObjectName("guidePreviewHome")
        self.home_button.clicked.connect(lambda: self.pageRequested.emit("StartPage"))
        controls.addWidget(self.home_button)
        self.source_button = QToolButton()
        self.source_button.setObjectName("guidePreviewMode")
        self.source_button.setCheckable(True)
        self.source_button.clicked.connect(lambda: self.targetRequested.emit(False))
        controls.addWidget(self.source_button)
        self.target_button = QToolButton()
        self.target_button.setObjectName("guidePreviewMode")
        self.target_button.setCheckable(True)
        self.target_button.clicked.connect(lambda: self.targetRequested.emit(True))
        controls.addWidget(self.target_button)
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("guidePreviewSearch")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._filter_tree)
        controls.addWidget(self.search_edit, 1)
        root.addLayout(controls)

        self.content_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.toc_tree = QTreeWidget()
        self.toc_tree.setObjectName("guidePreviewToc")
        self.toc_tree.setHeaderHidden(True)
        self.toc_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.toc_tree.setVerticalScrollBar(_GuideScrollBar(assets, self.toc_tree))
        self.toc_tree.setMinimumWidth(150)
        self.toc_tree.itemClicked.connect(self._toc_item_clicked)
        self.body = QTextBrowser()
        self.body.setObjectName("guidePreviewBody")
        self.body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body.setVerticalScrollBar(_GuideScrollBar(assets, self.body))
        self.body.setFrameShape(QFrame.Shape.NoFrame)
        self.body.setAutoFillBackground(False)
        self.body.viewport().setAutoFillBackground(False)
        self.body.viewport().setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.body.setOpenLinks(False)
        self.body.setOpenExternalLinks(False)
        self.body.anchorClicked.connect(self._anchor_clicked)
        self.body.highlighted.connect(self._anchor_hovered)
        self.content_splitter.addWidget(self.toc_tree)
        self.body_frame = _GuideParchment(assets / "guide_parchment_hires.png")
        body_layout = QVBoxLayout(self.body_frame)
        body_layout.setContentsMargins(34, 27, 28, 25)
        body_layout.addWidget(self.body)
        self.content_splitter.addWidget(self.body_frame)
        self.content_splitter.setStretchFactor(0, 0)
        self.content_splitter.setStretchFactor(1, 1)
        self.content_splitter.setSizes([210, 790])
        root.addWidget(self.content_splitter, 1)

        self._retranslate()
        self.refresh_theme()

    def paintEvent(self, _event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if self._surface_texture.isNull():
            painter.fillRect(self.rect(), QColor("#1d2116"))
        else:
            source = self._surface_crop_rect(self._surface_texture, self.size().width(), self.size().height())
            painter.drawPixmap(self.rect(), self._surface_texture, source)
            painter.fillRect(self.rect(), QColor(24, 31, 15, 176))

    @staticmethod
    def _surface_crop_rect(texture: QPixmap, width: int, height: int) -> QRect:
        if width <= 0 or height <= 0:
            return texture.rect()
        target_ratio = width / height
        source_ratio = texture.width() / texture.height()
        if source_ratio > target_ratio:
            cropped_width = max(1, round(texture.height() * target_ratio))
            return QRect((texture.width() - cropped_width) // 2, 0, cropped_width, texture.height())
        cropped_height = max(1, round(texture.width() / target_ratio))
        return QRect(0, (texture.height() - cropped_height) // 2, texture.width(), cropped_height)

    def set_resolvers(
        self,
        *,
        tip: Callable[[str, bool], str] | None = None,
        dynamic: Callable[[str, bool], tuple[str, ...]] | None = None,
    ) -> None:
        self._tip_resolver = tip
        self._dynamic_resolver = dynamic

    def set_font_providers(
        self,
        *,
        text_glyph: Callable[
            [str, bool, tuple[int, int, int, int] | None], object | None
        ] | None = None,
        text_family: Callable[[bool], str] | None = None,
    ) -> None:
        self._text_glyph_provider = text_glyph
        self._text_font_family_provider = text_family
        self._render_page(preserve_scroll=True)

    @property
    def preview_font_enabled(self) -> bool:
        return self._preview_font_enabled

    def set_preview_font_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._preview_font_enabled:
            self.font_button.setChecked(enabled)
            return
        self._preview_font_enabled = enabled
        self.font_button.setChecked(enabled)
        self._retranslate()
        self._render_page(preserve_scroll=True)

    def set_guide(self, toc_text: str, page_id: str, page_text: str, *, target: bool) -> None:
        same_toc = toc_text == self._toc_text and target == self._target
        self._toc_text = toc_text
        self._page_id = page_id
        self._page_text = page_text
        self._target = target
        if not same_toc:
            self._rebuild_tree(parse_guide_toc(toc_text))
        self._select_page(page_id)
        self._render_page(preserve_scroll=False)
        self._sync_mode_buttons()
        self._retranslate()

    def refresh_page(self, page_text: str) -> None:
        self._page_text = page_text
        self._render_page(preserve_scroll=True)

    def set_zoom_factor(self, factor: float) -> None:
        self._zoom_factor = max(0.2, factor)
        self._render_page(preserve_scroll=True)

    def _apply_widget_fonts(self) -> str:
        font = QFont(QApplication.font())
        base_size = QApplication.font().pointSizeF()
        if base_size <= 0:
            base_size = 10.0
        font.setPointSizeF(max(1.0, base_size * self._zoom_factor))
        family = ""
        if self._preview_font_enabled and self._text_font_family_provider is not None:
            family = self._text_font_family_provider(self._target)
            if family:
                font.setFamily(family)
        self.body.setFont(font)
        self.body.document().setDefaultFont(font)
        self.toc_tree.setFont(font)
        return family

    def refresh_theme(self) -> None:
        self.setStyleSheet(
            """
            QWidget#guidePreviewPane { background: transparent; border: 0; }
            QFrame#guidePreviewHeader { background: transparent; border: 0; }
            QLabel#guidePreviewTitle { color: #e3c45f; font-weight: 900; font-size: 14px; }
            QToolButton#guidePreviewFont { min-width: 92px; max-width: 92px;
                min-height: 24px; max-height: 24px; padding: 0 6px; }
            QToolButton#guidePreviewFont:checked { color: #f0d874; border-color: #a68d43; }
            QToolButton#guidePreviewClose { min-width: 18px; max-width: 18px;
                min-height: 18px; max-height: 18px; padding: 0;
                background: transparent; border: 0; }
            QPushButton#guidePreviewHome, QToolButton#guidePreviewMode { min-height: 26px;
                max-height: 26px; padding: 0 10px; }
            QLineEdit#guidePreviewSearch { min-height: 26px; max-height: 26px; padding: 0 7px; }
            QTreeWidget#guidePreviewToc { background: rgba(15, 19, 10, 225); color: #d8c68f;
                border: 1px solid #75662f; }
            QTreeWidget#guidePreviewToc::item { min-height: 21px; }
            QTreeWidget#guidePreviewToc::item:selected { background: #51441f; color: #f0d874; }
            QTextBrowser#guidePreviewBody { background: transparent; color: #372618; border: 0; padding: 0; }
            """
        )
        self._render_page(preserve_scroll=True)

    def retranslate_ui(self) -> None:
        self._retranslate()

    def _retranslate(self) -> None:
        mode = "translation" if self._target else "source"
        self.title_label.setText(translate(f"guide_preview.title.{mode}"))
        self.close_button.setToolTip(translate("guide_preview.close"))
        self.home_button.setText(translate("guide_preview.home"))
        self.source_button.setText(translate("guide_preview.source"))
        self.target_button.setText(translate("guide_preview.translation"))
        self.search_edit.setPlaceholderText(translate("guide_preview.search"))
        font_mode = "settings" if self._preview_font_enabled else "app"
        self.font_button.setText(translate(f"guide_preview.font.{font_mode}"))
        self.font_button.setToolTip(translate(f"guide_preview.font.{font_mode}.hint"))

    def _sync_mode_buttons(self) -> None:
        self.source_button.setChecked(not self._target)
        self.target_button.setChecked(self._target)

    def _rebuild_tree(self, entries: tuple[GuideTocEntry, ...]) -> None:
        self.toc_tree.clear()
        self._tree_items.clear()
        parents: dict[int, QTreeWidgetItem] = {}
        for entry in entries:
            parent = parents.get(entry.depth - 1)
            item = QTreeWidgetItem(parent if parent is not None else self.toc_tree)
            item.setText(0, entry.title)
            item.setData(0, self.PAGE_ROLE, entry.page_id)
            item.setToolTip(0, entry.directive)
            selectable = bool(entry.page_id)
            flags = item.flags()
            item.setFlags(flags if selectable else flags & ~Qt.ItemFlag.ItemIsSelectable)
            if entry.kind in {"category", "subcategory"}:
                font = item.font(0)
                font.setBold(True)
                item.setFont(0, font)
                parents[entry.depth] = item
                for depth in tuple(parents):
                    if depth > entry.depth:
                        del parents[depth]
            self._tree_items.append(item)
        self.toc_tree.expandAll()

    def _select_page(self, page_id: str) -> None:
        wanted = page_id.casefold()
        for item in self._tree_items:
            if str(item.data(0, self.PAGE_ROLE) or "").casefold() == wanted:
                self.toc_tree.setCurrentItem(item)
                self.toc_tree.scrollToItem(item)
                return

    def _filter_tree(self, query: str) -> None:
        needle = query.strip().casefold()
        for item in self._tree_items:
            page_id = str(item.data(0, self.PAGE_ROLE) or "")
            visible = not needle or needle in item.text(0).casefold() or needle in page_id.casefold()
            item.setHidden(not visible and bool(page_id))

    def _toc_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        page_id = str(item.data(0, self.PAGE_ROLE) or "")
        if page_id:
            self.pageRequested.emit(page_id)

    def _render_page(self, *, preserve_scroll: bool) -> None:
        if not hasattr(self, "body"):
            return
        scroll = self.body.verticalScrollBar()
        old_value = scroll.value()
        old_maximum = scroll.maximum()
        ratio = old_value / old_maximum if old_maximum > 0 else 0.0
        fragment = render_guide_html(
            self._page_text,
            target=self._target,
            dynamic_resolver=self._dynamic_resolver,
        )
        css = """
            body { color: #372618; line-height: 1.06; margin: 1px 5px; }
            p { margin: 0 0 4px 0; }
            h2.guide-header { color: #913027; margin: 4px 0 1px 0; font-size: 1.12em; }
            hr { color: #86673d; height: 1px; margin: 1px 0 4px 0; }
            ul, ol { margin: 1px 0 4px 18px; }
            li { margin: 0; }
            a.guide-link { color: #8a6500; text-decoration: underline; font-weight: 600; }
            a.guide-tip { color: #8a6500; text-decoration: underline; }
            kbd { color: #6a201a; background: #cdb277; }
            table.guide-table { border-collapse: collapse; width: 100%; }
            td { border-bottom: 1px solid #a58a57; padding: 1px 5px; }
            .guide-unresolved { color: #7a4050; font-style: italic; }
        """
        self.body.setUpdatesEnabled(False)
        try:
            preview_family = self._apply_widget_fonts()
            self.body.setHtml(f"<html><head><style>{css}</style></head><body>{fragment}</body></html>")
            if self._preview_font_enabled and not preview_family:
                self._apply_preview_glyphs()
            if preserve_scroll:
                scroll.setValue(round(scroll.maximum() * ratio))
            else:
                scroll.setValue(0)
        finally:
            self.body.setUpdatesEnabled(True)
        self.body.viewport().update()

    def _apply_preview_glyphs(self) -> None:
        provider = self._text_glyph_provider
        if provider is None:
            return
        document = self.body.document()
        default_font = document.defaultFont()
        text = document.toPlainText()
        resources: dict[tuple[str, int, int], tuple[QUrl, float]] = {}
        cursor = QTextCursor(document)
        cursor.beginEditBlock()
        try:
            for position in range(len(text) - 1, -1, -1):
                char = text[position]
                if char.isspace() or char in {"\u2028", "\u2029", "\ufffc"}:
                    continue
                cursor.setPosition(position)
                cursor.movePosition(
                    QTextCursor.MoveOperation.NextCharacter,
                    QTextCursor.MoveMode.KeepAnchor,
                )
                source_format = cursor.charFormat()
                color = source_format.foreground().color()
                if not color.isValid():
                    color = QColor("#372618")
                font = source_format.font().resolve(default_font)
                height = max(8, QFontMetrics(font).height() - 1)
                resource_key = (char, color.rgba(), height)
                resource = resources.get(resource_key)
                if resource is None:
                    image = provider(
                        char,
                        self._target,
                        (color.red(), color.green(), color.blue(), color.alpha()),
                    )
                    if image is None or not hasattr(image, "isNull") or image.isNull():
                        continue
                    width = max(1.0, image.width() * height / max(1, image.height()))
                    resource_url = QUrl(
                        f"guide-font-{int(self._target)}-{ord(char)}-{color.rgba()}-{height}.png"
                    )
                    document.addResource(
                        QTextDocument.ResourceType.ImageResource,
                        resource_url,
                        image,
                    )
                    resource = (resource_url, width)
                    resources[resource_key] = resource
                resource_url, width = resource
                image_format = QTextImageFormat()
                image_format.setName(resource_url.toString())
                image_format.setWidth(width)
                image_format.setHeight(height)
                image_format.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignMiddle)
                if source_format.isAnchor():
                    image_format.setAnchor(True)
                    image_format.setAnchorHref(source_format.anchorHref())
                    image_format.setToolTip(source_format.toolTip())
                cursor.insertImage(image_format)
        finally:
            cursor.endEditBlock()

    def _anchor_clicked(self, url: QUrl) -> None:
        if url.scheme().casefold() == "guide":
            self.pageRequested.emit(url.path() or url.toString().split(":", 1)[-1])
        elif url.scheme().casefold() == "tip":
            self._show_tip(url.path() or url.toString().split(":", 1)[-1])

    def _anchor_hovered(self, url: QUrl) -> None:
        if url.scheme().casefold() == "tip":
            self._show_tip(url.path() or url.toString().split(":", 1)[-1])
        else:
            QToolTip.hideText()

    def _show_tip(self, tip_id: str) -> None:
        value = self._tip_resolver(tip_id, self._target) if self._tip_resolver is not None else ""
        QToolTip.showText(QCursor.pos(), value or tip_id, self.body)
