"""
Дайджест руководителя: РИСОВАЛЬЩИК телеграм-экранов поверх слоя данных.

Считает digest_data.py (без aiogram и разметки) — у него два потребителя:
этот модуль (текст + инлайн-клавиатура для чата) и вкладка руководителя в
приложении (web.py отдаёт тот же расчёт JSON'ом). Здесь живёт ТОЛЬКО
телеграм-специфика: html-экранирование, эмодзи-разделители, бюджеты 4096
символов (топ-3 исхода, 12 должников, обрезка полей до 900), индексы ei/ci
в callback и клавиатуры.

Callback-схема (компактная, только ASCII-индексы — ФИО в callback не попадают):
  dg:h:{date}            — домой (сводка со списком сотрудников)
  dg:e:{date}:{ei}       — сотрудник ei → его клиенты за дату
  dg:c:{date}:{ei}:{ci}  — клиент ci у сотрудника ei → карточка
  dg:p:{date}:{ei}:{ci}  — фото кейса альбомом в этот же чат (не правка сообщения)
  dg:pe:{date}:{ei}      — фото сотрудника за день, альбом на каждого клиента
  dg:o:{date}            — просроченные обещания по команде (контроль обещаний)
  dg:w:{date}:{days}     — сводка за период (7/30 дней до date включительно)
где date — ISO (YYYY-MM-DD), ei/ci — индексы в детерминированно отсортированных списках.
"""

from __future__ import annotations

import html
import os

from aiogram.types import InlineKeyboardMarkup, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

import db
import maplink
import photostore
# Публичные имена дайджеста не переезжают: на них стоят tests/test_digest*,
# tests/test_promises, app.py и handlers_manager.       # noqa: F401 ниже
from digest_data import (  # noqa: F401
    _outcomes, _person_status, _persons_total, _st_emoji, _totals_units, _v,
    daily_report_caption, daily_report_data, daily_report_filename,
    daily_report_xlsx,
    employee_data, human_date, manager_scope, overdue_by_employee, period_data,
    person_data, promises_data, scope_countries_info, scope_label,
    sorted_employees, summary_data, today_iso,
)


# --- утилиты значений -------------------------------------------------------
def _e(s) -> str:
    return html.escape(str(s)) if s not in (None, "") else ""


def owner_scope(uid: int) -> dict | None:
    """Синтетический руководитель для владельца — автопуши и чат-дайджест.
    Тонкий алиас: сборка одна на все контуры (digest_data.manager_scope)."""
    return manager_scope(uid, is_owner=True)


def scope_heading(mgr: dict) -> str:
    """Явная республика + города для раздельных руководительских сводок.

    У руководителя с обеими республиками показываем обе, а не молчим: раньше
    смешанный scope схлопывался в один город, и заказчик разделения РТ/РУ видел
    сводку вообще без признака страны."""
    specs = scope_countries_info(mgr)
    place = scope_label(mgr)
    if not specs:
        return f"🏙 {_e(place)}"
    label = " + ".join(f"{s['flag']} <b>{s['name']}</b>" for s in specs)
    return f"{label} · 🏙 {_e(place)}"


def _short(name: str, limit: int = 22) -> str:
    name = name or "—"
    return name if len(name) <= limit else name[:limit - 1] + "…"


def _app_button(kb: InlineKeyboardBuilder, on_date: str) -> None:
    """WebApp-кнопка в конец клавиатуры: пуш ведёт во вкладку приложения, но сам
    текст остаётся полным — если приложение недоступно, руководитель ничего не
    теряет. Кнопка ПОСЛЕДНЯЯ: существующие тесты коротят на первой совпавшей."""
    base = (os.getenv("MINIAPP_URL", "") or "").strip()
    if base.lower().startswith("https://"):
        kb.button(text="📱 Открыть сводку в приложении",
                  web_app=WebAppInfo(url=f"{base.rstrip('/')}?tab=digest&d={on_date}"))


# --- ЭКРАН 1: сводка (со статистикой по каждому сотруднику) ------------------
def daily_report_view(mgr: dict, on_date: str):
    """Сводка руководителю ФАЙЛОМ: (подпись, клавиатура, (имя файла, байты)).

    Решение владельца 08.09.2026: длинный текст дайджеста руководителям больше
    не нужен — нужна одна таблица по всем сотрудникам («закреплено, отработано
    сегодня, отработано всего»), а разбор по дням и карточки живут во вкладке
    приложения. Текстовый дайджест остаётся кнопкой в боте и фолбэком, если
    книга почему-то не собралась."""
    data = daily_report_data(mgr, on_date)
    kb = InlineKeyboardBuilder()
    _app_button(kb, on_date)
    return (daily_report_caption(data), kb.as_markup(),
            (daily_report_filename(on_date),
             daily_report_xlsx(mgr, on_date, data=data)))


def summary_view(mgr: dict, on_date: str) -> tuple[str, InlineKeyboardMarkup]:
    d = summary_data(mgr, on_date)
    t = d["totals"]

    head = [f"📊 <b>Дайджест · {human_date(on_date)}</b>",
            scope_heading(mgr),
            f"👥 Команда: {t['employees']} · активны: {t['active']} · "
            f"обработано: <b>{t['persons']}</b>"]
    if t["persons"]:
        head.append(f"📸 С фото: {t['with_photo']} из {t['persons']}")
    if t["money_text"]:
        head.append(f"💰 Сумма по кейсам дня: {t['money_text']}")
    head.append("━━━━━━━━━━━━━━")

    def _emp_lines(items):
        out = []
        for r in items:
            if r["persons"]:
                brief = " · ".join(f"{o['emoji']} {_e(o['status'])} {o['n']}"
                                   for o in r["outcomes"][:3])   # топ-3 — бюджет чата
                out.append(f"👤 <b>{_e(r['employee'])}</b> — {r['persons']} "
                           f"{_plural(r['persons'], 'кейс', 'кейса', 'кейсов')}"
                           f" · 📸 {r['with_photo']}/{r['persons']}")
                if brief:
                    out.append(f"    {brief}")
            else:
                out.append(f"▫️ <s>{_e(r['employee'])}</s> — 0")
        return out

    body = []
    if len(d["groups"]) == 1 and not d["groups"][0]["title"]:
        body = _emp_lines(d["groups"][0]["employees"])
    else:
        for g in d["groups"]:
            if g["title"]:
                n = len(g["employees"])
                body.append(f"{g['flag']} <b>{g['title']}</b> — {n} "
                            f"{_plural(n, 'сотрудник', 'сотрудника', 'сотрудников')}")
                body += _emp_lines(g["employees"])
                body.append("")
            else:                      # хвост без республики — без заголовка
                body += _emp_lines(g["employees"])
        while body and not body[-1]:
            body.pop()
    if not t["active"]:
        body = ["Сегодня выездов пока нет."]

    tail = ["━━━━━━━━━━━━━━", "Кнопки ниже — детали и кейсы сотрудника."]

    kb = InlineKeyboardBuilder()
    # Кнопки идут в том же порядке, что и строки текста: иначе у руководителя с обеими
    # республиками список сгруппирован по странам, а кнопки — вперемешку.
    for r in d["display"]:
        mark = "👤" if r["persons"] else "▫️"
        kb.button(text=f"{mark} {_short(r['employee'])} · {r['persons']}",
                  callback_data=f"dg:e:{on_date}:{r['ei']}")
    if t["overdue_promises"]:
        kb.button(text=f"⏰ Обещания просрочены: {t['overdue_promises']}",
                  callback_data=f"dg:o:{on_date}")
    kb.button(text="📅 Неделя", callback_data=f"dg:w:{on_date}:7")
    kb.button(text="🔄 Обновить", callback_data=f"dg:h:{on_date}")
    _app_button(kb, on_date)
    kb.adjust(1)
    return "\n".join(head + body + tail), kb.as_markup()


# --- ЭКРАН 2: карточка сотрудника (детали) + его кейсы -----------------------
def employee_view(mgr: dict, on_date: str, ei: int) -> tuple[str, InlineKeyboardMarkup]:
    emps = sorted_employees(mgr)
    if not (0 <= ei < len(emps)):
        return summary_view(mgr, on_date)
    emp = emps[ei]
    d = employee_data(mgr, on_date, emp, employees=emps)

    lines = [f"👤 <b>{_e(emp)}</b>",
             f"🗓 {human_date(on_date)} · обработано <b>{d['persons']}</b>"]

    if d["persons"]:
        lines.append("━━━━━━━━━━━━━━")
        lines.append("📊 <b>Исходы за день</b>")
        for o in d["outcomes"]:
            lines.append(f"  {o['emoji']} {_e(o['status'])} — {o['n']}")
        lines.append(f"📸 С фото: {d['with_photo']} из {d['persons']}")
        if d["money_text"]:
            lines.append(f"💰 Сумма по кейсам: {d['money_text']}")

    act = d["activity"]
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
    if d["cases"]:
        lines.append("━━━━━━━━━━━━━━")
        lines.append("Кейсы за сегодня:")
        for ci, c in enumerate(d["cases"]):
            kb.button(text=f"{c['emoji']} {_short(c['client'], 26)}"
                           + (" 📸" if c["photos"] else ""),
                      callback_data=f"dg:c:{on_date}:{ei}:{ci}")
    else:
        lines.append("━━━━━━━━━━━━━━")
        lines.append("Сегодня этот сотрудник ещё никого не обработал.")
    if d["photos_day"]:
        kb.button(text=f"📸 Фото за день ({d['photos_day']})",
                  callback_data=f"dg:pe:{on_date}:{ei}")
    base = (os.getenv("MINIAPP_URL", "") or "").strip()
    if base:   # карта дня — отдельная веб-страница по подписанной ссылке (48 ч)
        kb.button(text="🗺 Карта дня",
                  url=f"{base.rstrip('/')}/manager/map?{maplink.sign(emp, on_date)}")
    kb.button(text="⬅️ К сводке", callback_data=f"dg:h:{on_date}")
    kb.adjust(1)
    return "\n".join(lines), kb.as_markup()


# --- ЭКРАН 3: карточка клиента ----------------------------------------------
# Телеграм не принимает сообщение длиннее 4096 символов, а комментарий выезда
# ничем не ограничен — обрезаем каждое поле (бюджет чата, во вкладке его нет).
_CARD_FIELD_LIMIT = 900

# Эмодзи и подписи известных полей визита — порядок печати как раньше.
_CARD_VISIT_STYLE = {
    "call_status": ("📞", "Звонок: "),
    "agreed_payment": ("🤝", ""),
    "contact_with": ("👥", "Контакт: "),
    "who_at_address": ("🏠", "На адресе: "),
    "client_location": ("🌍", ""),
    "purpose_rf": ("🎯", "Цель в РФ: "),
}


def _clip(v: str) -> str:
    if len(v) > _CARD_FIELD_LIMIT:
        return v[:_CARD_FIELD_LIMIT].rstrip() + "… (обрезано)"
    return v


def person_card(mgr: dict, on_date: str, ei: int, ci: int) -> tuple[str, InlineKeyboardMarkup]:
    emps = sorted_employees(mgr)
    if not (0 <= ei < len(emps)):
        return summary_view(mgr, on_date)
    emp = emps[ei]
    persons = db.persons_processed(emp, on_date)
    if not (0 <= ci < len(persons)):
        return employee_view(mgr, on_date, ei)
    from digest_data import _person_payload
    d = _person_payload(persons[ci], on_date)

    # --- шапка: референс ---
    head = [f"👤 <b>{_e(d['client'])}</b>" + (f"  ·  {d['n']} КД" if d["n"] > 1 else "")]
    sub = []
    if d["partner"]:
        sub.append(f"🏢 {_e(d['partner'])}")
    if d["birth_date"]:
        sub.append(f"🎂 {_e(d['birth_date'])}")
    if sub:
        head.append("   ".join(sub))
    if d["phones"]:
        head.append("📞 " + " · ".join(_e(ph) for ph in d["phones"]))
    if d["address"]:
        head.append(f"📍 {_e(d['address'])}")
    head.append(f"💰 <b>{d['money_text']}</b>")   # обе валюты, не их сумма

    # --- итог выезда: заполненное (из доказанного снимка; расхождение с
    # реестром печатаем ВТОРОЙ величиной, а не прячем — правка выездника после
    # «Сохранить выезд» ложится в сырые колонки и в снимок не попадает) ---
    body = []
    if d["outcome"] and d["outcome"] != "—":
        body.append(f"✅ {_e(d['outcome'])}")
    for item in d["visit"]:
        style = _CARD_VISIT_STYLE.get(item["key"])
        if style is None or not item["value"]:
            continue
        emoji, label = style
        line = f"{emoji} {label}{_e(_clip(item['value']))}"
        if item["registry"]:
            line += f" · в реестре сейчас: {_e(_clip(item['registry']))}"
        body.append(line)
    if d["tips"]:
        body.append(f"📇 Доп. адрес/номер: {_e(_clip(d['tips']))}")
    if d["comment"]:
        body.append(f"💬 {_e(_clip(d['comment']))}")

    # гео-контроль: где реально был телефон при закрытии последнего визита.
    # Вердикт СОХРАНЁННЫЙ (см. digest_data): пересчёт задним числом переписывал
    # бы историю. Визит, закрытый руководителем через act_as, не судим как
    # нарушение сотрудника — точка снята с чужого телефона.
    g = d["geo"]
    if g:
        if g.get("by_other"):
            body.append(f"📍 визит закрыл руководитель ({g['pretty']} от адреса)")
        elif g["far"]:
            body.append(f"⚠️ Визит закрыт в {g['pretty']} от адреса!")
        else:
            body.append(f"📍 Точка закрытия: {g['pretty']} от адреса")

    tail = []
    if d["dates"]["visit_date"]:
        tail.append(f"🗓 Выезд: {_e(d['dates']['visit_date'])}")
    if d["dates"]["reminder_date"]:
        tail.append(f"🔁 Повтор: {_e(d['dates']['reminder_date'])}")
    if d["dates"]["meeting_date"]:
        tail.append(f"📅 Встреча: {_e(d['dates']['meeting_date'])}")

    text = "\n".join(head)
    text += "\n━━━━━━━━━━━━━━\n🧾 <b>Итог выезда</b>\n"
    text += "\n".join(body) if body else "— форма визита не заполнена —"
    if tail:
        text += "\n" + "   ".join(tail)

    kb = InlineKeyboardBuilder()
    if d["photos"]:
        kb.button(text=f"📸 Фото ({d['photos']})", callback_data=f"dg:p:{on_date}:{ei}:{ci}")
    kb.button(text="⬅️ Назад к сотруднику", callback_data=f"dg:e:{on_date}:{ei}")
    kb.button(text="🏠 К сводке", callback_data=f"dg:h:{on_date}")
    kb.adjust(1)
    return text, kb.as_markup()


# --- ЭКРАН 5: сводка за период (неделя/месяц) --------------------------------
def period_view(mgr: dict, on_date: str,
                days: int = 7) -> tuple[str, InlineKeyboardMarkup]:
    """Динамика команды за N дней до on_date включительно: кейсы, конверсия
    в «Оплачен», фото-дисциплина, деньги. Кнопки 7/30 переключают период."""
    d = period_data(mgr, on_date, days)
    t = d["totals"]
    days = d["days"]

    def pct(a, b):
        return f" ({round(100 * a / b)}%)" if b else ""

    lines = [f"📅 <b>Сводка за {days} дн · {human_date(d['from'])} — {human_date(d['to'])}</b>",
             scope_heading(mgr),
             f"👥 Обработано: <b>{t['persons']}</b>"]
    if t["persons"]:
        lines.append(f"✅ Оплачен: {t['paid']}{pct(t['paid'], t['persons'])}"
                     + (f" · закрыто: {t['money_paid_text']}" if t["money_paid_text"] else ""))
        lines.append(f"📸 С фото: {t['with_photo']} из {t['persons']}"
                     f"{pct(t['with_photo'], t['persons'])}")
    if t["money_text"]:
        lines.append(f"💰 Долг по обработанным: {t['money_text']}")
    lines.append("━━━━━━━━━━━━━━")

    for r in d["employees"]:
        if not r["persons"]:
            lines.append(f"▫️ <s>{_e(r['employee'])}</s> — 0")
            continue
        lines.append(f"👤 <b>{_e(r['employee'])}</b> — {r['persons']} "
                     f"{_plural(r['persons'], 'кейс', 'кейса', 'кейсов')}"
                     f" · ✅ {r['paid']}{pct(r['paid'], r['persons'])}"
                     f" · 📸 {r['with_photo']}/{r['persons']}")
        brief = " · ".join(f"{o['emoji']} {_e(o['status'])} {o['n']}"
                           for o in r["outcomes"][:3])
        if brief:
            lines.append(f"    {brief}")
    if not t["persons"]:
        lines.append("За период выездов нет.")

    kb = InlineKeyboardBuilder()
    other = 30 if days == 7 else 7
    kb.button(text=f"📅 За {other} дней", callback_data=f"dg:w:{on_date}:{other}")
    kb.button(text="🔄 Обновить", callback_data=f"dg:w:{on_date}:{days}")
    kb.button(text="⬅️ К сводке", callback_data=f"dg:h:{on_date}")
    kb.adjust(1)
    return "\n".join(lines), kb.as_markup()


# --- ЭКРАН 4: просроченные обещания (контроль обещаний) ----------------------
def promises_view(mgr: dict, on_date: str) -> tuple[str, InlineKeyboardMarkup]:
    """«Обещал и пропал»: promise_date в прошлом, оплаты нет. Самые тухлые сверху."""
    d = promises_data(mgr, on_date)
    t = d["totals"]
    lines = [f"⏰ <b>Просроченные обещания · {human_date(on_date)}</b>",
             f"🏙 {_e(d['scope'])} · всего: <b>{t['persons']}</b>"]
    if t["persons"]:
        # Отвечает на вопрос «обновлена информация или нет»: сколько из списка
        # реально трогали после обещания, а сколько лежит нетронутым.
        lines.append(f"🟢 отработано после обещания: {t['worked']} · "
                     f"⚪ не тронуто: {t['untouched']}")
    if t.get("deferred"):
        # Обещание в будущем убирает должника из маршрута сразу (киллер-фильтр),
        # а руководителю оно было видно только после срыва — «маршрут можно
        # тихо расчистить на месяц». Полный экран будущих обещаний — отдельная
        # работа; здесь хотя бы число.
        lines.append(f"🕓 отложено обещанием: {t['deferred']}")
    if not d["employees"]:
        lines.append("━━━━━━━━━━━━━━")
        lines.append("Просроченных обещаний нет 🎉")
    # Телеграм рубит сообщение на 4096 символах, а список просрочек сам не рассасывается:
    # он копится, пока обещание не закроют. Держим бюджет и честно говорим, что урезали, —
    # молча оборванный список руководитель принял бы за полный.
    budget, hidden_p, hidden_e = 3600, 0, 0
    for r in d["employees"]:                 # уже отсортированы: тухлые первыми
        block = ["━━━━━━━━━━━━━━", f"👤 <b>{_e(r['employee'])}</b> — {r['persons']}"]
        for p in r["items"][:12]:
            money = p["money_text"]
            if p["visited_after_promise"]:
                fresh = f" · 🟢 визит {human_date(p['last_visit'])}"
            elif p["last_visit"]:
                fresh = f" · 🟡 визит {human_date(p['last_visit'])}, до обещания"
            else:
                fresh = " · ⚪ визитов не было"
            promised = (f" · 🤝 {p['promised_sum']:,}".replace(",", " ")
                        if p.get("promised_sum") else "")
            block.append(f"  🔴 {_e(p['client'])} — обещал {human_date(p['promise_date'])}, "
                         f"{p['days_overdue']} дн назад" + promised
                         + (f" · {money}" if money else "")
                         + fresh)
        if r["persons"] > 12:
            block.append(f"  …и ещё {r['persons'] - 12}")
        # Меряем ГОТОВЫЙ блок: у одного сотрудника это до 14 строк, и проверка «до»
        # позволяла перевалить лимит последним же блоком.
        if sum(len(x) + 1 for x in lines + block) > budget:
            hidden_e += 1
            hidden_p += r["persons"]
            continue
        lines += block
    if hidden_e:
        lines.append("━━━━━━━━━━━━━━")
        lines.append(f"…и ещё {hidden_p} "
                     f"{_plural(hidden_p, 'должник', 'должника', 'должников')} у {hidden_e} "
                     f"{_plural(hidden_e, 'сотрудника', 'сотрудников', 'сотрудников')} — "
                     "не поместились, смотрите по сотруднику из сводки")
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 Обновить", callback_data=f"dg:o:{on_date}")
    kb.button(text="⬅️ К сводке", callback_data=f"dg:h:{on_date}")
    _app_button(kb, on_date)
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
