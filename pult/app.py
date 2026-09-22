"""Сборка приложения «Хард · Пульт».

Любая ошибка при старте — нет PySide6, нет папки `server` рядом, битые
модули — это понятное окно с причиной, а не тишина: пульт запускается без
консоли, и трейсбек в stderr никто не увидит.
"""

from __future__ import annotations

import sys
import traceback

from .core import bootstrap
from .core.bootstrap import StartupError

APP_TITLE = "Хард · Пульт"
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


def _split_contour(argv: list[str] | None) -> tuple[str, list[str]]:
    """Вынуть `--contour Имя` (или `--contour=Имя`) из аргументов запуска.

    Ярлык «Пульт · Компания» — это `Пульт.pyw --contour Компания`: окно
    открывается сразу в нужном сервере. Неизвестное имя — обычный старт.
    """
    args = list(argv if argv is not None else sys.argv[1:])
    name = ""
    if "--contour" in args:
        i = args.index("--contour")
        del args[i]
        if i < len(args) and not args[i].startswith("-"):
            name = args.pop(i)
    for a in list(args):
        if a.startswith("--contour="):
            args.remove(a)
            name = a.split("=", 1)[1]
    return name, args


def main(argv: list[str] | None = None) -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        fatal("Пульт не запускается", NO_QT_TEXT)
        return 1
    from .ui import theme

    contour, rest = _split_contour(argv)
    app = QApplication.instance() or QApplication(rest)
    app.setApplicationName(APP_TITLE)
    app.setOrganizationName("Хард")
    theme.apply(app)
    try:
        bootstrap.ensure_hardbot()
        from .ui.main_window import Pult
        window = Pult(profile=contour)
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
