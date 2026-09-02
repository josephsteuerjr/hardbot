# -*- coding: utf-8 -*-
"""Хард · Перенос реестров — запуск двойным кликом, без консоли.

Две ручные операции (в общий реестр / в личные) в одном окне на PySide6.
Расширение .pyw открывается через pythonw/pyw — консольного окна не бывает.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")


def _fallback(title: str, text: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)
    except Exception:                                  # noqa: BLE001
        print(f"{title}\n\n{text}", file=sys.stderr)


try:
    from registry_cycle.manual_app import main
except Exception as exc:                               # noqa: BLE001
    import traceback
    _fallback("Перенос реестров не запускается",
              f"Не удалось загрузить приложение: {type(exc).__name__}: {exc}\n\n"
              + traceback.format_exc()[-1200:])
    raise SystemExit(1)

main()
