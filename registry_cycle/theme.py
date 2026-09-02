"""Тема окна «Хард · Перенос реестров» (PySide6).

Токены берутся из `config/theme.json` (`colors`, `typography`, `layout`) —
это тот же тёплый светлый стиль, что и у пульта: слоновая кость, белые
карточки с тонкой рамкой, графитовый акцент, без градиентов и теней.
Модуль лежит внутри `registry_cycle/`, потому что пакет уезжает в офис
отдельно от `pult/` (см. tools/build_registry_transfer_share.py).
"""

from __future__ import annotations

from typing import Any

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

FONT_STACK = ["Segoe UI Variable", "Segoe UI", "Inter", "Arial"]
RADIUS = 10


def font(typography: dict[str, Any], size: int | str, weight: str = "normal") -> QFont:
    """QFont по ключу типографики («body», «heading»…) или размеру в pt."""
    f = QFont()
    families = list(FONT_STACK)
    for key in ("family", "fallback"):
        value = str(typography.get(key) or "")
        if value and value not in families:
            families.append(value)
    f.setFamilies(families)
    f.setPointSize(int(typography[size]) if isinstance(size, str) else int(size))
    if weight == "bold":
        f.setWeight(QFont.Weight.DemiBold)
    return f


def qss(colors: dict[str, str], typography: dict[str, Any]) -> str:
    c = colors
    body = int(typography.get("body", 10))
    small = int(typography.get("small", 9))
    return f"""
QMainWindow, QDialog, QScrollArea, QWidget#Canvas {{ background: {c["canvas"]}; }}
QWidget {{ color: {c["text"]}; }}
QAbstractScrollArea {{ border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QFrame#Card {{ background: {c["card"]}; border: 1px solid {c["border"]}; border-radius: {RADIUS}px; }}
QFrame#Metric {{ background: {c["alt"]}; border-radius: 8px; }}
QFrame#Rule {{ background: {c["alt"]}; max-height: 1px; min-height: 1px; border: none; }}
QFrame#Card QLabel, QFrame#Metric QLabel {{ background: transparent; }}
QLabel {{ background: transparent; }}
QLabel[role="muted"] {{ color: {c["text_muted"]}; }}
QLabel[role="small"] {{ color: {c["text_muted"]}; font-size: {small}pt; }}
QLabel[role="caps"] {{ color: {c["text_muted"]}; font-size: {small}pt; font-weight: 600; }}
QLabel[role="danger"] {{ color: {c["danger"]}; font-size: {small}pt; }}
QLabel#Dot {{ font-size: {body}pt; }}

QPushButton {{ background: {c["card"]}; color: {c["text"]}; border: 1px solid {c["border"]};
    border-radius: 8px; padding: 7px 15px; font-size: {body}pt; }}
QPushButton:hover {{ background: {c["alt"]}; }}
QPushButton:pressed {{ background: {c["border"]}; }}
QPushButton:disabled {{ color: {c["text_muted"]}; background: {c["canvas"]}; }}
QPushButton[kind="primary"] {{ background: {c["accent"]}; color: {c["accent_text"]};
    border-color: {c["accent"]}; font-weight: 600; }}
QPushButton[kind="primary"]:hover {{ background: {c["accent_hover"]}; border-color: {c["accent_hover"]}; }}
QPushButton[kind="primary"]:disabled {{ background: #A9A59E; border-color: #A9A59E;
    color: {c["accent_text"]}; }}
QPushButton[kind="compact"] {{ padding: 5px 10px; font-size: {small}pt; }}

QLineEdit {{ background: {c["card"]}; border: 1px solid {c["border"]}; border-radius: 8px;
    padding: 6px 8px; selection-background-color: {c["accent"]};
    selection-color: {c["accent_text"]}; }}
QLineEdit:focus {{ border-color: {c["focus"]}; }}

QProgressBar {{ background: {c["alt"]}; border: none; border-radius: 4px;
    max-height: 8px; min-height: 8px; text-align: center; }}
QProgressBar::chunk {{ background: {c["accent"]}; border-radius: 4px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #D8D3C8; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #C4BEB2; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

QToolTip {{ background: {c["accent"]}; color: {c["accent_text"]}; border: none; padding: 6px 8px; }}
QMessageBox QLabel {{ color: {c["text"]}; }}
"""


def apply(app: QApplication, theme: dict[str, Any]) -> None:
    colors = theme["colors"]
    typography = theme["typography"]
    app.setStyle("Fusion")
    app.setFont(font(typography, "body"))
    pal = app.palette()
    for role, key in ((QPalette.ColorRole.Window, "canvas"),
                      (QPalette.ColorRole.WindowText, "text"),
                      (QPalette.ColorRole.Base, "card"),
                      (QPalette.ColorRole.AlternateBase, "alt"),
                      (QPalette.ColorRole.Text, "text"),
                      (QPalette.ColorRole.Button, "card"),
                      (QPalette.ColorRole.ButtonText, "text"),
                      (QPalette.ColorRole.Highlight, "accent"),
                      (QPalette.ColorRole.HighlightedText, "accent_text"),
                      (QPalette.ColorRole.PlaceholderText, "text_muted"),
                      (QPalette.ColorRole.ToolTipBase, "accent"),
                      (QPalette.ColorRole.ToolTipText, "accent_text"),
                      (QPalette.ColorRole.Mid, "border")):
        pal.setColor(role, QColor(colors[key]))
    muted = QColor(colors["text_muted"])
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText,
                 QPalette.ColorRole.WindowText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, muted)
    app.setPalette(pal)
    app.setStyleSheet(qss(colors, typography))
