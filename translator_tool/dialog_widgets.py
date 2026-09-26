"""Shared spacing and screen bounds for auxiliary windows."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFormLayout, QVBoxLayout, QWidget


def dialog_layout(dialog: QDialog) -> QVBoxLayout:
    dialog.setProperty("workspaceDialog", True)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(12)
    return layout


def fit_dialog(dialog: QDialog, width: int, height: int) -> None:
    available = dialog.screen().availableGeometry()
    dialog.resize(min(width, available.width() - 48), min(height, available.height() - 64))


def settings_form(section: QWidget) -> QFormLayout:
    form = QFormLayout(section)
    form.setContentsMargins(0, 8, 0, 4)
    form.setHorizontalSpacing(18)
    form.setVerticalSpacing(8)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    return form
