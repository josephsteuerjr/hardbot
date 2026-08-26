"""
Smoke: каждый .py в hardbot/ и engine/ КОМПИЛИРУЕТСЯ (ловит рассинхрон/битый файл сразу),
а ключевые модули ИМПОРТИРУЮТСЯ без ошибок (ловит обрыв связей после рефактора).
"""
import importlib
import py_compile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PY_DIRS = [ROOT / "hardbot", ROOT / "engine"]

# Модули с тяжёлыми/побочными импортами (Tk-окно, локальные пути запуска) — только компиляция.
SKIP_IMPORT = {"gui", "theme", "miniapp_dev", "_dev_seed"}

# Каталоги, которые не компилируем (сборки/venv).
SKIP_DIRS = {"target", ".venv", "venv", "node_modules", "__pycache__"}


def _all_py():
    for base in PY_DIRS:
        for p in base.rglob("*.py"):
            if not any(part in SKIP_DIRS for part in p.parts):
                yield p


@pytest.mark.parametrize("path", list(_all_py()), ids=lambda p: str(p.name))
def test_compiles(path):
    py_compile.compile(str(path), doraise=True)


# Импортируемые «безопасные» модули — ядро бота и движка.
# («app» опущен: имя коллизирует с пакетом engine/app — hardbot/app.py покрыт компиляцией
#  и через импорт его зависимостей web/handlers; «selftest» мутирует HARDBOT_DATA при импорте.)
CORE_IMPORTS = [
    "util", "gazetteer", "scoring_config", "scoring", "db", "config",
    "fields", "i18n", "keyboards", "normalize", "geocode", "web", "digest_data",
    "handlers_main", "handlers_field", "card", "notify", "synth",
    "photoaudit", "photo_backfill",
    "geo", "routing", "planner", "build_demo",
]


@pytest.mark.parametrize("mod", CORE_IMPORTS)
def test_imports(mod):
    importlib.import_module(mod)


def test_engine_app_imports():
    importlib.import_module("app.main")    # FastAPI-движок (engine/app/main.py)
