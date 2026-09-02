"""
Общая настройка тестов: пути к hardbot/ и engine/, UTF-8 вывод (консоль бывает cp1251),
изолированная временная БД (рабочую hardbot.db не трогаем).
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hardbot"))
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
os.environ.setdefault("HARDBOT_DATA", tempfile.mkdtemp(prefix="hardbot_pytest_"))
