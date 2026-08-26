"""
Бот выездного взыскания — точка входа.

Управление (доверенные лица, конфиг, импорт, дев-режим) — в Tkinter-пульте `gui.py`.
Сам бот обслуживает только выездников (whitelist). Запуск: python app.py
"""

import asyncio
import logging
import os
import time as _time
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
import notify
import web
from config import ADMIN_ID

MSK = ZoneInfo("Europe/Moscow")

logging.basicConfig(level=logging.INFO)

# reminder_loop выпилен 26.08.2026: писать reminder_date было некому (set_reminder
# не вызывался ниоткуда, ключа нет в форме), цикл крутился вхолостую раз в час и
# создавал впечатление работающего канала. Повторный выезд обеспечивает
# киллер-фильтр маршрута по дате обещания. Колонка reminder_date, db.set_reminder
# и db.due_reminders оставлены — их читает карточка дайджеста.

PUSH_RETRY_LIMIT = 6   # тик 45 с → примерно 4,5 минуты добора

# Фолбэк потолка в памяти процесса: недоступность settings не должна отключать
# потолок (иначе устойчивый сбой БД давал бы тик каждые 45 с до смены суток).
_RETRY_MEM: dict[str, tuple[str, int]] = {}
# Улаженные адресаты этого окна: строка push_log — телеметрия и имеет право
# потеряться, а опирать на неё защиту от ПОВТОРНОЙ доставки нельзя. Память
# покрывает окно добора (шесть тиков, тот же процесс), БД — страховка на рестарт.
_SETTLED_MEM: dict[tuple[str, str], set[int]] = {}
# Флуд-контроль: адресат, попросивший RetryAfter, не трогается до срока —
# долбиться в него каждые 45 секунд значит продлевать блокировку.
_HOLD_UNTIL: dict[tuple[str, int], float] = {}


def _retry_bump(kind: str, today: str) -> int:
    """Номер попытки за день (persist в settings — рестарт не обнуляет потолок)."""
    try:
        day_key, n_key = f"{kind}_retry_day", f"{kind}_retry_n"
        if db.get_setting(day_key) != today:
            db.set_setting(day_key, today)
            db.set_setting(n_key, "0")
        n = int(db.get_setting(n_key) or 0) + 1
        db.set_setting(n_key, str(n))
        return n
    except Exception as exc:  # noqa: BLE001
        logging.warning("retry counter unavailable (%s), считаю в памяти", exc)
        day, n = _RETRY_MEM.get(kind, ("", 0))
        n = n + 1 if day == today else 1
        _RETRY_MEM[kind] = (today, n)
        return n


OWNER_PUSH_KEYS = {db.PUSH_DIGEST: "owner_digest",
                   db.PUSH_PROMISE: "owner_promise"}
# Владелец получает СВОЙ дайджест отдельным ключом (26.08.2026): раньше его
# рассылку выключал только общий рубильник — вместе со всеми руководителями.
# Обещания ему по умолчанию не идут: «дайджест только, отдельно».
OWNER_PUSH_DEFAULTS = {db.PUSH_DIGEST: "1", db.PUSH_PROMISE: "0"}


def _digest_targets(kind: str | None = None) -> list[dict]:
    """Адресаты дневных рассылок. Владелец — синтетическим руководителем, БЕЗ
    записи в managers: роль владельца — это ADMIN_ID в .env, и удаление
    руководителя из пульта не должно отбирать права у владельца.

    kind задан — применяем личный рубильник владельца. Он действует и когда
    владелец заведён руководителем руками: иначе «выключить себе обещания»
    зависело бы от того, есть ли на него строка в managers."""
    targets = db.list_managers()
    if ADMIN_ID and all(m["telegram_id"] != ADMIN_ID for m in targets):
        owner = digest.owner_scope(ADMIN_ID)
        if owner:
            targets.append(owner)
    key = OWNER_PUSH_KEYS.get(kind or "")
    if key and ADMIN_ID and not db.push_enabled(
            key, OWNER_PUSH_DEFAULTS.get(kind or "", "1")):
        targets = [m for m in targets if m["telegram_id"] != ADMIN_ID]
    return targets


async def _daily_once(kind: str, today: str, send) -> bool:
    """Один тик дневной рассылки. True — защёлку можно ставить.

    send(attempt) -> сколько адресатов остались НЕулаженными. Улажен = пуш ушёл
    ИЛИ Телеграм ответил Forbidden (это чинит человек, ретрай бессмысленен).
    0 неулаженных — защёлкиваем: и когда слать было некому, и когда всё дошло.
    Иначе повторяем на следующем тике до потолка PUSH_RETRY_LIMIT.

    Проверка потолка стоит ПОСЛЕ обработки исключения, а не после успешного
    возврата: вне per-target try остаются обращения к БД (list_managers,
    push_settled_today, состав владельца — полный GROUP BY по clients), и любой
    устойчивый сбой на них — лок под импортом, read-only том — иначе давал бы
    тик каждые 45 с от 16:30 МСК до смены UTC-даты (~840 попыток за вечер)."""
    attempt = _retry_bump(kind, today)
    try:
        pending = await send(attempt)
    except Exception as exc:  # noqa: BLE001
        logging.warning("%s за %s: попытка %s упала: %s", kind, today, attempt, exc)
        db.log_push(kind, db.PUSH_FAILED, note="исключение цикла",
                    error=f"{type(exc).__name__}: {exc}"[:300], attempt=attempt)
        pending = -1   # «неизвестно, но не ноль»
    if pending == 0:
        return True
    if attempt >= PUSH_RETRY_LIMIT:
        logging.warning("%s за %s: %s попыток, %s адресатов без доставки — "
                        "до завтра молчим", kind, today, attempt, pending)
        return True
    logging.warning("%s за %s: попытка %s из %s, без доставки %s — повтор через тик",
                    kind, today, attempt, PUSH_RETRY_LIMIT, pending)
    return False


def _settled(kind: str, on_date: str) -> set[int]:
    mem = _SETTLED_MEM.setdefault((kind, on_date), set())
    return db.push_settled_today(kind, on_date) | mem


def _mark_settled(kind: str, on_date: str, tid: int) -> None:
    _SETTLED_MEM.setdefault((kind, on_date), set()).add(tid)


def _held(kind: str, tid: int) -> bool:
    until = _HOLD_UNTIL.get((kind, tid))
    return until is not None and _time.monotonic() < until


async def _send_daily(kind: str, bot: Bot, on_date: str, attempt: int,
                      build) -> int:
    """Общий каркас дневной рассылки (дайджест/обещания). build(mgr) -> (text,
    kb) | None (None = этому адресату нечего слать). Возвращает число
    НЕулаженных (failed) адресатов."""
    targets = _digest_targets(kind)
    if not targets:
        db.log_push(kind, db.PUSH_SKIP_NO_TARGETS, attempt=attempt)
        return 0
    settled = _settled(kind, on_date)
    todo = [m for m in targets if m["telegram_id"] not in settled]
    if not todo:
        return 0
    if kind == db.PUSH_DIGEST:
        # единственный позитивный след в логах — по нему грепают, сохраняем
        logging.info("Дайджест: рассылка %s руководителям за %s (попытка %s)",
                     len(todo), on_date, attempt)
    failed = 0
    for mgr in todo:
        tid = mgr["telegram_id"]
        if _held(kind, tid):
            failed += 1        # ждём срок флуд-контроля, адресат не улажен
            continue
        target_kind = "owner" if tid == ADMIN_ID and not db.get_manager(tid) \
            else "manager"
        try:
            built = build(mgr)
        except Exception as exc:  # noqa: BLE001
            db.log_push(kind, db.PUSH_FAILED, target_kind=target_kind,
                        target_id=tid, note="build", attempt=attempt,
                        error=f"{type(exc).__name__}: {exc}"[:300])
            failed += 1
            continue
        if built is None:
            db.log_push(kind, db.PUSH_SKIP_NOTHING, target_kind=target_kind,
                        target_id=tid, attempt=attempt)
            _mark_settled(kind, on_date, tid)
            continue
        text, kb = built
        try:
            await bot.send_message(tid, text, reply_markup=kb)
        except Exception as exc:  # noqa: BLE001
            status, err = notify.classify_send_error(exc)
            db.log_push(kind, status, target_kind=target_kind, target_id=tid,
                        error=err, attempt=attempt)
            hold = notify.retry_after_s(exc)
            if hold:
                _HOLD_UNTIL[(kind, tid)] = _time.monotonic() + hold
            if status == db.PUSH_BLOCKED:
                try:
                    db.set_can_dm(tid, False)
                except Exception:  # noqa: BLE001
                    logging.warning("set_can_dm(%s, False) не записан", tid)
                _mark_settled(kind, on_date, tid)   # чинит человек, не ретрай
            else:
                failed += 1
            logging.warning("%s push %s failed (%s): %s", kind, tid, status, err)
            continue
        db.log_push(kind, db.PUSH_SENT, target_kind=target_kind, target_id=tid,
                    attempt=attempt)
        _mark_settled(kind, on_date, tid)
        try:
            db.set_can_dm(tid, True)
        except Exception:  # noqa: BLE001
            logging.warning("set_can_dm(%s, True) не записан", tid)
    return failed


def _setting_time(key: str, dh: int, dm: int) -> tuple[int, int]:
    """HH:MM из настроек (по МСК), кривое/пустое значение → дефолт."""
    raw = (db.get_setting(key) or "").strip()
    try:
        h, m = raw.split(":")
        h, m = int(h), int(m)
        if 0 <= h < 24 and 0 <= m < 60:
            return h, m
    except (ValueError, AttributeError):
        pass
    return dh, dm


def _digest_time() -> tuple[int, int]:
    return _setting_time("digest_time", 16, 30)


async def _push_digests(bot: Bot, on_date: str, attempt: int = 1) -> int:
    """Разослать дайджест; вернуть число НЕулаженных адресатов."""
    return await _send_daily(
        db.PUSH_DIGEST, bot, on_date, attempt,
        lambda mgr: digest.summary_view(mgr, on_date))


async def _push_promises(bot: Bot, on_date: str, attempt: int = 1) -> int:
    """Просроченные обещания; тихий день адресата — skipped_nothing, не ретрай."""
    def build(mgr):
        if not digest.overdue_by_employee(mgr, on_date):
            return None
        return digest.promises_view(mgr, on_date)
    return await _send_daily(db.PUSH_PROMISE, bot, on_date, attempt, build)


async def _daily_loop(bot: Bot, kind: str, enabled_key: str, time_key: str,
                      default_hm: tuple[int, int], last_run_key: str,
                      off_noted_key: str, push) -> None:
    """Общий цикл раз-в-день с догоном (— если бот лежал в 16:30 и поднялся
    позже, разошлёт при старте) и защёлкой ПО ФАКТУ доставки: раньше last_run
    ставился, даже если все send_message упали, и до завтра была тишина."""
    while True:
        try:
            today = date.today().isoformat()
            th, tm = _setting_time(time_key, *default_hm)
            now = datetime.now(MSK)
            window = (now.hour, now.minute) >= (th, tm)
            if window and db.get_setting(last_run_key) != today:
                if not db.push_enabled(enabled_key):
                    # Инструментируем выключенность РОВНО раз в сутки; last_run
                    # не ставим — включение в середине дня обязано разослать.
                    if db.get_setting(off_noted_key) != today:
                        db.log_push(kind, db.PUSH_SKIP_DISABLED,
                                    note=f"{enabled_key}=0")
                        db.set_setting(off_noted_key, today)
                elif await _daily_once(kind, today,
                                       lambda attempt: push(bot, today, attempt)):
                    db.set_setting(last_run_key, today)
                    db.push_trim()
        except Exception as e:  # noqa: BLE001
            logging.warning("%s_loop: %s", kind, e)
        await asyncio.sleep(45)


async def digest_loop(bot: Bot):
    """Автопуш дайджеста руководителям в конце дня (по умолчанию 16:30 МСК).
    Дата дайджеста = date.today() (та же система, что и visit_date)."""
    await _daily_loop(bot, db.PUSH_DIGEST, "digest_enabled", "digest_time",
                      (16, 30), "digest_last_run", "digest_off_noted",
                      _push_digests)


async def promise_loop(bot: Bot):
    """Утренний контроль обещаний (по умолчанию 09:30 МСК): каждому руководителю —
    список ПРОСРОЧЕННЫХ обещаний его сотрудников (promise_date прошла, оплаты
    нет). Дни без просрочек не шумят — пуш не уходит."""
    await _daily_loop(bot, db.PUSH_PROMISE, "promise_enabled", "promise_time",
                      (9, 30), "promise_last_run", "promise_off_noted",
                      _push_promises)


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

    asyncio.create_task(digest_loop(bot))
    asyncio.create_task(promise_loop(bot))
    logging.info("Бот запущен. ADMIN_ID=%s", ADMIN_ID)
    # drop_pending_updates=False: сообщения за время простоя/перезагрузки не теряются
    await dp.start_polling(bot, drop_pending_updates=False)


if __name__ == "__main__":
    asyncio.run(main())
