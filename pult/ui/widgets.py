"""Виджеты пульта: карточка, тост, плашки KV, чипсы, кнопки трёх уровней,
поле даты с «сегодня», таблицы на QTableView, списки с галочками.

У виджетов, на которые смотрит логика из `core/import_flow.py`, есть маленький
протокол вида: `configure(text=…, state=…)`, `get()`, `set()`, `set_pairs()`.
Это не дань Tk, а общий язык между чистой логикой и любым окном.
"""

from __future__ import annotations

from PySide6.QtCore import (QAbstractTableModel, QModelIndex, QPoint, QRect, QSize,
                            QSortFilterProxyModel, Qt, QTimer)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFrame,
                               QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLayout,
                               QLineEdit, QPushButton, QScrollArea, QSizePolicy, QSlider,
                               QTableView, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from . import theme
from ..core.text import fmt_n, today_text


def repolish(w: QWidget) -> None:
    """Динамическое свойство поменялось — QSS надо применить заново."""
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


def clear_layout(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


# --- текст --------------------------------------------------------------------
class Note(QLabel):
    """Подпись с ролью (`muted`, `small`, `tiny`, `warn`, `danger`, `success`,
    `title`, `caps`, `text`) и переносом строк."""

    def __init__(self, text: str = "", role: str = "muted", wrap: bool = True,
                 parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setWordWrap(wrap)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setProperty("role", role)

    def set_role(self, role: str) -> None:
        if self.property("role") != role:
            self.setProperty("role", role)
            repolish(self)

    def configure(self, text: str | None = None, role: str | None = None, **_ignored) -> None:
        if role is not None:
            self.set_role(role)
        if text is not None:
            self.setText(text)

    def set(self, text: str) -> None:
        self.setText(text)


def label(text: str, role: str | None = None, bold: bool = False) -> QLabel:
    w = QLabel(text)
    if role:
        w.setProperty("role", role)
    if bold:
        w.setFont(theme.app_font(theme.BASE_PT, bold=True))
    return w


class Divider(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Divider")
        self.setFixedHeight(1)


# --- кнопки, переключатели, поля -----------------------------------------------
class Button(QPushButton):
    """accent (графит) | ghost (обводка) | danger | chip | icon."""

    def __init__(self, text: str, on_click=None, kind: str = "ghost",
                 parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setProperty("kind", kind)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        if on_click is not None:
            self.clicked.connect(lambda _checked=False: on_click())

    def configure(self, text: str | None = None, state: str | None = None, **_ignored) -> None:
        if text is not None:
            self.setText(text)
        if state is not None:
            self.setEnabled(state == "normal")


class Check(QCheckBox):
    def __init__(self, text: str = "", value: bool = False, on_toggle=None,
                 role: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setChecked(bool(value))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if role:
            self.setProperty("role", role)
        if on_toggle is not None:
            self.toggled.connect(lambda _on: on_toggle())

    def get(self) -> bool:
        return self.isChecked()

    def set(self, value: bool) -> None:
        self.blockSignals(True)
        self.setChecked(bool(value))
        self.blockSignals(False)

    def configure(self, state: str | None = None, text: str | None = None, **_ignored) -> None:
        if state is not None:
            self.setEnabled(state == "normal")
        if text is not None:
            self.setText(text)


class WrapCheck(QWidget):
    """Галочка с длинной подписью: QCheckBox не переносит текст, поэтому
    подпись — отдельная Note, клик по ней тоже переключает."""

    def __init__(self, text: str, value: bool = False, on_toggle=None,
                 role: str = "warn", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self._role = role
        self.box = Check("", value, on_toggle)
        self.label = Note(text, role=role)
        self.label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.label.mousePressEvent = lambda _e: self.box.isEnabled() and self.box.click()
        lay.addWidget(self.box, 0, Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self.label, 1)
        self.toggled = self.box.toggled

    def get(self) -> bool:
        return self.box.get()

    def set(self, value: bool) -> None:
        self.box.set(value)

    def configure(self, state: str | None = None, text: str | None = None, **_ignored) -> None:
        if state is not None:
            self.box.setEnabled(state == "normal")
            self.label.set_role(self._role if state == "normal" else "muted")
        if text is not None:
            self.label.setText(text)


class Field(QLineEdit):
    def __init__(self, text: str = "", width_chars: int | None = None,
                 secret: bool = False, placeholder: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        if placeholder:
            self.setPlaceholderText(placeholder)
        if secret:
            self.setEchoMode(QLineEdit.EchoMode.Password)
        if width_chars:
            self.setFixedWidth(self.fontMetrics().averageCharWidth() * width_chars + 22)

    def get(self) -> str:
        return self.text()

    def set(self, text: str) -> None:
        self.setText(text or "")

    @property
    def secret(self) -> bool:
        return self.echoMode() == QLineEdit.EchoMode.Password

    def toggle_secret(self) -> None:
        self.setEchoMode(QLineEdit.EchoMode.Normal if self.secret
                         else QLineEdit.EchoMode.Password)

    def configure(self, show: str | None = None, state: str | None = None, **_ignored) -> None:
        if show is not None:
            self.setEchoMode(QLineEdit.EchoMode.Password if show else QLineEdit.EchoMode.Normal)
        if state is not None:
            self.setEnabled(state == "normal")


class DateField(QWidget):
    """Поле «ДД.ММ.ГГГГ» с кнопкой «сегодня»."""

    def __init__(self, parent: QWidget | None = None, with_today: bool = True) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.edit = Field(width_chars=11, placeholder="ДД.ММ.ГГГГ")
        lay.addWidget(self.edit)
        if with_today:
            lay.addWidget(Button("сегодня", self.today, kind="icon"))

    def get(self) -> str:
        return self.edit.text()

    def set(self, text: str) -> None:
        self.edit.setText(text or "")

    def today(self) -> None:
        self.set(today_text())


class Combo(QComboBox):
    """Комбобокс, который не крутится колесом мыши: колесо листает страницу."""

    def __init__(self, values=(), width_chars: int | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        if width_chars:
            self.setMinimumContentsLength(width_chars)
            self.setFixedWidth(self.fontMetrics().averageCharWidth() * width_chars + 40)
        self.set_values(list(values))

    def wheelEvent(self, event) -> None:  # noqa: N802 — Qt
        event.ignore()

    def set_values(self, values) -> None:
        current = self.currentText()
        self.blockSignals(True)
        self.clear()
        self.addItems([str(v) for v in values])
        idx = self.findText(current)
        if idx >= 0:
            self.setCurrentIndex(idx)
        self.blockSignals(False)

    def get(self) -> str:
        return self.currentText()

    def current(self) -> int:
        return self.currentIndex()

    def set_current(self, index: int) -> None:
        self.blockSignals(True)
        self.setCurrentIndex(index)
        self.blockSignals(False)

    def on_select(self, fn) -> None:
        """Только выбор человеком (как <<ComboboxSelected>> в Tk)."""
        self.activated.connect(lambda _i: fn())


class Slider(QWidget):
    """Ползунок 0–100 с подписью «Долг 50»."""

    def __init__(self, title: str, value: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.title = title
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.label = Note(f"{title} {value}", role="small")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(value)
        self.slider.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.slider.wheelEvent = lambda e: e.ignore()
        self.slider.valueChanged.connect(lambda v: self.label.setText(f"{title} {v}"))
        lay.addWidget(self.label)
        lay.addWidget(self.slider)

    def value(self) -> int:
        return self.slider.value()

    def get(self) -> int:
        return self.value()


# --- контейнеры -----------------------------------------------------------------
def hrow(*items, spacing: int = 8, stretch: bool = True) -> QHBoxLayout:
    """Строка: виджеты, вложенные раскладки, числа (отступ) и None (растяжка)."""
    lay = QHBoxLayout()
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for it in items:
        if it is None:
            lay.addStretch(1)
        elif isinstance(it, int):
            lay.addSpacing(it)
        elif isinstance(it, QLayout):
            lay.addLayout(it)
        else:
            lay.addWidget(it)
    if stretch:
        lay.addStretch(1)
    return lay


def vcol(*items, spacing: int = 6) -> QVBoxLayout:
    lay = QVBoxLayout()
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for it in items:
        if isinstance(it, QLayout):
            lay.addLayout(it)
        elif isinstance(it, int):
            lay.addSpacing(it)
        else:
            lay.addWidget(it)
    return lay


class Card(QFrame):
    """Секция экрана: заголовок, необязательное описание и содержимое.

    Рамки и фона у неё нет — от соседней секции её отделяет волосяная черта
    сверху (см. theme.qss). До 21.09.2026 это была белая карточка с рамкой, и
    экран из пяти таких карточек читался как стопка коробок, а не как одна
    страница. Отступы по горизонтали нулевые: содержимое секции выравнивается
    по общему полю страницы, и колонки разных секций стоят друг под другом.
    """

    def __init__(self, title: str, desc: str | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(0, 22, 0, 26)
        self.body.setSpacing(10)
        self.title = QLabel(title)
        self.title.setProperty("role", "title")
        self.body.addWidget(self.title)
        if desc:
            self.desc = Note(desc, role="small")
            self.body.addWidget(self.desc)
        self.body.addSpacing(2)

    def add(self, widget: QWidget, spacing_before: int = 0) -> QWidget:
        if spacing_before:
            self.body.addSpacing(spacing_before)
        self.body.addWidget(widget)
        return widget

    def add_layout(self, layout: QLayout, spacing_before: int = 0) -> QLayout:
        if spacing_before:
            self.body.addSpacing(spacing_before)
        self.body.addLayout(layout)
        return layout

    def add_row(self, *items, spacing_before: int = 0, stretch: bool = True) -> QHBoxLayout:
        return self.add_layout(hrow(*items, stretch=stretch), spacing_before)


class Pane(QFrame):
    """Рамка внутри карточки (панель республики, список с галочками)."""

    def __init__(self, parent: QWidget | None = None, margins: int = 10) -> None:
        super().__init__(parent)
        self.setObjectName("Pane")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(margins, margins, margins, margins)
        self.body.setSpacing(4)


class Rows(QWidget):
    """Перестраиваемый список строк (привязки, устройства, беседы…)."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 2) -> None:
        super().__init__(parent)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(spacing)

    def clear(self) -> None:
        clear_layout(self.body)

    def add(self, widget: QWidget) -> QWidget:
        self.body.addWidget(widget)
        return widget

    def add_layout(self, layout: QLayout) -> QLayout:
        self.body.addLayout(layout)
        return layout

    def add_row(self, *items, stretch: bool = True) -> QHBoxLayout:
        return self.add_layout(hrow(*items, stretch=stretch))

    def set_empty(self, text: str) -> None:
        self.clear()
        self.add(Note(text, role="small"))


class Page(QScrollArea):
    """Прокручиваемое содержимое раздела."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Page")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.inner = QWidget()
        self.inner.setObjectName("PageBody")
        self.body = QVBoxLayout(self.inner)
        self.body.setContentsMargins(34, 6, 34, 44)
        # Ноль: секции сами держат воздух отступами и отбиты чертой. Со
        # спейсингом между ними черта повисала в пустоте отдельной линией.
        self.body.setSpacing(0)
        self.body.addStretch(1)
        self.setWidget(self.inner)

    def add(self, widget: QWidget) -> QWidget:
        # Первая секция на странице живёт без черты сверху: линия под самым
        # заголовком экрана выглядит как обрезанная шапка.
        if widget.objectName() == "Card" and self.body.count() == 1:
            widget.setProperty("first", "true")
        self.body.insertWidget(self.body.count() - 1, widget)
        return widget

    def scroll_top(self) -> None:
        self.verticalScrollBar().setValue(0)

    def scroll_to(self, widget: QWidget) -> None:
        """Показать карточку, лежащую ниже видимой области."""
        self.inner.layout().activate()
        self.verticalScrollBar().setValue(max(0, widget.y() - 8))


# --- плашки, чипсы, полоса -------------------------------------------------------
class KVGrid(QWidget):
    """Показатели: число крупно, подпись словом под ним.

    До 21.09.2026 это были серые плашки с числом в 13pt — они читались как
    подписи, а не как ответ на вопрос. В общем языке обоих пультов число и есть
    главное: 26pt, без коробки, подпись под ним мелким. Больше COLS в ряд не
    влезает — переносим.
    """

    COLS = 6

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(40)
        self.grid.setVerticalSpacing(18)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def set_pairs(self, pairs, cols: int | None = None) -> None:
        clear_layout(self.grid)
        cols = cols or self.COLS
        for i, (k, v) in enumerate(pairs):
            row, col = divmod(i, cols)
            tile = QFrame()
            lay = QVBoxLayout(tile)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(0)
            lay.addWidget(label(fmt_n(v), "metric"))
            lay.addWidget(label(str(k), "small"))
            self.grid.addWidget(tile, row, col, Qt.AlignmentFlag.AlignLeft)
        self.grid.setColumnStretch(cols, 1)


class FlowLayout(QLayout):
    """Раскладка с автопереносом (классический пример Qt)."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 4) -> None:
        super().__init__(parent)
        self._items = []
        self._space = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:  # noqa: N802 — Qt
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index):  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._layout(rect, False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _layout(self, rect: QRect, test_only: bool) -> int:
        x, y, line_h = rect.x(), rect.y(), 0
        for item in self._items:
            w, h = item.sizeHint().width(), item.sizeHint().height()
            if x + w > rect.right() + 1 and line_h > 0:
                x = rect.x()
                y += line_h + self._space
                line_h = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x += w + self._space
            line_h = max(line_h, h)
        return y + line_h - rect.y()


class ChipFlow(QWidget):
    """Поток «чипсов» с автопереносом: строки — пассивные, кортежи
    (текст, включён, on_click) — переключатели."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.flow = FlowLayout(self)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

    def set_chips(self, items) -> None:
        clear_layout(self.flow)
        for it in items:
            if isinstance(it, str):
                chip = QLabel(it)
                chip.setProperty("role", "chip")
            else:
                text, on, cb = it
                chip = Button(text, cb, kind="chip")
                chip.setCheckable(True)
                chip.setChecked(bool(on))
            self.flow.addWidget(chip)
        self.updateGeometry()


class Bars(QWidget):
    """Столбики по дням: значение над столбиком, число дня под ним. Выходные
    бледнее, последний день (сегодня) — зелёный."""

    H = 96

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: list[dict] = []
        self.setFixedHeight(self.H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_series(self, items: list[dict]) -> None:
        self._items = list(items or [])
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        items = self._items
        if not items:
            p.setPen(QColor(theme.MUTED))
            p.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), "нет данных")
            return
        w, h = self.width(), self.height()
        top, bottom = 16, 18
        gap = 6
        bw = max(4, (w - gap * (len(items) - 1)) / len(items))
        mx = max(1, max(int(it.get("value") or 0) for it in items))
        p.setFont(theme.app_font(theme.TINY_PT))
        for i, it in enumerate(items):
            v = int(it.get("value") or 0)
            x = i * (bw + gap)
            bh = max(2, (h - top - bottom) * v / mx)
            color = (theme.SUCCESS if i == len(items) - 1
                     else "#C9C3B6" if it.get("weekend") else theme.ACCENT)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(color))
            p.drawRoundedRect(QRect(int(x), int(h - bottom - bh), int(bw), int(bh)), 3, 3)
            p.setPen(QColor(theme.TEXT if v else theme.MUTED))
            if v:
                p.drawText(QRect(int(x) - 8, int(h - bottom - bh) - 15, int(bw) + 16, 14),
                           int(Qt.AlignmentFlag.AlignCenter), str(v))
            p.setPen(QColor(theme.MUTED))
            p.drawText(QRect(int(x) - 8, h - bottom + 2, int(bw) + 16, 14),
                       int(Qt.AlignmentFlag.AlignCenter), str(it.get("label") or ""))


class Meter(QWidget):
    """Полоса покрытия: 8 px, без градиентов."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pc = 0
        self.setFixedHeight(8)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_percent(self, pc: float) -> None:
        self._pc = max(0.0, min(100.0, float(pc or 0)))
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.ALT))
        p.drawRoundedRect(self.rect(), 4, 4)
        w = int(self.width() * self._pc / 100)
        if w > 0:
            p.setBrush(QColor(theme.SUCCESS))
            p.drawRoundedRect(QRect(0, 0, w, self.height()), 4, 4)
        p.end()


# --- таблицы --------------------------------------------------------------------
class RowsModel(QAbstractTableModel):
    def __init__(self, titles: list[str], numeric: set[int], parent=None,
                 tint=None, tint_cols: set[int] | None = None,
                 checkable: bool = False) -> None:
        super().__init__(parent)
        self.titles = titles
        self.numeric = numeric
        # Необязательные галочки в первой колонке (массовые действия в «Входе»).
        # Отмечено хранится по ЗНАЧЕНИЮ первой колонки, а не по номеру строки:
        # строки пересобираются при каждом фильтре и перечитывании списка, а
        # отметка человека пропадать от этого не должна.
        self.checkable = checkable
        self.checked: set = set()
        self.on_check = None
        # Необязательная раскраска: tint(строка) → цвет (hex) или None. Нужна
        # там, где состояние — главное в строке («Вход»); по умолчанию выключена,
        # чтобы остальные таблицы остались слово в слово прежними.
        self.tint = tint
        self.tint_cols = set(tint_cols or ())
        self.rows: list[tuple] = []

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.titles)

    def flags(self, index):
        base = super().flags(index)
        if self.checkable and index.isValid() and index.column() == 0:
            return base | Qt.ItemFlag.ItemIsUserCheckable
        return base

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):  # noqa: N802
        if not (self.checkable and index.isValid() and index.column() == 0
                and role == Qt.ItemDataRole.CheckStateRole):
            return False
        key = self.rows[index.row()][0]
        state = value.value if hasattr(value, "value") else int(value)
        if state == Qt.CheckState.Checked.value:
            self.checked.add(key)
        else:
            self.checked.discard(key)
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
        if self.on_check is not None:
            self.on_check(len(self.checked))
        return True

    def set_checked(self, keys) -> None:
        self.checked = set(keys)
        if self.rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows) - 1, 0),
                                  [Qt.ItemDataRole.CheckStateRole])
        if self.on_check is not None:
            self.on_check(len(self.checked))

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if (role == Qt.ItemDataRole.CheckStateRole and self.checkable
                and index.column() == 0):
            key = self.rows[index.row()][0]
            return (Qt.CheckState.Checked if key in self.checked
                    else Qt.CheckState.Unchecked)
        if role == Qt.ItemDataRole.DisplayRole:
            row = self.rows[index.row()]
            return "" if index.column() >= len(row) else str(row[index.column()])
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if index.column() in self.numeric:
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ToolTipRole:
            row = self.rows[index.row()]
            return "" if index.column() >= len(row) else str(row[index.column()])
        if role == Qt.ItemDataRole.UserRole:
            # ключ сортировки: «1 234» и «12%» сортируются как числа, остальное —
            # как текст без учёта регистра
            row = self.rows[index.row()]
            return sort_key(None if index.column() >= len(row) else row[index.column()])
        if role == Qt.ItemDataRole.ForegroundRole and self.tint is not None:
            if not self.tint_cols or index.column() in self.tint_cols:
                color = self.tint(self.rows[index.row()])
                return QColor(color) if color else None
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.titles[section]
        return None

    def set_rows(self, rows: list[tuple]) -> None:
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()


def sort_key(value):
    """Число — числом, строка — строкой без регистра, пусто — в конец."""
    if value is None or value == "":
        return (2, 0.0, "")
    if isinstance(value, (int, float)):
        return (0, float(value), "")
    s = str(value).replace("\u00a0", "").replace(" ", "").rstrip("%₽")
    try:
        return (0, float(s.replace(",", ".")), "")
    except ValueError:
        return (1, 0.0, str(value).casefold())


class Table(QTableView):
    """Таблица на модели с сортировкой кликом по заголовку и поиском по всем
    колонкам. columns: ((ключ, заголовок, ширина), …); numeric — ключи с
    выравниванием вправо; stretch — ключи, тянущиеся по ширине."""

    ROW_H = 28

    def __init__(self, columns, numeric=(), stretch=(), visible_rows: int = 14,
                 tint=None, tint_cols=(), checkable: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.keys = [c[0] for c in columns]
        self.visible_rows = visible_rows
        numeric_idx = {i for i, c in enumerate(columns) if c[0] in set(numeric)}
        tint_idx = {i for i, c in enumerate(columns) if c[0] in set(tint_cols)}
        self.model_ = RowsModel([c[1] for c in columns], numeric_idx, self,
                                tint=tint, tint_cols=tint_idx, checkable=checkable)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model_)
        self.proxy.setSortRole(Qt.ItemDataRole.UserRole)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)
        self.setModel(self.proxy)
        self.setSortingEnabled(True)
        self.proxy.sort(-1)              # исходный порядок, пока не кликнули
        self._on_select = None
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(self.ROW_H)
        self.setShowGrid(False)
        self.setAlternatingRowColors(False)
        self.setWordWrap(False)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setStretchLastSection(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for i, (key, _title, width) in enumerate(columns):
            if key in set(stretch):
                header.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
            else:
                header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
                self.setColumnWidth(i, int(width))
        self.set_rows([])

    def set_rows(self, rows: list[tuple]) -> None:
        self.model_.set_rows(rows)
        self._fit(len(rows))

    def _fit(self, n_rows: int) -> None:
        n = min(max(n_rows, 2), self.visible_rows)
        self.setFixedHeight(self.horizontalHeader().sizeHint().height() + n * self.ROW_H + 4)

    def set_filter(self, text: str) -> None:
        self.proxy.setFilterFixedString((text or "").strip())
        self._fit(self.proxy.rowCount())

    def visible_count(self) -> int:
        return self.proxy.rowCount()

    def selected_row(self) -> tuple | None:
        idx = self.currentIndex()
        if not idx.isValid():
            return None
        src = self.proxy.mapToSource(idx)
        return self.model_.rows[src.row()] if 0 <= src.row() < len(self.model_.rows) else None

    def on_select(self, fn) -> None:
        """fn(row | None) при смене выбранной строки."""
        self._on_select = fn
        self.selectionModel().currentRowChanged.connect(lambda _c, _p: fn(self.selected_row()))

    def select_key(self, value, col: int = 0) -> bool:
        """Выбрать строку по значению колонки. Нужно после перезагрузки списка:
        оператор нажал кнопку на строке — строка не должна убегать из-под руки."""
        for i, row in enumerate(self.model_.rows):
            if len(row) > col and row[col] == value:
                idx = self.proxy.mapFromSource(self.model_.index(i, col))
                if idx.isValid():
                    self.setCurrentIndex(idx)
                    return True
                return False
        return False

    def clear(self) -> None:
        self.set_rows([])

    # ── галочки (только у таблицы с checkable=True) ────────────────────
    def checked_keys(self) -> list:
        """Отмеченные — в порядке строк модели; отмеченные, но скрытые текущим
        фильтром, тоже здесь: фильтр меняет вид, а не выбор."""
        in_rows = [r[0] for r in self.model_.rows if r and r[0] in self.model_.checked]
        rest = sorted(k for k in self.model_.checked if k not in set(in_rows))
        return in_rows + rest

    def set_checked(self, keys) -> None:
        self.model_.set_checked(keys)

    def check_visible(self, on: bool = True) -> None:
        """Отметить (или снять) всех, кого сейчас видно после фильтра и поиска."""
        visible = set()
        for i in range(self.proxy.rowCount()):
            src = self.proxy.mapToSource(self.proxy.index(i, 0))
            if 0 <= src.row() < len(self.model_.rows):
                visible.add(self.model_.rows[src.row()][0])
        keys = set(self.model_.checked)
        keys = keys | visible if on else keys - visible
        self.model_.set_checked(keys)

    def on_check(self, fn) -> None:
        """fn(число отмеченных) при каждой смене отметок."""
        self.model_.on_check = fn


class DecisionTable(QTableWidget):
    """Таблица конфликтов с выбором решения в последней колонке.

    columns — как у Table ((ключ, заголовок, ширина), …); в каждой строке
    справа выпадашка с вариантами labels (ключ → подпись). decisions() отдаёт
    {(КД, поле): ключ решения} — ровно то, что ждёт enrich.build_plan."""

    ROW_H = 32
    DECISION_W = 210

    def __init__(self, columns, labels: dict, order: tuple, default: str,
                 stretch=(), visible_rows: int = 12, parent: QWidget | None = None) -> None:
        super().__init__(0, len(columns) + 1, parent)
        self.columns = list(columns)
        self.labels = dict(labels)
        self.order = tuple(order)
        self.default = default
        self.visible_rows = visible_rows
        self.records: list[dict] = []
        self.setHorizontalHeaderLabels([c[1] for c in columns] + ["Решение"])
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(self.ROW_H)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setStretchLastSection(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for i, (key, _title, width) in enumerate(columns):
            if key in set(stretch):
                header.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
            else:
                header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
                self.setColumnWidth(i, int(width))
        last = len(columns)
        header.setSectionResizeMode(last, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(last, self.DECISION_W)
        self._fit(0)

    def _fit(self, n_rows: int) -> None:
        n = min(max(n_rows, 2), self.visible_rows)
        self.setFixedHeight(self.horizontalHeader().sizeHint().height() + n * self.ROW_H + 4)

    def set_conflicts(self, conflicts: list[dict], rows: list[tuple]) -> None:
        """conflicts — словари конфликтов, rows — их строки для показа (та же
        длина и порядок)."""
        self.setRowCount(0)
        self.records = list(conflicts)
        for i, (record, row) in enumerate(zip(conflicts, rows)):
            self.insertRow(i)
            for j, value in enumerate(row):
                item = QTableWidgetItem(str(value))
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                item.setToolTip(str(value))
                self.setItem(i, j, item)
            combo = QComboBox()
            for key in self.order:
                combo.addItem(self.labels.get(key, key), key)
            current = str(record.get("decision") or self.default)
            combo.setCurrentIndex(max(self.order.index(current), 0)
                                  if current in self.order else 0)
            self.setCellWidget(i, len(row), combo)
        self._fit(len(conflicts))

    def set_all(self, choice: str) -> None:
        if choice not in self.order:
            return
        idx = self.order.index(choice)
        for i in range(self.rowCount()):
            combo = self.cellWidget(i, self.columnCount() - 1)
            if combo is not None:
                combo.setCurrentIndex(idx)

    def decisions(self) -> dict:
        out: dict = {}
        for i, record in enumerate(self.records):
            combo = self.cellWidget(i, self.columnCount() - 1)
            if combo is None:
                continue
            out[(str(record.get("contract_id") or ""),
                 str(record.get("key") or ""))] = str(combo.currentData() or self.default)
        return out

    def clear(self) -> None:
        self.set_conflicts([], [])


class ColumnMapTable(QTableWidget):
    """Таблица «колонка файла → что с ней делать» для экрана «Колонки реестра».

    Строка = заголовок, который есть в книге, но бот его не читает. Оператор
    выбирает решение (не читать / связать с полем / дополнительное поле /
    скрыть) и, для дополнительного поля, задаёт подпись для карточки выездника.
    Подпись включается только там, где она осмысленна: у остальных решений
    поле неактивно, чтобы человек не заполнял его впустую.
    """

    ROW_H = 32
    DECISION_W = 260
    LABEL_W = 200

    def __init__(self, parent: QWidget | None = None, visible_rows: int = 10) -> None:
        super().__init__(0, 4, parent)
        self.visible_rows = visible_rows
        self.records: list[dict] = []
        self.options: list[tuple[str, str]] = []
        self.setHorizontalHeaderLabels(
            ["Кол.", "Заголовок в файле", "Что делать", "Подпись в карточке"])
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(self.ROW_H)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setStretchLastSection(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(0, 48)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(2, self.DECISION_W)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(3, self.LABEL_W)
        self._fit(0)

    def _fit(self, n_rows: int) -> None:
        n = min(max(n_rows, 2), self.visible_rows)
        self.setFixedHeight(
            self.horizontalHeader().sizeHint().height() + n * self.ROW_H + 4)

    def set_rows(self, records: list[dict], options: list[tuple[str, str]]) -> None:
        """records — [{column, title, decision, label}], options — [(ключ, подпись)]."""
        self.setRowCount(0)
        self.records = list(records)
        self.options = list(options)
        for i, record in enumerate(self.records):
            self.insertRow(i)
            for j, value in enumerate((record.get("column", ""), record["title"])):
                item = QTableWidgetItem(str(value))
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                item.setToolTip(str(value))
                self.setItem(i, j, item)
            combo = QComboBox()
            for key, label_text in self.options:
                combo.addItem(label_text, key)
            current = str(record.get("decision") or "skip")
            index = next((n for n, (key, _) in enumerate(self.options)
                          if key == current), 0)
            combo.setCurrentIndex(index)
            self.setCellWidget(i, 2, combo)
            edit = QLineEdit(str(record.get("label") or record["title"]))
            self.setCellWidget(i, 3, edit)
            combo.currentIndexChanged.connect(
                lambda _n=0, row=i: self._sync_label(row))
            self._sync_label(i)
        self._fit(len(self.records))

    def _sync_label(self, row: int) -> None:
        combo = self.cellWidget(row, 2)
        edit = self.cellWidget(row, 3)
        if combo is None or edit is None:
            return
        edit.setEnabled(str(combo.currentData() or "") == "extra")

    def decisions(self) -> list[dict]:
        """[{title, decision, label}] — по одной записи на строку таблицы."""
        out: list[dict] = []
        for i, record in enumerate(self.records):
            combo = self.cellWidget(i, 2)
            edit = self.cellWidget(i, 3)
            out.append({
                "title": record["title"],
                "decision": str(combo.currentData() or "skip") if combo else "skip",
                "label": edit.text().strip() if edit else record["title"],
            })
        return out

    def clear_rows(self) -> None:
        self.set_rows([], self.options)


# --- список с галочками ---------------------------------------------------------
class CheckList(Pane):
    """Рамка со строками «галочка · подпись · (справа)». Строит `set_items`,
    сообщает `on_toggle(key, on)`."""

    def __init__(self, parent: QWidget | None = None, columns: int = 1) -> None:
        super().__init__(parent, margins=6)
        self.columns = columns
        self.grid = QGridLayout()
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(2)
        self.body.addLayout(self.grid)
        self.checks: dict[str, Check] = {}
        self.on_toggle = None

    def set_items(self, items: list[dict], on_toggle=None) -> None:
        clear_layout(self.grid)
        self.checks = {}
        self.on_toggle = on_toggle
        cols = self.columns
        for i, it in enumerate(items):
            key = it["key"]
            row, col = divmod(i, cols)
            cell = QWidget()
            lay = QHBoxLayout(cell)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(8)
            check = Check(it.get("text", ""), it.get("checked", False),
                          role=it.get("role"))
            check.toggled.connect(lambda on, k=key: self._toggled(k, on))
            lay.addWidget(check)
            if it.get("note"):
                lay.addWidget(Note(it["note"], role="tiny", wrap=False))
            lay.addStretch(1)
            if it.get("right"):
                lay.addWidget(Note(it["right"], role="tiny", wrap=False))
            self.grid.addWidget(cell, row, col)
            self.checks[key] = check
        for c in range(cols):
            self.grid.setColumnStretch(c, 1)

    def _toggled(self, key: str, on: bool) -> None:
        if self.on_toggle:
            self.on_toggle(key, on)

    def set_checked(self, keys) -> None:
        keys = set(keys)
        for key, check in self.checks.items():
            check.set(key in keys)

    def set_all(self, on: bool) -> None:
        for check in self.checks.values():
            check.set(on)

    def checked_keys(self) -> list[str]:
        return [k for k, c in self.checks.items() if c.get()]


# --- тост ------------------------------------------------------------------------
class Toast(QLabel):
    """Короткое уведомление внизу окна; красное — при ошибке."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_text(self, text: str, err: bool = False, msecs: int = 3200) -> None:
        self.setProperty("err", "true" if err else "false")
        repolish(self)
        self.setText(text)
        self.setMaximumWidth(int(self.parentWidget().width() * 0.7))
        self.adjustSize()
        self.reposition()
        self.show()
        self.raise_()
        self._timer.start(msecs)

    def reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.move((parent.width() - self.width()) // 2,
                  parent.height() - self.height() - 26)
