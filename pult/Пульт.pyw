# -*- coding: utf-8 -*-
"""Хард · Пульт — запуск двойным кликом, без консоли.

Расширение .pyw открывается через pythonw/pyw: консольного окна не бывает ни
при старте, ни во время работы. Всё, что может пойти не так на старте,
показывается окном с причиной (см. pult/app.py).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _fallback(title: str, text: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)
    except Exception:                                  # noqa: BLE001
        print(f"{title}\n\n{text}", file=sys.stderr)


try:
    from pult.app import main
except Exception as exc:                               # noqa: BLE001
    import traceback
    _fallback("Пульт не запускается",
              f"Не удалось загрузить пульт: {type(exc).__name__}: {exc}\n\n"
              + traceback.format_exc()[-1200:])
    raise SystemExit(1)

raise SystemExit(main())
