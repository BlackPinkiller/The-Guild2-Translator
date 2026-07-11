from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QProxyStyle, QStyle, QStyleOption, QStyleOptionButton, QStyleOptionComboBox, QStyleOptionSlider, QStyleOptionToolButton


ASSET_ROOT = Path(__file__).resolve().parents[1] / "assets" / "game_theme"


class GameAssetSet:
    _shared_pixmaps: dict[str, QPixmap] = {}

    def pixmap(self, name: str) -> QPixmap:
        if name not in self._shared_pixmaps:
            self._shared_pixmaps[name] = QPixmap(str(ASSET_ROOT / name))
        return self._shared_pixmaps[name]

    def nine_slice(self, painter: QPainter, rect: QRect, family: str) -> None:
        pieces = [self.pixmap(f"{family}_{index}.png") for index in range(9)]
        if any(piece.isNull() for piece in pieces):
            painter.fillRect(rect, QColor("#2b2419"))
            painter.setPen(QColor("#a88a4f"))
            painter.drawRect(rect.adjusted(1, 1, -2, -2))
            return
        left = max(pieces[0].width(), pieces[3].width(), pieces[6].width())
        right = max(pieces[2].width(), pieces[5].width(), pieces[8].width())
        top = max(pieces[0].height(), pieces[1].height(), pieces[2].height())
        bottom = max(pieces[6].height(), pieces[7].height(), pieces[8].height())
        if rect.width() < left + right or rect.height() < top + bottom:
            painter.drawPixmap(rect, pieces[4])
            return
        x = (rect.left(), rect.left() + left, rect.right() - right + 1, rect.right() + 1)
        y = (rect.top(), rect.top() + top, rect.bottom() - bottom + 1, rect.bottom() + 1)
        targets = (
            QRect(x[0], y[0], left, top),
            QRect(x[1], y[0], x[2] - x[1], top),
            QRect(x[2], y[0], right, top),
            QRect(x[0], y[1], left, y[2] - y[1]),
            QRect(x[1], y[1], x[2] - x[1], y[2] - y[1]),
            QRect(x[2], y[1], right, y[2] - y[1]),
            QRect(x[0], y[2], left, bottom),
            QRect(x[1], y[2], x[2] - x[1], bottom),
            QRect(x[2], y[2], right, bottom),
        )
        for target, source in zip(targets, pieces):
            painter.drawPixmap(target, source)


class GameHeaderFrame(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.assets = GameAssetSet()

    def paintEvent(self, event) -> None:  # noqa: N802
        app = QApplication.instance()
        if app is None or app.property("guild2Theme") is not True:
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setClipRect(self.rect())
        texture = self.assets.pixmap("header_red_nocompression.png")
        if not texture.isNull():
            for x in range(self.rect().left(), self.rect().right() + 1, texture.width()):
                painter.drawPixmap(x, self.rect().center().y() - texture.height() // 2, texture)
        self.assets.nine_slice(painter, self.rect(), "Border_Gold_01")
        self.assets.nine_slice(painter, self.rect().adjusted(4, 4, -4, -4), "border_header_red")
        painter.end()


class GamePanelFrame(QFrame):
    def __init__(self, parent=None, family: str = "B_3DWindow_01") -> None:
        super().__init__(parent)
        self.assets = GameAssetSet()
        self.family = family

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        app = QApplication.instance()
        if app is None or app.property("guild2Theme") is not True:
            return
        painter = QPainter(self)
        self.assets.nine_slice(painter, self.rect(), self.family)
        painter.end()


class GameThemeStyle(QProxyStyle):
    """Pixel-oriented game controls backed by the extracted Guild 2 atlas pieces."""

    def __init__(self) -> None:
        super().__init__("Fusion")
        self.assets = GameAssetSet()

    @staticmethod
    def _text_color() -> QColor:
        return QColor("#d8c68f")

    def sizeFromContents(self, contents_type, option, contents_size, widget=None):  # noqa: N802
        size = super().sizeFromContents(contents_type, option, contents_size, widget)
        if contents_type == QStyle.ContentsType.CT_PushButton:
            return QSize(max(size.width(), 92), max(size.height(), 34))
        if contents_type == QStyle.ContentsType.CT_ToolButton:
            has_menu = isinstance(option, QStyleOptionToolButton) and bool(
                option.features & QStyleOptionToolButton.ToolButtonFeature.HasMenu
            )
            return QSize(max(size.width() + (22 if has_menu else 0), 54), max(size.height(), 32))
        if contents_type == QStyle.ContentsType.CT_TabBarTab:
            return QSize(max(size.width() + 24, 92), max(size.height() + 8, 36))
        return size

    def drawPrimitive(self, element, option, painter, widget=None):  # noqa: N802
        if element in {QStyle.PrimitiveElement.PE_Frame, QStyle.PrimitiveElement.PE_FrameLineEdit}:
            self.assets.nine_slice(painter, option.rect, "B_ButtonFrame_01")
            return
        if element == QStyle.PrimitiveElement.PE_FrameGroupBox:
            self.assets.nine_slice(painter, option.rect, "B_3DWindow_01")
            return
        super().drawPrimitive(element, option, painter, widget)

    def drawControl(self, element, option, painter, widget=None):  # noqa: N802
        if element == QStyle.ControlElement.CE_ComboBoxLabel:
            return
        if element == QStyle.ControlElement.CE_PushButton and isinstance(option, QStyleOptionButton):
            pressed = bool(option.state & QStyle.StateFlag.State_Sunken)
            image = self.assets.pixmap("button_start_pressed.png" if pressed else "button_start.png")
            painter.drawPixmap(option.rect, image)
            painter.setPen(self._text_color())
            font = QFont(painter.font())
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, option.text)
            return
        if element == QStyle.ControlElement.CE_CheckBox and isinstance(option, QStyleOptionButton):
            box = QRect(option.rect.left(), option.rect.center().y() - 8, 16, 16)
            self.assets.nine_slice(painter, box, "border_green")
            if option.state & QStyle.StateFlag.State_On:
                painter.setPen(self._text_color())
                painter.drawText(box, Qt.AlignmentFlag.AlignCenter, "✓")
            painter.setPen(self._text_color())
            painter.drawText(option.rect.adjusted(23, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, option.text)
            return
        if element == QStyle.ControlElement.CE_TabBarTabShape:
            selected = bool(option.state & QStyle.StateFlag.State_Selected)
            tab_rect = option.rect.adjusted(0, 0 if selected else 4, 0, 0)
            painter.fillRect(tab_rect, QColor("#5b3920" if selected else "#271f16"))
            self.assets.nine_slice(painter, tab_rect, "B_Header_01")
            painter.setPen(QColor("#c5a65a" if selected else "#725b33"))
            painter.drawRect(tab_rect.adjusted(1, 1, -2, -2))
            painter.setPen(QColor("#3a2a18"))
            painter.drawLine(tab_rect.left() + 4, tab_rect.bottom() - 1, tab_rect.right() - 4, tab_rect.bottom() - 1)
            if selected:
                painter.setPen(QColor("#e0c277"))
                painter.drawLine(tab_rect.left() + 5, tab_rect.top() + 2, tab_rect.right() - 5, tab_rect.top() + 2)
            return
        if element == QStyle.ControlElement.CE_TabBarTabLabel:
            selected = bool(option.state & QStyle.StateFlag.State_Selected)
            font = QFont(painter.font())
            font.setBold(selected)
            painter.setFont(font)
            painter.setPen(QColor("#ead79a" if selected else "#aa915c"))
            painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, option.text)
            return
        super().drawControl(element, option, painter, widget)

    def drawComplexControl(self, control, option, painter, widget=None):  # noqa: N802
        if control == QStyle.ComplexControl.CC_ToolButton and isinstance(option, QStyleOptionToolButton):
            pressed = bool(option.state & QStyle.StateFlag.State_Sunken)
            if pressed:
                painter.drawPixmap(option.rect, self.assets.pixmap("button_start_pressed.png"))
            else:
                self.assets.nine_slice(painter, option.rect, "Border_BtnGreen_small")
            has_menu = bool(option.features & QStyleOptionToolButton.ToolButtonFeature.HasMenu)
            label = QStyleOptionToolButton(option)
            if has_menu:
                label.rect = option.rect.adjusted(0, 0, -24, 0)
            self.drawControl(QStyle.ControlElement.CE_ToolButtonLabel, label, painter, widget)
            if has_menu:
                arrow_rect = QRect(option.rect.right() - 23, option.rect.top() + 3, 21, option.rect.height() - 6)
                painter.setPen(QColor("#ad8b43"))
                painter.drawLine(arrow_rect.left(), arrow_rect.top() + 1, arrow_rect.left(), arrow_rect.bottom() - 1)
                arrow = self.assets.pixmap("scrolldown.png")
                target = QRect(arrow_rect.center().x() - 8, arrow_rect.center().y() - 8, 16, 16)
                painter.drawPixmap(target, arrow)
            return
        if control == QStyle.ComplexControl.CC_ComboBox and isinstance(option, QStyleOptionComboBox):
            self.assets.nine_slice(painter, option.rect, "B_ButtonFrame_01")
            arrow = self.assets.pixmap("scrolldown.png")
            arrow_rect = QRect(option.rect.right() - 22, option.rect.center().y() - 8, 16, 16)
            painter.drawPixmap(arrow_rect, arrow)
            label = QStyleOptionComboBox(option)
            label.rect = option.rect.adjusted(8, 0, -26, 0)
            painter.setPen(self._text_color())
            font = QFont(painter.font())
            font.setBold(True)
            painter.setFont(font)
            text = QFontMetrics(font).elidedText(
                option.currentText,
                Qt.TextElideMode.ElideRight,
                max(1, label.rect.width()),
            )
            painter.drawText(label.rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
            return
        if control == QStyle.ComplexControl.CC_ScrollBar and isinstance(option, QStyleOptionSlider):
            self._draw_scrollbar(option, painter)
            return
        super().drawComplexControl(control, option, painter, widget)

    def _draw_scrollbar(self, option: QStyleOptionSlider, painter: QPainter) -> None:
        horizontal = option.orientation == Qt.Orientation.Horizontal
        painter.fillRect(option.rect, QColor("#1b1510"))
        if horizontal:
            handle = self.subControlRect(QStyle.ComplexControl.CC_ScrollBar, option, QStyle.SubControl.SC_ScrollBarSlider)
            painter.drawPixmap(handle, self.assets.pixmap("scrollbar_horizontal.png"))
            first = self.subControlRect(QStyle.ComplexControl.CC_ScrollBar, option, QStyle.SubControl.SC_ScrollBarSubLine)
            last = self.subControlRect(QStyle.ComplexControl.CC_ScrollBar, option, QStyle.SubControl.SC_ScrollBarAddLine)
            painter.drawPixmap(first, self.assets.pixmap("scrollup.png"))
            painter.drawPixmap(last, self.assets.pixmap("scrolldown.png"))
        else:
            handle = self.subControlRect(QStyle.ComplexControl.CC_ScrollBar, option, QStyle.SubControl.SC_ScrollBarSlider)
            painter.drawPixmap(handle, self.assets.pixmap("scrollbar_vertical.png"))
            first = self.subControlRect(QStyle.ComplexControl.CC_ScrollBar, option, QStyle.SubControl.SC_ScrollBarSubLine)
            last = self.subControlRect(QStyle.ComplexControl.CC_ScrollBar, option, QStyle.SubControl.SC_ScrollBarAddLine)
            painter.drawPixmap(first, self.assets.pixmap("scrollup.png"))
            painter.drawPixmap(last, self.assets.pixmap("scrolldown.png"))


def install_game_theme_style(app) -> None:
    style = GameThemeStyle()
    app._game_theme_style = style
    app.setStyle(style)
