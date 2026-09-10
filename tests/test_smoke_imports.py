"""
Smoke: каждый .py в server/ и engine/ КОМПИЛИРУЕТСЯ (ловит рассинхрон/битый файл сразу),
а ключевые модули ИМПОРТИРУЮТСЯ без ошибок (ловит обрыв связей после рефактора).
"""
import importlib
import py_compile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
# engine/ в пакете для ИТ отсутствует — тогда проверяем только server/.
ENGINE = (ROOT / "engine").is_dir()


def _importable(name: str) -> bool:
    """Именно импорт, а не поиск файла: библиотека бывает установлена и всё
    равно не поднимается."""
    try:
        importlib.import_module(name)
    except Exception:      # noqa: BLE001 — любая поломка импорта = «нет её»
        return False
    return True


# Само приложение движка написано на FastAPI, а его нет ни в зависимостях
# сервера, ни в образе ядра. Каталог есть, библиотеки нет — импорт не
# «пропускается сам», а падает на ImportError. Поэтому у веб-части движка
# условие своё, а компиляция и лёгкие модули (geo/routing/planner) остаются.
ENGINE_APP = ENGINE and _importable("fastapi")
PY_DIRS = [d for d in (ROOT / "server", ROOT / "engine") if d.is_dir()]

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
# («app» опущен: имя коллизирует с пакетом engine/app — server/app.py покрыт компиляцией
#  и через импорт его зависимостей web/handlers; «selftest» мутирует HARDBOT_DATA при импорте.)
CORE_IMPORTS = [
    "util", "gazetteer", "scoring_config", "scoring", "db", "config",
    "fields", "i18n", "keyboards", "normalize", "geocode", "web", "digest_data",
    "handlers_main", "handlers_field", "card", "notify", "synth",
    "photoaudit", "photo_backfill",
]
if ENGINE:
    CORE_IMPORTS += ["geo", "routing", "planner", "build_demo"]


@pytest.mark.parametrize("mod", CORE_IMPORTS)
def test_imports(mod):
    importlib.import_module(mod)


@pytest.mark.skipif(not ENGINE_APP, reason="нет engine/ или не установлен fastapi")
def test_engine_app_imports():
    importlib.import_module("app.main")    # FastAPI-движок (engine/app/main.py)
