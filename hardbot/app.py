"""
Бот выездного взыскания — точка входа.

Управление (доверенные лица, конфиг, импорт, дев-режим) — в Tkinter-пульте `gui.py`.
Сам бот обслуживает только выездников (whitelist). Запуск: python app.py
"""

import asyncio
import logging
import os
from datetime import date

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import MenuButtonWebApp, WebAppInfo

import db
import handlers_field
import handlers_main
import web
from config import ADMIN_ID, lang_of
from i18n import t

logging.basicConfig(level=logging.INFO)


async def reminder_loop(bot: Bot):
    """Утренние напоминания о повторных выездах (на языке выездника)."""
    sent = set()
    while True:
        today = date.today().isoformat()
        for row in db.due_reminders(today):
            tid = db.telegram_for_employee(row["responsible"]) if row["responsible"] else None
            key = (row["client"], row["birth_date"], today)   # один человек — одно напоминание
            if not tid or key in sent:
                continue
            try:
                await bot.send_message(tid, t(
                    "reminder_push", lang_of(tid),
                    client=row["client"], cid=row["contract_id"], addr=row["address"] or "—"))
                sent.add(key)
            except Exception as e:  # noqa: BLE001
                logging.warning("reminder failed: %s", e)
        await asyncio.sleep(3600)


async def main():
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("Не задан BOT_TOKEN (.env). Токен берётся у @BotFather.")
    db.init_db()
    if ADMIN_ID:
        db.ensure_owner(ADMIN_ID)
    try:
        import scoring
        got = scoring.geocode_city_centroids()
        logging.info("Геокодинг по центроидам: %s точек", got)
    except Exception as e:  # noqa: BLE001
        logging.warning("geocode on startup: %s", e)

    bot = Bot(token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(handlers_main.router)
    dp.include_router(handlers_field.router)

    # Веб-сервер миниаппа рядом с поллингом (один loop, общая БД).
    host = os.getenv("MINIAPP_HOST", "0.0.0.0")
    port = int(os.getenv("MINIAPP_PORT", "8080"))
    await web.start_site(bot, host=host, port=port)
    logging.info("Миниапп слушает %s:%s", host, port)

    url = os.getenv("MINIAPP_URL", "").strip()
    if url:  # кнопка-меню «Приложение» появляется только при заданном https-URL (тоннель/VPS)
        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="Приложение", web_app=WebAppInfo(url=url)))
        except Exception as e:  # noqa: BLE001
            logging.warning("set_chat_menu_button: %s", e)

    asyncio.create_task(reminder_loop(bot))
    logging.info("Бот запущен. ADMIN_ID=%s", ADMIN_ID)
    # drop_pending_updates=False: сообщения за время простоя/перезагрузки не теряются
    await dp.start_polling(bot, drop_pending_updates=False)


if __name__ == "__main__":
    asyncio.run(main())
