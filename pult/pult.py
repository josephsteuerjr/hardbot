"""
Выезды · Пульт — совместимая точка входа.

Сам пульт живёт в пакете `pult/`: логика без Qt — в `pult/core/`, окно на
PySide6 — в `pult/ui/`, сборка — в `pult/app.py`. Этот файл остаётся ради
документов и тестов, которые ссылаются на `pult.py`: он поднимает то же
приложение (`python pult.py`) и реэкспортирует прежние имена — класс `Pult`,
рубильники ограниченного теста, помощники экспорта и чтения книг.

Запуск без консоли — двойной клик по «Пульт.pyw».
"""

from __future__ import annotations

import sys
import urllib.parse  # noqa: F401 — на `pult.urllib.parse` смотрят тесты
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
# Пакет `pult` должен найтись раньше этого файла: когда скрипт запускают из
# папки `pult/`, sys.path[0] — она сама, и `import pult` попал бы сюда же.
if str(APP_DIR.parent) not in sys.path:
    sys.path.insert(0, str(APP_DIR.parent))
if __name__ == "pult":
    # Папка pult/ стояла в sys.path раньше корня (так делают тесты и запуск
    # скриптом), и `import pult` нашёл этот файл вместо пакета. Подменяем себя
    # настоящим пакетом: importlib берёт из sys.modules то, что лежит там ПОСЛЕ
    # выполнения модуля, поэтому `from pult.core import …` у вызывающего сработает.
    import importlib.util
    _spec = importlib.util.spec_from_file_location(
        "pult", APP_DIR / "__init__.py", submodule_search_locations=[str(APP_DIR)])
    _pkg = importlib.util.module_from_spec(_spec)
    sys.modules["pult"] = _pkg
    _spec.loader.exec_module(_pkg)

from pult.app import fatal, main  # noqa: E402

try:
    from pult.core.bootstrap import HARDBOT_DIR, ensure_hardbot  # noqa: E402
    ensure_hardbot()
    from pult.core.constants import (  # noqa: E402,F401
        DEFAULT_KEY, DEFAULT_URL, IMPORT_SOURCE_PUBLISHED_MASTER,
        IMPORT_SOURCE_TEST_SUBSET, LIMITED_TEST_ENRICH_DISABLED_MESSAGE,
        LIMITED_TEST_ENRICH_WRITES_ENABLED, LIMITED_TEST_MASTER_IMPORT_DISABLED_MESSAGE,
        LIMITED_TEST_MASTER_IMPORT_UI_ENABLED, LIMITED_TEST_RESET_DISABLED_MESSAGE,
        LIMITED_TEST_RESET_UI_ENABLED,
    )
    from pult.core.export import (  # noqa: E402,F401
        MARKER_COLS, _col_width, write_markers_xlsx, write_registry_xlsx,
    )
    from pult.core.settings import SETTINGS_PATH  # noqa: E402,F401
    from pult.core.text import fmt_n, names_look_different, parse_num, to_iso  # noqa: E402,F401
    from pult.core.workbook import _cell_json, read_workbook_sheets  # noqa: E402,F401
    from pult.ui import dialogs as messagebox  # noqa: E402,F401
    from pult.ui.main_window import Pult  # noqa: E402,F401
    from pult.ui.nav import SECTIONS, TITLES  # noqa: E402,F401
except Exception as _exc:                              # noqa: BLE001
    if __name__ == "__main__":
        import traceback
        fatal("Пульт не запускается",
              f"Не удалось загрузить модули пульта: {type(_exc).__name__}: {_exc}\n\n"
              + traceback.format_exc()[-1200:])
    raise


if __name__ == "__main__":
    raise SystemExit(main())
