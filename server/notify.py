"""Пересылка в беседу (группу): сводки по выездам и файл реестра.
Плюс общий отправщик фото-альбомов (дайджест руководителя и пуш по визиту).

ID беседы берётся из принятой настройки (db.settings['group_chat_id']) — её бот
подхватывает автоматически, когда его добавляют в группу и владелец принимает.
Фолбэк — GROUP_CHAT_ID из .env.
"""

import html
import logging

from aiogram import Bot
from aiogram.exceptions import (TelegramAPIError, TelegramForbiddenError,
                                TelegramMigrateToChat, TelegramRetryAfter)
from aiogram.types import FSInputFile, InputMediaDocument, InputMediaPhoto

import db
from config import GROUP_CHAT_ID

ALBUM = 10   # лимит Телеграма на медиагруппу

# Режим без Telegram (BOT_TOKEN пуст): bot=None везде. Предупреждаем один раз
# за процесс — каждое сохранение визита не должно рожать строку в журнале.
_NO_BOT_WARNED = False


def bot_missing(bot) -> bool:
    """True, если Telegram отключён; первое обращение оставляет одну строку warning."""
    global _NO_BOT_WARNED
    if bot is not None:
        return False
    if not _NO_BOT_WARNED:
        _NO_BOT_WARNED = True
        logging.warning("Telegram отключён: уведомления и сводки не отправляются "
                        "(BOT_TOKEN пуст)")
    return True


def classify_send_error(exc: Exception) -> tuple[str, str]:
    """(статус для push_log, короткий текст) — ЕДИНСТВЕННЫЙ классификатор
    ошибки Телеграма на весь проект. Forbidden — это «руководитель не нажал
    /start» или «заблокировал бота»: лечится человеком, а не ретраем. Полгода
    это и сетевой сбой приземлялись в один logging.warning и были неотличимы."""
    if isinstance(exc, TelegramForbiddenError):
        return db.PUSH_BLOCKED, f"Forbidden: {exc}"[:300]
    if isinstance(exc, TelegramRetryAfter):
        return db.PUSH_FAILED, f"RetryAfter {getattr(exc, 'retry_after', 0)}s"[:300]
    return db.PUSH_FAILED, f"{type(exc).__name__}: {exc}"[:300]


def retry_after_s(exc: Exception) -> int:
    """Сколько секунд просил подождать флуд-контроль (0 — не просил). Долбиться
    в RetryAfter каждые 45 секунд значит продлевать блокировку, а не добирать."""
    if isinstance(exc, TelegramRetryAfter):
        try:
            return int(getattr(exc, "retry_after", 0) or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def _fmt_promise_sum(raw) -> str:
    s = str(raw or "").strip()
    if s.isdigit():
        return f"{int(s):,}".replace(",", " ")
    return s


def promise_line(row) -> str:
    """«🤝 Обещал X к ДД.ММ» — обещание, зафиксированное ВЫЕЗДНИКОМ.

    Зовётся ТОЛЬКО с notify_row (строка, переопределённая по доказанному
    снимку BOT_SNAPSHOT_KEYS) — сырые колонки офиса сюда не попадают, иначе
    офисный прогноз выдавался бы за обещание должника. «Сумма прогноза» и
    «дата обещания» — общие с офисом колонки, и различает их ровно снимок. Пока ключей обещания в
    снимке нет (оболочки до v33) — возвращает пустую строку и никому не мешает."""
    def g(key):
        try:
            return str(row[key] or "").strip()
        except (KeyError, IndexError, TypeError):
            return ""
    sum_ = _fmt_promise_sum(g("forecast_sum"))
    d = db._as_date(g("promise_date"))
    when = d.strftime("%d.%m") if d else ""
    if not sum_ and not when:
        return ""
    if sum_ and when:
        return f"🤝 Обещал {html.escape(sum_)} к {when}"
    if when:
        return f"🤝 Обещал к {when}"
    return f"🤝 Обещал {html.escape(sum_)}"


def group_chat_id() -> int | None:
    val = db.get_setting("group_chat_id")
    if val:
        try:
            return int(val)
        except ValueError:
            return None
    return GROUP_CHAT_ID


async def post_to_group(bot: Bot, text: str | None = None,
                        document_path: str | None = None,
                        caption: str | None = None, *,
                        strict: bool = False) -> bool:
    """strict=True — пробросить ошибку наверх (телеметрии нужен настоящий
    статус); дефолт False сохраняет поведение существующих вызовов."""
    chat_id = group_chat_id()
    if not chat_id or bot_missing(bot):
        return False
    try:
        if document_path:
            await bot.send_document(chat_id, FSInputFile(document_path), caption=caption)
        if text:
            await bot.send_message(chat_id, text)
        return True
    except Exception as e:  # noqa: BLE001
        logging.warning("post_to_group failed: %s", e)
        if strict:
            raise
        return False


def _maps_link(lat, lon) -> str:
    return f"https://yandex.ru/maps/?pt={lon},{lat}&z=17&l=map"


def visit_report_text(row, dist_m=None, visit_geo=None, photo_geo=None,
                      photos_n: int = 0, actor: str | None = None) -> str:
    """Отчёт о выезде для беседы — по образцу постов сотрудников (клиент, адрес,
    рассказ), но с доказуемыми точками: гео закрытия визита и гео съёмки фото.
    row — клиент ПОСЛЕ сохранения (sqlite.Row или dict). Бот шлёт с
    parse_mode=HTML, поэтому все данные экранируются: «<» в комментарии не
    должен ронять доставку, а разметка из реестра — рендериться в беседе."""
    def raw(key):
        try:
            return str(row[key] or "")
        except (KeyError, IndexError, TypeError):
            return ""

    def g(key):
        return html.escape(raw(key))
    lines = ["✅ Выезд отработан",
             f"🧑‍💼 {g('responsible') or '—'}",
             f"👤 {g('client') or '—'} ({g('contract_id')})"]
    # Руководитель работает В ПРИЛОЖЕНИИ от имени своего сотрудника, и закрытый им
    # визит выглядел бы визитом этого сотрудника — с координатами руководителя.
    # Кто фактически закрыл, обязано быть видно в отчёте, иначе гео-контроль врёт.
    if actor and actor != raw("responsible"):
        lines.insert(1, f"👤 закрыл: {html.escape(str(actor))} (от имени сотрудника)")
    place = " · ".join(x for x in (g("city"), g("address")) if x)
    if place:
        lines.append(f"🏠 {place}")
    lines.append(f"📝 {g('call_status') or '—'} · {g('agreed_payment') or '—'}")
    # Обещание, зафиксированное выездником (из доказанного снимка — row здесь
    # уже переопределён по BOT_SNAPSHOT_KEYS). До оболочки v33 строка пустая.
    pline = promise_line(row)
    if pline:
        lines.append(pline)
    comment = g("visit_comment") or g("call_comment")
    if comment:
        lines.append(f"💬 {comment}")
    # Доп. адрес / доп. номер: выездник записывает туда телефон родственника и
    # второй адрес — то, ради чего руководитель и открывает карточку. Поле
    # bot-only (в реестр не уходит), и до 08.09.2026 его не было ни в одной из
    # трёх карточек: люди жаловались, что «в отчёте нет доп. адреса и номера».
    tips = g("tips")
    if tips:
        lines.append(f"📇 {tips}")
    if visit_geo:
        s = f"📍 Точка закрытия: {_maps_link(*visit_geo)}"
        if dist_m is not None:
            pretty = f"{dist_m / 1000:.1f} км" if dist_m >= 1000 else f"{dist_m} м"
            s += f" ({pretty} от адреса)"
        lines.append(s)
    if photos_n:
        s = f"📸 Фото: {photos_n}"
        if photo_geo:
            s += f" · точка съёмки: {_maps_link(*photo_geo)}"
        lines.append(s)
    return "\n".join(lines)


async def post_visit_report(bot: Bot, chat_ids, text: str, files,
                            on_result=None) -> None:
    """Разослать отчёт по закреплённым беседам; сбой одной беседы не мешает
    остальным и не ломает сохранение визита. Группа, ставшая супергруппой,
    переезжает на новый chat_id и получает отчёт туда же.

    on_result(chat_id, status, error) — телеметрия доставки по КАЖДОЙ беседе;
    колбэк не имеет права ронять рассылку (свой try)."""
    def _report(chat_id, status, error=None):
        if on_result is None:
            return
        try:
            on_result(chat_id, status, error)
        except Exception as exc:  # noqa: BLE001 — телеметрия не роняет доставку
            logging.warning("visit report telemetry failed: %s", exc)

    if bot_missing(bot):
        for chat_id in chat_ids:
            _report(chat_id, db.PUSH_SKIP_NO_BOT)
        return

    async def _send(chat_id):
        if files:
            res = await send_album(bot, chat_id, files, text)
            if not res["ok"]:
                raise RuntimeError(res["error"] or "album failed")
        else:
            await bot.send_message(chat_id, text)

    for chat_id in chat_ids:
        try:
            await _send(chat_id)
            _report(chat_id, db.PUSH_SENT)
        except TelegramMigrateToChat as e:
            new_id = e.migrate_to_chat_id
            db.migrate_report_chat(chat_id, new_id)
            try:
                await _send(new_id)
                _report(new_id, db.PUSH_SENT)   # успех отчитывается по НОВОМУ id
            except Exception as e2:  # noqa: BLE001
                logging.warning("visit report to migrated chat %s failed: %s",
                                new_id, e2)
                status, err = classify_send_error(e2)
                _report(new_id, status, err)
        except Exception as e:  # noqa: BLE001
            logging.warning("visit report to chat %s failed: %s", chat_id, e)
            status, err = classify_send_error(e)
            _report(chat_id, status, err)


CAPTION_MAX = 1024   # телеграмный лимит подписи медиа


async def send_album(bot: Bot, chat_id: int, files, caption: str | None = None,
                     *, media_ids: list[str] | None = None) -> dict:
    """Файлы альбомами по 10, подпись — на первом. Что не влезло фотографией
    (>10 МБ, HEIC…) — повторяем документом; совсем битое — честно говорим, не молчим.
    Подпись длиннее лимита не роняет весь пост (голосовой комментарий бывает
    длинным): альбом уходит без подписи, текст — отдельным сообщением следом.

    Возвращает ОРАКУЛ доставки {'ok','media_ids','error'}: раньше отсутствие
    исключения выдавалось за доставку, и «sent» писался по пушу, где ушло
    только «⚠️ Часть фото не удалось отправить». media_ids — file_id уже
    загруженных снимков: второй адресат получает те же файлы БЕЗ повторной
    загрузки (иначе починка адресации умножала бы исходящий трафик на число
    руководителей внутри HTTP-запроса выездника).

    Если не ушла НИ ОДНА пачка — caption отправляется обычным сообщением:
    текст визита важнее фото, а подпись едет на первом элементе альбома, и при
    провале альбома руководитель не узнал бы даже, чей это визит."""
    if bot_missing(bot):
        return {"ok": False, "media_ids": [], "error": "Telegram отключён"}
    long_caption = None
    if caption and len(caption) > CAPTION_MAX:
        long_caption, caption = caption, None
    sent_any = False
    first_error: str | None = None
    got_ids: list[str] = []
    use_ids = list(media_ids or [])
    items = use_ids if use_ids else [str(p) for p in files]
    for i in range(0, len(items), ALBUM):
        chunk = items[i:i + ALBUM]
        cap = caption if i == 0 else None
        by_id = bool(use_ids)
        classes = (InputMediaPhoto,) if by_id else (InputMediaPhoto, InputMediaDocument)
        for cls in classes:
            try:
                def _media(x):
                    return x if by_id else FSInputFile(x)
                if len(chunk) == 1:   # медиагруппа = минимум 2 элемента
                    if cls is InputMediaPhoto:
                        msg = await bot.send_photo(chat_id, _media(chunk[0]), caption=cap)
                        if msg and msg.photo:
                            got_ids.append(msg.photo[-1].file_id)
                    else:
                        await bot.send_document(chat_id, _media(chunk[0]), caption=cap)
                else:
                    msgs = await bot.send_media_group(chat_id, [
                        cls(media=_media(p), caption=cap if j == 0 else None)
                        for j, p in enumerate(chunk)])
                    if cls is InputMediaPhoto:
                        for m in msgs or []:
                            if m.photo:
                                got_ids.append(m.photo[-1].file_id)
                sent_any = True
                break
            except TelegramMigrateToChat:
                raise   # миграцию группы решает вызывающий (post_visit_report)
            except TelegramAPIError as e:
                if first_error is None:
                    first_error = f"{type(e).__name__}: {e}"[:300]
                if cls is classes[-1]:
                    logging.warning("send_album failed: %s", e)
                    await bot.send_message(chat_id, "⚠️ Часть фото не удалось отправить.")
    if long_caption:
        await bot.send_message(chat_id, long_caption[:4096])
    if not sent_any and caption:
        # текст визита важнее фото — доносим хотя бы его
        try:
            await bot.send_message(chat_id, caption)
            return {"ok": True, "media_ids": [], "error": first_error}
        except TelegramMigrateToChat:
            raise
        except TelegramAPIError as e:
            return {"ok": False, "media_ids": [],
                    "error": f"{type(e).__name__}: {e}"[:300]}
    if not sent_any and long_caption:
        return {"ok": True, "media_ids": [], "error": first_error}
    return {"ok": sent_any, "media_ids": got_ids, "error": first_error}
