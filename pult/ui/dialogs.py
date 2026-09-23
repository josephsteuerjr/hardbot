"""Диалоги пульта поверх QMessageBox / QInputDialog / QFileDialog.

Имена — как у `tkinter.messagebox`/`filedialog`: на `askyesno` опираются
пробники в тестах (`pult.messagebox.askyesno`), и их удобно читать в
обработчиках: подтверждение — это `askyesno(заголовок, текст)`.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QInputDialog,
                               QLabel, QLineEdit, QMessageBox, QVBoxLayout, QWidget)

from .widgets import Button, Field, Note


def copy_text(text: str) -> None:
    """Положить в буфер обмена. Отдельной функцией — чтобы разделы не тянули
    QApplication ради одной строки."""
    QApplication.clipboard().setText(text or "")


def qr_pixmap(url: str, size: int = 300):
    """QR-картинка ссылки или None, если segno не установлен.

    Телефону проще отсканировать QR с экрана пульта, чем аналитику
    пересылать ссылку; белая подложка — ради сканируемости в любой теме."""
    try:
        import io

        import segno
    except ImportError:
        return None
    qr = segno.make(url, error="m")
    modules = len(qr.matrix)
    scale = max(4, round(size / (modules + 4)))
    buf = io.BytesIO()
    qr.save(buf, kind="png", scale=scale, border=2,
            dark="#101418", light="#ffffff")
    from PySide6.QtGui import QPixmap

    px = QPixmap()
    px.loadFromData(buf.getvalue(), "PNG")
    return px


QR_MISSING = ("QR не показан: пульту не хватает модуля segno "
              "(pip install segno) — ссылку можно только скопировать.")


def link_panel(url: str, lines=(), size: int = 300, side: bool = False,
               parent: QWidget | None = None) -> QWidget:
    """Панель ссылки: крупный QR, короткий адрес, «Скопировать» и подписи.

    Одна и та же панель стоит и в карточке раздела «Вход», и в модальном окне:
    оператор показывает телефону то же самое, где бы её ни открыл, и
    переучиваться при переходе в окно не приходится.

    `lines` — подписи (роль темы, текст). `side` кладёт QR слева от подписей:
    в карточке места по ширине больше, чем по высоте, а в окне — наоборот.
    """
    panel = QWidget(parent)
    root = QHBoxLayout(panel) if side else QVBoxLayout(panel)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(16 if side else 10)

    qr_px = qr_pixmap(url, size)
    if qr_px is not None:
        qr_holder = QLabel()
        qr_holder.setObjectName("LinkQR")
        qr_holder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr_holder.setPixmap(qr_px)
        root.addWidget(qr_holder, 0, Qt.AlignmentFlag.AlignTop if side
                       else Qt.AlignmentFlag.AlignHCenter)
    else:
        root.addWidget(Note(QR_MISSING, "warn"))

    column = QVBoxLayout()
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(8)
    address = QHBoxLayout()
    address.setContentsMargins(0, 0, 0, 0)
    address.setSpacing(8)
    field = Field(url)
    field.setReadOnly(True)
    field.setCursorPosition(0)
    copy = Button("Скопировать", kind="accent")

    def _copy() -> None:
        copy_text(url)
        copy.setText("Скопировано")
        # Подпись возвращаем: панель живёт на экране долго, и «Скопировано»
        # навсегда перестаёт выглядеть как кнопка.
        QTimer.singleShot(1800, lambda: copy.setText("Скопировать"))

    copy.clicked.connect(lambda _checked=False: _copy())
    address.addWidget(field, 1)
    address.addWidget(copy, 0)
    column.addLayout(address)
    for role, text in lines or ():
        if text:
            column.addWidget(Note(text, role))
    column.addStretch(1)
    root.addLayout(column, 1)
    if not side:
        # В окне адрес выделен целиком — его забирают Ctrl+C. В карточке та же
        # подсветка выглядела бы так, будто в поле что-то печатают.
        field.selectAll()
    return panel


def _build_link_dialog(title: str, url: str, note: str = "",
                       parent: QWidget | None = None):
    """Диалог ссылки: QR, адрес, копирование. `show_link` показывает его
    модально; отдельный строитель — ради тестов без exec()."""
    from PySide6.QtWidgets import QDialog, QDialogButtonBox, QPushButton

    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(560)
    layout = QVBoxLayout(dialog)
    lines = [("small", part) for part in str(note or "").split("\n") if part.strip()]
    dialog.panel = link_panel(url, lines, size=320, parent=dialog)
    layout.addWidget(dialog.panel)
    buttons = QDialogButtonBox()
    close = QPushButton("Закрыть")
    buttons.addButton(close, QDialogButtonBox.ButtonRole.AcceptRole)
    close.clicked.connect(dialog.accept)
    layout.addWidget(buttons)
    return dialog


def show_link(title: str, url: str, note: str = "", parent: QWidget | None = None) -> None:
    """Показать ссылку так, чтобы её можно было забрать на телефон:
    QR с экрана, копирование одной кнопкой — QMessageBox текст не выделяет,
    а ссылку входа надо отдать человеку."""
    _build_link_dialog(title, url, note, parent).exec()


def _build_link_list_dialog(title: str, rows: list[dict], parent: QWidget | None = None,
                            on_saved=None):
    """Итог массовой выдачи: одна таблица «кто — ссылка», копирование всего
    списка и выгрузка в Excel. Отдельный строитель — ради тестов без exec()."""
    from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox,
                                   QHeaderView, QPushButton, QTableWidget,
                                   QTableWidgetItem)

    from ..core import links as lk

    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setMinimumSize(900, 520)
    layout = QVBoxLayout(dialog)
    dialog.summary = Note(lk.bulk_summary(rows), "small")
    layout.addWidget(dialog.summary)

    table = QTableWidget(len(rows), len(lk.BULK_HEADERS), dialog)
    table.setHorizontalHeaderLabels(list(lk.BULK_HEADERS))
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    for i, values in enumerate(lk.bulk_table(rows)):
        for j, value in enumerate(values):
            table.setItem(i, j, QTableWidgetItem(str(value)))
    # Ссылка — главное в строке: целиком и в одну строку. Растягивается
    # последняя колонка («Ошибка»), а не ссылка: иначе длинный текст отказа
    # отжимал ссылку в перенос и выгонял таблицу в горизонтальную прокрутку.
    table.setWordWrap(False)
    header = table.horizontalHeader()
    last = len(lk.BULK_HEADERS) - 1
    for j in range(last):
        header.setSectionResizeMode(j, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(last, QHeaderView.ResizeMode.Stretch)
    dialog.table = table
    layout.addWidget(table, 1)

    def copy_all():
        copy_text(lk.bulk_text(rows))
        dialog.summary.setText(lk.bulk_summary(rows) + " Список скопирован.")

    def save_xlsx():
        path = asksaveasfilename("Сохранить ссылки входа", "Ссылки входа.xlsx",
                                 [("Excel", "*.xlsx")], ".xlsx", parent=dialog)
        if not path:
            return
        lk.write_bulk_xlsx(path, rows)
        dialog.summary.setText(lk.bulk_summary(rows) + f" Сохранено: {path}")
        if on_saved is not None:
            on_saved(path)

    buttons = QDialogButtonBox()
    dialog.copy_btn = QPushButton("Копировать всё")
    dialog.copy_btn.clicked.connect(copy_all)
    dialog.save_btn = QPushButton("Сохранить в Excel…")
    dialog.save_btn.clicked.connect(save_xlsx)
    close = QPushButton("Закрыть")
    close.clicked.connect(dialog.accept)
    for btn in (dialog.copy_btn, dialog.save_btn):
        buttons.addButton(btn, QDialogButtonBox.ButtonRole.ActionRole)
        btn.setEnabled(any(r["url"] for r in rows))
    buttons.addButton(close, QDialogButtonBox.ButtonRole.AcceptRole)
    layout.addWidget(buttons)
    return dialog


def show_link_list(title: str, rows: list[dict], parent: QWidget | None = None) -> None:
    _build_link_list_dialog(title, rows, parent).exec()


def _box(icon, title: str, text: str, parent: QWidget | None) -> QMessageBox:
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setText(text)
    return box


def askyesno(title: str, text: str, parent: QWidget | None = None) -> bool:
    box = _box(QMessageBox.Icon.Question, title, text, parent)
    yes = box.addButton("Да", QMessageBox.ButtonRole.YesRole)
    no = box.addButton("Нет", QMessageBox.ButtonRole.NoRole)
    box.setDefaultButton(no)
    box.setEscapeButton(no)
    box.exec()
    return box.clickedButton() is yes


def showwarning(title: str, text: str, parent: QWidget | None = None) -> None:
    box = _box(QMessageBox.Icon.Warning, title, text, parent)
    box.addButton("Понятно", QMessageBox.ButtonRole.AcceptRole)
    box.exec()


def showerror(title: str, text: str, parent: QWidget | None = None) -> None:
    box = _box(QMessageBox.Icon.Critical, title, text, parent)
    box.addButton("Закрыть", QMessageBox.ButtonRole.AcceptRole)
    box.exec()


def showinfo(title: str, text: str, parent: QWidget | None = None) -> None:
    box = _box(QMessageBox.Icon.Information, title, text, parent)
    box.addButton("Понятно", QMessageBox.ButtonRole.AcceptRole)
    box.exec()


def askstring(title: str, prompt: str, initialvalue: str = "",
              parent: QWidget | None = None) -> str | None:
    dialog = QInputDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setLabelText(prompt)
    dialog.setTextValue(initialvalue)
    dialog.setInputMode(QInputDialog.InputMode.TextInput)
    dialog.setOkButtonText("Сохранить")
    dialog.setCancelButtonText("Отмена")
    dialog.resize(440, dialog.sizeHint().height())
    if dialog.exec() != QInputDialog.DialogCode.Accepted:
        return None
    return dialog.textValue()


def _filter(filetypes: list[tuple[str, str]]) -> str:
    return ";;".join(f"{label} ({patterns})" for label, patterns in filetypes)


def askopenfilename(title: str, filetypes: list[tuple[str, str]],
                    initialfile: str = "", parent: QWidget | None = None) -> str:
    path, _ = QFileDialog.getOpenFileName(parent, title, initialfile or "",
                                          _filter(filetypes))
    return path or ""


def asksaveasfilename(title: str, initialfile: str, filetypes: list[tuple[str, str]],
                      defaultextension: str = "", parent: QWidget | None = None) -> str:
    path, _ = QFileDialog.getSaveFileName(parent, title, initialfile, _filter(filetypes))
    if path and defaultextension and not path.lower().endswith(defaultextension.lower()):
        path += defaultextension
    return path or ""


def askdirectory(title: str, initialdir: str = "", parent: QWidget | None = None) -> str:
    return QFileDialog.getExistingDirectory(parent, title, initialdir or "") or ""


# Ради `QLineEdit.EchoMode` в обработчиках, где импортируют только dialogs.
Echo = QLineEdit.EchoMode
