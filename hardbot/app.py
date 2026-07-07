"""
Бот выездного взыскания — точка входа.

Управление (доверенные лица, конфиг, импорт, дев-режим) — в Tkinter-пульте `gui.py`.
Сам бот обслуживает только выездников (whitelist). Запуск: python app.py
"""

import asyncio
import logging
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import MenuButtonWebApp, WebAppInfo

import db
import digest
import handlers_field
import handlers_main
import handlers_manager
import web
from config import ADMIN_ID, lang_of
from i18n import t

MSK = ZoneInfo("Europe/Moscow")

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


def _digest_time() -> tuple[int, int]:
    """HH:MM из настроек (по МСК), дефолт 16:30. Кривое значение → дефолт."""
    raw = (db.get_setting("digest_time") or "16:30").strip()
    try:
        h, m = raw.split(":")
        h, m = int(h), int(m)
        if 0 <= h < 24 and 0 <= m < 60:
            return h, m
    except (ValueError, AttributeError):
        pass
    return 16, 30


async def digest_loop(bot: Bot):
    """Автопуш дайджеста руководителям в конце дня (по умолчанию 16:30 МСК).

    Раз в день: при первом тике, где текущее МСК-время >= целевого и сегодня ещё
    не рассылали (settings.digest_last_run), шлём сводку каждому руководителю.
    «>=» даёт догон: если бот лежал в 16:30 и поднялся позже — разошлёт при старте.
    Дата дайджеста = date.today() (та же система, что и visit_date)."""
    while True:
        try:
            enabled = (db.get_setting("digest_enabled") or "1").strip().lower() \
                not in ("0", "false", "no", "off")
            today = date.today().isoformat()
            th, tm = _digest_time()
            now = datetime.now(MSK)
            if enabled and (now.hour, now.minute) >= (th, tm) \
                    and db.get_setting("digest_last_run") != today:
                await _push_digests(bot, today)
                db.set_setting("digest_last_run", today)
        except Exception as e:  # noqa: BLE001
            logging.warning("digest_loop: %s", e)
        await asyncio.sleep(45)


async def _push_digests(bot: Bot, on_date: str):
    managers = db.list_managers()
    logging.info("Дайджест: рассылка %s руководителям за %s", len(managers), on_date)
    for mgr in managers:
        try:
            text, kb = digest.summary_view(mgr, on_date)
            await bot.send_message(mgr["telegram_id"], text, reply_markup=kb)
        except Exception as e:  # noqa: BLE001
            logging.warning("digest push %s failed: %s", mgr.get("telegram_id"), e)


async def main():
    db.init_db()
    if ADMIN_ID:
        db.ensure_owner(ADMIN_ID)
    # Токен: сперва durable-настройка в БД (том /data — переживает рестарт и пересборку
    # контейнера), иначе .env. Так «переключение на другой бот» из пульта
    # (POST /admin/bot_token пишет токен в settings) применяется после рестарта процесса,
    # даже если env контейнера ещё держит старый токен из env_file.
    token = db.get_setting("bot_token") or os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("Не задан BOT_TOKEN (.env). Токен берётся у @BotFather.")
    try:
        import scoring
        got = scoring.geocode_city_centroids()
        logging.info("Геокодинг по центроидам: %s точек", got)
    except Exception as e:  # noqa: BLE001
        logging.warning("geocode on startup: %s", e)

    bot = Bot(token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())
    # Роутер руководителя — ПЕРЕД основным: его message-хендлеры гейтятся IsManager,
    # обычные пользователи проходят «сквозь» к штатному стартовому экрану.
    dp.include_router(handlers_manager.router)
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
    asyncio.create_task(digest_loop(bot))
    logging.info("Бот запущен. ADMIN_ID=%s", ADMIN_ID)
    # drop_pending_updates=False: сообщения за время простоя/перезагрузки не теряются
    await dp.start_polling(bot, drop_pending_updates=False)


if __name__ == "__main__":
    asyncio.run(main())
