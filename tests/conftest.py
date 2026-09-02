"""
Общая настройка тестов: пути к server/ и engine/, UTF-8 вывод (консоль бывает cp1251),
изолированная временная БД (рабочую hardbot.db не трогаем).
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
# engine/ (аналитический движок) в пакет для ИТ не входит — его тесты пропускаются сами.
if (ROOT / "engine").is_dir():
    sys.path.insert(0, str(ROOT / "engine"))

# Вывод кириллицы/эмодзи при фейлах на cp1251-консоли — не падать в UnicodeEncodeError.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

# Тесты, трогающие БД, работают в отдельной временной папке (НЕ рабочая БД).
os.environ.setdefault("APP_DATA", tempfile.mkdtemp(prefix="vyezdy_pytest_"))


@pytest.fixture(autouse=True)
def _engine_stays_first():
    """Тесты пульта вставляют `pult/` в начало sys.path, а там лежит `pult/app.py` —
    впереди всех он затенял бы пакет движка `app` (engine/app), и `import app.main`
    в тестах движка получал бы сборку пульта. Держим engine/ первым, как и задумано
    выше: остальным папкам достаточно просто быть в пути."""
    engine = str(ROOT / "engine")
    if sys.path[:1] != [engine]:
        while engine in sys.path:
            sys.path.remove(engine)
        sys.path.insert(0, engine)
    yield
