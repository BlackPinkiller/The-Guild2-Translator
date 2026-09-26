"""Layout-only widgets used by the translation workspace."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QSize, Qt, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QStatusBar, QToolButton, QVBoxLayout, QWidget


class ElidedLabel(QLabel):
    """Single-line metadata with a full-text tooltip and no minimum-width pressure."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)

    def setText(self, text: str) -> None:  # noqa: N802
        self._full_text = text
        self.setToolTip(text)
        self._elide()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        super().setText(self.fontMetrics().elidedText(self._full_text, Qt.TextElideMode.ElideMiddle, self.width()))

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.fontMetrics().horizontalAdvance(self._full_text), super().sizeHint().height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, super().minimumSizeHint().height())


class FadingToolTip(QLabel):
    """A stationary help popup with symmetric opacity transitions."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("searchHelp")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setWordWrap(True)
        self.fade = QPropertyAnimation(self, b"windowOpacity", self)
        self.fade.setDuration(120)
        self.fade.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self.fade.finished.connect(self._finish_fade)
        self.expiry = QTimer(self)
        self.expiry.setSingleShot(True)
        self.expiry.timeout.connect(self.dismiss)

    def show_for(self, anchor: QWidget) -> None:
        text = anchor.toolTip()
        if not text:
            return
        self.setText(text)
        available = anchor.screen().availableGeometry().adjusted(8, 8, -8, -8)
        self.setMaximumWidth(min(460, available.width()))
        self.adjustSize()
        point = anchor.mapToGlobal(QPoint(0, anchor.height() + 6))
        if point.y() + self.height() > available.bottom():
            point.setY(anchor.mapToGlobal(QPoint(0, 0)).y() - self.height() - 6)
        point.setX(max(available.left(), min(point.x(), available.right() - self.width() + 1)))
        point.setY(max(available.top(), min(point.y(), available.bottom() - self.height() + 1)))
        self.move(point)
        if not self.isVisible():
            self.setWindowOpacity(0.0)
            self.show()
        self._fade_to(1.0)
        self.expiry.start(20000)

    def dismiss(self) -> None:
        self.expiry.stop()
        if self.isVisible():
            self._fade_to(0.0)

    def _fade_to(self, opacity: float) -> None:
        self.fade.stop()
        self.fade.setStartValue(self.windowOpacity())
        self.fade.setEndValue(opacity)
        self.fade.start()

    def _finish_fade(self) -> None:
        if self.fade.endValue() == 0.0:
            self.hide()

    def hideEvent(self, event) -> None:  # noqa: N802
        self.fade.stop()
        self.expiry.stop()
        super().hideEvent(event)


class WorkspaceStatusBar(QStatusBar):
    """Keep timed messages and persistent statistics in one aligned footer row."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSizeGripEnabled(False)
        content = QWidget()
        content.setMinimumHeight(28)
        self.content_layout = QHBoxLayout(content)
        self.content_layout.setContentsMargins(8, 2, 8, 2)
        self.content_layout.setSpacing(12)
        self.message_label = ElidedLabel()
        self.message_label.setObjectName("statusMessage")
        self.content_layout.addWidget(self.message_label, 1)
        # The full-width permanent row owns the message area. Retain QStatusBar's
        # timeout/currentMessage semantics, with an eliding label for long text.
        self.addPermanentWidget(content, 1)
        self.messageChanged.connect(self.message_label.setText)


class EditorPanel(QFrame):
    """A real header layout keeps editor actions clear at every window width."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("editorPanel")
        self.content_layout = QVBoxLayout(self)
        self.content_layout.setContentsMargins(1, 1, 1, 1)
        self.content_layout.setSpacing(0)
        header = QFrame()
        header.setObjectName("editorHeader")
        self.header_layout = QHBoxLayout(header)
        self.header_layout.setContentsMargins(9, 2, 8, 2)
        self.header_layout.setSpacing(6)
        self.title_label = QLabel()
        self.title_label.setObjectName("editorTitle")
        self.header_layout.addWidget(self.title_label)
        self.mode_label = QLabel()
        self.mode_label.setObjectName("editorMode")
        self.header_layout.addWidget(self.mode_label)
        self.code_button = QToolButton()
        self.code_button.setObjectName("codeReferenceButton")
        self.code_button.hide()
        self.header_layout.addWidget(self.code_button)
        self.reference_label = ElidedLabel()
        self.reference_label.setObjectName("codeReferenceCount")
        self.reference_label.hide()
        self.header_layout.addWidget(self.reference_label, 1)
        self.header_layout.addStretch(1)
        self.preview_button = QToolButton()
        self.preview_button.setObjectName("previewToggle")
        self.preview_button.setCheckable(True)
        self.header_layout.addWidget(self.preview_button)
        self.content_layout.addWidget(header)

    def setTitle(self, text: str) -> None:  # noqa: N802
        self.title_label.setText(text)
