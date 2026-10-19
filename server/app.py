"""
«Хард» — точка входа сервера: приложение выездника (aiohttp) плюс, если задан
BOT_TOKEN, Telegram-бот на том же цикле событий.

Telegram — необязательный канал. Пустой BOT_TOKEN (и нет `bot_token` в
settings) — сервер поднимает только приложение: визиты, фото, пульт работают,
пуши и сводки не отправляются (каждый пропуск оставляет строку в push_log).
Запуск: python app.py
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
from aiogram.types import BufferedInputFile, MenuButtonWebApp, WebAppInfo

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
                   db.PUSH_PROMISE: "owner_promise",
                   db.PUSH_VISIT: "owner_visits"}
# Владелец получает СВОЙ дайджест отдельным ключом (26.08.2026): раньше его
# рассылку выключал только общий рубильник — вместе со всеми руководителями.
# Обещания ему по умолчанию не идут: «дайджест только, отдельно».
# Карточки по визитам (09.09.2026): раньше своего ключа у владельца не было
# вовсе — он их просто не получал, потому что не входит в managers, и включить
# себе их было нечем. Умолчание «выключено» сохраняет прежнее поведение.
OWNER_PUSH_DEFAULTS = {db.PUSH_DIGEST: "1", db.PUSH_PROMISE: "0",
                       db.PUSH_VISIT: "0"}
# Те же рассылки, но рубильник личный у КАЖДОГО руководителя (колонка в
# managers), а не один общий на всех.
MANAGER_PUSH_KEYS = {db.PUSH_DIGEST: "push_digest",
                     db.PUSH_PROMISE: "push_promises"}


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
    # Личный рубильник руководителя. Владельца он не касается: у него свои
    # ключи выше, а записи в managers у него может не быть вовсе.
    flag = MANAGER_PUSH_KEYS.get(kind or "")
    if flag:
        targets = [m for m in targets
                   if m["telegram_id"] == ADMIN_ID or m.get(flag, True)]
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


async def _send_daily(kind: str, bot: Bot | None, on_date: str, attempt: int,
                      build) -> int:
    """Общий каркас дневной рассылки (дайджест/обещания). build(mgr) -> (text,
    kb) | (text, kb, (имя файла, байты)) | None (None = этому адресату нечего
    слать). Возвращает число НЕулаженных (failed) адресатов."""
    if notify.bot_missing(bot):
        # Telegram отключён: слать некуда и нечем, ретраи бессмысленны — день
        # улажен одной строкой телеметрии.
        db.log_push(kind, db.PUSH_SKIP_NO_BOT, attempt=attempt)
        return 0
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
        text, kb, doc = built if len(built) == 3 else (*built, None)
        try:
            if doc:
                # Подпись документа ограничена 1024 символами — сводка в неё
                # укладывается намеренно, всё остальное лежит в самой книге.
                await bot.send_document(
                    tid, BufferedInputFile(doc[1], filename=doc[0]),
                    caption=text, reply_markup=kb)
            else:
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


def _digest_build(mgr, on_date: str):
    """Сводка руководителю: книгой, а при сбое сборки — прежним текстом.

    Падение сборки книги не имеет права оставить руководителя вообще без
    сводки: рассылка идёт раз в день, и «сегодня ничего не пришло» замечают
    только назавтра. Рубильник digest_xlsx=0 возвращает прежний текст без
    выкладки."""
    if db.push_enabled("digest_xlsx"):
        try:
            return digest.daily_report_view(mgr, on_date)
        except Exception as exc:  # noqa: BLE001
            logging.warning("сводка книгой не собралась (%s) — шлём текстом", exc)
    return digest.summary_view(mgr, on_date)


async def _push_digests(bot: Bot, on_date: str, attempt: int = 1) -> int:
    """Разослать дайджест; вернуть число НЕулаженных адресатов."""
    return await _send_daily(
        db.PUSH_DIGEST, bot, on_date, attempt,
        lambda mgr: _digest_build(mgr, on_date))


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
                import account_policy
                if account_policy.strict():
                    # Telegram identities are not approved account/device credentials.
                    # Record one explicit skip per day instead of silently leaving
                    # the scheduled delivery looking overdue forever.
                    if db.get_setting(off_noted_key) != today:
                        db.log_push(kind, db.PUSH_SKIP_DISABLED, note="account_cutover")
                        db.set_setting(off_noted_key, today)
                elif not db.push_enabled(enabled_key):
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


async def digest_loop(bot: Bot | None):
    """Автопуш дайджеста руководителям в конце дня (по умолчанию 16:30 МСК).
    Дата дайджеста = date.today() (та же система, что и visit_date)."""
    await _daily_loop(bot, db.PUSH_DIGEST, "digest_enabled", "digest_time",
                      (16, 30), "digest_last_run", "digest_off_noted",
                      _push_digests)


async def promise_loop(bot: Bot | None):
    """Утренний контроль обещаний (по умолчанию 09:30 МСК): каждому руководителю —
    список ПРОСРОЧЕННЫХ обещаний его сотрудников (promise_date прошла, оплаты
    нет). Дни без просрочек не шумят — пуш не уходит."""
    await _daily_loop(bot, db.PUSH_PROMISE, "promise_enabled", "promise_time",
                      (9, 30), "promise_last_run", "promise_off_noted",
                      _push_promises)


async def housekeeping_loop():
    """Уборка по сроку хранения: раз в сутки стираем координаты старых снимков.

    Отдельный цикл, а не довесок к рассылкам: он обязан идти и в дни, когда
    дайджест выключен, и не должен зависеть от того, поднялся ли Телеграм.
    Отметка дня — та же схема, что у рассылок: сделали сегодня, значит до
    завтра молчим (перезапуск контейнера не запускает уборку заново)."""
    while True:
        try:
            today = date.today().isoformat()
            if db.get_setting("photo_geo_sweep_day") != today:
                cleared = db.photo_geo_sweep()
                db.set_setting("photo_geo_sweep_day", today)
                if cleared:
                    logging.info("Срок хранения: стёрты координаты у %s снимков",
                                 cleared)
        except Exception as e:  # noqa: BLE001
            logging.warning("housekeeping_loop: %s", e)
        await asyncio.sleep(1800)


NO_TELEGRAM_NOTE = "Telegram отключён: BOT_TOKEN пуст, работает только приложение"


def bot_token() -> str:
    """Токен: сперва durable-настройка в БД (том /data — переживает рестарт и
    пересборку контейнера), иначе .env. Так «переключение на другой бот» из
    пульта (POST /admin/bot_token пишет токен в settings) применяется после
    рестарта процесса, даже если env контейнера ещё держит старый токен из
    env_file. Пусто — режим без Telegram."""
    return (db.get_setting("bot_token") or os.getenv("BOT_TOKEN") or "").strip()


def _geocode_on_startup() -> None:
    try:
        import scoring
        got = scoring.geocode_city_centroids()
        logging.info("Геокодинг по центроидам: %s точек", got)
    except Exception as e:  # noqa: BLE001
        logging.warning("geocode on startup: %s", e)


async def _wait_forever() -> None:
    await asyncio.Event().wait()


async def main():
    import account_policy
    account_policy.cutover_at()  # invalid operator configuration must fail startup
    db.init_db()
    account_policy.activate()  # durable one-way activation across restart
    if ADMIN_ID:
        db.ensure_owner(ADMIN_ID)
    token = bot_token()
    _geocode_on_startup()

    host = os.getenv("MINIAPP_HOST", "0.0.0.0")
    port = int(os.getenv("MINIAPP_PORT", "8080"))
    if not token:
        # Только приложение. Циклы рассылок всё равно крутятся: они оставляют
        # в push_log строку «skipped_no_bot» раз в день — пульт видит, почему
        # сводки не приходят, а включение токена через пульт + рестарт
        # поднимает бота без правки этого кода.
        logging.info(NO_TELEGRAM_NOTE)
        await web.start_site(None, host=host, port=port)
        logging.info("Приложение слушает %s:%s", host, port)
        asyncio.create_task(digest_loop(None))
        asyncio.create_task(promise_loop(None))
        asyncio.create_task(housekeeping_loop())
        await _wait_forever()
        return

    bot = Bot(token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())
    # Роутер руководителя — ПЕРЕД основным: его message-хендлеры гейтятся IsManager,
    # обычные пользователи проходят «сквозь» к штатному стартовому экрану.
    dp.include_router(handlers_manager.router)
    dp.include_router(handlers_main.router)
    dp.include_router(handlers_field.router)

    # Веб-сервер миниаппа рядом с поллингом (один loop, общая БД).
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
    asyncio.create_task(housekeeping_loop())
    logging.info("Бот запущен. ADMIN_ID=%s", ADMIN_ID)
    # drop_pending_updates=False: сообщения за время простоя/перезагрузки не теряются
    await dp.start_polling(bot, drop_pending_updates=False)


if __name__ == "__main__":
    asyncio.run(main())
