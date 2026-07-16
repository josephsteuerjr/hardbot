"""
Дайджест руководителя: сводка «кто кого обработал сегодня» + разбор по сотрудникам
и карточкам клиентов. Механика бота (без миниаппа): сообщение со сводкой, под ним
кнопки-ФИО сотрудников → список клиентов → карточка (заполненное по визиту + референс).

Три экрана строятся здесь (текст + инлайн-клавиатура), навигация — правкой того же
сообщения. Источник данных — db.persons_processed (визиты за дату по clients.visit_date).
Всё на русском: руководители читают по-русски, значения статусов уже каноничные.

Callback-схема (компактная, только ASCII-индексы — ФИО в callback не попадают):
  dg:h:{date}            — домой (сводка со списком сотрудников)
  dg:e:{date}:{ei}       — сотрудник ei → его клиенты за дату
  dg:c:{date}:{ei}:{ci}  — клиент ci у сотрудника ei → карточка
  dg:p:{date}:{ei}:{ci}  — фото кейса альбомом в этот же чат (не правка сообщения)
  dg:pe:{date}:{ei}      — фото сотрудника за день, альбом на каждого клиента
  dg:o:{date}            — просроченные обещания по команде (контроль обещаний)
где date — ISO (YYYY-MM-DD), ei/ci — индексы в детерминированно отсортированных списках.
"""

from __future__ import annotations

import html
from datetime import date, datetime

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

import db
import debt
import photostore

_MONTHS = ["", "января", "февраля", "марта", "апреля", "мая", "июня", "июля",
           "августа", "сентября", "октября", "ноября", "декабря"]


def human_date(iso: str) -> str:
    d = db._as_date(iso)
    if not d:
        return iso or "—"
    return f"{d.day} {_MONTHS[d.month]}"


def today_iso() -> str:
    # Та же система координат, что и у visit_date (date.today() в момент завершения
    # визита) — чтобы фильтр «за сегодня» совпадал с тем, как визиты записаны.
    return date.today().isoformat()


# --- утилиты значений -------------------------------------------------------
def _e(s) -> str:
    return html.escape(str(s)) if s not in (None, "") else ""


def _v(contracts, key):
    """Первое непустое значение поля среди договоров человека."""
    return next((c[key] for c in contracts if c[key] not in (None, "")), None)


_money = debt.fmt    # единая логика денег/валюты (была третья копия — card.py, web.py, здесь)


# Эмодзи для исходов визита — чтобы сводка читалась «глазами», а не строкой текста.
_STATUS_EMOJI = {
    "Оплачен": "💰", "Обещает оплатить": "✅", "Рассрочка": "🤝",
    "Отказ от оплаты": "🚫", "Нет контакта": "📵", "В работе": "⏳",
    "Не обработан": "⬜", "Передана инф 3 лицу": "📨", "Тюрьма": "🔒",
    "Смерть": "🕯", "Кредит не брал": "❓", "Доп. Обогащение": "➕",
}


def _st_emoji(s: str) -> str:
    return _STATUS_EMOJI.get(s, "•")


def _person_status(p: dict) -> str:
    return _v(p["contracts"], "overall_result") or _v(p["contracts"], "call_status") or "—"


def _outcomes(persons: list[dict]) -> list[tuple[str, int]]:
    """[(статус, число)] по исходам за день, по убыванию — для сводной статистики."""
    counts: dict[str, int] = {}
    for p in persons:
        st = _person_status(p)
        counts[st] = counts.get(st, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def _persons_total(persons: list[dict]) -> str:
    """Сумма долга по обработанным кейсам, раздельно по валютам: «132 900 ₽ · 11 550 000 сум».

    Складывать сумы ЗК с рублями Делимобиля в одно число нельзя — а именно это и
    получалось у смешанной команды (единица при этом молча становилась пустой).
    """
    acc: dict[str, int] = {}
    for p in persons:
        for x in p["totals"]:
            acc[x["unit"]] = acc.get(x["unit"], 0) + x["sum"]
    return " · ".join(_money(s, u) for u, s in acc.items() if s)


def sorted_employees(mgr: dict) -> list[str]:
    """Детерминированный порядок для стабильной индексации в callback."""
    return sorted(set(mgr.get("employees") or []))


def scope_label(mgr: dict) -> str:
    cities = [c for c in (mgr.get("cities") or []) if c]
    return " · ".join(cities) if cities else "все города"


# --- построение сводки ------------------------------------------------------
def _employee_persons(mgr: dict, on_date: str):
    """[(ei, employee, persons)] — по всем сотрудникам руководителя за дату."""
    return [(ei, emp, db.persons_processed(emp, on_date))
            for ei, emp in enumerate(sorted_employees(mgr))]


def _short(name: str, limit: int = 22) -> str:
    name = name or "—"
    return name if len(name) <= limit else name[:limit - 1] + "…"


# --- ЭКРАН 1: сводка (со статистикой по каждому сотруднику) ------------------
def summary_view(mgr: dict, on_date: str) -> tuple[str, InlineKeyboardMarkup]:
    rows = _employee_persons(mgr, on_date)
    active = [r for r in rows if r[2]]
    total_clients = sum(len(ps) for _, _, ps in rows)
    grand_total = _persons_total([p for _, _, ps in rows for p in ps])
    # покрытие фото: у скольких обработанных есть снимки за этот день
    cov = {emp: sum(1 for p in ps if photostore.day_photos(p["contracts"], on_date))
           for _ei, emp, ps in rows}

    head = [f"📊 <b>Дайджест · {human_date(on_date)}</b>",
            f"🏙 {_e(scope_label(mgr))}",
            f"👥 Команда: {len(rows)} · активны: {len(active)} · "
            f"обработано: <b>{total_clients}</b>"]
    if total_clients:
        head.append(f"📸 С фото: {sum(cov.values())} из {total_clients}")
    if grand_total:
        head.append(f"💰 Сумма по кейсам дня: {grand_total}")
    head.append("━━━━━━━━━━━━━━")

    # порядок отображения: активные по убыванию числа кейсов, затем «нулевые»
    order = sorted(rows, key=lambda x: (-len(x[2]), x[1].lower()))
    body = []
    for _ei, emp, ps in order:
        if ps:
            oc = _outcomes(ps)
            brief = " · ".join(f"{_st_emoji(s)} {_e(s)} {n}" for s, n in oc[:3])
            body.append(f"👤 <b>{_e(emp)}</b> — {len(ps)} {_plural(len(ps), 'кейс', 'кейса', 'кейсов')}"
                        f" · 📸 {cov[emp]}/{len(ps)}")
            if brief:
                body.append(f"    {brief}")
        else:
            body.append(f"▫️ <s>{_e(emp)}</s> — 0")
    if not active:
        body = ["Сегодня выездов пока нет."]

    tail = ["━━━━━━━━━━━━━━", "Кнопки ниже — детали и кейсы сотрудника."]

    kb = InlineKeyboardBuilder()
    for ei, emp, ps in order:
        mark = "👤" if ps else "▫️"
        kb.button(text=f"{mark} {_short(emp)} · {len(ps)}",
                  callback_data=f"dg:e:{on_date}:{ei}")
    n_over = sum(len(ps) for _, ps in overdue_by_employee(mgr, on_date))
    if n_over:
        kb.button(text=f"⏰ Обещания просрочены: {n_over}", callback_data=f"dg:o:{on_date}")
    kb.button(text="🔄 Обновить", callback_data=f"dg:h:{on_date}")
    kb.adjust(1)
    return "\n".join(head + body + tail), kb.as_markup()


# --- ЭКРАН 2: карточка сотрудника (детали) + его кейсы -----------------------
def employee_view(mgr: dict, on_date: str, ei: int) -> tuple[str, InlineKeyboardMarkup]:
    emps = sorted_employees(mgr)
    if not (0 <= ei < len(emps)):
        return summary_view(mgr, on_date)
    emp = emps[ei]
    persons = db.persons_processed(emp, on_date)
    total = _persons_total(persons)
    # у кого из кейсов есть фото за этот день (для статы и меток на кнопках)
    with_ph = {ci for ci, p in enumerate(persons)
               if photostore.day_photos(p["contracts"], on_date)}

    lines = [f"👤 <b>{_e(emp)}</b>",
             f"🗓 {human_date(on_date)} · обработано <b>{len(persons)}</b>"]

    if persons:
        lines.append("━━━━━━━━━━━━━━")
        lines.append("📊 <b>Исходы за день</b>")
        for s, n in _outcomes(persons):
            lines.append(f"  {_st_emoji(s)} {_e(s)} — {n}")
        lines.append(f"📸 С фото: {len(with_ph)} из {len(persons)}")
        if total:
            lines.append(f"💰 Сумма по кейсам: {total}")

    # телеметрия активности — если сотрудник привязан к telegram
    act = db.employee_activity(emp)
    if act:
        lines.append("━━━━━━━━━━━━━━")
        lines.append("📈 <b>Активность</b>")
        lines.append(f"  Визиты: 7д {act['visits_7d']} · 30д {act['visits_30d']}")
        if act["markers_7d"] or act["markers_30d"]:
            lines.append(f"  Метки: 7д {act['markers_7d']} · 30д {act['markers_30d']}")
        if act["last_activity"]:
            idle = act["idle_days"]
            when = str(act["last_activity"])[:10]
            lines.append(f"  Последняя активность: {when}"
                         + (f" · простой {idle} дн" if idle and idle >= 2 else ""))

    kb = InlineKeyboardBuilder()
    if persons:
        lines.append("━━━━━━━━━━━━━━")
        lines.append("Кейсы за сегодня:")
        for ci, p in enumerate(persons):
            st = _person_status(p)
            kb.button(text=f"{_st_emoji(st)} {_short(p['client'], 26)}"
                           + (" 📸" if ci in with_ph else ""),
                      callback_data=f"dg:c:{on_date}:{ei}:{ci}")
    else:
        lines.append("━━━━━━━━━━━━━━")
        lines.append("Сегодня этот сотрудник ещё никого не обработал.")
    nday = sum(len(photostore.day_photos(p["contracts"], on_date)) for p in persons)
    if nday:
        kb.button(text=f"📸 Фото за день ({nday})", callback_data=f"dg:pe:{on_date}:{ei}")
    kb.button(text="⬅️ К сводке", callback_data=f"dg:h:{on_date}")
    kb.adjust(1)
    return "\n".join(lines), kb.as_markup()


# --- ЭКРАН 3: карточка клиента ----------------------------------------------
def person_card(mgr: dict, on_date: str, ei: int, ci: int) -> tuple[str, InlineKeyboardMarkup]:
    emps = sorted_employees(mgr)
    if not (0 <= ei < len(emps)):
        return summary_view(mgr, on_date)
    emp = emps[ei]
    persons = db.persons_processed(emp, on_date)
    if not (0 <= ci < len(persons)):
        return employee_view(mgr, on_date, ei)
    p = persons[ci]
    cs = p["contracts"]

    def val(key):
        return _v(cs, key)

    # --- шапка: референс ---
    head = [f"👤 <b>{_e(p['client'])}</b>" + (f"  ·  {p['n']} КД" if p["n"] > 1 else "")]
    partner = ", ".join(p["partners"]) or (val("partner") or "")
    sub = []
    if partner:
        sub.append(f"🏢 {_e(partner)}")
    if val("birth_date"):
        sub.append(f"🎂 {_e(val('birth_date'))}")
    if sub:
        head.append("   ".join(sub))
    phones = [val("phone"), val("phone_main")]
    phones = [ph for ph in phones if ph]
    if phones:
        head.append("📞 " + " · ".join(_e(ph) for ph in phones))
    addr = val("address") or val("address_customer") or val("extra_address")
    if addr:
        head.append(f"📍 {_e(addr)}")
    head.append(f"💰 <b>{_persons_total([p])}</b>")   # ЗК + Делимобиль → обе валюты, не их сумма

    # --- итог выезда: заполненное ---
    body = []

    def add(emoji, label, key, prefix=""):
        v = val(key)
        if v:
            body.append(f"{emoji} {label}{_e(v)}" if not prefix else f"{emoji} {label}{prefix}{_e(v)}")

    add("✅", "", "overall_result")
    add("📞", "Звонок: ", "call_status")
    add("🤝", "", "agreed_payment")
    add("👥", "Контакт: ", "contact_with")
    add("🏠", "На адресе: ", "who_at_address")
    add("🌍", "", "client_location")
    add("🎯", "Цель в РФ: ", "purpose_rf")
    tips = val("tips")
    if tips:
        body.append(f"🧭 Наводки: {_e(tips)}")
    comment = val("call_comment") or val("visit_comment")
    if comment:
        body.append(f"💬 {_e(comment)}")

    tail = []
    if val("visit_date"):
        tail.append(f"🗓 Выезд: {_e(val('visit_date'))}")
    if val("reminder_date"):
        tail.append(f"🔁 Повтор: {_e(val('reminder_date'))}")
    if val("meeting_date"):
        tail.append(f"📅 Встреча: {_e(val('meeting_date'))}")

    text = "\n".join(head)
    text += "\n━━━━━━━━━━━━━━\n🧾 <b>Итог выезда</b>\n"
    text += "\n".join(body) if body else "— форма визита не заполнена —"
    if tail:
        text += "\n" + "   ".join(tail)

    kb = InlineKeyboardBuilder()
    nph = len(photostore.person_photos(cs))
    if nph:
        kb.button(text=f"📸 Фото ({nph})", callback_data=f"dg:p:{on_date}:{ei}:{ci}")
    kb.button(text="⬅️ Назад к сотруднику", callback_data=f"dg:e:{on_date}:{ei}")
    kb.button(text="🏠 К сводке", callback_data=f"dg:h:{on_date}")
    kb.adjust(1)
    return text, kb.as_markup()


# --- ЭКРАН 4: просроченные обещания (контроль обещаний) ----------------------
def overdue_by_employee(mgr: dict, on_date: str) -> list[tuple[str, list[dict]]]:
    """[(employee, люди с просроченными обещаниями)] — только у кого они есть."""
    out = []
    for emp in sorted_employees(mgr):
        ps = db.overdue_promises(emp, on_date)
        if ps:
            out.append((emp, ps))
    return out


def promises_view(mgr: dict, on_date: str) -> tuple[str, InlineKeyboardMarkup]:
    """«Обещал и пропал»: promise_date в прошлом, оплаты нет. Самые тухлые сверху."""
    items = overdue_by_employee(mgr, on_date)
    total = sum(len(ps) for _, ps in items)
    lines = [f"⏰ <b>Просроченные обещания · {human_date(on_date)}</b>",
             f"🏙 {_e(scope_label(mgr))} · всего: <b>{total}</b>"]
    if not items:
        lines.append("━━━━━━━━━━━━━━")
        lines.append("Просроченных обещаний нет 🎉")
    for emp, ps in items:
        lines.append("━━━━━━━━━━━━━━")
        lines.append(f"👤 <b>{_e(emp)}</b> — {len(ps)}")
        for p in ps[:12]:
            money = _persons_total([p])
            lines.append(f"  🔴 {_e(p['client'])} — обещал {human_date(p['promise_date'])}, "
                         f"{p['days_overdue']} дн назад" + (f" · {money}" if money else ""))
        if len(ps) > 12:
            lines.append(f"  …и ещё {len(ps) - 12}")
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 Обновить", callback_data=f"dg:o:{on_date}")
    kb.button(text="⬅️ К сводке", callback_data=f"dg:h:{on_date}")
    kb.adjust(1)
    return "\n".join(lines), kb.as_markup()


# --- фото (данные для альбомов; сама отправка — в handlers_manager) ----------
def photos_for_person(mgr: dict, on_date: str, ei: int, ci: int):
    """(person, все его фото) по кейсу дайджеста — или (None, []) при сбитых индексах."""
    emps = sorted_employees(mgr)
    if not (0 <= ei < len(emps)):
        return None, []
    persons = db.persons_processed(emps[ei], on_date)
    if not (0 <= ci < len(persons)):
        return None, []
    p = persons[ci]
    return p, photostore.person_photos(p["contracts"])


def photos_for_employee_day(mgr: dict, on_date: str, ei: int):
    """(employee, [(person, его фото за on_date)]) — только люди, у кого фото есть."""
    emps = sorted_employees(mgr)
    if not (0 <= ei < len(emps)):
        return None, []
    emp = emps[ei]
    items = []
    for p in db.persons_processed(emp, on_date):
        files = photostore.day_photos(p["contracts"], on_date)
        if files:
            items.append((p, files))
    return emp, items


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    d = n % 10
    if 11 <= n <= 14:
        return many
    if d == 1:
        return one
    if 2 <= d <= 4:
        return few
    return many
