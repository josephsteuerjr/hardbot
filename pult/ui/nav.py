"""Левая рейка: логотип, разделы с монохромными иконками, индикатор связи."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from . import theme

# Порядок — по частоте использования: сначала день и вход людей, потом
# настройки. «Вход» — персональные ссылки PWA: в контуре без Telegram это
# единственный путь сотрудника в систему. «Активность» слилась с «Людьми»
# в «Сотрудников» (одни и те же люди в двух разделах), рассылки и беседы
# ушли из «Руководителей» в «Каналы».
SECTIONS = [
    ("dash", "Сегодня"), ("links", "Вход"), ("people", "Сотрудники"),
    ("mgrs", "Руководители"), ("channels", "Каналы"), ("registry", "Реестр"),
    ("prio", "Приоритет"), ("conn", "Подключение"),
]
TITLES = dict(SECTIONS) | {"prio": "Приоритет выезда",
                           "links": "Вход сотрудников"}

# Иконки 24×24, штрих currentColor — красятся под состояние кнопки.
_STROKE = ('fill="none" stroke="{c}" stroke-width="1.7" stroke-linecap="round" '
           'stroke-linejoin="round"')
ICONS = {
    "dash": ('<rect x="3.5" y="3.5" width="7" height="7" rx="1.5"/>'
             '<rect x="13.5" y="3.5" width="7" height="7" rx="1.5"/>'
             '<rect x="3.5" y="13.5" width="7" height="7" rx="1.5"/>'
             '<rect x="13.5" y="13.5" width="7" height="7" rx="1.5"/>'),
    "registry": ('<rect x="3.5" y="4.5" width="17" height="15" rx="2"/>'
                 '<path d="M3.5 9.5h17M9.5 9.5v10M3.5 14.5h17"/>'),
    "prio": '<path d="M5 19.5v-6M12 19.5v-11M19 19.5v-15"/>',
    "people": ('<circle cx="12" cy="8" r="3.5"/>'
               '<path d="M5 20c0-3.6 3.1-6 7-6s7 2.4 7 6"/>'),
    "links": ('<rect x="4" y="4" width="6" height="6" rx="1"/>'
              '<rect x="14" y="4" width="6" height="6" rx="1"/>'
              '<rect x="4" y="14" width="6" height="6" rx="1"/>'
              '<path d="M14 14h2v2h-2zM18 14h2v2h-2zM14 18h2v2h-2zM18 18h2v2h-2z"/>'),
    "mgrs": ('<circle cx="9" cy="8" r="3"/><path d="M3.5 19c0-3 2.5-5 5.5-5s5.5 2 5.5 5"/>'
             '<path d="M16 5.5a3 3 0 0 1 0 5.6M17 14.3c2.2.6 3.5 2.3 3.5 4.7"/>'),
    "channels": ('<path d="M4 11.5v-2a1.5 1.5 0 0 1 1.5-1.5H9l7-4v16l-7-4H5.5A1.5 1.5 0 0 1 4 14.5v-3z"/>'
                 '<path d="M19 9.5a3.5 3.5 0 0 1 0 5"/>'),
    "conn": ('<path d="M10 14l4-4"/>'
             '<path d="M8.5 15.5l-1.7 1.7a3.2 3.2 0 0 1-4.5-4.5l3.4-3.4a3.2 3.2 0 0 1 4.5 0"/>'
             '<path d="M15.5 8.5l1.7-1.7a3.2 3.2 0 0 1 4.5 4.5l-3.4 3.4a3.2 3.2 0 0 1-4.5 0"/>'),
}


def svg_icon(name: str, color: str, size: int = 18, dpr: float = 1.0) -> QPixmap:
    body = ICONS[name]
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
           f'<g {_STROKE.format(c=color)}>{body}</g></svg>')
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    px = QPixmap(int(size * dpr), int(size * dpr))
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, px.width(), px.height()))
    painter.end()
    px.setDevicePixelRatio(dpr)
    return px


def section_icon(name: str, dpr: float) -> QIcon:
    icon = QIcon()
    icon.addPixmap(svg_icon(name, theme.MUTED, dpr=dpr), QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(svg_icon(name, theme.TEXT, dpr=dpr), QIcon.Mode.Active, QIcon.State.Off)
    icon.addPixmap(svg_icon(name, theme.ON_ACCENT, dpr=dpr), QIcon.Mode.Normal, QIcon.State.On)
    icon.addPixmap(svg_icon(name, theme.ON_ACCENT, dpr=dpr), QIcon.Mode.Active, QIcon.State.On)
    return icon


class NavRail(QFrame):
    selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Rail")
        self.setFixedWidth(224)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 16, 8, 12)
        lay.setSpacing(0)

        logo = QHBoxLayout()
        logo.setContentsMargins(6, 0, 6, 14)
        mark = QLabel("В")
        mark.setObjectName("LogoMark")
        mark.setFixedSize(28, 28)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.addWidget(mark)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        name = QLabel("Хард")
        name.setFont(theme.app_font(11, bold=True))
        sub = QLabel("ПУЛЬТ")
        sub.setProperty("role", "caps")
        titles.addWidget(name)
        titles.addWidget(sub)
        logo.addSpacing(9)
        logo.addLayout(titles)
        logo.addStretch(1)
        lay.addLayout(logo)

        dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
        self.buttons: dict[str, QPushButton] = {}
        for sec, label in SECTIONS:
            if sec == "conn":
                lay.addStretch(1)
            b = QPushButton(label)
            b.setProperty("kind", "nav")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setIcon(section_icon(sec, dpr))
            b.setIconSize(QSize(18, 18))
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            b.clicked.connect(lambda _c=False, s=sec: self.selected.emit(s))
            lay.addWidget(b)
            lay.addSpacing(2)
            self.buttons[sec] = b

        foot = QHBoxLayout()
        foot.setContentsMargins(6, 12, 6, 0)
        self.dot = QLabel()
        self.dot.setFixedSize(10, 10)
        foot.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
        foot.addSpacing(8)
        ft = QVBoxLayout()
        ft.setSpacing(0)
        self.conn_lbl = QLabel("не подключено")
        self.conn_lbl.setProperty("role", "small")
        self.conn_lbl.setStyleSheet(f"color: {theme.TEXT};")
        self.host_lbl = QLabel("—")
        self.host_lbl.setProperty("role", "tiny")
        ft.addWidget(self.conn_lbl)
        ft.addWidget(self.host_lbl)
        foot.addLayout(ft)
        foot.addStretch(1)
        lay.addLayout(foot)
        self.set_connection(False, "не подключено", "—")

    def set_active(self, sec: str) -> None:
        for s, b in self.buttons.items():
            b.setChecked(s == sec)

    def set_connection(self, ok: bool, text: str, host: str) -> None:
        color = theme.SUCCESS if ok else theme.DANGER
        self.dot.setStyleSheet(f"background: {color}; border-radius: 5px;")
        self.conn_lbl.setText(text)
        self.host_lbl.setText(host)
