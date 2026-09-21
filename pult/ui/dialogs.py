"""Диалоги пульта поверх QMessageBox / QInputDialog / QFileDialog.

Имена — как у `tkinter.messagebox`/`filedialog`: на `askyesno` опираются
пробники в тестах (`pult.messagebox.askyesno`), и их удобно читать в
обработчиках: подтверждение — это `askyesno(заголовок, текст)`.
"""

from __future__ import annotations

from PySide6.QtWidgets import QFileDialog, QInputDialog, QLineEdit, QMessageBox, QWidget


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


def _build_link_dialog(title: str, url: str, note: str = "",
                       parent: QWidget | None = None):
    """Диалог ссылки: QR, адрес, копирование. `show_link` показывает его
    модально; отдельный строитель — ради тестов без exec()."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QLabel,
                                   QLineEdit, QPushButton, QVBoxLayout)

    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(560)
    layout = QVBoxLayout(dialog)
    if note:
        text = QLabel(note)
        text.setWordWrap(True)
        layout.addWidget(text)
    qr_px = qr_pixmap(url)
    if qr_px is not None:
        qr_holder = QLabel()
        qr_holder.setObjectName("LinkQR")
        qr_holder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr_holder.setPixmap(qr_px)
        layout.addWidget(qr_holder, 0, Qt.AlignmentFlag.AlignHCenter)
    else:
        hint = QLabel("QR не показан: пульту не хватает модуля segno "
                      "(pip install segno) — ссылку можно только скопировать.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
    field = QLineEdit(url)
    field.setReadOnly(True)
    field.setCursorPosition(0)
    layout.addWidget(field)
    buttons = QDialogButtonBox()
    copy = QPushButton("Скопировать")
    close = QPushButton("Закрыть")
    buttons.addButton(copy, QDialogButtonBox.ButtonRole.ActionRole)
    buttons.addButton(close, QDialogButtonBox.ButtonRole.AcceptRole)

    def _copy() -> None:
        QApplication.clipboard().setText(url)
        copy.setText("Скопировано")

    copy.clicked.connect(_copy)
    close.clicked.connect(dialog.accept)
    layout.addWidget(buttons)
    field.selectAll()
    return dialog


def show_link(title: str, url: str, note: str = "", parent: QWidget | None = None) -> None:
    """Показать ссылку так, чтобы её можно было забрать на телефон:
    QR с экрана, копирование одной кнопкой — QMessageBox текст не выделяет,
    а ссылку входа надо отдать человеку."""
    _build_link_dialog(title, url, note, parent).exec()


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
