"""Тема пульта: токены и QSS.

Тёплая слоновая кость, белые карточки с тонкой рамкой, графитовый акцент.
Сетка 8 px, скругления 10 px, без градиентов и теней. Шрифт — Segoe UI
Variable, иначе Segoe UI. Размеры в pt: Qt 6 сам масштабирует их под DPI.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

BG = "#FAF9F5"          # фон окна
RAIL = "#F4F2EC"        # рейка навигации
CARD = "#FFFFFF"        # карточка
ALT = "#F5F3EC"         # вторичная заливка (плашки, чипсы)
BORDER = "#E6E2D9"      # рамка
TEXT = "#2B2825"        # чернила
MUTED = "#6B6660"       # приглушённый текст
ACCENT = "#36332E"      # графит
ACCENT_H = "#4A463F"    # графит при наведении
ACCENT_DIM = "#A9A59E"  # графит выключенной кнопки
ON_ACCENT = "#FAF9F5"   # текст на графите
SUCCESS = "#5B8A3C"
WARN = "#8A6D00"
DANGER = "#B0472C"
DANGER_SOFT = "#F5E3DD"
SCROLL = "#D8D3C8"
SCROLL_H = "#C4BEB2"

FONT_STACK = ["Segoe UI Variable", "Segoe UI", "Inter", "Arial"]
BASE_PT = 10
SMALL_PT = 9
TINY_PT = 8
GRID = 8
RADIUS = 10

_CHECK_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
    '<path d="M3.5 8.5l3 3 6-6" fill="none" stroke="{color}" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round"/></svg>'
)
_CHEVRON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
    '<path d="M4 6l4 4 4-4" fill="none" stroke="{color}" stroke-width="1.8" '
    'stroke-linecap="round" stroke-linejoin="round"/></svg>'
)


def asset_dir() -> Path:
    """Картинки для QSS (галочка, шеврон): QSS умеет только файлы, поэтому
    пишем их во временную папку один раз за запуск."""
    folder = Path(tempfile.gettempdir()) / "hard-pult-ui"
    folder.mkdir(parents=True, exist_ok=True)
    for name, svg, color in (("check.svg", _CHECK_SVG, ON_ACCENT),
                             ("chevron.svg", _CHEVRON_SVG, MUTED)):
        path = folder / name
        text = svg.format(color=color)
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
    return folder


def qss(assets: Path | None = None) -> str:
    folder = assets or asset_dir()
    check = (folder / "check.svg").as_posix()
    chevron = (folder / "chevron.svg").as_posix()
    return f"""
QMainWindow, QDialog, QScrollArea, QWidget#Page, QWidget#PageBody, QInputDialog,
QMessageBox {{ background: {BG}; }}
QWidget {{ color: {TEXT}; }}
QAbstractScrollArea {{ border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

QFrame#Rail {{ background: {RAIL}; border-right: 1px solid {BORDER}; }}
QFrame#Card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: {RADIUS}px; }}
QFrame#Pane {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 8px; }}
QFrame#Tile {{ background: {ALT}; border-radius: 8px; }}
QFrame#Divider {{ background: {BORDER}; max-height: 1px; min-height: 1px; border: none; }}
QFrame#Card QLabel, QFrame#Pane QLabel, QFrame#Tile QLabel {{ background: transparent; }}

QLabel {{ background: transparent; }}
QLabel[role="muted"] {{ color: {MUTED}; }}
QLabel[role="small"] {{ color: {MUTED}; font-size: {SMALL_PT}pt; }}
QLabel[role="tiny"] {{ color: {MUTED}; font-size: {TINY_PT}pt; }}
QLabel[role="caps"] {{ color: {MUTED}; font-size: {TINY_PT}pt; font-weight: 600; }}
QLabel[role="warn"] {{ color: {WARN}; font-size: {SMALL_PT}pt; }}
QLabel[role="danger"] {{ color: {DANGER}; font-size: {SMALL_PT}pt; }}
QLabel[role="success"] {{ color: {SUCCESS}; font-size: {SMALL_PT}pt; }}
QLabel[role="title"] {{ font-weight: 600; }}
QLabel[role="h1"] {{ font-size: 15pt; font-weight: 600; }}
QLabel[role="metric"] {{ font-size: 17pt; font-weight: 600; }}
QLabel[role="kv"] {{ font-size: 11pt; font-weight: 600; }}
QLabel[role="chip"] {{ background: {ALT}; border-radius: 11px; padding: 3px 10px;
    font-size: {SMALL_PT}pt; }}
QLabel[role="badge"] {{ background: {ALT}; color: {MUTED}; border-radius: 6px;
    padding: 2px 8px; font-size: {TINY_PT}pt; }}
QLabel#Toast {{ background: {ACCENT}; color: {ON_ACCENT}; padding: 9px 18px;
    border-radius: {RADIUS}px; font-size: {SMALL_PT}pt; }}
QLabel#Toast[err="true"] {{ background: {DANGER}; }}
QLabel#LogoMark {{ background: {ACCENT}; color: {ON_ACCENT}; border-radius: 6px;
    font-weight: 700; font-size: 11pt; }}

QPushButton {{ background: {CARD}; color: {TEXT}; border: 1px solid {BORDER};
    border-radius: 8px; padding: 6px 14px; font-size: {SMALL_PT}pt; }}
QPushButton:hover {{ background: {ALT}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: {MUTED}; background: {BG}; }}
QPushButton[kind="accent"] {{ background: {ACCENT}; color: {ON_ACCENT}; border-color: {ACCENT}; }}
QPushButton[kind="accent"]:hover {{ background: {ACCENT_H}; border-color: {ACCENT_H}; }}
QPushButton[kind="accent"]:pressed {{ background: {TEXT}; }}
QPushButton[kind="accent"]:disabled {{ background: {ACCENT_DIM}; border-color: {ACCENT_DIM};
    color: {ON_ACCENT}; }}
QPushButton[kind="danger"] {{ color: {DANGER}; }}
QPushButton[kind="danger"]:hover {{ background: {DANGER_SOFT}; }}
QPushButton[kind="chip"] {{ border-radius: 11px; padding: 3px 10px; color: {MUTED};
    background: {CARD}; }}
QPushButton[kind="chip"]:hover {{ background: {ALT}; color: {TEXT}; }}
QPushButton[kind="chip"]:checked {{ background: {ACCENT}; color: {ON_ACCENT};
    border-color: {ACCENT}; }}
QPushButton[kind="nav"] {{ background: transparent; border: none; border-radius: 8px;
    text-align: left; padding: 7px 12px; color: {MUTED}; font-size: {BASE_PT}pt; }}
QPushButton[kind="nav"]:hover {{ background: {ALT}; color: {TEXT}; }}
QPushButton[kind="nav"]:checked {{ background: {ACCENT}; color: {ON_ACCENT}; }}
QPushButton[kind="icon"] {{ padding: 5px 8px; }}

QLineEdit {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 5px 8px; selection-background-color: {ACCENT}; selection-color: {ON_ACCENT}; }}
QLineEdit:focus {{ border-color: {ACCENT}; }}
QLineEdit:disabled {{ color: {MUTED}; background: {BG}; }}

QComboBox {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 5px 26px 5px 8px; }}
QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; subcontrol-origin: padding;
    subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url("{chevron}"); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {CARD}; border: 1px solid {BORDER};
    selection-background-color: {ALT}; selection-color: {TEXT}; outline: 0; padding: 4px; }}

QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid #C9C4BA;
    border-radius: 4px; background: {CARD}; }}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT};
    image: url("{check}"); }}
QCheckBox::indicator:disabled {{ background: {BG}; border-color: {BORDER}; }}
QCheckBox:disabled {{ color: {MUTED}; }}
QCheckBox[role="warn"] {{ color: {WARN}; font-size: {SMALL_PT}pt; }}
QCheckBox[role="small"] {{ font-size: {SMALL_PT}pt; }}

QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 16px; height: 16px; margin: -6px 0;
    background: {ACCENT}; border-radius: 8px; }}
QSlider::handle:horizontal:hover {{ background: {ACCENT_H}; }}

QTableView {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 8px;
    gridline-color: {BG}; selection-background-color: {ALT}; selection-color: {TEXT};
    font-size: {SMALL_PT}pt; }}
QTableView::item {{ padding: 2px 6px; border: none; }}
QHeaderView {{ background: {CARD}; }}
QHeaderView::section {{ background: {CARD}; color: {MUTED}; font-size: {TINY_PT}pt;
    font-weight: 600; border: none; border-bottom: 1px solid {BORDER}; padding: 6px; }}
QTableCornerButton::section {{ background: {CARD}; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {SCROLL}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {SCROLL_H}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {SCROLL}; border-radius: 3px; min-width: 30px; }}
QScrollBar::handle:horizontal:hover {{ background: {SCROLL_H}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}

QProgressBar {{ background: {ALT}; border: none; border-radius: 4px; max-height: 8px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}

QToolTip {{ background: {ACCENT}; color: {ON_ACCENT}; border: none; padding: 6px 8px; }}
QMessageBox QLabel, QInputDialog QLabel {{ color: {TEXT}; }}
"""


def app_font(size_pt: int = BASE_PT, bold: bool = False) -> QFont:
    font = QFont()
    font.setFamilies(FONT_STACK)
    font.setPointSize(size_pt)
    if bold:
        font.setWeight(QFont.Weight.DemiBold)
    return font


def apply(app: QApplication) -> None:
    """Стиль, шрифт, палитра и QSS на всё приложение."""
    app.setStyle("Fusion")
    app.setFont(app_font())
    pal = app.palette()
    roles = {
        QPalette.ColorRole.Window: BG, QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: CARD, QPalette.ColorRole.AlternateBase: ALT,
        QPalette.ColorRole.Text: TEXT, QPalette.ColorRole.Button: CARD,
        QPalette.ColorRole.ButtonText: TEXT, QPalette.ColorRole.Highlight: ACCENT,
        QPalette.ColorRole.HighlightedText: ON_ACCENT,
        QPalette.ColorRole.PlaceholderText: MUTED, QPalette.ColorRole.ToolTipBase: ACCENT,
        QPalette.ColorRole.ToolTipText: ON_ACCENT, QPalette.ColorRole.Link: ACCENT,
        QPalette.ColorRole.Mid: BORDER, QPalette.ColorRole.Dark: MUTED,
        QPalette.ColorRole.Light: CARD,
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(MUTED))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(MUTED))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(MUTED))
    app.setPalette(pal)
    app.setStyleSheet(qss())
