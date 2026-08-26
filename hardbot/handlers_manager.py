"""
Роль «Руководитель»: дайджест в чате бота (без миниаппа).

Роутер подключается ПЕРЕД handlers_main, но все message-хендлеры гейтятся фильтром
IsManager — обычные пользователи проходят «сквозь» к штатному стартовому экрану.
Руководитель (или владелец ADMIN — «супер-обзор» всех сотрудников) видит сводку
«кто кого обработал сегодня» + разбор по сотрудникам и карточкам клиентов.

Реалтайм: постоянная кнопка «📊 Дайджест за сегодня». Автопуш в конце дня —
digest_loop в app.py. Навигация внутри — правкой сообщения (digest.py).
"""

import html
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import BaseFilter, Command
from aiogram.types import (
    CallbackQuery, KeyboardButton, Message, ReplyKeyboardMarkup,
)

import db
import digest
import notify
from config import ADMIN_ID, lang_of
from handlers_main import (_applink_refusal, _applink_text,
                           _is_applink_request, _open_app_kb)

router = Router()

BTN_DIGEST = "📊 Дайджест за сегодня"

_MAX_PHOTOS = 30   # за одно нажатие — чтобы личка не превращалась в ленту


def manager_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_DIGEST)]],
        resize_keyboard=True,
    )


def scope(uid: int) -> dict | None:
    """Область видимости пользователя для дайджеста.
    Руководитель → его города/сотрудники. Владелец (ADMIN) без явной роли →
    «супер-обзор»: все сотрудники реестра. Сборка одна на бот, автопуши и
    вкладку приложения — digest_data.manager_scope, иначе контуры разъезжались
    (владелец видел нули там, где сборку забыли повторить)."""
    return digest.manager_scope(uid, is_owner=(uid == ADMIN_ID))


class IsManager(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        uid = message.from_user.id
        return uid == ADMIN_ID or db.is_manager(uid)


async def _send_requested_applink(message: Message) -> bool:
    """Выдать ссылку dual-role руководителю только по явному слову «ссылка»."""
    lang = lang_of(message.from_user.id)
    text = _applink_text(message.from_user.id, lang)
    if text:
        await message.answer(text, reply_markup=_open_app_kb(lang, message.from_user.id))
        return True
    return False


async def _send_digest(message: Message) -> None:
    mgr = scope(message.from_user.id)
    if not mgr:
        return
    on_date = digest.today_iso()
    text, kb = digest.summary_view(mgr, on_date)
    await message.answer(text, reply_markup=kb)


@router.message(Command("start"), F.chat.type == "private", IsManager())
async def start(message: Message):
    try:
        db.touch_guest(message.from_user.id, message.from_user.username,
                       " ".join(p for p in (message.from_user.first_name,
                                            message.from_user.last_name) if p) or None)
    except Exception:  # noqa: BLE001
        pass
    mgr = scope(message.from_user.id)
    name = html.escape((mgr.get("name") if mgr else "") or message.from_user.first_name or "руководитель")
    where = html.escape(digest.scope_label(mgr) if mgr else "")
    await message.answer(
        f"👋 Здравствуйте, {name}!\n"
        f"Вы вошли как <b>руководитель</b>" + (f" · 🏙 {where}" if where and where != 'все города' else "") + ".\n"
        "Нажмите «📊 Дайджест за сегодня» в любой момент — или дождитесь автосводки в конце дня.",
        reply_markup=manager_menu(),
    )
    await _send_digest(message)


@router.message(Command("digest"), F.chat.type == "private", IsManager())
async def digest_cmd(message: Message):
    await _send_digest(message)


@router.message(F.text == BTN_DIGEST, F.chat.type == "private", IsManager())
async def digest_btn(message: Message):
    await _send_digest(message)


# Любой НЕ-командный текст руководителя в личке — не отдаём его в «выездной» стартовый
# экран (open-app), а мягко возвращаем к дайджесту. Команды (/whoami и пр.) пропускаем
# «сквозь» в handlers_main. Стоит последним в этом роутере.
@router.message(F.chat.type == "private", IsManager(), F.text & ~F.text.startswith("/"))
async def fallback(message: Message):
    if _is_applink_request(message.text):
        if await _send_requested_applink(message):
            return
        # Не выдалась — говорим почему. Раньше руководитель просто получал дайджест
        # вместо ссылки и не понимал, что пошло не так.
        await message.answer(_applink_refusal(message.from_user.id,
                                              lang_of(message.from_user.id)))
        return
    await message.answer("Меню — снизу. Нажмите «📊 Дайджест за сегодня».",
                         reply_markup=manager_menu())
    await _send_digest(message)


async def _send_album(message: Message, files, caption: str | None) -> None:
    await notify.send_album(message.bot, message.chat.id, files, caption)


async def _send_photos(cb: CallbackQuery, mgr: dict, kind: str,
                       on_date: str, parts: list[str]) -> None:
    """dg:p / dg:pe — фото шлём НОВЫМИ сообщениями в чат, дайджест не трогаем."""
    if kind == "p":
        try:
            p, files = digest.photos_for_person(mgr, on_date, int(parts[3]), int(parts[4]))
        except (IndexError, ValueError):
            p, files = None, []
        if not files:
            await cb.answer("Фото по кейсу нет", show_alert=True)
            return
        db.audit_access(cb.from_user.id, "digest_photos", target=p["rep"], detail=on_date)
        cap = f"📸 {html.escape(p['client'])} · {len(files)} фото"
        if len(files) > _MAX_PHOTOS:
            cap += f" (показаны последние {_MAX_PHOTOS})"
        await _send_album(cb.message, files[:_MAX_PHOTOS], cap)
    else:   # pe: по каждому клиенту сотрудника — свой альбом с подписью-ФИО
        try:
            emp, items = digest.photos_for_employee_day(mgr, on_date, int(parts[3]))
        except (IndexError, ValueError):
            emp, items = None, []
        if not items:
            await cb.answer("За этот день фото нет", show_alert=True)
            return
        db.audit_access(cb.from_user.id, "digest_photos_day", target=emp, detail=on_date)
        sent = 0
        for p, files in items:
            left = _MAX_PHOTOS - sent
            if left <= 0:
                await cb.message.answer(
                    f"…лимит {_MAX_PHOTOS} фото за раз — остальное смотрите по кейсам.")
                break
            take = files[:left]
            await _send_album(cb.message, take, f"📸 {html.escape(p['client'])} · {len(take)} фото")
            sent += len(take)
    await cb.answer()


@router.callback_query(F.data.startswith("dg:"))
async def navigate(cb: CallbackQuery):
    mgr = scope(cb.from_user.id)
    if not mgr:
        await cb.answer("Доступа нет", show_alert=True)
        return
    parts = cb.data.split(":")
    kind = parts[1] if len(parts) > 1 else "h"
    on_date = parts[2] if len(parts) > 2 else digest.today_iso()
    if kind in ("p", "pe"):
        await _send_photos(cb, mgr, kind, on_date, parts)
        return
    try:
        if kind == "e":
            text, kb = digest.employee_view(mgr, on_date, int(parts[3]))
        elif kind == "o":
            text, kb = digest.promises_view(mgr, on_date)
        elif kind == "w":
            text, kb = digest.period_view(mgr, on_date, int(parts[3]))
        elif kind == "c":
            db.audit_access(cb.from_user.id, "digest_view_card", detail=on_date)
            text, kb = digest.person_card(mgr, on_date, int(parts[3]), int(parts[4]))
        else:
            text, kb = digest.summary_view(mgr, on_date)
    except (IndexError, ValueError):
        text, kb = digest.summary_view(mgr, on_date)
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as e:
        # «message is not modified» (напр. «Обновить» без новых данных) — не ошибка.
        # А вот всё остальное (чаще всего слишком длинное сообщение) раньше тоже
        # глоталось: экран не менялся, ошибки не было ни на виду, ни в логах, и
        # кейс с самым подробным отчётом — ровно тот, который откроют на показе, —
        # выглядел как неработающая кнопка.
        if "not modified" not in str(e).lower():
            logging.warning("digest edit failed: %s", e)
            await cb.answer("Не удалось показать карточку", show_alert=True)
            return
    await cb.answer()
