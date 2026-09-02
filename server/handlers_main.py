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

import html
import os

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery, ChatMemberUpdated, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, Message, ReplyKeyboardMarkup, WebAppInfo,
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


def _open_app_kb(lang: str, uid: int) -> InlineKeyboardMarkup | None:
    """Inline-кнопка WebApp «Открыть приложение» — только при заданном https-URL миниаппа.
    Дублирует нативную menu-кнопку (её ставит app.py), но появляется прямо в сообщении.
    Привязанному выезднику добавляется вторая кнопка — одноразовая ссылка для входа в
    автономное приложение (PWA, работает без интернета).

    `uid` ОБЯЗАТЕЛЕН. Он был необязательным, и два вызова из четырёх его не
    передавали — ровно те, через которые проходит новый человек: `/start` и
    ответ на любой текст. Кнопка заявки на роль зависит от uid, поэтому
    руководитель писал боту и не видел вообще ничего, кроме «Открыть приложение».
    Значение по умолчанию тут не удобство, а способ потерять кнопку молча."""
    url = os.getenv("MINIAPP_URL", "").strip()
    if not url or not url.lower().startswith("https://"):
        return None   # WebApp-кнопки требуют https; без URL кнопку не рисуем
    rows = [[InlineKeyboardButton(text=t("btn_open_app", lang),
                                  web_app=WebAppInfo(url=url))]]
    # Заявка на роль — тем, у кого ни роли, ни привязки. Вторым сообщением это
    # было бы спамом на каждый /start, а без неё человеку некуда нажать, кроме
    # как писать владельцу в личку. Сбой БД не должен ломать саму клавиатуру.
    # Оба похода в БД — под одной защитой. Раньше прикрыт был только первый, и
    # сбой на втором ронял ВСЮ клавиатуру: человек получал сообщение вообще без
    # кнопок, включая «Открыть приложение», ради которой всё и затевалось.
    try:
        bound = db.get_binding(uid) if uid else None
        if uid and uid != ADMIN_ID and not bound and not db.is_manager(uid):
            rows.append([InlineKeyboardButton(text=BTN_MANAGER_REQ,
                                              callback_data="mgrself")])
        if bound:
            # Ссылка уже в тексте сообщения — вместо кнопки «дай ссылку» кладём кнопки
            # с инструкцией установки под платформу (по тапу приходит пошаговый мануал).
            rows.append([InlineKeyboardButton(text=t("btn_manual_android", lang),
                                              callback_data="manual:android")])
            rows.append([InlineKeyboardButton(text=t("btn_manual_ios", lang),
                                              callback_data="manual:ios")])
    except Exception:  # noqa: BLE001
        pass
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _may_open_app(uid: int) -> bool:
    """Кому ссылка входа вообще имеет смысл.

    Выезднику — по привязке. Руководителю — по назначенным ему сотрудникам.
    Владельцу — ВСЕГДА: это его контур, и отказывать ему из-за того, что реестр
    ещё не залит, бессмысленно — он увидит пустой экран и поймёт больше, чем из
    отказа. Раньше здесь стояла одна проверка на привязку, поэтому владелец писал
    «ссылка» и не получал вообще ничего, даже объяснения."""
    if uid == ADMIN_ID:
        return True
    if db.get_binding(uid):
        return True
    return bool(db.manager_employees(uid))


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
    if db.is_manager(uid):
        # Руководитель есть, а сотрудников ему не назначили — причина совсем другая,
        # и чинится она в пульте, а не привязкой.
        return t("applink_no_employees", lang)
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
    kb = _open_app_kb(lang, message.from_user.id)
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
# --- Заявка на роль руководителя ------------------------------------------------
# Раньше руководителя заводили только руками в пульте: человек, которому дали
# бота, упирался в экран выездника и писал владельцу в личку. Теперь он жмёт
# кнопку, владелец получает заявку, вписывает имя и выбирает направление.
BTN_MANAGER_REQ = "🧑‍💼 Запрос для руководителей"

_PENDING_MGR = "mgr_request_from"     # settings: чья заявка сейчас оформляется


def _direction_kb() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=title, callback_data=f"mgrdir:{code}")]
            for code, (title, _cities) in db.DIRECTIONS.items()]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "mgrself")
async def manager_request_cb(cb: CallbackQuery):
    await _manager_request(cb.from_user, cb.message)
    await cb.answer("Заявка отправлена")


@router.message(F.text == BTN_MANAGER_REQ, F.chat.type == "private")
async def manager_request(message: Message):
    await _manager_request(message.from_user, message)


async def _manager_request(user, message):
    uid = user.id
    if db.is_manager(uid) or uid == ADMIN_ID:
        await message.answer("Роль руководителя у вас уже есть.")
        return
    who = " ".join(p for p in (user.first_name, user.last_name) if p) or "—"
    tag = f"@{user.username}" if user.username else "без ника"
    db.audit_access(uid, "manager_request")
    await message.answer("Заявка отправлена. Как только её подтвердят, "
                         "нажмите «Дайджест за сегодня».")
    if not ADMIN_ID:
        return
    try:
        await message.bot.send_message(
            ADMIN_ID,
            f"🧑‍💼 <b>Заявка на роль руководителя</b>\n"
            f"{html.escape(who)} · {html.escape(tag)} · id <code>{uid}</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Назначить", callback_data=f"mgrreq:{uid}"),
                InlineKeyboardButton(text="Отклонить", callback_data=f"mgrno:{uid}"),
            ]]))
    except Exception:  # noqa: BLE001
        pass


@router.callback_query(F.data.startswith("mgrreq:"))
async def manager_request_accept(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("⛔", show_alert=True)
        return
    uid = int(cb.data.split(":", 1)[1])
    db.set_setting(_PENDING_MGR, str(uid))
    await cb.message.edit_text(
        cb.message.html_text
        + "\n✍️ Пришлите ИМЯ этого руководителя одним сообщением.")
    await cb.answer()


@router.callback_query(F.data.startswith("mgrno:"))
async def manager_request_reject(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("⛔", show_alert=True)
        return
    uid = int(cb.data.split(":", 1)[1])
    db.set_setting(_PENDING_MGR, "")
    await cb.message.edit_text(cb.message.html_text + "\n❌ Отклонено.")
    try:
        await cb.bot.send_message(uid, "Заявка на роль руководителя отклонена.")
    except Exception:  # noqa: BLE001
        pass
    await cb.answer()


@router.callback_query(F.data.startswith("mgrdir:"))
async def manager_request_direction(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("⛔", show_alert=True)
        return
    code = cb.data.split(":", 1)[1]
    spec = db.DIRECTIONS.get(code)
    raw = db.get_setting(_PENDING_MGR) or ""
    name = db.get_setting(_PENDING_MGR + "_name") or ""
    if not spec or not raw.isdigit():
        await cb.answer("Заявка уже закрыта", show_alert=True)
        return
    uid = int(raw)
    title, cities = spec
    # employees НЕ фиксируем: у руководителя направления список считается живьём.
    db.set_manager(uid, name or title, list(cities), [], direction=code)
    db.set_setting(_PENDING_MGR, "")
    db.set_setting(_PENDING_MGR + "_name", "")
    db.audit_access(ADMIN_ID, "manager_grant", str(uid), f"{name} · {title}")
    n = len(db.manager_employees(uid))
    await cb.message.edit_text(
        f"✅ <b>{html.escape(name or title)}</b> — руководитель направления "
        f"«{html.escape(title)}». Сотрудников сейчас: {n}.")
    try:
        await cb.bot.send_message(
            uid, f"Вам выдана роль руководителя по направлению «{title}».\n"
                 f"Нажмите «📊 Дайджест за сегодня».")
    except Exception:  # noqa: BLE001
        pass
    await cb.answer()


@router.message(F.chat.type == "private", F.text & ~F.text.startswith("/"))
async def manager_name_reply(message: Message):
    """Владелец прислал имя для заявки — показываем выбор направления.

    Хендлер молчит для всех, кроме владельца с открытой заявкой, и пропускает
    сообщение дальше: иначе он съел бы обычную переписку.
    """
    if message.from_user.id != ADMIN_ID:
        raise SkipHandler
    raw = db.get_setting(_PENDING_MGR) or ""
    if not raw.isdigit():
        raise SkipHandler
    name = (message.text or "").strip()[:80]
    if not name:
        raise SkipHandler
    db.set_setting(_PENDING_MGR + "_name", name)
    await message.answer(
        f"Имя: <b>{html.escape(name)}</b>. Теперь направление:",
        reply_markup=_direction_kb())


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
    kb = _open_app_kb(lang, message.from_user.id)
    if kb:
        await message.answer(t("start_app", lang), reply_markup=kb)

