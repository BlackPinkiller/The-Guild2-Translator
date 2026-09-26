from __future__ import annotations

from pathlib import Path
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from .theme import INTERFACE_COLORS


def configure_interface_scale(percent: int) -> None:
    """Set Qt's application-wide multiplier before QApplication is created."""
    os.environ["QT_SCALE_FACTOR"] = str(percent / 100)


def apply_interface_style(app: QApplication, theme: str) -> None:
    """Apply one consistent control geometry to the light and dark palettes."""
    colors = INTERFACE_COLORS[theme]
    app.setProperty("darkTheme", theme == "dark")
    app.setStyleSheet("")
    app.setStyle("Fusion")
    app.setEffectEnabled(Qt.UIEffect.UI_AnimateTooltip, False)
    app.setEffectEnabled(Qt.UIEffect.UI_FadeTooltip, True)
    font = app.font()
    font.setFamilies(["Microsoft YaHei UI", "Segoe UI"])
    app.setFont(font)
    palette = QPalette()
    for role, key in (
        (QPalette.ColorRole.Window, "window"), (QPalette.ColorRole.WindowText, "text"),
        (QPalette.ColorRole.Base, "base"), (QPalette.ColorRole.AlternateBase, "panel"),
        (QPalette.ColorRole.ToolTipBase, "panel"), (QPalette.ColorRole.ToolTipText, "text"),
        (QPalette.ColorRole.Text, "text"), (QPalette.ColorRole.Button, "panel"),
        (QPalette.ColorRole.ButtonText, "text"), (QPalette.ColorRole.BrightText, "danger_text"),
        (QPalette.ColorRole.Link, "accent"), (QPalette.ColorRole.Highlight, "selection"),
        (QPalette.ColorRole.HighlightedText, "selection_text"), (QPalette.ColorRole.PlaceholderText, "muted"),
        (QPalette.ColorRole.Mid, "border"), (QPalette.ColorRole.Dark, "muted"),
        (QPalette.ColorRole.Light, "border"), (QPalette.ColorRole.Shadow, "border"),
        (QPalette.ColorRole.Midlight, "raised"),
    ):
        palette.setColor(role, QColor(colors[key]))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors["disabled"]))
    app.setPalette(palette)
    icons = (Path(__file__).resolve().parents[1] / "assets" / "interface").as_posix()
    app.setStyleSheet(_INTERFACE_QSS.format(**colors, icons=icons, theme=theme))


_INTERFACE_QSS = """
QWidget {{ color: {text}; font-family: "Microsoft YaHei UI", "Segoe UI"; font-size: 13px; }}
QMainWindow, #root, QDialog {{ background: {window}; }}
#titlebar {{ background: transparent; border: 0; }}
#titlebar QToolButton, #titlebar QPushButton {{ padding: 4px 9px; font-size: 12px; }}
#titlebar QComboBox {{ padding-top: 3px; padding-bottom: 3px; min-height: 18px; font-size: 12px; }}
#titlebar QLabel {{ font-size: 12px; color: {muted}; }}
#tablePanel {{ background: {base}; border: 1px solid {border}; border-top: 0; }}
#toolbar {{ background: {panel}; border: 1px solid {border}; border-bottom: 0; }}
#toolbar[standalone="true"] {{ border-bottom: 1px solid {border}; }}
#toolbar QLabel {{ color: {muted}; font-weight: 400; }}
#toolbar QLabel, #toolbar QCheckBox {{ font-size: 12px; }}
#toolbar QLineEdit, #toolbar QComboBox {{ padding-top: 3px; padding-bottom: 3px; min-height: 18px; font-size: 12px; }}
#toolbar QPushButton, #toolbar QToolButton {{ padding: 4px 8px; font-size: 12px; }}
#counts, #statusMessage {{ color: {muted}; font-size: 11px; }}
#emptyEntries {{ color: {muted}; font-size: 14px; background: transparent; }}
#issues {{ background: transparent; color: {muted}; border: 0; border-radius: 5px; padding: 3px 8px; font-size: 12px; }}
#issues[severity="warning"] {{ background: {warning_bg}; color: {warning_text}; border-color: {warning_bg}; }}
#issues[severity="error"] {{ background: {danger_bg}; color: {danger_text}; border-color: {danger_bg}; }}
#hint, #historyHint, #historyIndexStatus, #suggestionStatus {{ color: {muted}; font-size: 12px; }}
#dialogHeading {{ font-size: 17px; font-weight: 600; }}
#projectManagerSummary, #projectManagerGameRoot, #projectManagerPath {{ color: {muted}; font-size: 12px; }}
#projectManagerRow {{ background: {panel}; border: 0; border-bottom: 1px solid {border}; border-radius: 0; }}
#projectManagerName {{ font-size: 14px; font-weight: 600; }}
#projectManagerFeedback {{ color: {muted}; font-size: 12px; }}
#projectKindBadge {{ color: {muted}; font-size: 11px; }}
#projectManagerProgress {{ max-height: 6px; }}
QGroupBox {{ background: {panel}; border: 1px solid {border}; border-radius: 8px; margin-top: 16px; padding-top: 10px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; left: 12px; padding: 0 6px; background: {window}; color: {text}; }}
#settingsDialog QGroupBox {{ background: transparent; border: 0; border-top: 1px solid {border}; border-radius: 0; margin-top: 10px; padding-top: 8px; }}
#settingsDialog QGroupBox::title {{ left: 0; padding: 0 10px 0 0; }}
#providerTabs QGroupBox {{ border: 0; margin: 0; padding: 4px 0; }}
#settingsDialog QScrollArea, #settingsDialog QScrollArea > QWidget > QWidget {{ background: {window}; }}
QTableView, QListWidget, QPlainTextEdit, QTextEdit, QTextBrowser {{ background: {base}; color: {text}; border: 1px solid {border}; border-radius: 7px; selection-background-color: {selection}; selection-color: {selection_text}; }}
QTableView {{ gridline-color: {divider}; }}
QTableView#entryTable {{ border: 0; border-radius: 0; }}
QTableView::item {{ background: transparent; border-bottom: 1px solid {divider}; padding: 2px 8px; }}
QTableView::item:selected, QListWidget::item:selected {{ background: {selection}; color: {selection_text}; }}
QHeaderView::section {{ background: {panel}; color: {muted}; border: 0; border-bottom: 1px solid {border}; padding: 5px 8px; font-weight: 500; font-size: 12px; }}
QPlainTextEdit, QTextEdit, QTextBrowser {{ padding: 8px; }}
#editorPanel {{ background: {base}; border: 1px solid {border}; border-radius: 3px; }}
#editorPanel[focused="true"] {{ border-color: {accent}; }}
#editorHeader {{ background: {panel}; border: 0; border-bottom: 1px solid {divider}; }}
#editorTitle {{ font-size: 12px; font-weight: 600; }}
#editorMode {{ color: {muted}; font-size: 11px; }}
#editorPanel QPlainTextEdit, #editorPanel QTextEdit {{ border: 0; background: {base}; }}
#editorPanel[target="false"] QTextEdit {{ background: {panel}; }}
QListWidget {{ padding: 4px; }}
QListWidget::item {{ padding: 7px 8px; border-radius: 4px; }}
QListWidget::item:hover:!selected {{ background: {panel}; }}
QLineEdit, QComboBox, QSpinBox {{ background: {base}; color: {text}; border: 1px solid {border}; border-radius: 6px; padding: 6px 9px; min-height: 20px; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {accent}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{ background: {panel}; color: {disabled}; }}
QComboBox QAbstractItemView {{ background: {base}; color: {text}; border: 1px solid {border}; selection-background-color: {selection}; selection-color: {selection_text}; padding: 4px; }}
QComboBox {{ padding-right: 26px; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right; width: 24px; border: 0; background: transparent; }}
QComboBox::down-arrow {{ image: url("{icons}/chevron-{theme}.svg"); width: 12px; height: 12px; }}
QPushButton, QToolButton {{ background: {panel}; color: {text}; border: 1px solid {border}; border-radius: 5px; padding: 6px 10px; font-weight: 500; }}
QPushButton:hover, QToolButton:hover {{ background: {raised}; border-color: {muted}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {selection}; border-color: {accent}; }}
QPushButton:focus, QToolButton:focus {{ border-color: {accent}; }}
QPushButton#primary, QPushButton:default {{ background: {accent}; color: {accent_text}; border-color: {accent}; font-weight: 600; }}
QPushButton#primary:hover, QPushButton:default:hover {{ background: {accent_hover}; border-color: {accent_hover}; }}
QDialog[workspaceDialog="true"] QPushButton:default {{ background: {panel}; color: {text}; border-color: {border}; font-weight: 500; }}
QDialog[workspaceDialog="true"] QPushButton[primaryAction="true"] {{ background: {accent}; color: {accent_text}; border-color: {accent}; font-weight: 600; }}
QDialog[workspaceDialog="true"] QPushButton[primaryAction="true"]:hover {{ background: {accent_hover}; border-color: {accent_hover}; }}
QDialog[workspaceDialog="true"] QPushButton[primaryAction="true"]:disabled {{ background: {panel}; color: {disabled}; border-color: {border}; }}
QDialog[workspaceDialog="true"] QDialogButtonBox {{ padding-top: 8px; }}
QPushButton#batchAi[mode="cancel"] {{ background: {danger_bg}; color: {danger_text}; border-color: {danger_bg}; }}
QPushButton#batchAi[mode="busy"] {{ background: {success_bg}; color: {success_text}; }}
QPushButton#batchAi[mode="cancelling"] {{ background: {warning_bg}; color: {warning_text}; }}
QPushButton:disabled, QToolButton:disabled, QPushButton#primary:disabled {{ background: {panel}; color: {disabled}; border-color: {border}; }}
QToolButton#previewToggle, QToolButton#codeReferenceButton, QToolButton#entryHistory {{ padding: 2px 6px; font-size: 12px; border-color: transparent; background: transparent; color: {muted}; }}
QToolButton#previewToggle:hover, QToolButton#codeReferenceButton:hover, QToolButton#entryHistory:hover {{ background: {raised}; color: {text}; }}
QToolButton[quiet="true"] {{ background: transparent; border-color: transparent; color: {muted}; }}
QToolButton[quiet="true"]:hover {{ background: {raised}; color: {text}; }}
QToolButton[quiet="true"]:focus {{ border-color: {accent}; }}
QToolButton#previewToggle:checked {{ background: {selection}; color: {selection_text}; border-color: {accent}; }}
QToolButton#searchCaseButton {{ background: transparent; color: {muted}; padding: 0; border: 1px solid transparent; border-radius: 3px; font-size: 11px; font-weight: 500; }}
QToolButton#searchCaseButton:hover {{ background: {raised}; color: {text}; }}
QToolButton#searchCaseButton:checked {{ color: {text}; border-bottom-color: {muted}; }}
QToolButton#searchCaseButton:focus:!checked {{ border-bottom: 1px dashed {muted}; }}
QLabel#codeReferenceCount {{ color: {muted}; background: transparent; padding: 0 6px; font-size: 12px; }}
QCheckBox {{ spacing: 6px; color: {text}; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {muted}; border-radius: 3px; background: {base}; }}
QCheckBox::indicator:hover {{ border-color: {accent}; }}
QCheckBox::indicator:checked {{ background: {accent}; border-color: {accent}; image: url("{icons}/check-{theme}.svg"); }}
QCheckBox::indicator:disabled {{ border-color: {disabled}; background: {panel}; }}
QCheckBox:disabled {{ color: {disabled}; }}
QCheckBox#projectAddedCheck::indicator {{ background: transparent; border-color: {muted}; }}
QCheckBox#projectAddedCheck::indicator:checked {{ background: {muted}; border-color: {muted}; }}
QTabWidget::pane {{ background: {window}; border: 0; border-top: 1px solid {border}; top: -1px; }}
QTabBar::tab {{ background: {window}; color: {muted}; border: 0; border-bottom: 2px solid transparent; padding: 9px 14px; }}
QTabBar::tab:selected {{ color: {text}; border-bottom-color: {accent}; font-weight: 600; }}
QTabBar::tab:hover:!selected {{ background: {panel}; }}
#providerTabs QTabBar::tab {{ padding: 6px 12px; font-size: 12px; }}
QMenu {{ background: {panel}; color: {text}; border: 1px solid {border}; padding: 5px; }}
QMenu::item {{ padding: 7px 24px 7px 12px; border-radius: 4px; }}
QMenu::item:selected {{ background: {selection}; color: {selection_text}; }}
QMenu::item:disabled {{ color: {disabled}; }}
QMenu::separator {{ height: 1px; background: {border}; margin: 5px 8px; }}
QToolTip, QLabel#searchHelp {{ background: {panel}; color: {text}; border: 1px solid {border}; padding: 6px 8px; font-size: 13px; }}
QStatusBar {{ background: {window}; color: {muted}; font-size: 12px; }}
QStatusBar::item {{ border: 0; }}
QToolButton#reviewAttention {{ background: transparent; color: {muted}; border: 1px solid transparent; border-radius: 3px; padding: 2px 5px; font-size: 11px; font-weight: 400; }}
QToolButton#reviewAttention:hover {{ background: {raised}; color: {text}; }}
QToolButton#reviewAttention:focus {{ border-color: {accent}; }}
QScrollBar:vertical {{ background: {base}; width: 12px; margin: 2px; }}
QScrollBar:horizontal {{ background: {base}; height: 12px; margin: 2px; }}
QScrollBar::handle {{ background: {border}; border-radius: 4px; min-height: 28px; min-width: 28px; }}
QScrollBar::handle:hover {{ background: {muted}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QSplitter::handle {{ background: {window}; }}
QSplitter::handle:hover {{ background: {border}; }}
QProgressBar {{ background: {base}; border: 1px solid {border}; border-radius: 4px; color: {text}; text-align: center; }}
QProgressBar::chunk {{ background: {selection}; border-radius: 3px; }}
"""
