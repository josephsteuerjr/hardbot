"""Запуск пульта руководителя: `python -m pult_ruk`.

Главное свойство этого файла — он никогда не показывает трассировку. Пульт
запускает не инженер: ярлык .pyw открывается без консоли, и всё, что напечатано
в неё, не существует. Поэтому любая беда старта кончается окном с одной русской
строкой и советом, что сделать, а подробности уходят в журнал рядом с пультом.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
LOG_PATH = APP_DIR / "пульт руководителя.log"


def has_console() -> bool:
    """Есть ли куда печатать. Под pythonw.exe (ярлык .pyw) вывода нет вовсе."""
    return bool(getattr(sys, "stdout", None)) and not sys.executable.lower().endswith(
        "pythonw.exe")


def fatal(title: str, text: str) -> None:
    """Сказать человеку, почему пульт не открылся. На Windows — окном, потому
    что консоли у него нет; в консоли — строкой."""
    logging.getLogger("пульт-руководителя").error("%s: %s", title, text)
    if has_console():
        print(f"{title}\n\n{text}", file=sys.stderr)
        return
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)
            return
        except Exception:      # noqa: BLE001 — показать было нечем, остаётся журнал
            pass


def setup_log() -> None:
    """Журнал рядом с пультом: когда у руководителя «не работает», по нему
    видно, дошёл ли пульт до сети и что ответил «Хард». Секретов в нём нет —
    ни токена, ни кода-пропуска, ни адреса тоннеля с ключом."""
    handlers: list[logging.Handler] = []
    try:
        handlers.append(logging.FileHandler(LOG_PATH, encoding="utf-8"))
    except OSError:
        pass
    if has_console():
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%d.%m %H:%M:%S")


def _args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pult_ruk", description="Пульт руководителя направления")
    parser.add_argument("--port", type=int, default=0,
                        help="порт (по умолчанию — любой свободный)")
    parser.add_argument("--no-browser", action="store_true",
                        help="не открывать браузер самому")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _args(argv)
    setup_log()
    say = logging.getLogger("пульт-руководителя").info
    try:
        from . import server
    except ImportError as exc:
        fatal("Пульт руководителя не запускается",
              "Не хватает библиотеки, на которой работает пульт "
              f"({exc}).\n\nПоставьте её командой:\n"
              "pip install aiohttp segno")
        return 2
    if not (server.WEB_DIR / "index.html").is_file():
        fatal("Пульт руководителя не запускается",
              "Рядом с пультом нет страницы.\n\nОжидается файл:\n"
              f"{server.WEB_DIR / 'index.html'}\n\n"
              "Похоже, папку «pult_ruk» скопировали не целиком.")
        return 2
    try:
        asyncio.run(server.serve(port=args.port,
                                 open_browser=not args.no_browser, say=say))
    except KeyboardInterrupt:
        say("Пульт закрыт с клавиатуры.")
        return 0
    except OSError as exc:
        fatal("Пульт руководителя не запускается",
              f"Не удалось занять порт на этом компьютере: {exc}\n\n"
              "Обычно это значит, что пульт уже открыт в другом окне.")
        return 1
    except Exception as exc:      # noqa: BLE001 — последний рубеж перед .pyw
        logging.getLogger("пульт-руководителя").exception("пульт упал")
        fatal("Пульт руководителя закрылся",
              f"Внутренняя ошибка: {exc}\n\nПодробности записаны в файл:\n"
              f"{LOG_PATH}\n\nПокажите его аналитику.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
