"""
Локальный просмотр миниаппа с синтетикой — без Телеграма и без тоннеля.

Поднимает веб-сервер на 127.0.0.1, кладёт данные в изолированный каталог
`_devdata` (реальную hardbot.db не трогает) и открывает браузер на карточках.
Двойной клик: miniapp_dev.bat
"""

import os
import threading
import webbrowser

_here = os.path.dirname(os.path.abspath(__file__))
os.environ["HARDBOT_DATA"] = os.path.join(_here, "_devdata")
os.environ["HARDBOT_DEV"] = "1"
os.environ.setdefault("BOT_TOKEN", "dev")

import db        # noqa: E402
import synth     # noqa: E402
import web       # noqa: E402
from aiohttp import web as aw  # noqa: E402

db.init_db()
if not db.employees_in_registry():
    db.import_xlsx(
        synth.generate(n=30, responsible=synth.DEV_EMPLOYEE),
        source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
    )
synth.seed_geo()  # координаты для демо-карты (пины)
db.bind_agent(111, synth.DEV_EMPLOYEE)

PORT = int(os.getenv("MINIAPP_PORT", "8080"))
URL = f"http://127.0.0.1:{PORT}/?dev_uid=111"
if os.getenv("HARDBOT_NO_BROWSER") != "1":
    threading.Timer(1.2, lambda: webbrowser.open(URL)).start()
print("Миниапп (дев-режим):", URL)
aw.run_app(web.build_app(), host="127.0.0.1", port=PORT)
