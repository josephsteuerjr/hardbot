"""
Дайджест руководителя: сводка «кто кого обработал сегодня» + разбор по сотрудникам
и карточкам клиентов. Механика бота (без миниаппа): сообщение со сводкой, под ним
кнопки-ФИО сотрудников → список клиентов → карточка (заполненное по визиту + референс).

Три экрана строятся здесь (текст + инлайн-клавиатура), навигация — правкой того же
сообщения. Источник данных — db.persons_processed (подтверждённые визиты по bot_visit_at).
Всё на русском: руководители читают по-русски, значения статусов уже каноничные.

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
from datetime import date, datetime, timedelta

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

import db
import country_scope
import debt
import maplink
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
    """Исход по человеку: сначала ДОКАЗАННЫЙ снимок визита бота, потом реестр.

    Порядок был обратный, и дайджест врал в обе стороны. Форма выезда пишет
    результат в `call_status`, а `overall_result` заполняет только импорт мастера.
    Поэтому выезд с реальным сбором показывался как «Обещает оплатить», сводка
    недели рапортовала «✅ Оплачен: 0 (0%)», а кейс, где в файле стоит «Оплачен»,
    а выездник закрыл «Нет контакта», прибавлял чужой долг к «закрыто».

    Мгновенная отбивка о визите при этом брала исход правильно — продукт
    противоречил сам себе в пределах одного дня. Тот же приём, что в
    scoring.outcome() и card.py: снимок сильнее импорта.
    """
    for row in p["contracts"]:
        try:
            snap = db.bot_visit_snapshot(row) or {}
        except Exception:                                  # noqa: BLE001
            snap = {}
        v = snap.get("call_status")
        if v not in (None, ""):
            return v
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
    """Детерминированный порядок для стабильной индексации в callback.

    Состав спрашиваем у db.manager_employees, а НЕ у mgr["employees"]. У
    руководителя направления сохранённый список пуст намеренно — состав считается
    живьём по стране кейса. Пока дайджест читал сохранённое поле, все четыре
    руководителя направлений видели пустую сводку: «Команда: 0 · обработано: 0»
    при восемнадцати закрытых выездах у одного только Уткирова.

    Точечный руководитель (список из пульта) не страдает: manager_employees
    возвращает ему тот же явный список.

    ВЛАДЕЛЕЦ — особый случай, и на нём я эту правку и сломал. У него нет записи
    в managers: дайджест собирает ему синтетического «руководителя» со всеми
    сотрудниками реестра прямо в mgr["employees"]. Запрос в БД по его id
    возвращает пусто, и владелец увидел «Команда: 0 · обработано: 0» при живой
    работе у пятерых. Поэтому пустой ответ БД — повод посмотреть в сам объект,
    а не считать, что сотрудников нет.
    """
    live = db.manager_employees(mgr["telegram_id"])
    if not live:
        live = [e for e in (mgr.get("employees") or []) if str(e).strip()]
    return sorted(set(live))


def scope_label(mgr: dict) -> str:
    cities = [c for c in (mgr.get("cities") or []) if c]
    return " · ".join(cities) if cities else "все города"


def scope_heading(mgr: dict) -> str:
    """Явная республика + города для раздельных руководительских сводок.

    У руководителя с обеими республиками показываем обе, а не молчим: раньше
    смешанный scope схлопывался в один город, и заказчик разделения РТ/РУ видел
    сводку вообще без признака страны."""
    codes = country_scope.scope_countries(mgr.get("cities") or [])
    place = scope_label(mgr)
    if not codes:
        return f"🏙 {_e(place)}"
    specs = [country_scope.info(c) for c in codes]
    label = " + ".join(f"{s['flag']} <b>{s['name']}</b>" for s in specs)
    return f"{label} · 🏙 {_e(place)}"


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
            scope_heading(mgr),
            f"👥 Команда: {len(rows)} · активны: {len(active)} · "
            f"обработано: <b>{total_clients}</b>"]
    if total_clients:
        head.append(f"📸 С фото: {sum(cov.values())} из {total_clients}")
    if grand_total:
        head.append(f"💰 Сумма по кейсам дня: {grand_total}")
    head.append("━━━━━━━━━━━━━━")

    # порядок отображения: активные по убыванию числа кейсов, затем «нулевые»
    order = sorted(rows, key=lambda x: (-len(x[2]), x[1].lower()))

    def _emp_lines(items):
        out = []
        for _ei, emp, ps in items:
            if ps:
                oc = _outcomes(ps)
                brief = " · ".join(f"{_st_emoji(s)} {_e(s)} {n}" for s, n in oc[:3])
                out.append(f"👤 <b>{_e(emp)}</b> — {len(ps)} "
                           f"{_plural(len(ps), 'кейс', 'кейса', 'кейсов')}"
                           f" · 📸 {cov[emp]}/{len(ps)}")
                if brief:
                    out.append(f"    {brief}")
            else:
                out.append(f"▫️ <s>{_e(emp)}</s> — 0")
        return out

    # У руководителя с обеими республиками сводка раньше шла общим списком — тот, кто
    # просил разделения РТ/РУ, его как раз и не видел. Теперь при смешанном scope
    # сотрудники разложены по странам; у одно-страновых руководителей всё как было.
    codes = country_scope.scope_countries(mgr.get("cities") or [])
    body, display = [], order
    if len(codes) > 1:
        # Таблица руководителей крошечная (единицы строк), и читаем её только когда
        # scope действительно смешанный — на одно-страновых сводках запроса нет вовсе.
        mapping = country_scope.employee_countries(db.list_managers())
        groups = country_scope.split_employees([r[1] for r in order], mapping)
        # Один и тот же ФИО может встретиться у руководителя дважды (тёзки в разных
        # городах) — идём по очереди, а не по словарю, иначе часть строк потерялась бы.
        pool = list(order)
        display = []
        for code, emps in groups:
            spec = country_scope.info(code) if code else None
            title = (f"{spec['flag']} <b>{spec['name']}</b>" if spec
                     else "❔ <b>Без республики</b>")
            picked = []
            for name in emps:
                idx = next((i for i, r in enumerate(pool) if r[1] == name), None)
                if idx is not None:
                    picked.append(pool.pop(idx))
            if not picked:
                continue
            body.append(f"{title} — {len(picked)} "
                        f"{_plural(len(picked), 'сотрудник', 'сотрудника', 'сотрудников')}")
            body += _emp_lines(picked)
            body.append("")
            display += picked
        display += pool                       # никого не потеряли
        body += _emp_lines(pool)
        while body and not body[-1]:
            body.pop()
    else:
        body = _emp_lines(order)
    if not active:
        body = ["Сегодня выездов пока нет."]

    tail = ["━━━━━━━━━━━━━━", "Кнопки ниже — детали и кейсы сотрудника."]

    kb = InlineKeyboardBuilder()
    # Кнопки идут в том же порядке, что и строки текста: иначе у руководителя с обеими
    # республиками список сгруппирован по странам, а кнопки — вперемешку.
    for ei, emp, ps in display:
        mark = "👤" if ps else "▫️"
        kb.button(text=f"{mark} {_short(emp)} · {len(ps)}",
                  callback_data=f"dg:e:{on_date}:{ei}")
    n_over = sum(len(ps) for _, ps in overdue_by_employee(mgr, on_date))
    if n_over:
        kb.button(text=f"⏰ Обещания просрочены: {n_over}", callback_data=f"dg:o:{on_date}")
    kb.button(text="📅 Неделя", callback_data=f"dg:w:{on_date}:7")
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
    base = (os.getenv("MINIAPP_URL", "") or "").strip()
    if base:   # карта дня — отдельная веб-страница по подписанной ссылке (48 ч)
        kb.button(text="🗺 Карта дня",
                  url=f"{base.rstrip('/')}/manager/map?{maplink.sign(emp, on_date)}")
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

    # Телеграм не принимает сообщение длиннее 4096 символов, а комментарий выезда
    # ничем не ограничен: длинный текст из реестра или пять-шесть диктовок подряд
    # переваливают порог. Раньше кейс с самым подробным отчётом — ровно тот, что
    # откроют на показе, — просто переставал открываться: ошибку глотал except.
    _CARD_FIELD_LIMIT = 900

    def add(emoji, label, key, prefix=""):
        v = val(key)
        if not v:
            return
        if len(v) > _CARD_FIELD_LIMIT:
            v = v[:_CARD_FIELD_LIMIT].rstrip() + "… (обрезано)"
        body.append(f"{emoji} {label}{_e(v)}" if not prefix else f"{emoji} {label}{prefix}{_e(v)}")

    # Итог выезда — из доказанного снимка (как и всюду выше). Печатать здесь
    # «overall_result» значило бы поставить рядом две противоречащие строки:
    # офисную колонку и настоящий исход, который выездник закрыл сегодня.
    outcome = _person_status(p)
    if outcome and outcome != "—":
        body.append(f"✅ {_e(outcome)}")
    add("📞", "Звонок: ", "call_status")
    add("🤝", "", "agreed_payment")
    add("👥", "Контакт: ", "contact_with")
    add("🏠", "На адресе: ", "who_at_address")
    add("🌍", "", "client_location")
    add("🎯", "Цель в РФ: ", "purpose_rf")
    tips = val("tips")
    if tips:
        body.append(f"📇 Доп. адрес/номер: {_e(tips)}")
    comment = val("visit_comment") or val("call_comment")   # выездник пишет в visit_comment
    if comment:
        body.append(f"💬 {_e(comment)}")

    # гео-контроль: где реально был телефон при закрытии последнего визита
    gv = db.last_visit_geo([c["contract_id"] for c in cs])
    if gv and gv["dist_m"] is not None:
        d = gv["dist_m"]
        pretty = f"{d / 1000:.1f} км" if d >= 1000 else f"{d} м"
        # Вердикт берём СОХРАНЁННЫЙ на момент визита, а не пересчитываем сейчас:
        # точность точки должника меняется после каждого геокодинга, и пересчёт
        # задним числом переписывал бы историю — честно закрытый вчера визит
        # сегодня становился бы нарушением. Старые записи (geo_far нет) судим
        # по прежнему общему порогу, чтобы накопленное не поехало.
        far = gv["geo_far"] if "geo_far" in gv.keys() else None
        if far is None:
            far = d > db.geo_alert_m()
        body.append(f"⚠️ Визит закрыт в {pretty} от адреса!" if far
                    else f"📍 Точка закрытия: {pretty} от адреса")

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


# --- ЭКРАН 5: сводка за период (неделя/месяц) --------------------------------
def period_view(mgr: dict, on_date: str,
                days: int = 7) -> tuple[str, InlineKeyboardMarkup]:
    """Динамика команды за N дней до on_date включительно: кейсы, конверсия
    в «Оплачен», фото-дисциплина, деньги. Кнопки 7/30 переключают период."""
    days = days if days in (7, 30) else 7
    end = db._as_date(on_date) or date.today()
    start = end - timedelta(days=days - 1)
    d_from, d_to = start.isoformat(), end.isoformat()

    rows = []   # (emp, persons, с фото, оплачено)
    for emp in sorted_employees(mgr):
        ps = db.persons_processed_range(emp, d_from, d_to)
        ph = sum(1 for p in ps if photostore.photos_between(p["contracts"], d_from, d_to))
        paid = [p for p in ps if _person_status(p) == "Оплачен"]
        rows.append((emp, ps, ph, paid))

    total = sum(len(ps) for _, ps, _, _ in rows)
    total_ph = sum(ph for _, _, ph, _ in rows)
    paid_all = [p for _, _, _, paid in rows for p in paid]
    money_all = _persons_total([p for _, ps, _, _ in rows for p in ps])
    money_paid = _persons_total(paid_all)

    def pct(a, b):
        return f" ({round(100 * a / b)}%)" if b else ""

    lines = [f"📅 <b>Сводка за {days} дн · {human_date(d_from)} — {human_date(d_to)}</b>",
             scope_heading(mgr),
             f"👥 Обработано: <b>{total}</b>"]
    if total:
        lines.append(f"✅ Оплачен: {len(paid_all)}{pct(len(paid_all), total)}"
                     + (f" · закрыто: {money_paid}" if money_paid else ""))
        lines.append(f"📸 С фото: {total_ph} из {total}{pct(total_ph, total)}")
    if money_all:
        lines.append(f"💰 Долг по обработанным: {money_all}")
    lines.append("━━━━━━━━━━━━━━")

    order = sorted(rows, key=lambda x: (-len(x[1]), x[0].lower()))
    for emp, ps, ph, paid in order:
        if not ps:
            lines.append(f"▫️ <s>{_e(emp)}</s> — 0")
            continue
        lines.append(f"👤 <b>{_e(emp)}</b> — {len(ps)} "
                     f"{_plural(len(ps), 'кейс', 'кейса', 'кейсов')}"
                     f" · ✅ {len(paid)}{pct(len(paid), len(ps))} · 📸 {ph}/{len(ps)}")
        brief = " · ".join(f"{_st_emoji(s)} {_e(s)} {n}"
                           for s, n in _outcomes(ps)[:3])
        if brief:
            lines.append(f"    {brief}")
    if not total:
        lines.append("За период выездов нет.")

    kb = InlineKeyboardBuilder()
    other = 30 if days == 7 else 7
    kb.button(text=f"📅 За {other} дней", callback_data=f"dg:w:{on_date}:{other}")
    kb.button(text="🔄 Обновить", callback_data=f"dg:w:{on_date}:{days}")
    kb.button(text="⬅️ К сводке", callback_data=f"dg:h:{on_date}")
    kb.adjust(1)
    return "\n".join(lines), kb.as_markup()


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
    everyone = [p for _, ps in items for p in ps]
    worked = sum(1 for p in everyone if p.get("visited_after_promise"))
    lines = [f"⏰ <b>Просроченные обещания · {human_date(on_date)}</b>",
             f"🏙 {_e(scope_label(mgr))} · всего: <b>{total}</b>"]
    if total:
        # Отвечает на вопрос «обновлена информация или нет»: сколько из списка
        # реально трогали после обещания, а сколько лежит нетронутым.
        lines.append(f"🟢 отработано после обещания: {worked} · "
                     f"⚪ не тронуто: {total - worked}")
    if not items:
        lines.append("━━━━━━━━━━━━━━")
        lines.append("Просроченных обещаний нет 🎉")
    # Телеграм рубит сообщение на 4096 символах, а список просрочек сам не рассасывается:
    # он копится, пока обещание не закроют. Держим бюджет и честно говорим, что урезали, —
    # молча оборванный список руководитель принял бы за полный.
    budget, hidden_p, hidden_e = 3600, 0, 0
    items = sorted(items, key=lambda x: -len(x[1]))   # самые тухлые сотрудники первыми
    for emp, ps in items:
        block = ["━━━━━━━━━━━━━━", f"👤 <b>{_e(emp)}</b> — {len(ps)}"]
        for p in ps[:12]:
            money = _persons_total([p])
            if p.get("visited_after_promise"):
                fresh = f" · 🟢 визит {human_date(p['last_visit'])}"
            elif p.get("last_visit"):
                fresh = f" · 🟡 визит {human_date(p['last_visit'])}, до обещания"
            else:
                fresh = " · ⚪ визитов не было"
            block.append(f"  🔴 {_e(p['client'])} — обещал {human_date(p['promise_date'])}, "
                         f"{p['days_overdue']} дн назад" + (f" · {money}" if money else "")
                         + fresh)
        if len(ps) > 12:
            block.append(f"  …и ещё {len(ps) - 12}")
        # Меряем ГОТОВЫЙ блок: у одного сотрудника это до 14 строк, и проверка «до»
        # позволяла перевалить лимит последним же блоком.
        if sum(len(x) + 1 for x in lines + block) > budget:
            hidden_e += 1
            hidden_p += len(ps)
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
