"""Старт бота: только приветствие + нативная/inline-кнопка входа в мини-апп.

Весь рабочий процесс (клиенты, карточки, визиты, поиск, планы) живёт в миниаппе.
В ЧАТЕ ничего этого нет — по требованию заказчика бот в чате максимально пустой:
одна кнопка «Открыть приложение». Самовыбор сотрудника и выбор языка тоже ушли в
миниапп (там свой онбординг: /api/bootstrap → needsOnboarding).

Оставлено для сервера/владельца (НЕ дублируется чатом):
  • регистрация гостя (db.touch_guest) — чтобы в пульте был список id/ников/имён;
  • аппрув/отклонение привязки владельцем (approvebind/rejectbind) — колбэки из
    заявок, которые шлёт web.py при BIND_APPROVAL=1;
  • авто-подхват рабочей беседы (my_chat_member + acceptgroup) — утилита владельца.
"""

import os

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery, ChatMemberUpdated, InlineKeyboardButton, InlineKeyboardMarkup,
    Message, WebAppInfo,
)

import db
from config import ADMIN_ID, lang_of
from i18n import t
from keyboards import accept_group_kb

router = Router()


def _remember(user) -> None:
    """Сохранить гостя (id, @username, имя) — источник списка выбора в пульте.
    Не роняет обработку при любой ошибке БД."""
    try:
        name = " ".join(p for p in (user.first_name, user.last_name) if p) or None
        db.touch_guest(user.id, user.username, name)
    except Exception:  # noqa: BLE001
        pass


def _open_app_kb(lang: str, uid: int | None = None) -> InlineKeyboardMarkup | None:
    """Inline-кнопка WebApp «Открыть приложение» — только при заданном https-URL миниаппа.
    Дублирует нативную menu-кнопку (её ставит app.py), но появляется прямо в сообщении.
    Привязанному выезднику добавляется вторая кнопка — одноразовая ссылка для входа в
    автономное приложение (PWA, работает без интернета)."""
    url = os.getenv("MINIAPP_URL", "").strip()
    if not url or not url.lower().startswith("https://"):
        return None   # WebApp-кнопки требуют https; без URL кнопку не рисуем
    rows = [[InlineKeyboardButton(text=t("btn_open_app", lang),
                                  web_app=WebAppInfo(url=url))]]
    if uid and db.get_binding(uid):
        # Ссылка уже в тексте сообщения — вместо кнопки «дай ссылку» кладём кнопки
        # с инструкцией установки под платформу (по тапу приходит пошаговый мануал).
        rows.append([InlineKeyboardButton(text=t("btn_manual_android", lang),
                                          callback_data="manual:android")])
        rows.append([InlineKeyboardButton(text=t("btn_manual_ios", lang),
                                          callback_data="manual:ios")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _may_open_app(uid: int) -> bool:
    """Кому ссылка входа вообще имеет смысл.

    Выезднику — по привязке. Руководителю и владельцу — по роли: привязки у них
    нет и быть не должно, но приложение их пускает (действуют от имени своего
    сотрудника, см. web._employee). Раньше здесь стояла одна проверка на привязку,
    поэтому владелец писал «ссылка» и не получал ничего — вообще без объяснений."""
    if db.get_binding(uid):
        return True
    return bool(db.manager_employees(uid, is_owner=(uid == ADMIN_ID)))


def _applink_text(uid: int, lang: str) -> str | None:
    """Короткое сообщение с одноразовой ссылкой входа в автономное приложение.
    Код — в #-фрагменте: не попадает в логи сервера."""
    url = os.getenv("MINIAPP_URL", "").strip().rstrip("/")
    if not url or not _may_open_app(uid):
        return None
    code = db.create_app_link(uid)
    db.audit_access(uid, "app_link_issued")
    return t("applink_msg", lang, url=f"{url}/#link={code}", days=db.APP_LINK_DAYS)


def _applink_refusal(uid: int, lang: str) -> str:
    """ПОЧЕМУ ссылку выдать нельзя. Молчание здесь — худший вариант: человек пишет
    «ссылка», получает обычную кнопку и не понимает, сломан бот или он сам."""
    if not os.getenv("MINIAPP_URL", "").strip():
        return t("applink_no_url", lang)
    return t("applink_not_bound", lang)


def _is_applink_request(text: str | None) -> bool:
    """Ссылка выпускается только на отдельное слово «ссылка».

    Регистр и внешние пробелы несущественны; фразы и знаки препинания не подходят,
    чтобы случайное сообщение не создавало новый код входа.
    """
    return (text or "").strip().casefold() == "ссылка"


# --- Старт: приветствие + кнопка входа в приложение -------------------------
@router.message(Command("start"), F.chat.type == "private")
async def cmd_start(message: Message):
    _remember(message.from_user)
    lang = lang_of(message.from_user.id)
    # /start никогда не выпускает автономную ссылку. Для неё сотрудник явно пишет
    # отдельное слово «ссылка» — случайный старт/текст больше не плодит коды входа.
    kb = _open_app_kb(lang)
    await message.answer(t("start_app", lang) if kb else t("start_no_app", lang),
                         reply_markup=kb)


# --- Инструкция установки автономного приложения (кнопки под ссылкой) --------
@router.callback_query(F.data == "manual:android")
async def manual_android_cb(cb: CallbackQuery):
    await cb.message.answer(t("manual_android", lang_of(cb.from_user.id)))
    await cb.answer()


@router.callback_query(F.data == "manual:ios")
async def manual_ios_cb(cb: CallbackQuery):
    await cb.message.answer(t("manual_ios", lang_of(cb.from_user.id)))
    await cb.answer()


@router.message(Command("whoami"))
async def whoami(message: Message):
    _remember(message.from_user)
    emp = db.get_binding(message.from_user.id)
    await message.answer(f"telegram_id: <code>{message.from_user.id}</code>\n"
                         f"сотрудник: {emp or '—'}")


# --- Аппрув привязки владельцем (колбэки заявок из web.py) -------------------
@router.callback_query(F.data.startswith("approvebind:"))
async def approve_bind(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("⛔", show_alert=True)
        return
    uid = int(cb.data.split(":", 1)[1])
    emp = db.approve_binding(uid)
    db.audit_access(cb.from_user.id, "approve_bind", str(uid), emp or "")
    await cb.message.edit_text(
        t("bind_admin_done", lang_of(ADMIN_ID), emp=emp or "—", status="✅"))
    if emp:
        try:
            await cb.bot.send_message(uid, t("bind_approved_user", lang_of(uid), emp=emp))
        except Exception:  # noqa: BLE001
            pass
    await cb.answer()


@router.callback_query(F.data.startswith("rejectbind:"))
async def reject_bind(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("⛔", show_alert=True)
        return
    uid = int(cb.data.split(":", 1)[1])
    db.reject_binding(uid)
    db.audit_access(cb.from_user.id, "reject_bind", str(uid))
    await cb.message.edit_text(
        t("bind_admin_done", lang_of(ADMIN_ID), emp=str(uid), status="❌"))
    try:
        await cb.bot.send_message(uid, t("bind_rejected_user", lang_of(uid)))
    except Exception:  # noqa: BLE001
        pass
    await cb.answer()


# --- Авто-подхват рабочей беседы (утилита владельца) ------------------------
@router.my_chat_member()
async def bot_added(event: ChatMemberUpdated):
    if event.chat.type not in ("group", "supergroup"):
        return
    new = event.new_chat_member.status
    old = event.old_chat_member.status
    if new in ("member", "administrator") and old in ("left", "kicked"):
        # Любая беседа, куда добавили бота, попадает в реестр бесед отчётов
        # (выключенной) — включение и сотрудники настраиваются пультом.
        db.upsert_report_chat(event.chat.id, event.chat.title or "")
        if not ADMIN_ID:
            return
        await event.bot.send_message(
            ADMIN_ID,
            t("group_accept_q", lang_of(ADMIN_ID), title=event.chat.title or "—",
              id=event.chat.id),
            reply_markup=accept_group_kb(event.chat.id, lang_of(ADMIN_ID)))
    elif new in ("left", "kicked"):
        # Бота выгнали — беседа гаснет в пульте, настройки сохраняются.
        db.remove_report_chat(event.chat.id)


@router.callback_query(F.data.startswith("acceptgroup:"))
async def accept_group(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("⛔", show_alert=True)
        return
    chat_id = cb.data.split(":", 1)[1]
    db.set_setting("group_chat_id", chat_id)
    await cb.message.edit_text(t("group_accepted", lang_of(ADMIN_ID)))
    await cb.answer()


@router.callback_query(F.data == "declinegroup")
async def decline_group(cb: CallbackQuery):
    await cb.message.delete()
    await cb.answer()


# --- Сообщения в группах: тихая регистрация беседы ---------------------------
# Беседы, где бот сидел ЕЩЁ ДО этого релиза, не дадут события my_chat_member —
# без этого их нельзя включить в пульте. Любое видимое боту сообщение группы
# (команда/упоминание; все — если бот админ) обновляет реестр бесед. Молча:
# отвечать в рабочие беседы нельзя.
@router.message(F.chat.type.in_({"group", "supergroup"}))
async def group_message(message: Message):
    db.upsert_report_chat(message.chat.id, message.chat.title or "")


# --- Любое другое сообщение в ЛИЧКЕ: «ссылка» или обычная кнопка приложения ---
# Стоит ПОСЛЕДНИМ, чтобы не перехватывать команды/колбэки выше. Только private —
# чтобы не спамить рабочую беседу. Код входа создаётся только на отдельное слово
# «ссылка»; произвольный текст больше не выпускает и не отправляет ссылку.
@router.message(F.chat.type == "private")
async def any_message(message: Message):
    _remember(message.from_user)
    lang = lang_of(message.from_user.id)
    if _is_applink_request(message.text):
        text = _applink_text(message.from_user.id, lang)
        if text:
            await message.answer(text, reply_markup=_open_app_kb(lang, message.from_user.id))
        else:
            # Явный отказ с причиной вместо тихого падения в обычную кнопку.
            await message.answer(_applink_refusal(message.from_user.id, lang))
        return
    kb = _open_app_kb(lang)
    if kb:
        await message.answer(t("start_app", lang), reply_markup=kb)
