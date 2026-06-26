"""Старт/самовыбор/язык, авто-подхват беседы, списки людей, поиск, планы, навигация."""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message

import db
from card import show_card
from config import ADMIN_ID, approval_required, employee_of, is_trusted, lang_of
from i18n import LANGUAGES, t, variants
from keyboards import (
    accept_group_kb, approve_bind_kb, lang_picker, menu, person_list_kb, self_select_kb,
)

router = Router()


class Search(StatesGroup):
    query = State()


class SelfSelect(StatesGroup):
    choosing = State()


# --- Старт / язык / самовыбор ----------------------------------------------
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    uid = message.from_user.id
    if is_trusted(uid):
        lang = lang_of(uid)
        await message.answer(t("welcome", lang), reply_markup=menu(lang))
        return
    if db.get_lang(uid) is None:          # первый раз — сперва язык
        await message.answer(t("choose_language"), reply_markup=lang_picker())
        return
    await _offer_self_select(message, state)


async def _offer_self_select(target, state: FSMContext):
    uid = target.from_user.id
    emps = db.employees_in_registry()
    if not emps:
        await _reply(target, t("no_employees", lang_of(uid)))
        return
    await state.set_state(SelfSelect.choosing)
    await state.update_data(emps=[e["employee"] for e in emps])
    await _reply(target, t("who_are_you", lang_of(uid)), self_select_kb(emps))


async def _reply(target, text, kb=None):
    if isinstance(target, CallbackQuery):
        await target.message.answer(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


@router.callback_query(SelfSelect.choosing, F.data.startswith("self:"))
async def self_pick(cb: CallbackQuery, state: FSMContext):
    idx = int(cb.data.split(":", 1)[1])
    emps = (await state.get_data()).get("emps", [])
    if idx >= len(emps):
        await cb.answer()
        return
    emp = emps[idx]
    uid = cb.from_user.id
    db.set_lang(uid, lang_of(uid))
    lang = lang_of(uid)
    await state.clear()

    if approval_required():
        # ИБ: привязка создаётся в ожидании, доступ — только после аппрува ADMIN.
        db.bind_agent(uid, emp, approved=False)
        db.audit_access(uid, "bind_request", emp)
        await cb.message.edit_text(t("bind_pending", lang))
        try:
            await cb.bot.send_message(
                ADMIN_ID, t("bind_request_admin", lang_of(ADMIN_ID), id=uid, emp=emp),
                reply_markup=approve_bind_kb(uid, lang_of(ADMIN_ID)))
        except Exception:  # noqa: BLE001 — недоступность ADMIN не должна ронять онбординг
            pass
        await cb.answer()
        return

    db.bind_agent(uid, emp)                    # одноразово (locked), сразу активна
    await cb.message.edit_text(t("bound_ok", lang, emp=emp))
    await cb.message.answer(t("welcome", lang), reply_markup=menu(lang))
    await cb.answer()


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
            ulang = lang_of(uid)
            await cb.bot.send_message(uid, t("bind_approved_user", ulang, emp=emp),
                                      reply_markup=menu(ulang))
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


@router.message(Command("language"))
@router.message(F.text.in_(variants("btn_language")))
async def choose_language(message: Message):
    await message.answer(t("choose_language", lang_of(message.from_user.id)),
                         reply_markup=lang_picker())


@router.callback_query(F.data.startswith("lang:"))
async def set_language(cb: CallbackQuery, state: FSMContext):
    code = cb.data.split(":", 1)[1]
    if code not in LANGUAGES:
        await cb.answer()
        return
    db.set_lang(cb.from_user.id, code)
    await cb.message.delete()
    if is_trusted(cb.from_user.id):
        await cb.message.answer(t("language_saved", code), reply_markup=menu(code))
    else:
        await _offer_self_select(cb, state)
    await cb.answer()


@router.message(Command("whoami"))
async def whoami(message: Message):
    emp = employee_of(message.from_user.id)
    await message.answer(f"telegram_id: <code>{message.from_user.id}</code>\n"
                         f"сотрудник: {emp or '—'}")


# --- Авто-подхват беседы ----------------------------------------------------
@router.my_chat_member()
async def bot_added(event: ChatMemberUpdated):
    if event.chat.type not in ("group", "supergroup"):
        return
    new = event.new_chat_member.status
    old = event.old_chat_member.status
    if new in ("member", "administrator") and old in ("left", "kicked"):
        if not ADMIN_ID:
            return
        await event.bot.send_message(
            ADMIN_ID,
            t("group_accept_q", lang_of(ADMIN_ID), title=event.chat.title or "—",
              id=event.chat.id),
            reply_markup=accept_group_kb(event.chat.id, lang_of(ADMIN_ID)))


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


# --- Списки людей -----------------------------------------------------------
@router.message(F.text.in_(variants("btn_my_clients")))
async def my_clients(message: Message):
    if not is_trusted(message.from_user.id):
        return
    lang = lang_of(message.from_user.id)
    emp = employee_of(message.from_user.id)
    persons = db.persons_for_employee(emp) if emp else []
    if not persons:
        await message.answer(t("no_clients", lang))
        return
    await message.answer(t("clients_count", lang, n=len(persons)),
                         reply_markup=person_list_kb(persons))


@router.message(F.text.in_(variants("btn_my_plans")))
async def my_plans(message: Message):
    if not is_trusted(message.from_user.id):
        return
    lang = lang_of(message.from_user.id)
    emp = employee_of(message.from_user.id)
    rows = db.plans_for_employee(emp) if emp else []
    if not rows:
        await message.answer(t("no_plans", lang))
        return
    seen, lines = set(), []
    for r in rows:
        key = (r["client"], r["birth_date"])
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"📅 {r['reminder_date']} — {r['client']}")
    await message.answer(t("plans_header", lang) + "\n\n" + "\n".join(lines))


# --- Поиск ------------------------------------------------------------------
@router.message(F.text.in_(variants("btn_search")))
async def search_start(message: Message, state: FSMContext):
    if not is_trusted(message.from_user.id):
        return
    await state.set_state(Search.query)
    await message.answer(t("search_prompt", lang_of(message.from_user.id)))


@router.message(Search.query)
async def search_run(message: Message, state: FSMContext):
    await state.clear()
    lang = lang_of(message.from_user.id)
    emp = employee_of(message.from_user.id)
    persons = db.search_persons(emp, message.text.strip()) if emp else []
    if not persons:
        await message.answer(t("nothing_found", lang))
        return
    await message.answer(t("found_count", lang, n=len(persons)),
                         reply_markup=person_list_kb(persons))


# --- Навигация по карточке --------------------------------------------------
@router.callback_query(F.data == "list")
async def back_to_list(cb: CallbackQuery):
    lang = lang_of(cb.from_user.id)
    emp = employee_of(cb.from_user.id)
    persons = db.persons_for_employee(emp) if emp else []
    await cb.message.edit_text(t("clients_count", lang, n=len(persons)),
                               reply_markup=person_list_kb(persons))
    await cb.answer()


@router.callback_query(F.data.startswith("open:"))
async def open_card(cb: CallbackQuery):
    if not is_trusted(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    rep = cb.data.split(":", 1)[1]
    db.audit_access(cb.from_user.id, "view_card", rep)   # ИБ: доступ к ПД в журнал
    await show_card(cb, rep, lang_of(cb.from_user.id))
    await cb.answer()


@router.callback_query(F.data.startswith("hist:"))
async def history(cb: CallbackQuery):
    lang = lang_of(cb.from_user.id)
    rows = db.person_history(cb.data.split(":", 1)[1])
    if not rows:
        await cb.answer(t("no_history", lang), show_alert=True)
        return
    lines = [f"🗓 {r['created_at']}: {r['payload']}" for r in rows]
    await cb.message.answer(t("history_header", lang) + "\n\n" + "\n\n".join(lines))
    await cb.answer()
