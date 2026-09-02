"""Сборка приложения «Выезды · Пульт».

Любая ошибка при старте — нет PySide6, нет папки `hardbot` рядом, битые
модули — это понятное окно с причиной, а не тишина: пульт запускается без
консоли, и трейсбек в stderr никто не увидит.
"""

from __future__ import annotations

import sys
import traceback

from .core import bootstrap
from .core.bootstrap import StartupError

APP_TITLE = "Выезды · Пульт"
NO_QT_TEXT = (
    "Не установлена библиотека PySide6 (Qt 6), на которой построено окно пульта.\n\n"
    "Установи зависимости один раз:\n"
    "    pip install -r pult\\requirements.txt\n\n"
    "и запусти пульт снова."
)


def _native_box(title: str, text: str) -> bool:
    """Окно средствами Windows, когда Qt недоступен. Под pythonw stderr нет."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)   # MB_ICONERROR
        return True
    except Exception:                                  # noqa: BLE001
        return False


def fatal(title: str, text: str) -> None:
    """Показать причину человеку и выйти."""
    shown = False
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance() or QApplication(sys.argv)
        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle(title)
        box.setText(text)
        box.addButton("Закрыть", QMessageBox.ButtonRole.AcceptRole)
        box.exec()
        shown = True
        del app
    except Exception:                                  # noqa: BLE001
        shown = _native_box(title, text)
    if not shown:
        print(f"{title}\n\n{text}", file=sys.stderr)
    raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        fatal("Пульт не запускается", NO_QT_TEXT)
        return 1
    from .ui import theme

    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setOrganizationName("Выезды")
    theme.apply(app)
    try:
        bootstrap.ensure_hardbot()
        from .ui.main_window import Pult
        window = Pult()
    except StartupError as exc:
        fatal(exc.title, exc.text)
        return 1
    except Exception as exc:                           # noqa: BLE001
        fatal("Пульт не запускается",
              f"Ошибка при построении окна: {type(exc).__name__}: {exc}\n\n"
              + traceback.format_exc()[-1500:])
        return 1
    window.show()
    return app.exec()
