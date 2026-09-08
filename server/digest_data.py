"""
Слой ДАННЫХ дайджеста руководителя — без телеграма и без разметки.

У дайджеста два потребителя: чат бота (digest.py рисует текст + клавиатуру) и
вкладка руководителя в приложении (web.py отдаёт JSON). Считают оба ЗДЕСЬ —
поэтому бот и вкладка физически не могут разойтись в цифрах.

Правила, которые нельзя нарушать при правках:
  * aiogram и html сюда не импортируются (это прибито тестом): в данных нет
    разметки, экранирует каждый потребитель сам;
  * «снимок бота сильнее реестра» — исходы и поля визита берутся из доказанного
    db.bot_visit_snapshot, сырая колонка только фолбэк (легаси-снимки);
  * деньги по валютам НЕ складываются (сумы ЗК ≠ рубли Делимобиля);
  * телеграм-бюджеты (топ-3 исхода, 12 должников, 3600 символов, обрезка полей)
    живут в рисовальщике digest.py — данные отдаются целиком.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

import country_scope
import db
import debt
import photostore
from i18n import tf

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


def _v(contracts, key):
    """Первое непустое значение поля среди договоров человека."""
    return next((c[key] for c in contracts if c[key] not in (None, "")), None)


_money = debt.fmt    # единая логика денег/валюты


# Эмодзи для исходов визита — нужны обоим потребителям (кнопки бота и вкладка).
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
    Тот же приём, что в scoring.outcome() и card.py: снимок сильнее импорта.
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


def _outcome_rows(persons: list[dict]) -> list[dict]:
    """ВСЕ исходы (топ-3 — это телеграм-бюджет рисовальщика, а не правило)."""
    return [{"status": s, "n": n, "emoji": _st_emoji(s)} for s, n in _outcomes(persons)]


def _totals_units(persons: list[dict]) -> list[dict]:
    """Суммы по валютам раздельно: [{'unit','sum'}], только ненулевые."""
    acc: dict[str, int] = {}
    for p in persons:
        for x in p["totals"]:
            acc[x["unit"]] = acc.get(x["unit"], 0) + x["sum"]
    return [{"unit": u, "sum": s} for u, s in acc.items() if s]


def _persons_total(persons: list[dict]) -> str:
    """«132 900 ₽ · 11 550 000 сум» — раздельно по валютам, как и было."""
    return " · ".join(_money(x["sum"], x["unit"]) for x in _totals_units(persons))


def _contracts_of(persons: list[dict]) -> int:
    """Договоры отдельно от должников: дайджест считает ЛЮДЕЙ, и подписи
    обязаны говорить единицу явно (ловушка единиц из памяти проекта)."""
    return sum(p["n"] for p in persons)


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


def manager_scope(uid: int, *, is_owner: bool = False) -> dict | None:
    """Единый сборщик mgr-словаря для бота, автопушей и вкладки приложения.

    У владельца нет записи в managers — ему собирается синтетический
    «руководитель» со всеми сотрудниками реестра (состав — из той же точки
    выдачи прав db.manager_employees, а не отдельным запросом). Ключ
    'direction' обязателен: на .get по нему опираются сравнения scope."""
    mgr = db.get_manager(uid)
    if mgr:
        return mgr
    if is_owner:
        return {"telegram_id": uid, "name": "Владелец", "cities": [],
                "employees": db.manager_employees(uid, is_owner=True),
                "direction": ""}
    return None


def scope_label(mgr: dict) -> str:
    cities = [c for c in (mgr.get("cities") or []) if c]
    return " · ".join(cities) if cities else "все города"


def scope_countries_info(mgr: dict) -> list[dict]:
    """[{'code','name','flag'}] по странам scope — шапки сводок и вкладки."""
    codes = country_scope.scope_countries(mgr.get("cities") or [])
    return [{"code": c, **{k: country_scope.info(c)[k] for k in ("name", "flag")}}
            for c in codes]


def _public(obj):
    """Вычистить служебные ключи (с ведущим '_') перед отдачей в JSON:
    в '_persons' лежат sqlite3.Row, их нельзя отдавать наружу."""
    if isinstance(obj, dict):
        return {k: _public(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, list):
        return [_public(x) for x in obj]
    return obj


# --- строки по сотрудникам ---------------------------------------------------
def _employee_row(emp: str, on_date: str) -> dict:
    persons = db.persons_processed(emp, on_date)
    return {
        "employee": emp,
        "persons": len(persons),
        "contracts": _contracts_of(persons),
        "with_photo": sum(1 for p in persons
                          if photostore.day_photos(p["contracts"], on_date)),
        "outcomes": _outcome_rows(persons),
        "money": _totals_units(persons),
        "money_text": _persons_total(persons),
        "_persons": persons,
    }


def _overdue_rows(mgr: dict, on_date: str,
                  employees: list[str]) -> list[tuple[str, list[dict]]]:
    """[(employee, люди с просроченными обещаниями)] — только у кого они есть."""
    out = []
    for emp in employees:
        ps = db.overdue_promises(emp, on_date)
        if ps:
            out.append((emp, ps))
    return out


def overdue_by_employee(mgr: dict, on_date: str) -> list[tuple[str, list[dict]]]:
    return _overdue_rows(mgr, on_date, sorted_employees(mgr))


# --- ЭКРАН 1: сводка ---------------------------------------------------------
def summary_data(mgr: dict, on_date: str, employees: list[str] | None = None) -> dict:
    # Состав считается РОВНО ОДИН РАЗ и передаётся дальше: у направленца каждый
    # вызов sorted_employees — полный DISTINCT-скан clients.
    employees = employees if employees is not None else sorted_employees(mgr)
    rows = [{"ei": i, **_employee_row(emp, on_date)}
            for i, emp in enumerate(employees)]
    # порядок отображения: активные по убыванию числа кейсов, затем «нулевые»
    order = sorted(rows, key=lambda r: (-r["persons"], r["employee"].lower()))

    # У руководителя с обеими республиками сводка идёт по странам; у одно-страновых
    # руководителей — одна безымянная группа (плоский список, как и было).
    codes = country_scope.scope_countries(mgr.get("cities") or [])
    groups, display = [], list(order)
    if len(codes) > 1:
        mapping = country_scope.employee_countries(db.list_managers())
        split = country_scope.split_employees([r["employee"] for r in order], mapping)
        # Один и тот же ФИО может встретиться дважды (тёзки в разных городах) —
        # идём по очереди, а не по словарю, иначе часть строк потерялась бы.
        pool = list(order)
        display = []
        for code, emps in split:
            spec = country_scope.info(code) if code else None
            picked = []
            for name in emps:
                idx = next((i for i, r in enumerate(pool) if r["employee"] == name), None)
                if idx is not None:
                    picked.append(pool.pop(idx))
            if not picked:
                continue
            # У группы без определённой республики свой видимый заголовок —
            # хвостовая группа "rest" от неё отличается пустым title.
            groups.append({"code": code,
                           "title": spec["name"] if spec else "Без республики",
                           "flag": spec["flag"] if spec else "❔",
                           "employees": picked})
            display += picked
        if pool:
            groups.append({"code": None, "title": "", "flag": "", "employees": pool})
            display += pool
    else:
        groups = [{"code": None, "title": "", "flag": "", "employees": order}]

    all_persons = [p for r in rows for p in r["_persons"]]
    active = [r for r in rows if r["persons"]]
    return {
        "screen": "summary",
        "employees": employees,
        "groups": groups,
        "display": display,
        "totals": {
            "employees": len(rows),
            "active": len(active),
            "persons": sum(r["persons"] for r in rows),
            "contracts": sum(r["contracts"] for r in rows),
            "with_photo": sum(r["with_photo"] for r in rows),
            "money": _totals_units(all_persons),
            "money_text": _persons_total(all_persons),
            "overdue_promises": sum(
                len(ps) for _, ps in _overdue_rows(mgr, on_date, employees)),
        },
        "scope": scope_label(mgr),
        "countries": scope_countries_info(mgr),
        "manager_name": mgr.get("name") or "",
    }


# --- ЭКРАН 2: карточка сотрудника -------------------------------------------
_ACTIVITY_KEYS = ("visits_7d", "visits_30d", "markers_7d", "markers_30d",
                  "last_activity", "idle_days")


def employee_data(mgr: dict, on_date: str, employee: str,
                  employees: list[str] | None = None) -> dict | None:
    employees = employees if employees is not None else sorted_employees(mgr)
    if employee not in employees:
        return None   # единая точка проверки принадлежности
    row = _employee_row(employee, on_date)
    persons = row["_persons"]
    act = db.employee_activity(employee)
    # Белый список: db.employee_activity отдаёт строку activity_stats как есть,
    # а там telegram_id, kind и photos_total — наружу им нельзя.
    activity = {k: act.get(k) for k in _ACTIVITY_KEYS} if act else None
    cases = []
    for p in persons:
        st = _person_status(p)
        cases.append({
            "rep": p["rep"], "client": p["client"], "n": p["n"],
            "outcome": st, "emoji": _st_emoji(st),
            "money": _totals_units([p]), "money_text": _persons_total([p]),
            "photos": len(photostore.day_photos(p["contracts"], on_date)),
        })
    return {
        "screen": "employee",
        **row,
        "photos_day": sum(len(photostore.day_photos(p["contracts"], on_date))
                          for p in persons),
        "activity": activity,
        "cases": cases,
    }


# --- ЭКРАН 3: карточка клиента ----------------------------------------------
def _visit_pick(cs):
    """pick(key): первое непустое значение среди доказанных снимков; иначе —
    сырая колонка. Ветки «снимок сказал пусто» нет: db._bot_snapshot не
    записывает пустые значения (db.py:185-189), а чтение прогоняет тот же
    фильтр — состояния «ключ есть, но пуст» система создать не может."""
    snaps = []
    for row in cs:
        try:
            snaps.append(db.bot_visit_snapshot(row) or {})
        except Exception:                                  # noqa: BLE001
            snaps.append({})

    def pick(key):
        for s in snaps:
            v = s.get(key)
            if v not in (None, ""):
                return v
        return _v(cs, key)
    return pick


# Порядок печати известных полей визита — совпадает с нынешней карточкой.
_VISIT_ORDER = ("call_status", "agreed_payment", "contact_with",
                "who_at_address", "client_location", "purpose_rf")


def _visit_keys() -> list[str]:
    """Состав полей визита — из контракта, а не литералами: новые ключи формы
    (когда появятся в snapshot_keys) доедут до карточки сами."""
    known = set(_VISIT_ORDER) | {"visit_date", "visit_comment", "call_comment", "tips"}
    tail = [k for k in db.BOT_SNAPSHOT_KEYS if k not in known]
    return list(_VISIT_ORDER) + tail


def _person_payload(p: dict, on_date: str) -> dict:
    cs = p["contracts"]
    pick = _visit_pick(cs)

    def rawv(key):
        try:
            return _v(cs, key)
        except (KeyError, IndexError):
            return None

    # Блок визита: значение из снимка; расхождение с реестром не прячем, а
    # отдаём второй величиной — правка, сделанная выездником после «Сохранить
    # выезд», ложится в сырые колонки и в снимок не попадает (web.py пишет
    # update_person на каждом сохранении, а снимок — только на finish).
    visit = []
    for key in _visit_keys():
        val = pick(key)
        raw = rawv(key)
        # title — подпись поля: без неё карточка была столбиком голых значений
        # («3 лицо», «Ждём», «17512»), и что есть что — угадывай.
        visit.append({"key": key, "value": val, "title": tf(key, "ru"),
                      "registry": raw if (raw and val and raw != val) else None})

    comment = pick("visit_comment") or pick("call_comment")

    gv = db.last_visit_geo([c["contract_id"] for c in cs])
    geo = None
    if gv and gv["dist_m"] is not None:
        d = gv["dist_m"]
        far = gv["geo_far"] if "geo_far" in gv.keys() else None
        source = "stored"
        if far is None:
            far = d > db.geo_alert_m()
            source = "threshold"
        # Визит мог закрыть руководитель от имени сотрудника (act_as): тогда
        # гео-точка — телефон руководителя, и судить сотрудника по ней нельзя.
        tids = set(db.agent_tids(rawv("responsible") or ""))
        by_other = bool(tids) and gv["agent_id"] not in tids
        geo = {"dist_m": d,
               "pretty": f"{d / 1000:.1f} км" if d >= 1000 else f"{d} м",
               "far": bool(far), "source": source, "by_other": by_other}

    return {
        "screen": "person",
        "rep": p["rep"], "client": p["client"], "n": p["n"],
        "birth_date": rawv("birth_date"),
        "partners": list(p.get("partners") or []),
        "partner": ", ".join(p.get("partners") or []) or (rawv("partner") or ""),
        "phones": [ph for ph in (rawv("phone"), rawv("phone_main")) if ph],
        "address": rawv("address") or rawv("address_customer") or rawv("extra_address"),
        "money": _totals_units([p]),
        "money_text": _persons_total([p]),
        "outcome": _person_status(p),
        "visit": visit,
        "comment": comment,
        "tips": pick("tips"),
        "geo": geo,
        # Даты тоже через pick: пока ключа нет в снимке, отдаётся сырая колонка;
        # когда контракт добавит promise_date в snapshot_keys — снимок победит сам.
        "dates": {"visit_date": pick("visit_date"),
                  "reminder_date": pick("reminder_date"),
                  "promise_date": pick("promise_date"),
                  "meeting_date": pick("meeting_date")},
        "photos": len(photostore.person_photos(cs)),
    }


def person_data(mgr: dict, on_date: str, employee: str, rep: str,
                employees: list[str] | None = None) -> dict | None:
    employees = employees if employees is not None else sorted_employees(mgr)
    if employee not in employees:
        return None
    persons = db.persons_processed(employee, on_date)
    p = next((x for x in persons if x["rep"] == rep), None)
    if p is None:
        # Экран статистики ведёт в карточку и по ПРОШЛОМУ выезду: человек, к
        # которому с тех пор съездили ещё раз, в persons_processed того дня уже
        # не значится. Карточка показывает текущее состояние — снимок сильнее.
        rows = db.person_contracts(rep)
        if rows and (rows[0]["responsible"] or "") == employee:
            p = next(iter(db._group_persons(rows)), None)
    if p is None:
        return None
    return _person_payload(p, on_date)


# --- ЭКРАН 4: просроченные обещания -----------------------------------------
def promises_data(mgr: dict, on_date: str,
                  employees: list[str] | None = None) -> dict:
    employees = employees if employees is not None else sorted_employees(mgr)
    items = _overdue_rows(mgr, on_date, employees)
    everyone = [p for _, ps in items for p in ps]
    worked = sum(1 for p in everyone if p.get("visited_after_promise"))
    out_emps = []
    # самые тухлые сотрудники первыми — как в promises_view
    for emp, ps in sorted(items, key=lambda x: -len(x[1])):
        out_emps.append({
            "employee": emp,
            "persons": len(ps),
            "items": [{
                "rep": p.get("rep"), "client": p["client"],
                "promise_date": p["promise_date"],
                "promised_sum": p.get("promised_sum"),
                "days_overdue": p["days_overdue"],
                "last_visit": p.get("last_visit"),
                "visited_after_promise": bool(p.get("visited_after_promise")),
                "money": _totals_units([p]), "money_text": _persons_total([p]),
            } for p in ps],
        })
    return {
        "screen": "promises",
        "totals": {"persons": len(everyone), "worked": worked,
                   "untouched": len(everyone) - worked,
                   # обещания В БУДУЩЕМ: скоринг уже убрал этих должников из
                   # маршрута, а видимыми они становились только после срыва
                   "deferred": sum(db.deferred_promises_count(e, on_date)
                                   for e in employees)},
        "employees": out_emps,
        "scope": scope_label(mgr),
    }


# --- ЭКРАН 5: сводка за период ----------------------------------------------
def period_data(mgr: dict, on_date: str, days: int = 7,
                employees: list[str] | None = None) -> dict:
    days = days if days in (7, 30) else 7
    end = db._as_date(on_date) or date.today()
    start = end - timedelta(days=days - 1)
    d_from, d_to = start.isoformat(), end.isoformat()
    employees = employees if employees is not None else sorted_employees(mgr)

    rows = []
    for emp in employees:
        ps = db.persons_processed_range(emp, d_from, d_to)
        ph = sum(1 for p in ps if photostore.photos_between(p["contracts"], d_from, d_to))
        paid = [p for p in ps if _person_status(p) == "Оплачен"]
        rows.append({"employee": emp, "persons": len(ps),
                     "contracts": _contracts_of(ps),
                     "with_photo": ph, "paid": len(paid),
                     "outcomes": _outcome_rows(ps),
                     "money": _totals_units(ps), "money_text": _persons_total(ps),
                     "_persons": ps, "_paid": paid})

    all_ps = [p for r in rows for p in r["_persons"]]
    paid_all = [p for r in rows for p in r["_paid"]]
    return {
        "screen": "period",
        "days": days, "from": d_from, "to": d_to,
        "totals": {
            "persons": len(all_ps), "contracts": _contracts_of(all_ps),
            "paid": len(paid_all),
            "with_photo": sum(r["with_photo"] for r in rows),
            "money": _totals_units(all_ps), "money_text": _persons_total(all_ps),
            "money_paid": _totals_units(paid_all),
            "money_paid_text": _persons_total(paid_all),
        },
        "employees": sorted(rows, key=lambda r: (-r["persons"], r["employee"].lower())),
        "note": "человек попадает в период один раз — по дате последнего выезда",
    }


# --- ЭКРАН 6: статистика за произвольный период ------------------------------
# Считается по ЖУРНАЛУ выездов (db.visits), а не по последнему снимку: человек,
# к которому съездили 2-го и 9-го, виден в оба дня, каждый — со своим
# результатом. Исход дня берётся из записи журнала (текст web._save «Статус: …;
# Договорённость: …;»); для последнего выезда человека — из доказанного снимка,
# в котором живут и принятые офисные правки.
_MONTHS_SHORT = ["", "янв", "фев", "мар", "апр", "мая", "июн", "июл",
                 "авг", "сен", "окт", "ноя", "дек"]
_PAYLOAD_STATUS = re.compile(r"Статус:\s*(.*?);\s*Договорённость:\s*(.*?);", re.S)
STATS_PRESETS = ("today", "yesterday", "7d", "30d", "month", "custom")
_PRESET_DAYS = {"today": 1, "7d": 7, "30d": 30}
STATS_MAX_DAYS = 366
OUTCOME_PAID = "Оплачен"
OUTCOME_PROMISE = "Обещает оплатить"


def _short_date(d: date) -> str:
    return f"{d.day} {_MONTHS_SHORT[d.month]}"


def range_label(d_from: date, d_to: date) -> str:
    if d_from == d_to:
        return f"{d_from.day} {_MONTHS[d_from.month]}"
    if (d_from.year, d_from.month) == (d_to.year, d_to.month):
        return f"{d_from.day}–{d_to.day} {_MONTHS[d_from.month]}"
    if d_from.year != d_to.year:
        return f"{_short_date(d_from)} {d_from.year} – {_short_date(d_to)} {d_to.year}"
    return f"{_short_date(d_from)} – {_short_date(d_to)}"


def _month_shift(d: date, n: int) -> date:
    """Первое число месяца, отстоящего на n месяцев от месяца d."""
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def stats_range(preset: str | None, shift: int = 0, frm: str | None = None,
                to: str | None = None, today: date | None = None) -> dict:
    """Окно статистики. Даты считает СЕРВЕР: клиент шлёт только пресет и число
    сдвигов (или две строки из полей «с/по») и ни разу не строит дату сам —
    контейнер живёт по UTC, телефон руководителя по +3/+5.

    today/7d/30d — окно, прикнутое к сегодня, shift двигает его на свою длину
    (в будущее — нет). yesterday — синоним today со сдвигом −1. month —
    календарный месяц, shift в месяцах. custom — как ввели, не длиннее
    STATS_MAX_DAYS; сдвиг применяется сразу и в ответе обнуляется."""
    today = today or db._as_date(today_iso())
    preset = preset if preset in STATS_PRESETS else None
    lo = db._as_date(frm) if frm else None
    hi = db._as_date(to) if to else None
    try:
        shift = max(-1000, min(1000, int(shift or 0)))
    except (TypeError, ValueError):
        shift = 0
    if preset == "custom" or (preset is None and (lo or hi)):
        preset = "custom"
        lo = lo or hi or today
        hi = hi or lo
        if lo > hi:
            lo, hi = hi, lo
        if (hi - lo).days + 1 > STATS_MAX_DAYS:
            lo = hi - timedelta(days=STATS_MAX_DAYS - 1)
        n = (hi - lo).days + 1
        lo, hi = lo + timedelta(days=n * shift), hi + timedelta(days=n * shift)
        shift = 0
    elif preset == "month":
        shift = min(shift, 0)
        lo = _month_shift(today.replace(day=1), shift)
        hi = _month_shift(lo, 1) - timedelta(days=1)
    else:
        if preset == "yesterday":
            preset, shift = "today", shift - 1
        preset = preset or "today"
        shift = min(shift, 0)
        n = _PRESET_DAYS[preset]
        hi = today + timedelta(days=n * shift)
        lo = hi - timedelta(days=n - 1)
    days = (hi - lo).days + 1

    def window(k: int) -> tuple[date, date]:
        if preset == "month":
            f = _month_shift(lo, k)
            return f, _month_shift(f, 1) - timedelta(days=1)
        return lo + timedelta(days=days * k), hi + timedelta(days=days * k)

    p_lo, p_hi = window(-1)
    n_lo, n_hi = window(1)
    return {
        "preset": preset, "shift": shift,
        "from": lo.isoformat(), "to": hi.isoformat(), "days": days,
        "label": range_label(lo, hi), "today": today.isoformat(),
        "is_today": lo == hi == today,
        "prev": {"from": p_lo.isoformat(), "to": p_hi.isoformat()},
        "next": None if hi >= today else {"from": n_lo.isoformat(),
                                          "to": n_hi.isoformat()},
    }


def _range_days(rng: dict) -> list[dict]:
    lo, hi = db._as_date(rng["from"]), db._as_date(rng["to"])
    out = []
    d = lo
    while d <= hi:
        out.append({"date": d.isoformat(), "day": d.day, "label": _short_date(d),
                    "weekend": d.weekday() >= 5})
        d += timedelta(days=1)
    return out


def _payload_status(payload) -> tuple[str, str]:
    m = _PAYLOAD_STATUS.search(str(payload or ""))
    if not m:
        return "", ""
    return m.group(1).strip(), m.group(2).strip()


def _snapshot_day(p: dict) -> str:
    return next((str(c["bot_visit_at"] or "")[:10]
                 for c in p["contracts"] if c["bot_visit_at"]), "")


def _employee_stats(emp: str, d_from: str, d_to: str, *,
                    with_photos: bool = True) -> dict:
    """Строка сотрудника за окно. «_entries» — выезды по дням (человек-день),
    новые в конце; «_paid» — люди, чей последний исход в окне «Оплачен».

    with_photos=False пропускает подсчёт снимков: он ходит в файловую систему
    по каждому человеку, а окну «за всё время» это стоило бы сотен обращений
    ради колонки, которой в сводном отчёте нет."""
    persons = db._group_persons(db.contracts_of_responsible(emp))
    by_cid = {c["contract_id"]: p for p in persons for c in p["contracts"]}
    # (день, человек) → последняя запись журнала этого дня: повторное нажатие
    # «Сохранить выезд» рождает вторую строку, но это тот же выезд.
    entries: dict[tuple[str, str], dict] = {}
    for v in db.visits_for_employee(emp, d_from, d_to):
        p = by_cid.get(v["contract_id"])
        if p is None:
            continue
        status, agreed = _payload_status(v["payload"])
        entries[(v["created_at"], p["rep"])] = {
            "date": v["created_at"], "person": p, "status": status, "agreed": agreed}
    ordered = sorted(entries.values(), key=lambda e: (e["date"], e["person"]["client"]))
    for e in ordered:
        p = e["person"]
        if e["date"] == _snapshot_day(p) or not e["status"]:
            e["status"] = _person_status(p)
    last: dict[str, dict] = {}
    for e in ordered:
        last[e["person"]["rep"]] = e
    people = [e["person"] for e in last.values()]
    paid = [e["person"] for e in last.values() if e["status"] == OUTCOME_PAID]
    promised = [e for e in last.values() if e["status"] == OUTCOME_PROMISE]
    counts: dict[str, int] = {}
    for e in last.values():
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    by_day: dict[str, int] = {}
    for e in ordered:
        by_day[e["date"]] = by_day.get(e["date"], 0) + 1
    act = db.employee_activity(emp)
    return {
        "employee": emp,
        "visits": len(ordered), "persons": len(people),
        "contracts": _contracts_of(people),
        "paid": len(paid), "promised": len(promised),
        # Просрочка — на конец окна, но не из будущего: у пресета «Месяц» конец
        # 30-е число, и обещания «до 25-го» оказывались бы просроченными сегодня.
        "overdue": len(db.overdue_promises(emp, min(d_to, today_iso()))),
        "with_photo": (sum(1 for p in people
                           if photostore.photos_between(p["contracts"], d_from, d_to))
                       if with_photos else 0),
        "money": _totals_units(people), "money_text": _persons_total(people),
        "money_paid": _totals_units(paid), "money_paid_text": _persons_total(paid),
        "outcomes": [{"status": s, "n": n, "emoji": _st_emoji(s)}
                     for s, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))],
        "activity": {k: act.get(k) for k in _ACTIVITY_KEYS} if act else None,
        "portfolio": len(persons),
        "_by_day": by_day, "_entries": ordered, "_paid": paid,
    }


def _employee_country_index() -> dict[str, str]:
    """ФИО → tj/uz по стране кейсов (как у направлений). Сотрудник, чьи кейсы
    в обеих странах, остаётся в первой — статистика не задваивается."""
    out: dict[str, str] = {}
    for code in ("tj", "uz"):
        for emp in db.employees_by_direction(code):
            out.setdefault(emp, code)
    return out


def stats_data(mgr: dict, rng: dict, employees: list[str] | None = None) -> dict:
    employees = employees if employees is not None else sorted_employees(mgr)
    d_from, d_to = rng["from"], rng["to"]
    country = _employee_country_index()
    days = _range_days(rng)
    rows = []
    for emp in employees:
        r = _employee_stats(emp, d_from, d_to)
        r["country"] = country.get(emp)
        r["by_day"] = [r["_by_day"].get(x["date"], 0) for x in days]
        rows.append(r)
    for i, x in enumerate(days):
        x["visits"] = sum(r["by_day"][i] for r in rows)
    people = [e["person"] for r in rows for e in
              {e["person"]["rep"]: e for e in r["_entries"]}.values()]
    paid = [p for r in rows for p in r["_paid"]]
    order = [spec["code"] for spec in country_scope.COUNTRIES]
    codes = sorted({r["country"] for r in rows if r["country"]}, key=order.index)
    return {
        "screen": "stats",
        "range": rng,
        "totals": {
            "employees": len(rows),
            "active": sum(1 for r in rows if r["visits"]),
            "visits": sum(r["visits"] for r in rows),
            "persons": len(people),
            "contracts": _contracts_of(people),
            "paid": sum(r["paid"] for r in rows),
            "promised": sum(r["promised"] for r in rows),
            "overdue": sum(r["overdue"] for r in rows),
            "with_photo": sum(r["with_photo"] for r in rows),
            "money": _totals_units(people), "money_text": _persons_total(people),
            "money_paid": _totals_units(paid), "money_paid_text": _persons_total(paid),
        },
        "by_day": days,
        "employees": sorted(rows, key=lambda r: (-r["visits"], r["employee"].lower())),
        "countries": [{"code": c,
                       **{k: country_scope.info(c)[k] for k in ("name", "short", "flag")},
                       "employees": sum(1 for r in rows if r["country"] == c)}
                      for c in codes],
        "scope": scope_label(mgr),
        "manager_name": mgr.get("name") or "",
    }


def stats_employee_data(mgr: dict, rng: dict, employee: str,
                        employees: list[str] | None = None) -> dict | None:
    employees = employees if employees is not None else sorted_employees(mgr)
    if employee not in employees:
        return None
    r = _employee_stats(employee, rng["from"], rng["to"])
    days = _range_days(rng)
    for x in days:
        x["visits"] = r["_by_day"].get(x["date"], 0)
    cases = []
    for e in reversed(r["_entries"]):          # новые сверху
        p = e["person"]
        cases.append({
            "date": e["date"], "day_label": _short_date(db._as_date(e["date"])),
            "rep": p["rep"], "client": p["client"], "n": p["n"],
            "outcome": e["status"], "emoji": _st_emoji(e["status"]),
            "agreed": e["agreed"],
            "money": _totals_units([p]), "money_text": _persons_total([p]),
            "photos": len(photostore.day_photos(p["contracts"], e["date"])),
        })
    return {"screen": "emp", "range": rng, **r, "by_day": days, "cases": cases}


def stats_xlsx(mgr: dict, rng: dict, employees: list[str] | None = None) -> bytes:
    """Та же статистика книгой: «Сотрудники», «Выезды» (человек-день), «По дням»."""
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import Font

    data = stats_data(mgr, rng, employees)
    names = {c["code"]: c["name"] for c in data["countries"]}
    wb = Workbook()
    ws = wb.active
    ws.title = "Сотрудники"
    ws.append([f"Статистика выездов · {data['scope']} · {rng['label']} "
               f"({rng['from']} — {rng['to']})"])
    ws["A1"].font = Font(bold=True)
    ws.append([])
    head = ["Сотрудник", "Страна", "Выездов", "Должников", "Договоров", "Оплачен",
            "Обещает оплатить", "Просрочено обещаний", "С фото", "Долг",
            "Закрыто (оплачен)", "Исходы"]
    ws.append(head)
    for r in data["employees"]:
        ws.append([r["employee"], names.get(r["country"], ""), r["visits"], r["persons"],
                   r["contracts"], r["paid"], r["promised"], r["overdue"], r["with_photo"],
                   r["money_text"], r["money_paid_text"],
                   " · ".join(f"{o['status']} {o['n']}" for o in r["outcomes"])])
    t = data["totals"]
    ws.append(["Итого", "", t["visits"], t["persons"], t["contracts"], t["paid"],
               t["promised"], t["overdue"], t["with_photo"], t["money_text"],
               t["money_paid_text"], ""])
    for cell in ws[3]:
        cell.font = Font(bold=True)
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
    widths = [28, 12, 9, 10, 10, 9, 16, 18, 8, 22, 22, 60]
    for i, w in enumerate(widths):
        ws.column_dimensions[chr(ord("A") + i)].width = w

    ws2 = wb.create_sheet("Выезды")
    ws2.append(["Дата", "Сотрудник", "Клиент", "Договоров", "Результат",
                "Договорённость", "Долг"])
    for cell in ws2[1]:
        cell.font = Font(bold=True)
    for r in data["employees"]:
        for e in r["_entries"]:
            p = e["person"]
            ws2.append([e["date"], r["employee"], p["client"], p["n"], e["status"],
                        e["agreed"], _persons_total([p])])
    for col, w in zip("ABCDEFG", (12, 28, 32, 10, 22, 22, 18)):
        ws2.column_dimensions[col].width = w

    ws3 = wb.create_sheet("По дням")
    ws3.append(["Дата", "Выездов"] + [r["employee"] for r in data["employees"]])
    for cell in ws3[1]:
        cell.font = Font(bold=True)
    for i, x in enumerate(data["by_day"]):
        ws3.append([x["date"], x["visits"]] + [r["by_day"][i] for r in data["employees"]])
    ws3.column_dimensions["A"].width = 12

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


# --- Сводный отчёт руководителю: закреплено / сегодня / всего ----------------
# Просьба руководителей 08.09.2026: «общей таблицей по всем сотрудникам —
# закреплено, отработано сегодня, отработано всего по нарастающей», причём
# Таджикистан и Узбекистан вместе. Вместе они и так: направление «СНГ» видит
# обе республики, а колонка «Республика» разделяет их внутри одной таблицы.
DAILY_REPORT_COLUMNS = (
    ("employee", "Сотрудник", 28),
    ("country_name", "Республика", 14),
    ("assigned_persons", "Закреплено должников", 20),
    ("assigned_contracts", "Закреплено договоров", 20),
    ("today_persons", "Отработано сегодня", 18),
    ("today_visits", "Выездов сегодня", 16),
    ("today_paid", "Оплачен сегодня", 16),
    ("today_promised", "Обещают сегодня", 16),
    ("today_photo", "С фото сегодня", 15),
    ("total_persons", "Отработано всего", 17),
    ("total_visits", "Выездов всего", 14),
    ("overdue", "Просрочено обещаний", 19),
    ("first_day", "Первый выезд", 13),
    ("assigned_money", "Долг закреплённый", 20),
)
# «Всего» считается с этой даты — раньше боевых выездов не было, а вычитать
# демо-строки из отчёта руководителя не за чем.
TOTALS_SINCE = "2026-01-01"


def daily_report_data(mgr: dict, on_date: str,
                      employees: list[str] | None = None) -> dict:
    """Строки сводного отчёта. Порядок — по отработанному сегодня, затем по
    имени: сверху те, кто сегодня работал."""
    employees = employees if employees is not None else sorted_employees(mgr)
    country = _employee_country_index()
    rows = []
    for emp in employees:
        today = _employee_stats(emp, on_date, on_date)
        total = _employee_stats(emp, TOTALS_SINCE, on_date, with_photos=False)
        assigned = db._group_persons(db.contracts_of_responsible(emp))
        first = min((e["date"] for e in total["_entries"]), default="")
        code = country.get(emp)
        rows.append({
            "employee": emp,
            "country": code,
            "country_name": country_scope.info(code)["name"] if code else "",
            "assigned_persons": len(assigned),
            "assigned_contracts": sum(p["n"] for p in assigned),
            "assigned_money": _persons_total(assigned),
            "today_persons": today["persons"], "today_visits": today["visits"],
            "today_paid": today["paid"], "today_promised": today["promised"],
            "today_photo": today["with_photo"],
            "total_persons": total["persons"], "total_visits": total["visits"],
            "total_paid": total["paid"],
            "overdue": today["overdue"],
            "first_day": first,
            "outcomes": today["outcomes"],
        })
    rows.sort(key=lambda r: (-r["today_persons"], -r["total_persons"],
                             r["employee"].lower()))
    keys = ("assigned_persons", "assigned_contracts", "today_persons", "today_visits",
            "today_paid", "today_promised", "today_photo", "total_persons",
            "total_visits", "total_paid", "overdue")
    totals = {k: sum(r[k] for r in rows) for k in keys}
    totals["employees"] = len(rows)
    totals["active"] = sum(1 for r in rows if r["today_persons"])
    return {"screen": "daily_report", "date": on_date, "rows": rows,
            "totals": totals, "scope": scope_label(mgr),
            "manager_name": mgr.get("name") or "",
            "countries": [{"code": c, **{k: country_scope.info(c)[k]
                                         for k in ("name", "short", "flag")},
                           "employees": sum(1 for r in rows if r["country"] == c)}
                          for c in sorted({r["country"] for r in rows if r["country"]},
                                          key=lambda x: [s["code"] for s in
                                                         country_scope.COUNTRIES].index(x))]}


def daily_report_caption(data: dict) -> str:
    """Подпись к файлу: 1024 символа — предел Telegram, поэтому только итог."""
    t = data["totals"]
    parts = [f"📊 <b>Сводка за {human_date(data['date'])}</b>"]
    if data["scope"]:
        parts.append(data["scope"])
    parts.append(f"👥 Команда: {t['employees']} · сегодня работали: {t['active']}")
    parts.append(f"🗓 Отработано сегодня: <b>{t['today_persons']}</b> "
                 f"должников за {t['today_visits']} выездов")
    parts.append(f"📈 Всего с начала работы: <b>{t['total_persons']}</b> должников "
                 f"({t['total_visits']} выездов)")
    parts.append(f"📌 Закреплено: {t['assigned_persons']} должников "
                 f"({t['assigned_contracts']} договоров)")
    if t["overdue"]:
        parts.append(f"⏰ Просрочено обещаний: {t['overdue']}")
    parts.append("Таблица по каждому сотруднику — в файле; "
                 "разбор по дням и карточки — во вкладке «Сводка» приложения.")
    return "\n".join(parts)


def daily_report_xlsx(mgr: dict, on_date: str,
                      employees: list[str] | None = None,
                      data: dict | None = None) -> bytes:
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    data = data if data is not None else daily_report_data(mgr, on_date, employees)
    wb = Workbook()
    ws = wb.active
    ws.title = "Сводка"
    ws.append([f"Выезды · сводка на {human_date(on_date)} ({on_date})"
               + (f" · {data['scope']}" if data["scope"] else "")])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([])
    ws.append([title for _key, title, _w in DAILY_REPORT_COLUMNS])
    for cell in ws[3]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    for r in data["rows"]:
        ws.append([r.get(key, "") for key, _t, _w in DAILY_REPORT_COLUMNS])
    t = data["totals"]
    ws.append(["Итого", ""] + [t.get(key, "") for key, _t, _w in DAILY_REPORT_COLUMNS[2:]])
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
    for i, (_key, _title, width) in enumerate(DAILY_REPORT_COLUMNS):
        ws.column_dimensions[chr(ord("A") + i)].width = width
    ws.freeze_panes = "C4"

    ws2 = wb.create_sheet("Исходы за день")
    ws2.append(["Сотрудник", "Исход", "Должников"])
    for cell in ws2[1]:
        cell.font = Font(bold=True)
    for r in data["rows"]:
        for o in r["outcomes"]:
            ws2.append([r["employee"], o["status"], o["n"]])
    for col, w in zip("ABC", (28, 28, 12)):
        ws2.column_dimensions[col].width = w

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def daily_report_filename(on_date: str) -> str:
    return f"Сводка выездов {on_date}.xlsx"


# --- Экран «Сегодня» в пульте --------------------------------------------------
# Пульт показывал состояние склада (сколько загружено), а не работы (сколько
# сделано). Этот срез — про день: выезды сегодня/вчера/за неделю, столбики,
# кто молчит, дошли ли карточки, когда обогащали.
def today_overview(on_date: str | None = None, days: int = 14) -> dict:
    on_date = on_date or today_iso()
    end = db._as_date(on_date)
    start = end - timedelta(days=days - 1)
    employees = [e["employee"] for e in db.employees_in_registry()]
    # выезды по дням — человек-день по всей базе, одним запросом
    with db.connect() as conn:
        per_day = {r["d"]: r["n"] for r in conn.execute(
            "SELECT substr(created_at,1,10) d, COUNT(DISTINCT contract_id) n FROM visits "
            "WHERE substr(created_at,1,10) BETWEEN ? AND ? GROUP BY d",
            (start.isoformat(), end.isoformat()))}
        reasons = {(r["reason"] or "(нет)"): r["n"] for r in conn.execute(
            "SELECT unassigned_reason reason, COUNT(*) n FROM clients "
            "WHERE IFNULL(TRIM(responsible),'')='' GROUP BY unassigned_reason")}
        ledger = conn.execute(
            "SELECT COUNT(*) cells, COUNT(DISTINCT contract_id) contracts, "
            "MAX(delivered_at) last FROM enrichment_ledger").fetchone()
        total_clients = conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
        visited = conn.execute(
            "SELECT COUNT(*) FROM clients WHERE IFNULL(TRIM(bot_visit_at),'')<>''").fetchone()[0]
    series = []
    d = start
    while d <= end:
        iso = d.isoformat()
        series.append({"date": iso, "day": d.day, "label": _short_date(d),
                       "weekend": d.weekday() >= 5, "visits": per_day.get(iso, 0)})
        d += timedelta(days=1)
    yesterday = (end - timedelta(days=1)).isoformat()
    week_from = (end - timedelta(days=6)).isoformat()

    rows, silent = [], []
    activity = {a["employee"]: a for a in db.activity_stats()}
    country = _employee_country_index()
    for emp in employees:
        r = _employee_stats(emp, on_date, on_date)
        assigned = db._group_persons(db.contracts_of_responsible(emp))
        act = activity.get(emp) or {}
        row = {"employee": emp, "country": country.get(emp),
               "today": r["visits"], "paid": r["paid"],
               "promised": r["promised"], "with_photo": r["with_photo"],
               "overdue": r["overdue"], "portfolio": len(assigned),
               "last_activity": act.get("last_activity"),
               "idle_days": act.get("idle_days"),
               "entry": act.get("entry", "none"), "bound": bool(act)}
        rows.append(row)
        if r["visits"] == 0 and assigned:
            silent.append(row)
    silent.sort(key=lambda x: (x["idle_days"] is None, -(x["idle_days"] or 0), x["employee"]))
    delivery = db.push_summary(on_date)
    by_kind = delivery.get("by_kind") or {}

    def kind_counts(kind):
        st = by_kind.get(kind) or {}
        return {"sent": st.get(db.PUSH_SENT, 0),
                "failed": st.get(db.PUSH_FAILED, 0) + st.get(db.PUSH_BLOCKED, 0),
                "skipped": sum(v for k, v in st.items() if str(k).startswith("skipped"))}

    managers = db.list_managers()
    # Республики — по стране кейсов, как у направлений. Старая «Сводка» брала
    # их из списков руководителей, а у направленцев списки пусты — и карточка
    # показывала нули при 29 работающих людях.
    countries = []
    for spec in country_scope.COUNTRIES:
        mine = [x for x in rows if x["country"] == spec["code"]]
        if not mine:
            continue
        countries.append({
            "code": spec["code"], "name": spec["name"], "flag": spec["flag"],
            "employees": len(mine), "active": sum(1 for x in mine if x["today"]),
            "visits_today": sum(x["today"] for x in mine),
            "paid_today": sum(x["paid"] for x in mine),
            "promised_today": sum(x["promised"] for x in mine),
            "portfolio": sum(x["portfolio"] for x in mine),
            "overdue": sum(x["overdue"] for x in mine),
        })
    return {
        "date": on_date, "generated_at": datetime.now().isoformat(timespec="seconds"),
        "days": series,
        "countries": countries,
        "kpi": {
            "visits_today": per_day.get(on_date, 0),
            "visits_yesterday": per_day.get(yesterday, 0),
            "visits_7d": sum(x["visits"] for x in series if x["date"] >= week_from),
            "employees_total": len(rows),
            "employees_active": sum(1 for x in rows if x["today"]),
            "paid_today": sum(x["paid"] for x in rows),
            "promised_today": sum(x["promised"] for x in rows),
            "with_photo_today": sum(x["with_photo"] for x in rows),
            "overdue": sum(x["overdue"] for x in rows),
        },
        "rows": rows,
        "silent": silent,
        "delivery": {"visit": kind_counts(db.PUSH_VISIT),
                     "digest": kind_counts(db.PUSH_DIGEST),
                     "promise": kind_counts(db.PUSH_PROMISE),
                     "report_chat": kind_counts(db.PUSH_REPORT_CHAT),
                     "blocked": delivery.get("blocked") or [],
                     "last_error": delivery.get("last_error")},
        "enrichment": {"ledger_cells": ledger["cells"] or 0,
                       "ledger_contracts": ledger["contracts"] or 0,
                       "last_delivered_at": ledger["last"] or ""},
        "registry": {"clients": total_clients, "visited": visited,
                     "assigned": total_clients - sum(reasons.values()),
                     "unassigned": reasons, "employees": len(employees)},
        "managers": {"total": len(managers),
                     "no_dm": [m["name"] for m in managers if not m.get("can_dm")]},
    }
