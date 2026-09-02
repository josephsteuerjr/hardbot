# -*- coding: utf-8 -*-
"""Хард · Реестры — окно цикла реестров (`python -m registry_cycle gui`),
запуск двойным кликом без консоли."""

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
    from registry_cycle.cli import main
except Exception as exc:                               # noqa: BLE001
    import traceback
    _fallback("Хард · Реестры не запускаются",
              f"Не удалось загрузить приложение: {type(exc).__name__}: {exc}\n\n"
              + traceback.format_exc()[-1200:])
    raise SystemExit(1)

code = main(["gui"])
if code:
    _fallback("Хард · Реестры", f"Приложение завершилось с ошибкой (код {code}).")
raise SystemExit(code)
