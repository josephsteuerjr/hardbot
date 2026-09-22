# -*- coding: utf-8 -*-
"""Хард · Пульт руководителя — запуск двойным кликом, без консоли.

Расширение .pyw открывается через pythonw: консольного окна не бывает ни при
старте, ни во время работы. Плата за это — никакой печати: всё, что программа
скажет в консоль, не увидит никто. Поэтому беда старта показывается окном
(дальше этим занимается `pult_ruk/__main__.py`), а молчащего ярлыка не бывает:
человек, который не читает трассировок, иначе нажимает ещё раз, и ещё, и звонит.

Сам пульт — это страница в браузере: ярлык поднимает местный сервер и открывает
её. Окна у пульта руководителя нет вовсе, и Qt ему не нужен.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _fallback(title: str, text: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)
    except Exception:                                  # noqa: BLE001
        print(f"{title}\n\n{text}", file=sys.stderr)


try:
    from pult_ruk.__main__ import main
except Exception as exc:                               # noqa: BLE001
    import traceback
    _fallback("Пульт руководителя не запускается",
              "Рядом с ярлыком нет папки «pult_ruk» или не хватает библиотеки "
              "(нужны aiohttp и segno):\n"
              f"{type(exc).__name__}: {exc}\n\n"
              + traceback.format_exc()[-1200:])
    raise SystemExit(1)

raise SystemExit(main([]))
