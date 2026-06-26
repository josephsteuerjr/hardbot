"""Действия выездника: визит, оплата, повторный выезд, гео, фото. Локализовано.

Сохранность: каждый выбранный пункт визита пишется в БД сразу (не копится в
памяти) — аварийная перезагрузка/остановка поллинга не теряет введённое.
"""

from datetime import date, datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import db
from card import show_card
from config import is_trusted, lang_of
from fields import DROPDOWNS
from i18n import t, tf, tv
from keyboards import photo_done_kb, visit_comment_kb, visit_step_kb
from notify import post_to_group

router = Router()

VISIT_STEPS = [
    "call_status", "contact_with", "who_at_address",
    "client_location", "purpose_rf", "agreed_payment",
]


class Visit(StatesGroup):
    flow = State()
    comment = State()


class Pay(StatesGroup):
    amount = State()


class Replan(StatesGroup):
    when = State()


class Photo(StatesGroup):
    wait = State()


class Geo(StatesGroup):
    wait = State()


def parse_date(text: str) -> str | None:
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y"):
        try:
            return datetime.strptime(text.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


# --- Визит ------------------------------------------------------------------
@router.callback_query(F.data.startswith("visit:"))
async def visit_start(cb: CallbackQuery, state: FSMContext):
    if not is_trusted(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(Visit.flow)
    await state.update_data(cid=cb.data.split(":", 1)[1], idx=0)
    await ask_visit_step(cb, state)
    await cb.answer()


async def ask_visit_step(cb: CallbackQuery, state: FSMContext):
    lang = lang_of(cb.from_user.id)
    data = await state.get_data()
    idx = data["idx"]
    if idx >= len(VISIT_STEPS):
        await state.set_state(Visit.comment)
        await cb.message.edit_text(t("comment_prompt", lang), reply_markup=visit_comment_kb(lang))
        return
    field_key = VISIT_STEPS[idx]
    header = t("step_header", lang, i=idx + 1, n=len(VISIT_STEPS))
    await cb.message.edit_text(f"{header}\n\n<b>{tf(field_key, lang)}</b>",
                               reply_markup=visit_step_kb(field_key, lang))


@router.callback_query(Visit.flow, F.data.startswith("pick:"))
async def visit_pick(cb: CallbackQuery, state: FSMContext):
    _, field_key, idx = cb.data.split(":")
    value = DROPDOWNS[field_key][int(idx)]          # каноничное русское значение
    data = await state.get_data()
    db.update_person(data["cid"], {field_key: value})   # во все договоры, сразу в БД
    await state.update_data(idx=data["idx"] + 1)
    await ask_visit_step(cb, state)
    await cb.answer("✓ " + tv(value, lang_of(cb.from_user.id)))


@router.callback_query(F.data == "visit_back")
async def visit_back(cb: CallbackQuery, state: FSMContext):
    cur = await state.get_state()
    if cur == Visit.comment.state:
        await state.set_state(Visit.flow)
        await state.update_data(idx=len(VISIT_STEPS) - 1)
        await ask_visit_step(cb, state)
    elif cur == Visit.flow.state:
        idx = (await state.get_data()).get("idx", 0)
        if idx <= 0:
            await _cancel_visit(cb, state)
        else:
            await state.update_data(idx=idx - 1)
            await ask_visit_step(cb, state)
    await cb.answer()


@router.callback_query(F.data == "visit_cancel")
async def visit_cancel(cb: CallbackQuery, state: FSMContext):
    await _cancel_visit(cb, state)
    await cb.answer()


async def _cancel_visit(cb: CallbackQuery, state: FSMContext):
    cid = (await state.get_data()).get("cid")
    await state.clear()
    if cid:
        await show_card(cb, cid, lang_of(cb.from_user.id))


@router.message(Visit.comment)
async def visit_comment(message: Message, state: FSMContext):
    cid = (await state.get_data())["cid"]
    db.update_person(cid, {"call_comment": message.text.strip()})
    await _finish_visit(message, state, cid)


@router.callback_query(Visit.comment, F.data == "visit_skip")
async def visit_skip(cb: CallbackQuery, state: FSMContext):
    await _finish_visit(cb, state, (await state.get_data())["cid"])
    await cb.answer()


async def _finish_visit(target, state: FSMContext, cid: str):
    await state.clear()
    db.update_person(cid, {"visit_date": date.today().isoformat()})
    row = db.get_client(cid)
    db.log_visit(cid, target.from_user.id,
                 f"Визит. Статус: {row['call_status']}; "
                 f"Договорённость: {row['agreed_payment']}; {row['call_comment'] or ''}")
    lang = lang_of(target.from_user.id)
    msg = target.message if isinstance(target, CallbackQuery) else target
    await msg.answer(t("visit_saved", lang))
    # сводка в беседу (на русском — внутренний канал КА)
    await post_to_group(msg.bot, text=(
        f"✅ Выезд отработан\n👤 {row['client']} ({cid})\n"
        f"🧑‍💼 {target.from_user.full_name}\n"
        f"📝 {row['call_status'] or '—'} · {row['agreed_payment'] or '—'}\n"
        f"💬 {row['call_comment'] or '—'}"))
    await show_card(msg, cid, lang)


# --- Оплата -----------------------------------------------------------------
@router.callback_query(F.data.startswith("pay:"))
async def pay_start(cb: CallbackQuery, state: FSMContext):
    if not is_trusted(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(Pay.amount)
    await state.update_data(cid=cb.data.split(":", 1)[1])
    await cb.message.answer(t("pay_prompt", lang_of(cb.from_user.id)))
    await cb.answer()


@router.message(Pay.amount)
async def pay_save(message: Message, state: FSMContext):
    cid = (await state.get_data())["cid"]
    lang = lang_of(message.from_user.id)
    db.update_person(cid, {"last_payment_sum": message.text.strip(),
                           "last_payment_date": date.today().isoformat()})
    db.log_visit(cid, message.from_user.id, f"Оплата/обещание: {message.text.strip()}")
    await state.clear()
    await message.answer(t("pay_saved", lang))
    await show_card(message, cid, lang)


# --- Повторный выезд --------------------------------------------------------
@router.callback_query(F.data.startswith("replan:"))
async def replan_start(cb: CallbackQuery, state: FSMContext):
    if not is_trusted(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(Replan.when)
    await state.update_data(cid=cb.data.split(":", 1)[1])
    await cb.message.answer(t("replan_prompt", lang_of(cb.from_user.id)))
    await cb.answer()


@router.message(Replan.when)
async def replan_save(message: Message, state: FSMContext):
    lang = lang_of(message.from_user.id)
    iso = parse_date(message.text)
    if not iso:
        await message.answer(t("replan_bad", lang))
        return
    cid = (await state.get_data())["cid"]
    db.update_person(cid, {"reminder_date": iso, "meeting_date": iso})
    await state.clear()
    pretty = datetime.fromisoformat(iso).strftime("%d.%m.%Y")
    await message.answer(t("replan_saved", lang, d=pretty))
    await show_card(message, cid, lang)


# --- Геолокация -------------------------------------------------------------
@router.callback_query(F.data.startswith("geo:"))
async def geo_start(cb: CallbackQuery, state: FSMContext):
    if not is_trusted(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    await state.set_state(Geo.wait)
    await state.update_data(cid=cb.data.split(":", 1)[1])
    await cb.message.answer(t("geo_prompt", lang_of(cb.from_user.id)))
    await cb.answer()


@router.message(Geo.wait, F.location)
async def geo_save(message: Message, state: FSMContext):
    cid = (await state.get_data())["cid"]
    lang = lang_of(message.from_user.id)
    db.update_person(cid, {"geo_lat": message.location.latitude,
                           "geo_lon": message.location.longitude,
                           "geo_src": "manual"})   # точка выездника — приоритетная, геокодинг не трогает
    await state.clear()
    await message.answer(t("geo_saved", lang))
    await show_card(message, cid, lang)


# --- Фотографии -------------------------------------------------------------
@router.callback_query(F.data.startswith("photo:"))
async def photo_start(cb: CallbackQuery, state: FSMContext):
    if not is_trusted(cb.from_user.id):
        await cb.answer("⛔", show_alert=True)
        return
    lang = lang_of(cb.from_user.id)
    await state.set_state(Photo.wait)
    await state.update_data(cid=cb.data.split(":", 1)[1])
    await cb.message.answer(t("photo_prompt", lang), reply_markup=photo_done_kb(lang))
    await cb.answer()


@router.message(Photo.wait, F.photo)
async def photo_save(message: Message, state: FSMContext):
    n = db.add_photo((await state.get_data())["cid"])
    await message.answer(t("photo_received", lang_of(message.from_user.id), n=n))


@router.callback_query(Photo.wait, F.data == "photo_done")
async def photo_done(cb: CallbackQuery, state: FSMContext):
    cid = (await state.get_data())["cid"]
    lang = lang_of(cb.from_user.id)
    await state.clear()
    await cb.message.answer(t("photos_saved", lang))
    await show_card(cb, cid, lang)
    await cb.answer()
