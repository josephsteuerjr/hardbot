"""Диалоги пульта поверх QMessageBox / QInputDialog / QFileDialog.

Имена — как у `tkinter.messagebox`/`filedialog`: на `askyesno` опираются
пробники в тестах (`pult.messagebox.askyesno`), и их удобно читать в
обработчиках: подтверждение — это `askyesno(заголовок, текст)`.
"""

from __future__ import annotations

from PySide6.QtWidgets import QFileDialog, QInputDialog, QLineEdit, QMessageBox, QWidget


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
