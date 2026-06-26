"""Клавиатуры (локализованные). Ролей нет — одно меню выездника."""

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from fields import DROPDOWNS
from i18n import LANGUAGES, t, tv


def menu(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t("btn_my_clients", lang)),
             KeyboardButton(text=t("btn_search", lang))],
            [KeyboardButton(text=t("btn_my_plans", lang)),
             KeyboardButton(text=t("btn_language", lang))],
        ],
        resize_keyboard=True,
    )


def lang_picker() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for code, native in LANGUAGES.items():
        kb.button(text=native, callback_data=f"lang:{code}")
    kb.adjust(1)
    return kb.as_markup()


def client_list_kb(clients, prefix: str = "open") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for c in clients:
        kb.button(text=f"{c['client']}  ·  {c['contract_id']}",
                  callback_data=f"{prefix}:{c['contract_id']}")
    kb.adjust(1)
    return kb.as_markup()


def person_list_kb(persons) -> InlineKeyboardMarkup:
    """Список людей (договоры объединены). Открываем по представителю (rep)."""
    kb = InlineKeyboardBuilder()
    for p in persons:
        suffix = f"  ·  {p['n']} КД" if p["n"] > 1 else ""
        kb.button(text=f"{p['client']}{suffix}", callback_data=f"open:{p['rep']}")
    kb.adjust(1)
    return kb.as_markup()


def self_select_kb(employees) -> InlineKeyboardMarkup:
    """Самовыбор: кто вы. employees — список (name, contracts)."""
    kb = InlineKeyboardBuilder()
    for idx, e in enumerate(employees):
        kb.button(text=f"{e['employee']}  ({e['contracts']})", callback_data=f"self:{idx}")
    kb.adjust(1)
    return kb.as_markup()


def accept_group_kb(chat_id: int, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t("btn_accept", lang), callback_data=f"acceptgroup:{chat_id}"),
        InlineKeyboardButton(text=t("btn_decline", lang), callback_data="declinegroup"),
    ]])


def approve_bind_kb(telegram_id: int, lang: str) -> InlineKeyboardMarkup:
    """ADMIN: одобрить/отклонить самопривязку выездника."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t("btn_approve", lang), callback_data=f"approvebind:{telegram_id}"),
        InlineKeyboardButton(text=t("btn_reject", lang), callback_data=f"rejectbind:{telegram_id}"),
    ]])


def card_kb(contract_id: str, lang: str) -> InlineKeyboardMarkup:
    cid = contract_id
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_fix_visit", lang), callback_data=f"visit:{cid}")],
        [InlineKeyboardButton(text=t("btn_fix_pay", lang), callback_data=f"pay:{cid}")],
        [InlineKeyboardButton(text=t("btn_replan", lang), callback_data=f"replan:{cid}"),
         InlineKeyboardButton(text=t("btn_geo", lang), callback_data=f"geo:{cid}")],
        [InlineKeyboardButton(text=t("btn_photos", lang), callback_data=f"photo:{cid}"),
         InlineKeyboardButton(text=t("btn_history", lang), callback_data=f"hist:{cid}")],
        [InlineKeyboardButton(text=t("btn_back_list", lang), callback_data="list")],
    ])


def _nav(lang: str) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=t("btn_back", lang), callback_data="visit_back"),
            InlineKeyboardButton(text=t("btn_cancel", lang), callback_data="visit_cancel")]


def visit_step_kb(field_key: str, lang: str) -> InlineKeyboardMarkup:
    """Варианты (показываем перевод, в callback — индекс каноничного значения)."""
    kb = InlineKeyboardBuilder()
    for idx, opt in enumerate(DROPDOWNS[field_key]):
        kb.button(text=tv(opt, lang), callback_data=f"pick:{field_key}:{idx}")
    kb.adjust(1)
    kb.row(*_nav(lang))
    return kb.as_markup()


def visit_comment_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_skip", lang), callback_data="visit_skip")],
        _nav(lang),
    ])


def photo_done_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_done", lang), callback_data="photo_done")],
    ])
