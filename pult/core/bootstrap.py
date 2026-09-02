"""Пути и стартовые проверки пульта.

Пульт делит с ботом контракт обмена и схему полей — они живут в `hardbot/`
рядом. Если папку `hardbot` не скопировали вместе с пультом, всё разваливалось
на `ModuleNotFoundError: exchange_contract` ещё до первого окна. А запускается
пульт без консоли — значит трейсбек уходил в никуда, и со стороны это
выглядело как «ярлык нажал, ничего не произошло». Поэтому проверка живёт
здесь, без Qt, и её результат показывает окно ошибки (`pult.app.fatal`).
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]        # …/pult
ROOT_DIR = APP_DIR.parent
HARDBOT_DIR = ROOT_DIR / "hardbot"


class StartupError(RuntimeError):
    """Причина, по которой пульт не может стартовать, — для окна ошибки."""

    def __init__(self, title: str, text: str) -> None:
        super().__init__(text)
        self.title = title
        self.text = text


def hardbot_missing_text() -> str:
    return (
        "Рядом с пультом нет папки «hardbot».\n\n"
        f"Ожидается здесь:\n{HARDBOT_DIR}\n\n"
        "Пульт использует общий с ботом контракт обмена и схему полей, "
        "поэтому папку «hardbot» надо копировать вместе с папкой «pult» — "
        "они должны лежать рядом, внутри одной папки проекта."
    )


def ensure_hardbot() -> None:
    if not (HARDBOT_DIR / "exchange_contract.py").is_file():
        raise StartupError("Пульт не запускается", hardbot_missing_text())


def ensure_paths() -> None:
    """`enrich`, `razbros`, `limited_test_import` и модули `hardbot` — модули
    верхнего уровня; так их видит запуск скриптом, так их импортируют тесты."""
    for folder in (HARDBOT_DIR, APP_DIR):
        if str(folder) not in sys.path:
            sys.path.insert(0, str(folder))
