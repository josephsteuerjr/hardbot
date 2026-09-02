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

from datetime import date, timedelta

import country_scope
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
        visit.append({"key": key, "value": val,
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
