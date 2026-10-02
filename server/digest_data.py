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
import evidence
import funnel
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


def _merge_units(chunks) -> list[dict]:
    """Сложить несколько наборов [{'unit','sum'}] в один, по валютам."""
    acc: dict[str, int] = {}
    for chunk in chunks:
        for x in chunk or ():
            acc[x["unit"]] = acc.get(x["unit"], 0) + x["sum"]
    return [{"unit": u, "sum": v} for u, v in acc.items() if v]


def _units_text(units: list[dict]) -> str:
    """«132 900 ₽ · 11 550 000 сум» — из уже посчитанных сумм по валютам."""
    return " · ".join(_money(x["sum"], x["unit"]) for x in units or ())


def _units_num(units: list[dict]) -> tuple[int | None, str]:
    """(число, валюта), если деньги одной валюты; иначе (None, '').

    Смешанные валюты числом не бывают (см. _totals_units) — «132 900 ₽ ·
    11 млн сум» остаётся текстом. А одна валюта кладётся в книгу ЧИСЛОМ:
    текст «1 508 299 ₽» SUM не складывает, и руководитель складывает колонку
    руками (жалоба Альберта 20.09.2026)."""
    units = list(units or ())
    if not units:
        return None, ""
    labels = {str(x.get("unit") or "") for x in units}
    if len(labels) > 1:
        return None, ""
    return sum(int(x.get("sum") or 0) for x in units), labels.pop()


def _money_fmt(unit: str) -> str:
    """Формат ячейки денег: «47 199 614 ₽» как во всём боте, но валюта — в
    формате, а не внутри строки."""
    return f'#,##0" {unit}"' if unit else "#,##0"


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


def _covers(mgr: dict, employee: str, countries: set[str],
            office_index: dict[str, str]) -> bool:
    """Входит ли сотрудник в охват руководителя, если судить не по портфелю
    (его может уже не быть), а по его выездам: страна выездов, офис, имя."""
    if db.get_manager(mgr["telegram_id"]) is None:
        return True                       # владелец без записи — видит всех
    direction = (mgr.get("direction") or "").strip()
    if direction:
        return direction == "all" or direction in countries
    key = db._name_key(employee)
    if any(db._name_key(e) == key for e in mgr.get("employees") or []):
        return True
    offices = {(c or "").strip().casefold() for c in mgr.get("office_cities") or []}
    return bool(offices) and \
        db.office_of(employee, office_index).strip().casefold() in offices


def window_team(mgr: dict, team: list[str], d_from: str, d_to: str) -> list[str]:
    """Команда ЗА ОКНО: живой состав плюс те, кто в этом окне выезжал.

    Живой состав строится по реестру — у кого сейчас есть договоры. С кого в
    конце месяца сняли весь портфель, из него выпадает, и это правильно: в
    сегодняшней сводке человеку без договоров делать нечего. Но выезды,
    которые он сделал до перезакрепления, — его работа, и в статистике того
    окна он обязан остаться со своими цифрами, а не исчезнуть вместе с ними.

    team — состав ДОСТУПА (sorted_employees): поимённый список и офис живут,
    пока их не поправят руками. Здесь из него остаются только те, у кого в
    реестре есть договоры, и в написании реестра (под ним портфели и выезды).
    Сам состав доступа не режем: по нему руководитель находит фото
    откреплённых договоров своих людей и выдаёт им вход. Пустой реестр (до
    первого импорта) не фильтрует ничего — иначе пустым стал бы и экран."""
    registry = {db._name_key(r["employee"]): r["employee"]
                for r in db.employees_in_registry()}
    if registry:
        team = [registry[db._name_key(e)] for e in team
                if db._name_key(e) in registry]
    out = dict.fromkeys(team)
    authors = db.visit_authors(d_from, d_to)
    extra = [e for e in authors if e not in out]
    if extra:
        index = db.employee_office_index()
        for emp in extra:
            if _covers(mgr, emp, authors[emp], index):
                out[emp] = None
    return sorted(out)


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
    """[(employee, люди с просроченными обещаниями)] — только у кого они есть.

    Окно и источник — один на весь продукт (db.promise_window): рассылка,
    кнопка в дайджесте, вкладка руководителя и счётчик в сводке обязаны
    показывать ОДНО число, иначе «просрочено 54» в сводке и три строки в
    списке — это баг в глазах руководителя, а не фильтр."""
    max_days, agent_only = db.promise_window()
    out = []
    for emp in employees:
        ps = db.overdue_promises(emp, on_date, max_days=max_days,
                                 agent_only=agent_only)
        if ps:
            out.append((emp, ps))
    return out


def _promise_window_kwargs() -> dict:
    max_days, agent_only = db.promise_window()
    return {"max_days": max_days, "agent_only": agent_only}


def overdue_by_employee(mgr: dict, on_date: str) -> list[tuple[str, list[dict]]]:
    return _overdue_rows(mgr, on_date, sorted_employees(mgr))


# --- ЭКРАН 1: сводка ---------------------------------------------------------
def summary_data(mgr: dict, on_date: str, employees: list[str] | None = None) -> dict:
    # Состав считается РОВНО ОДИН РАЗ и передаётся дальше: у направленца каждый
    # вызов sorted_employees — полный DISTINCT-скан clients. По умолчанию —
    # состав ПОКАЗА за этот день (window_team), как у бота и у вкладки.
    if employees is None:
        employees = window_team(mgr, sorted_employees(mgr), on_date, on_date)
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

    # Снимки — В КАРТОЧКЕ. Раньше они жили только на отдельном экране «Фото по
    # КД», и руководитель, открыв отчёт по клиенту, которого сам же просил
    # сфотографировать, фото не находил и считал, что его нет.
    shots = []
    for path in photostore.person_photos(cs)[:12]:
        try:
            taken = date.fromtimestamp(path.stat().st_mtime).isoformat()
        except OSError:
            continue
        shots.append({"file": photostore.photo_key(path), "date": taken,
                      "on_visit": taken == on_date})
    shots.sort(key=lambda x: (not x["on_visit"], x["date"]), reverse=False)

    last_row = db.last_visit_row([c["contract_id"] for c in cs])
    last_ev = evidence.parse(_cell_of(last_row, "evidence"))
    # Сверка «слова против денег»: сумма приложенных чеков против последнего
    # платежа из реестра. Обе величины рядом — СБ видит расхождение одним
    # взглядом, не собирая его из двух экранов.
    receipts = db.receipts_for([p["rep"]]).get(p["rep"], [])
    pay_date = None
    pay_sum = None
    for c in cs:
        d = db._as_date(str(_cell_of(c, "last_payment_date") or ""))
        if d and (pay_date is None or d > pay_date):
            pay_date = d
            digits = re.sub(r"[^\d]", "", str(_cell_of(c, "last_payment_sum") or ""))
            pay_sum = int(digits) if digits else None
    return {
        "screen": "person",
        "rep": p["rep"], "client": p["client"], "n": p["n"],
        # Сколько человек пробыл на карточке этого должника — граница измерения
        # честная: от открытия карточки до «Сохранить выезд».
        "fill_text": _fill_text(_cell_of(last_row, "fill_seconds")),
        # Подтверждение последнего выезда — вердикт и его разбор по следам.
        "evidence": _evidence_view(last_ev, _cell_of(last_row, "evidence_level")),
        # Адрес, уточнённый по выезду: руководитель должен видеть, что точка
        # на карте — не из справочника, а «где застали».
        "learned_point": (rawv("geo_src") or "") == "visit",
        # «photos» ниже — счётчик снимков, он был здесь раньше; список файлов
        # для показа в карточке кладём отдельным ключом.
        "shots": shots,
        # Чеки об оплате, приложенные выездником (контур СБ): показать рядом с
        # «Оплачен» из слов — сверка «слова против платежа» теперь в одном месте.
        "receipts": receipts,
        "receipts_total": round(sum(r["amount"] or 0 for r in receipts), 2),
        "registry_pay": {"date": pay_date.isoformat() if pay_date else None,
                         "sum": pay_sum},
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


def _evidence_view(ev: dict | None, level: str | None) -> dict | None:
    """Вердикт для экрана: заголовок, строка, разбор по пунктам."""
    level = level or (ev or {}).get("level")
    if not level:
        return None
    ev = ev or {}
    rows = []
    if ev.get("opened"):
        rows.append(f"карточка открыта в {ev['opened']}"
                    + (f", в {_pretty_m(ev['open_dist_m'])} от адреса"
                       if ev.get("open_dist_m") is not None else ""))
    if ev.get("near_first"):
        span = (f"{ev['near_first']}–{ev['near_last']} ({ev.get('near_min') or 0} мин)"
                if ev.get("near_last") and ev["near_last"] != ev["near_first"]
                else f"в {ev['near_first']}")
        rows.append(f"у адреса {span}")
    elif ev.get("min_dist_m") is not None and level == "far":
        rows.append(f"ближе всего к адресу: {_pretty_m(ev['min_dist_m'])}")
    if ev.get("saved"):
        rows.append(f"«Сохранить выезд» в {ev['saved']}"
                    + (f", в {_pretty_m(ev['save_dist_m'])} от адреса"
                       if ev.get("save_dist_m") is not None else "")
                    + (" — заполнен не на адресе" if ev.get("saved_away") else ""))
    if ev.get("online_save") is True:
        rows.append("связь при сохранении была")
    elif ev.get("online_save") is False:
        rows.append("сохранён без связи, доехал позже")
    if ev.get("served"):
        rows.append(f"сервер видел телефон на месте {ev['served']} раз(а) во время визита")
    if ev.get("photo_near") is True:
        rows.append("снимок сделан у адреса")
    elif ev.get("photo_near") is False:
        rows.append("снимок сделан не у адреса")
    if ev.get("foreground_min") is not None:
        rows.append(f"приложение было на экране {ev['foreground_min']} мин")
    return {"level": level, "title": evidence.LEVEL_TEXT.get(level, level),
            "emoji": evidence.LEVEL_EMOJI.get(level, "❔"),
            "note": ev.get("note") or "", "rows": rows,
            "geo_samples": ev.get("geo_samples")}


def _pretty_m(m) -> str:
    if m is None:
        return "—"
    return f"{m / 1000:.1f} км" if m >= 1000 else f"{int(m)} м"


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
        # Сюда же — должник, которого с тех пор перезакрепили: к нему ведёт
        # выезд этого сотрудника, и карточка по своему выезду открываться обязана.
        rows = db.person_contracts(rep)
        if rows and ((rows[0]["responsible"] or "") == employee
                     or db.employee_visited(employee,
                                            [r["contract_id"] for r in rows])):
            p = next(iter(db._group_persons(rows)), None)
    if p is None:
        return None
    return _person_payload(p, on_date)


# --- ЭКРАН: случаи за окном (тап по плашке сводки) ---------------------------
CASE_METRICS = {"visits", "paid", "promised", "with_photo", "persons",
                # Оплаты по реестру за окно: все и пришедшие после выезда.
                "pay", "pay_after",
                # Подтверждение выезда: подтверждён / у адреса не был /
                # проверить нечем / признаки серии / заполнено не на адресе.
                "confirmed", "far", "unverified", "series", "saved_away",
                # Ступени воронки Collection (funnel.py): экран приложения
                # рисует лестницу, и тап по ступени показывает те самые выезды.
                "carded", "contacts", "dead", "rpc", "third", "ptp",
                "promise_third"}

# Ступени считают ВЫЕЗДЫ, а не последние состояния людей — дедупликацию
# «по человеку» (ветка metric != visits ниже) для них делать нельзя.
FUNNEL_CASE_METRICS = {"carded", "contacts", "dead", "rpc", "third",
                       "ptp", "promise_third"}
CASES_LIMIT = 300

# Сотрудник без офиса не исчезает: у него свой чип «Без офиса». Пока чипа не
# было, новый выездник из реестра числился только во «Всех офисах», и
# руководитель Худжанда прочитал это как «списки не обновлены» — так и написал
# 16.09.2026, когда Калонов Абдухафиз и Рустамов Алишер работали уже неделю.
# Офис человеку ставит оператор, из данных он не выводится, и промолчать о
# незакреплённых нельзя: их не видно ровно там, где на них смотрят.
# Код-метка не может совпасть с городом — города не начинаются с «!».
NO_OFFICE = "!none"
NO_OFFICE_NAME = "Без офиса"


def _in_office(row: dict, code: str) -> bool:
    """Строка сводки принадлежит офису (или «Без офиса» для незакреплённых)."""
    office = row.get("office") or ""
    return not office if code == NO_OFFICE else office == code


def _offices_of(rows: list[dict], days: int) -> list[dict]:
    codes = sorted({(r.get("office") or "") for r in rows} - {""})
    out = [{"code": c, "name": c,
            "employees": sum(1 for r in rows if _in_office(r, c)),
            "totals": _totals_of([r for r in rows if _in_office(r, c)], days)}
           for c in codes]
    orphans = [r for r in rows if not (r.get("office") or "")]
    # Одинокий чип «Без офиса» бессмыслен: если офисов нет ни у кого, делить
    # нечего, и строка чипов в сводке не показывается вовсе.
    if out and orphans:
        out.append({"code": NO_OFFICE, "name": NO_OFFICE_NAME,
                    "employees": len(orphans),
                    "totals": _totals_of(orphans, days)})
    return out


def cases_data(mgr: dict, rng: dict, metric: str, country: str = "",
               employees: list[str] | None = None, office: str = "",
               employee: str = "") -> dict:
    """Список выездов за окно по одной метрике сводки.

    Плашки «выездов / оплачен / обещают / с фото» были голыми числами: за
    вопросом «а кто это» руководителю приходилось перебирать сотрудников по
    одному. Здесь тот же набор, из которого сложилась цифра. employee сужает
    список до одного человека — так ступень воронки в карточке сотрудника
    показывает его выезды, а не всей команды.
    """
    employees = employees if employees is not None else sorted_employees(mgr)
    if employee:
        employees = [e for e in employees if e == employee]
    metric = metric if metric in CASE_METRICS else "visits"
    country_of = _employee_country_index(rng["from"], rng["to"])
    if country:
        employees = [e for e in employees if country_of.get(e) == country]
    if office:
        by_office = db.employee_office_index()
        employees = [e for e in employees
                     if _in_office({"office": db.office_of(e, by_office)}, office)]
    items = []
    for emp in employees:
        r = _employee_stats(emp, rng["from"], rng["to"], with_photos=False)
        if metric in ("pay", "pay_after"):
            for x in r["_pays"]:
                if metric == "pay_after" and not x["visit_day"]:
                    continue
                p = x["person"]
                items.append({
                    "employee": emp, "date": x["day"].isoformat(),
                    "day_label": _short_date(x["day"]),
                    "rep": p["rep"], "client": p["client"], "n": p["n"],
                    "outcome": "Оплата", "emoji": "💳",
                    "agreed": (f"после выезда {_short_date(x['visit_day'])}"
                               if x["visit_day"] else "без выезда"),
                    "photos": 0, "money": x["units"], "money_text": _units_text(x["units"]),
                })
            continue
        entries = r["_entries"]
        # «Оплачен», «обещают» и «должников» — это ПОСЛЕДНЕЕ состояние человека
        # за окно, ровно как в плашках; «выездов» и ступени воронки — каждый
        # человеко-день: ступень считает выезды, дедупликация её порвала бы.
        if metric != "visits" and metric not in FUNNEL_CASE_METRICS:
            last = {}
            for e in entries:
                last[e["person"]["rep"]] = e
            entries = list(last.values())
        if metric == "paid":
            entries = [e for e in entries if e["status"] == OUTCOME_PAID]
        elif metric == "promised":
            entries = [e for e in entries if e["status"] == OUTCOME_PROMISE]
        elif metric in ("confirmed", "far"):
            entries = [e for e in entries if e.get("evidence_level") == metric]
        elif metric == "unverified":
            entries = [e for e in entries
                       if e.get("evidence_level") in evidence.UNVERIFIED]
        elif metric == "series":
            entries = [e for e in entries if evidence.is_series(e.get("series"))]
        elif metric == "saved_away":
            entries = [e for e in entries if (e.get("ev") or {}).get("saved_away")]
        elif metric in FUNNEL_CASE_METRICS:
            # Классификация — та же, что считает ступени в _funnel_of: два
            # правила на одну ступень разъехались бы в первый же день.
            def _picked(metric, e):
                contact = funnel.contact_kind(e.get("contact_with"))
                promise = funnel.promise_kind(e.get("agreed"))
                if metric == "carded":
                    return bool(contact)
                if metric == "contacts":
                    return contact in ("client", "third")
                if metric == "dead":
                    return contact == "none"
                if metric == "rpc":
                    return contact == "client"
                if metric == "third":
                    return contact == "third"
                if metric == "ptp":
                    return contact == "client" and promise == "client"
                return promise == "third"          # promise_third
            entries = [e for e in entries if _picked(metric, e)]
        for e in entries:
            p = e["person"]
            photos = len(photostore.day_photos(p["contracts"], e["date"]))
            if metric == "with_photo" and not photos:
                continue
            items.append({
                "employee": emp, "date": e["date"],
                "day_label": _short_date(db._as_date(e["date"])),
                "rep": p["rep"], "client": p["client"], "n": p["n"],
                "outcome": e["status"], "emoji": _st_emoji(e["status"]),
                "agreed": e["agreed"], "photos": photos,
                "late_days": e.get("late_days"), "geo": bool(e.get("geo")),
                "fill_text": _fill_text(e.get("fill_seconds")),
                "money": _totals_units([p]), "money_text": _persons_total([p]),
                **_case_marks(e),
            })
    items.sort(key=lambda c: (c["date"], c["client"]), reverse=True)
    return {
        "screen": "cases", "range": rng, "metric": metric, "country": country,
        "office": office, "employee": employee,
        "total": len(items), "cases": items[:CASES_LIMIT],
        "limit": CASES_LIMIT,
    }


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
# «Контакт» и «На адресе» появились в записи журнала 15.09.2026 (web.py) ради
# воронки. У выездов старше этой даты их в строке нет — тогда анкету берём из
# снимка человека, и только если снимок описывает ИМЕННО этот выезд.
_PAYLOAD_CONTACT = re.compile(r"Контакт:\s*(.*?);\s*На адресе:\s*(.*?);", re.S)
STATS_PRESETS = ("today", "yesterday", "7d", "30d", "month", "custom")
_PRESET_DAYS = {"today": 1, "7d": 7, "30d": 30}
STATS_MAX_DAYS = 366
# Деление исходов на успешные и неуспешные — решение офиса (Илья Павлюк,
# 11.09.2026): выезд считается успешным, если человека нашли и с ним есть
# результат, хоть бы и отказ. «Рассрочка» в списке офиса не названа, но это
# договорённость об оплате — она рядом с «Обещает оплатить».
# «Не обработан» — это отсутствие выезда, и он стоит рядом с «нет контакта».
# Всё остальное («Тюрьма», «Смерть», «Кредит не брал», любой новый статус)
# идёт в «прочие»: относить смерть должника к успеху или неуспеху — не наше
# решение, а терять её в отчёте нельзя.
OUTCOME_SUCCESS = ("В работе", "Обещает оплатить", "Оплачен", "Рассрочка",
                   "Передана инф 3 лицу", "Отказ от оплаты")
OUTCOME_FAIL = ("Нет контакта", "Не обработан")
OUTCOME_GROUPS = (("success", "Успешный выезд", OUTCOME_SUCCESS),
                  ("fail", "Не успешный выезд", OUTCOME_FAIL),
                  ("other", "Прочие исходы", ()))
OUTCOME_GROUP_LABEL = {"success": "успешный", "fail": "не успешный",
                       "other": "прочее"}


def outcome_group(status: str) -> str:
    """success / fail / other по исходу выезда."""
    text = (status or "").strip()
    if text in OUTCOME_SUCCESS:
        return "success"
    if text in OUTCOME_FAIL or not text:
        return "fail"
    return "other"


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


def _payload_contact(payload) -> tuple[str, str]:
    """(«С кем был контакт?», «Кто был на адресе?») из строки журнала выезда."""
    m = _PAYLOAD_CONTACT.search(str(payload or ""))
    if not m:
        return "", ""
    return m.group(1).strip(), m.group(2).strip()


def _snapshot_day(p: dict) -> str:
    return next((str(c["bot_visit_at"] or "")[:10]
                 for c in p["contracts"] if c["bot_visit_at"]), "")


def _cell_of(row, key):
    """Поле строки визита; у старых снимков и урезанных словарей его может не быть."""
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return None


def _avg_fill(values) -> int | None:
    """Среднее время на карточке, сек. None — мерить было нечего."""
    good = [int(v) for v in values or () if isinstance(v, (int, float)) and v >= 0]
    return round(sum(good) / len(good)) if good else None


def _stamp(value) -> str:
    """«11.09 14:32» — отметка времени без года и секунд, для книги."""
    text = str(value or "")
    if len(text) < 16:
        return ""
    return f"{text[8:10]}.{text[5:7]} {text[11:16]}"


def _fill_text(seconds) -> str:
    """«4 мин», «45 с», «1 ч 10 мин» — для книги и карточки."""
    if not isinstance(seconds, (int, float)) or seconds < 0:
        return ""
    seconds = int(seconds)
    if seconds < 90:
        return f"{seconds} с"
    if seconds < 3600:
        return f"{round(seconds / 60)} мин"
    return f"{seconds // 3600} ч {round((seconds % 3600) / 60)} мин"


def _late_days(visit_day: str, received_at) -> int | None:
    """На сколько дней выезд доехал позже своего дня. None — нечем судить.

    Ноль значит «в тот же день»: столько выездов и должно быть. Всё, что больше,
    — это работа, занесённая задним числом, и её офис просил видеть отдельно.
    """
    day, got = _as_day(visit_day), _as_day(received_at)
    if day is None or got is None:
        return None
    return max(0, (got - day).days)


def _as_day(value):
    try:
        return db._as_date(str(value or "")[:10])
    except (TypeError, ValueError):
        return None


def _mark_series(ordered: list[dict]) -> None:
    """Признаки серии — по дням: три сохранения за десять минут, одна точка на
    разные адреса, невозможная скорость. Считается по сохранённым фактам, а
    не хранится: точки и время сохранения не меняются, а вот правило может."""
    by_day: dict[str, list[dict]] = {}
    for e in ordered:
        by_day.setdefault(e["date"], []).append(e)
    for items in by_day.values():
        flags = evidence.series_flags([
            {"id": e["_vid"], "ms": e.get("_ms"),
             "lat": e["_pt"][0], "lon": e["_pt"][1],
             "alat": e["_addr"][0], "alon": e["_addr"][1]} for e in items])
        for e in items:
            e["series"] = flags.get(e["_vid"])


def _case_marks(e: dict) -> dict:
    """Общие для карточки-случая поля подтверждения (экран и список случаев)."""
    ev = e.get("ev") or {}
    return {"evidence_level": e.get("evidence_level"),
            "evidence_note": ev.get("note") or "",
            "near_min": ev.get("near_min"),
            "saved_away": bool(ev.get("saved_away")),
            "series": evidence.series_text(e.get("series"))}


# --- Воронка Collection ------------------------------------------------------
# Правила «кто такой контакт» и «что стало с обещанием» живут в funnel.py; здесь
# только сборка: откуда взять анкету выезда и как сложить строку.
_FUNNEL_SNAPSHOT_KEYS = ("contact_with", "who_at_address",
                         "promise_date", "forecast_sum")


def _snapshot_fields(p: dict, cache: dict) -> dict:
    """Поля анкеты из доказанного снимка человека. Один разбор снимка на
    человека: _visit_pick читает bot_visit_data по каждому договору, а у
    сотрудника с полусотней выездов это иначе полсотни лишних разборов JSON."""
    key = p["rep"]
    if key not in cache:
        pick = _visit_pick(p["contracts"])
        cache[key] = {k: pick(k) for k in _FUNNEL_SNAPSHOT_KEYS}
    return cache[key]


def _person_paid(p: dict) -> bool:
    """Кейс закрыт оплатой — то же чтение, что у db.overdue_promises."""
    return any("Оплачен" in ((_cell_of(c, "overall_result") or "")
                             + (_cell_of(c, "call_status") or ""))
               for c in p["contracts"])


def _last_payment_day(p: dict):
    """Самый поздний платёж среди договоров человека (реестр), иначе None."""
    days = [d for d in (db._as_date(_cell_of(c, "last_payment_date") or "")
                        for c in p["contracts"]) if d]
    return max(days) if days else None


# Платёж «после выезда»: к должнику ездили не раньше чем за столько дней до
# платежа. Дальше связь выезда и оплаты уже гадание.
PAY_AFTER_VISIT_DAYS = 30


def _payments_of(persons: list[dict], d_from: str, d_to: str) -> list[dict]:
    """Оплаты портфеля за окно — по реестру («Дата/Сумма последнего платежа»).

    Владелец 01.10.2026: «обещания — это хорошо, но что насчёт оплат?». Реестр
    знает только ПОСЛЕДНИЙ платёж договора, поэтому это «кто заплатил за окно и
    сколько последним платежом», а не выписка. Человек считается один раз, суммы —
    по его договорам с платежом в окне, раздельно по валютам. visit_day — самый
    поздний выезд к человеку за PAY_AFTER_VISIT_DAYS до платежа (кем бы ни был)."""
    lo, hi = db._as_date(d_from), db._as_date(d_to)
    if lo is None or hi is None:
        return []
    out = []
    for p in persons:
        day, acc = None, {}
        for c in p["contracts"]:
            d = db._as_date(_cell_of(c, "last_payment_date") or "")
            if d is None or not lo <= d <= hi:
                continue
            u = debt.unit([_cell_of(c, "partner")])
            acc[u] = acc.get(u, 0) + int(db.canon_amount(_cell_of(c, "last_payment_sum")) or 0)
            day = max(day, d) if day else d
        if day:
            out.append({"person": p, "day": day, "visit_day": None,
                        "units": [{"unit": u, "sum": s} for u, s in acc.items() if s]})
    if out:
        since = min(x["day"] for x in out) - timedelta(days=PAY_AFTER_VISIT_DAYS)
        seen = db.visit_days_for_contracts(
            [c["contract_id"] for x in out for c in x["person"]["contracts"]],
            since.isoformat(), hi.isoformat())
        for x in out:
            first = x["day"] - timedelta(days=PAY_AFTER_VISIT_DAYS)
            prior = [d for c in x["person"]["contracts"]
                     for d in map(db._as_date, seen.get(c["contract_id"], ()))
                     if d and first <= d <= x["day"]]
            x["visit_day"] = max(prior) if prior else None
    return out


def _promise_view(e: dict, snap: dict, on, first_day=None) -> dict:
    """Судьба обещания, взятого на этом выезде: состояние, дата, сумма, срок.

    Дату и сумму даёт снимок визита (в нём же живёт офисная правка этих ячеек),
    с откатом на колонку реестра — ровно тот же порядок, что у карточки должника.
    Обещание, датированное раньше ПЕРВОГО выезда к должнику, funnel.promise_state
    отбросит как чужое: колонку «дата обещания» заполняет ещё и офис по телефону.
    Граница берётся по первому выезду к должнику ЗА ВСЮ ИСТОРИЮ, а не по этому
    выезду и не по первому выезду окна: обещание принадлежит должнику, и ни
    повторный выезд к нему, ни короткое окно отчёта не имеют права гасить уже
    сорванное обещание (см. funnel.promise_state, db.first_visit_days).
    """
    p = e["person"]
    promise_day = db._as_date(str(snap.get("promise_date") or "")[:10])
    paid = _person_paid(p)
    state = funnel.promise_state(promise_day, paid=paid,
                                 last_payment_day=_last_payment_day(p),
                                 on=on, first_visit_day=first_day)
    amount = db.canon_amount(snap.get("forecast_sum"))
    overdue = ((on - promise_day).days
               if promise_day and state == funnel.PROMISE_BROKEN else 0)
    return {"state": state, "day": promise_day.isoformat() if promise_day else "",
            "sum": int(amount) if amount else 0, "days_overdue": overdue}


def _funnel_of(entries: list[dict]) -> dict:
    """Счётчики воронки по выездам сотрудника за окно.

    Первые ступени считают выезды, исполнение обещаний — людей (см. funnel.py):
    повторное обещание того же должника за период оплачивается один раз, и
    засчитывать его дважды значило бы завысить и KP, и BP.
    """
    out = funnel.empty_counts()
    seen_promise: dict[str, dict] = {}
    for e in entries:
        out["visits"] += 1
        contact = funnel.contact_kind(e.get("contact_with"))
        if not contact:
            continue                       # анкета не заполнена — не судим
        out["carded"] += 1
        if funnel.address_kind(e.get("who_at_address")) == "relatives":
            out["relatives"] += 1
        if contact == "none":
            out["dead"] += 1
            continue
        out["contacts"] += 1
        promise = funnel.promise_kind(e.get("agreed"))
        if promise == "third":
            out["promise_third"] += 1
        if contact == "third":
            out["third"] += 1
            continue
        out["rpc"] += 1
        if promise != "client":
            continue
        out["ptp"] += 1
        # Один должник — одно обещание в зачёт KP/BP. Оставляем ПОСЛЕДНЕЕ:
        # именно оно и живёт сейчас в реестре, по нему и судит платёж.
        seen_promise[e["person"]["rep"]] = e
    for e in seen_promise.values():
        out["ptp_persons"] += 1
        out[e["promise"]["state"]] += 1
        # Метка «в зачёт» едет в лист «Обещания»: по ней видно, почему клиентов
        # с обещанием меньше, чем обещаний, и сумма колонок сходится с воронкой.
        e["promise"]["counted"] = True
    return out


def _employee_stats(emp: str, d_from: str, d_to: str, *,
                    with_photos: bool = True) -> dict:
    """Строка сотрудника за окно. «_entries» — выезды по дням (человек-день),
    новые в конце; «_paid» — люди, чей последний исход в окне «Оплачен».

    with_photos=False пропускает подсчёт снимков: он ходит в файловую систему
    по каждому человеку, а окну «за всё время» это стоило бы сотен обращений
    ради колонки, которой в сводном отчёте нет."""
    persons = db._group_persons(db.contracts_of_responsible(emp))
    by_cid = {c["contract_id"]: p for p in persons for c in p["contracts"]}
    visits = db.visits_for_employee(emp, d_from, d_to)
    # Выезды по договорам, которые с тех пор перезакрепили: должника в портфеле
    # уже нет, но выезд — работа этого сотрудника, и выпасть из его статистики
    # он не должен. Такие договоры собираем в людей отдельно от портфеля.
    moved = {v["contract_id"] for v in visits} - set(by_cid)
    if moved:
        for p in db._group_persons(db.clients_by_ids(moved)):
            for c in p["contracts"]:
                by_cid[c["contract_id"]] = p
    # (день, человек) → последняя запись журнала этого дня: повторное нажатие
    # «Сохранить выезд» рождает вторую строку, но это тот же выезд.
    entries: dict[tuple[str, str], dict] = {}
    for v in visits:
        p = by_cid.get(v["contract_id"])
        if p is None:
            continue
        status, agreed = _payload_status(v["payload"])
        contact_with, who_at_address = _payload_contact(v["payload"])
        ev = evidence.parse(_cell_of(v, "evidence"))
        entries[(v["created_at"], p["rep"])] = {
            "date": v["created_at"], "person": p, "status": status, "agreed": agreed,
            # Анкета контакта этого выезда (воронка). Пусто — выезд старше
            # 15.09.2026; ниже такой выезд добирает анкету из снимка, но только
            # если снимок описывает именно его.
            "contact_with": contact_with, "who_at_address": who_at_address,
            # Три пометки контроля, о которых просил офис 11.09.2026:
            # когда выезд доехал до сервера (работа в поле или занесённая
            # назавтра «не было связи»), была ли точка телефона и кто закрыл —
            # сам выездник или руководитель от его имени.
            "received_at": _cell_of(v, "received_at"),
            "saved_at": _cell_of(v, "saved_at"),
            "late_days": _late_days(v["created_at"], _cell_of(v, "received_at")),
            "geo": _cell_of(v, "visit_lat") is not None,
            "fill_seconds": _cell_of(v, "fill_seconds"),
            "agent_id": _cell_of(v, "agent_id"),
            # Подтверждение выезда (evidence.py): вердикт, сохранённый на
            # момент визита, и сырьё для признаков серии — точка и момент
            # сохранения, адрес должника.
            "evidence_level": _cell_of(v, "evidence_level") or (ev or {}).get("level"),
            "ev": ev,
            "_vid": v["id"],
            "_pt": (_cell_of(v, "visit_lat"), _cell_of(v, "visit_lon")),
            "_addr": (_cell_of(v, "addr_lat"), _cell_of(v, "addr_lon")),
            "_ms": _cell_of(v, "saved_at") or _cell_of(v, "received_at")}
    ordered = sorted(entries.values(), key=lambda e: (e["date"], e["person"]["client"]))
    _mark_series(ordered)
    # Судим исполнение обещаний НА СЕГОДНЯ, а не на конец окна: обещание, данное
    # 3-го на 10-е, к 10-му числу либо оплачено, либо сорвано, и вопрос «с какими
    # долгами умеем работать» требует настоящего исхода, а не того, что было
    # видно на закрытии периода.
    on_today = db._as_date(today_iso())
    snap_cache: dict[str, dict] = {}
    # Первый выезд к КАЖДОМУ должнику — граница «своего» обещания. Она одна на
    # человека и НЕ ЗАВИСИТ ОТ ОКНА ОТЧЁТА: обещание принадлежит должнику, а не
    # поездке и тем более не отчёту. Границей по окну книга спорила сама с
    # собой: у вечерней книги окно — ОДИН день, повторный выезд к должнику
    # оказывался в нём первым, и вчерашнее сорванное обещание становилось
    # «без даты». Один и тот же должник на 22.09.2026 читался как «сорвано»
    # в книге за 30 дней и как «без даты» в вечерней — BP 100 % против 0 %.
    # Защита от офисной даты при этом цела: обещание раньше ПЕРВОГО В ЖИЗНИ
    # выезда к должнику по-прежнему nodate.
    visit_days: dict[str, list] = {}
    for cid, first_day in db.first_visit_days(emp).items():
        p = by_cid.get(cid)
        if p is not None:
            visit_days.setdefault(p["rep"], []).append(_as_day(first_day))
    # Выезды окна добавляем следом: они уже отобраны и страхуют границу, если
    # договор успели перезакрепить между двумя запросами.
    for e in ordered:
        visit_days.setdefault(e["person"]["rep"], []).append(_as_day(e["date"]))
    first_visit = {rep: funnel.first_visit_day(days)
                   for rep, days in visit_days.items()}
    for e in ordered:
        p = e["person"]
        snap_day = _snapshot_day(p)
        if e["date"] == snap_day or not e["status"]:
            e["status"] = _person_status(p)
        snap = _snapshot_fields(p, snap_cache)
        # Анкета выезда: своя строка журнала сильнее снимка. Снимок один на
        # человека и описывает ПОСЛЕДНИЙ выезд — приписывать его позапрошлому
        # значило бы сочинить контакт, которого на том выезде могло не быть.
        if not e["contact_with"] and e["date"] == snap_day:
            e["contact_with"] = snap.get("contact_with") or ""
            e["who_at_address"] = snap.get("who_at_address") or ""
        e["promise"] = _promise_view(e, snap, on_today,
                                     first_visit.get(p["rep"]))
    last: dict[str, dict] = {}
    for e in ordered:
        last[e["person"]["rep"]] = e
    people = [e["person"] for e in last.values()]
    paid = [e["person"] for e in last.values() if e["status"] == OUTCOME_PAID]
    promised = [e for e in last.values() if e["status"] == OUTCOME_PROMISE]
    counts: dict[str, int] = {}
    by_status: dict[str, list] = {}
    for e in last.values():
        counts[e["status"]] = counts.get(e["status"], 0) + 1
        by_status.setdefault(e["status"], []).append(e["person"])
    by_day: dict[str, int] = {}
    for e in ordered:
        by_day[e["date"]] = by_day.get(e["date"], 0) + 1
    act = db.employee_activity(emp)
    pays = _payments_of(persons, d_from, d_to)
    pays_after = [x for x in pays if x["visit_day"]]
    return {
        "employee": emp,
        "visits": len(ordered), "persons": len(people),
        "contracts": _contracts_of(people),
        "paid": len(paid), "promised": len(promised),
        # Просрочка — на конец окна, но не из будущего: у пресета «Месяц» конец
        # 30-е число, и обещания «до 25-го» оказывались бы просроченными сегодня.
        "overdue": len(db.overdue_promises(emp, min(d_to, today_iso()),
                                           **_promise_window_kwargs())),
        "with_photo": (sum(1 for p in people
                           if photostore.photos_between(p["contracts"], d_from, d_to))
                       if with_photos else 0),
        "money": _totals_units(people), "money_text": _persons_total(people),
        "money_paid": _totals_units(paid), "money_paid_text": _persons_total(paid),
        "outcomes": [{"status": s, "n": n, "emoji": _st_emoji(s)}
                     for s, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))],
        # Исход → сколько людей и сколько на них долга. Считается по ПОСЛЕДНЕМУ
        # исходу человека за окно — ровно как плашки сводки, поэтому суммы
        # колонок сходятся с «Должников», а деньги не задваиваются.
        # Сколько выездов доехало не в свой день и сколько закрыто без точки
        # телефона — два счётчика контроля рядом с остальными.
        "office": db.office_of(emp),
        # Среднее время на карточке — по тем выездам, где его удалось измерить.
        "fill_avg": _avg_fill([e.get("fill_seconds") for e in ordered]),
        "plan_per_day": db.visit_plan_for(emp),
        "late": sum(1 for e in ordered if (e.get("late_days") or 0) > 0),
        "no_geo": sum(1 for e in ordered if not e.get("geo")),
        # Подтверждение выезда: сколько подтверждено на адресе, сколько «у
        # адреса не был», сколько проверить нечем (адрес неточный, точка
        # неточная, без гео) и сколько с признаками серии — пачкой, с одной
        # точки на разные адреса, с невозможной скоростью между точками.
        "confirmed": sum(1 for e in ordered if e.get("evidence_level") == "confirmed"),
        "far": sum(1 for e in ordered if e.get("evidence_level") == "far"),
        "unverified": sum(1 for e in ordered
                          if e.get("evidence_level") in evidence.UNVERIFIED),
        "series": sum(1 for e in ordered if evidence.is_series(e.get("series"))),
        # Был у адреса, а «Сохранить выезд» нажал не там — отдельный счёт.
        "saved_away": sum(1 for e in ordered if (e.get("ev") or {}).get("saved_away")),
        "near_avg": _avg_fill([(e.get("ev") or {}).get("near_min") * 60
                               for e in ordered
                               if isinstance((e.get("ev") or {}).get("near_min"), int)
                               and e.get("evidence_level") == "confirmed"]),
        "by_outcome": {
            status: {"n": len(ps), "money": _totals_units(ps),
                     "money_text": _persons_total(ps)}
            for status, ps in by_status.items()},
        "activity": {k: act.get(k) for k in _ACTIVITY_KEYS} if act else None,
        "portfolio": len(persons),
        # Оплаты портфеля за окно по реестру и те из них, что пришли после выезда.
        "pay_persons": len(pays), "pay_money": _merge_units(x["units"] for x in pays),
        "pay_after": len(pays_after),
        "pay_after_money": _merge_units(x["units"] for x in pays_after),
        # Воронка Collection: счётчики ступеней. Проценты не храним — их считает
        # funnel.rates там, где известен план, иначе «средний процент по
        # средним процентам» разошёлся бы с итогом по команде.
        "funnel": _funnel_of(ordered),
        "_by_day": by_day, "_entries": ordered, "_paid": paid, "_pays": pays,
    }


def _employee_country_index(d_from: str | None = None,
                            d_to: str | None = None) -> dict[str, str]:
    """ФИО → tj/uz по стране кейсов (как у направлений). Сотрудник, чьи кейсы
    в обеих странах, остаётся в первой — статистика не задваивается.

    С окном — ещё и те, у кого портфеля уже нет: страна берётся по его выездам
    за окно, иначе человек, с которого сняли договоры, уезжал бы в «Без
    республики» и выпадал из фильтра своей страны вместе с выездами."""
    out: dict[str, str] = {}
    for code in ("tj", "uz"):
        for emp in db.employees_by_direction(code):
            out.setdefault(emp, code)
    if d_from and d_to:
        for emp, codes in db.visit_authors(d_from, d_to).items():
            if emp not in out:
                code = next((c for c in ("tj", "uz") if c in codes),
                            next(iter(sorted(codes)), None))
                if code:
                    out[emp] = code
    return out


def _pay_totals(rows: list[dict]) -> dict:
    """Блок «Оплаты» свода: люди и деньги, раздельно «всего» и «после выезда»."""
    money = _merge_units(r.get("pay_money") for r in rows)
    after = _merge_units(r.get("pay_after_money") for r in rows)
    return {"pay_persons": sum(r.get("pay_persons", 0) for r in rows),
            "pay_after": sum(r.get("pay_after", 0) for r in rows),
            "pay_money": money, "pay_money_text": _units_text(money),
            "pay_after_money": after, "pay_after_money_text": _units_text(after)}


def _totals_of(rows: list[dict], days: int) -> dict:
    """Свод по набору строк сотрудников. Набор — вся команда или одна страна.

    План считается от нормы на человека в день (settings visit_plan_per_day,
    по умолчанию 10): офис ставит задачу именно так — «10 выездов на
    сотрудника», — и без плана в сводке процент проработки каждый день
    складывали на бумаге.
    """
    people = [e["person"] for r in rows for e in
              {e["person"]["rep"]: e for e in r["_entries"]}.values()]
    paid = [p for r in rows for p in r["_paid"]]
    per_day = db.visit_plan_per_day()
    plan_map = db.visit_plan_map()
    # План команды — СУММА личных норм: в группах 8/10/12 умножение общей нормы
    # на число людей дало бы цифру, которой никто не ставил.
    plan_total = sum(db.visit_plan_for(r["employee"], plan_map) for r in rows)
    return {
        "employees": len(rows),
        "active": sum(1 for r in rows if r["visits"]),
        "visits": sum(r["visits"] for r in rows),
        "persons": len(people),
        "contracts": _contracts_of(people),
        "paid": sum(r["paid"] for r in rows),
        "promised": sum(r["promised"] for r in rows),
        "overdue": sum(r["overdue"] for r in rows),
        "with_photo": sum(r["with_photo"] for r in rows),
        # Контроль офиса: сколько выездов доехало не в свой день и сколько
        # закрыто без точки телефона.
        "late": sum(r.get("late", 0) for r in rows),
        "no_geo": sum(r.get("no_geo", 0) for r in rows),
        "confirmed": sum(r.get("confirmed", 0) for r in rows),
        "far": sum(r.get("far", 0) for r in rows),
        "unverified": sum(r.get("unverified", 0) for r in rows),
        "series": sum(r.get("series", 0) for r in rows),
        "saved_away": sum(r.get("saved_away", 0) for r in rows),
        "near_avg": _avg_fill([r.get("near_avg") for r in rows]),
        "fill_avg": _avg_fill([r.get("fill_avg") for r in rows]),
        "money": _totals_units(people), "money_text": _persons_total(people),
        "money_paid": _totals_units(paid), "money_paid_text": _persons_total(paid),
        **_pay_totals(rows),
        "plan_per_day": per_day,
        "plan": plan_total * max(1, days),
        # Воронка складывается счётчиками, а не процентами: доля команды — это
        # сумма числителей на сумму знаменателей, а не среднее долей.
        "funnel": funnel.add_counts(*[r.get("funnel") for r in rows]),
    }


def stats_data(mgr: dict, rng: dict, employees: list[str] | None = None) -> dict:
    employees = employees if employees is not None else sorted_employees(mgr)
    d_from, d_to = rng["from"], rng["to"]
    country = _employee_country_index(d_from, d_to)
    days = _range_days(rng)
    rows = []
    for emp in employees:
        r = _employee_stats(emp, d_from, d_to)
        r["country"] = country.get(emp)
        r["by_day"] = [r["_by_day"].get(x["date"], 0) for x in days]
        rows.append(r)
    for i, x in enumerate(days):
        x["visits"] = sum(r["by_day"][i] for r in rows)
    order = [spec["code"] for spec in country_scope.COUNTRIES]
    codes = sorted({r["country"] for r in rows if r["country"]}, key=order.index)
    return {
        "screen": "stats",
        "range": rng,
        "totals": _totals_of(rows, len(days)),
        "by_day": days,
        "employees": sorted(rows, key=lambda r: (-r["visits"], r["employee"].lower())),
        # Итоги КАЖДОЙ страны считаются той же функцией, что и общий свод.
        # Фильтр «Тадж/Узб» переключает не только таблицу: руководитель
        # направления смотрел на плашки всего СНГ и видел там чужие цифры.
        # Разбивка по офисам — просьба офиса «Худжанд»: у направления два города,
        # и руководителю нужен свой. Офис сотрудника ведёт оператор
        # (db.employee_city), из данных он не выводится, поэтому последним идёт
        # чип «Без офиса» — незакреплённые обязаны быть видны, а не пропадать.
        "offices": _offices_of(rows, len(days)),
        "countries": [{"code": c,
                       **{k: country_scope.info(c)[k] for k in ("name", "short", "flag")},
                       "employees": sum(1 for r in rows if r["country"] == c),
                       "totals": _totals_of([r for r in rows if r["country"] == c],
                                            len(days))}
                      for c in codes],
        "scope": scope_label(mgr),
        "manager_name": mgr.get("name") or "",
        # По какой день реестр знает об оплатах: импорт раз в день, и за
        # «сегодня» оплат ещё нет — без даты ноль читался бы провалом.
        "pay_asof": db.last_payment_asof(),
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
            "late_days": e.get("late_days"), "geo": bool(e.get("geo")),
            "fill_text": _fill_text(e.get("fill_seconds")),
            "money": _totals_units([p]), "money_text": _persons_total([p]),
            "photos": len(photostore.day_photos(p["contracts"], e["date"])),
            **_case_marks(e),
        })
    per_day = db.visit_plan_for(employee)
    return {"screen": "emp", "range": rng, **r, "by_day": days, "cases": cases,
            "plan_per_day": per_day, "plan": per_day * max(1, len(days))}


def _outcome_layout(rows: list[dict]) -> list[dict]:
    """Колонки исходов: блок → статусы внутри него, в порядке отображения.

    Известные статусы стоят на своих местах всегда — даже если за окно ни разу
    не встретились: колонка, которая то появляется, то исчезает, ломает и
    сравнение двух выгрузок, и формулы, написанные поверх одной из них.
    «Прочие» собираются по факту, из того, что реально было.
    """
    seen: set[str] = set()
    for r in rows:
        seen.update(r.get("by_outcome") or {})
    known = set(OUTCOME_SUCCESS) | set(OUTCOME_FAIL)
    others = sorted(st for st in seen if st and st not in known)
    out = []
    for code, title, statuses in OUTCOME_GROUPS:
        out.append({"code": code, "title": title,
                    "statuses": list(statuses) if code != "other" else others})
    return out


def _outcome_totals(rows: list[dict]) -> dict:
    """Свести разбивку по исходам с нескольких сотрудников в одну."""
    acc: dict[str, dict] = {}
    for r in rows:
        for status, item in (r.get("by_outcome") or {}).items():
            slot = acc.setdefault(status, {"n": 0, "money": []})
            slot["n"] += item["n"]
            slot["money"].append(item["money"])
    out = {}
    for status, slot in acc.items():
        units = _merge_units(slot["money"])
        out[status] = {"n": slot["n"], "money": units,
                       "money_text": _units_text(units)}
    return out


def _book_line(label: str, country: str, stats: dict, by_outcome: dict,
               layout: list[dict], plan: int, office: str = "") -> tuple[list, dict]:
    """Строка книги: метрики, план, затем исходы блоками.

    Возвращает (строка, форматы). Деньги и «% плана» кладутся ЧИСЛАМИ, а ₽ и
    знак процента живут в формате ячейки: по тексту «1 508 299 ₽» SUM молча
    даёт ноль, и колонку складывают руками (жалоба Альберта 20.09.2026).
    Форматы — {позиция с нуля: формат}; смешанные валюты остаются текстом."""
    fmts: dict[int, str] = {}

    def money(pos: int, units, text: str):
        num, unit = _units_num(units)
        if num is None:
            return text or ""
        fmts[pos] = _money_fmt(unit)
        return num

    done = ""
    if plan:
        done = round((stats["visits"] or 0) / plan, 2)
        fmts[5] = "0%"
    line = [label, country, office, stats["visits"], plan or "", done,
            stats["persons"], stats["contracts"], stats["overdue"],
            stats["with_photo"], stats.get("late") or "", stats.get("no_geo") or "",
            stats.get("confirmed") or "", stats.get("far") or "",
            stats.get("unverified") or "", stats.get("series") or "",
            stats.get("saved_away") or "",
            _fill_text(stats.get("near_avg")),
            _fill_text(stats.get("fill_avg")),
            money(19, stats.get("money"), stats["money_text"]),
            money(20, stats.get("money_paid"), stats["money_paid_text"])]
    holder = {"by_outcome": by_outcome}
    for block in layout:
        cells, total_n, units = _outcome_cells(holder, block["statuses"])
        base = len(line)
        line.append(total_n or "")
        line.append(money(base + 1, units, _units_text(units)))
        for i, status in enumerate(block["statuses"]):
            line.append(cells[2 * i])
            item = (by_outcome or {}).get(status) or {}
            line.append(money(base + 3 + 2 * i, item.get("money"),
                              item.get("money_text") or ""))
        if block["code"] == "other":
            line.append(" · ".join(f"{st} {by_outcome[st]['n']}"
                                   for st in block["statuses"] if by_outcome.get(st)))
    return line, fmts


def _outcome_cells(row: dict, statuses: list[str]) -> tuple[list, int, list]:
    """Ячейки «чел./сумма» по статусам плюс итог группы (людей и деньги)."""
    by = row.get("by_outcome") or {}
    cells: list = []
    total_n = 0
    units: list = []
    for status in statuses:
        item = by.get(status) or {"n": 0, "money": [], "money_text": ""}
        cells.extend([item["n"] or "", item["money_text"] or ""])
        total_n += item["n"]
        units.append(item["money"])
    return cells, total_n, _merge_units(units)


BOOK_BASE_HEAD = ("Сотрудник", "Страна", "Офис", "Выездов", "План", "% плана",
                  "Должников", "Договоров", "Просрочено обещаний", "С фото",
                  "Внесено позже", "Без гео",
                  # Подтверждение выезда (evidence.py): четыре счётчика и
                  # среднее время у адреса — по подтверждённым выездам.
                  "Подтверждено на адресе", "У адреса не был",
                  "Нечем проверить", "Признаки серии", "Заполнен не на адресе",
                  "У адреса, среднее",
                  "Заполнение карточки, среднее",
                  "Долг", "Закрыто (оплачен)")
BOOK_BASE_WIDTH = (28, 14, 14, 9, 8, 9, 11, 11, 19, 8, 15, 10,
                   16, 14, 14, 14, 16, 16, 20, 22, 22)


def _book_head(layout: list[dict]) -> tuple[list, list, list, list]:
    """Шапка книги: строка блоков, строка колонок, ширины, границы блоков."""
    block_row: list = [""] * len(BOOK_BASE_HEAD)
    head_row: list = list(BOOK_BASE_HEAD)
    widths: list = list(BOOK_BASE_WIDTH)
    spans: list = []
    for block in layout:
        first = len(head_row) + 1
        head_row.extend([f"{block['title']}: всего, чел.",
                         f"{block['title']}: всего, сумма"])
        widths.extend([22, 24])
        for status in block["statuses"]:
            head_row.extend([f"{status}, чел.", f"{status}, сумма"])
            widths.extend([max(12, len(status) + 3), 20])
        if block["code"] == "other":
            head_row.append("Какие исходы")
            widths.append(34)
        block_row.extend([block["title"]] + [""] * (len(head_row) - len(block_row) - 1))
        spans.append((block["code"], first, len(head_row)))
    return block_row, head_row, widths, spans


# --- Листы воронки Collection ------------------------------------------------
# Просьба Альберта 15.09.2026. Колонка описана тройкой (заголовок, ширина, тип):
# тип «pct» превращает ячейку в НАСТОЯЩИЙ процент Excel, а не в текст «71,4%», —
# иначе по показателю нельзя ни отсортировать, ни построить сводную, а книга
# заказана именно «для самостоятельного анализа».
def _workdays(days: list[dict], today: str) -> int:
    """Рабочие дни окна, которые УЖЕ наступили.

    Задание — «10 клиентов на РАБОЧИЙ день» (решение владельца 22.09.2026).
    Считать по календарным дням значило ставить задание за выходные, а у
    пресета «Месяц» (окно — месяц целиком) ещё и за дни, которые не наступили:
    21.09.2026 задание выходило 300 вместо 210, а Penetration — 2,7 % вместо
    настоящих 3,8 %. Этим множителем пользуются ТОЛЬКО листы воронки: план в
    сводке и на листе «Сотрудники» остаётся прежним, чтобы книга не разошлась
    с плашками приложения.
    """
    out = 0
    for x in days or ():
        day = str((x or {}).get("date") or "")
        if not day or (today and day > today):
            continue
        weekend = (x or {}).get("weekend")
        if weekend is None:
            d = db._as_date(day)
            weekend = bool(d and d.weekday() >= 5)
        if not weekend:
            out += 1
    return out


FUNNEL_COLUMNS = (
    ("Срез", 13, "text"), ("Имя", 28, "text"),
    ("Республика", 14, "text"), ("Офис", 14, "text"),
    ("Задание", 10, "int"), ("Рабочих дней", 12, "int"), ("Выездов", 10, "int"),
    ("Penetration, %", 13, "pct"),
    ("Анкета заполнена", 13, "int"), ("Без анкеты", 11, "int"),
    ("Контактов", 11, "int"), ("Contact Rate, %", 14, "pct"),
    ("Мёртвых адресов", 14, "int"),
    ("Контакт с клиентом", 14, "int"), ("RPC, %", 10, "pct"),
    ("Контакт с 3-м лицом", 14, "int"), ("Родственники на адресе", 15, "int"),
    ("Обещаний от клиента", 14, "int"), ("PTP, %", 10, "pct"),
    ("Обещание 3-го лица", 14, "int"),
    ("Клиентов с обещанием", 15, "int"),
    ("Исполнено (KP)", 12, "int"), ("KP, %", 10, "pct"),
    ("Сорвано (BP)", 12, "int"), ("BP, %", 10, "pct"),
    ("Ждём срока", 11, "int"), ("Без даты обещания", 14, "int"),
)


def _funnel_line(level: str, name: str, country: str, office: str,
                 counts: dict, plan: int, workdays: int = 0) -> list:
    """Строка воронки. Ноль печатаем ЯВНО, а не прочерком: «0 контактов при 9
    выездах» — это и есть ответ про мёртвые адреса, и пустая ячейка его прячет.

    «Рабочих дней» стоит рядом с «Заданием», чтобы задание проверялось глазом:
    задание = личная норма × рабочие наступившие дни, и руководителю видно, из
    чего сложился знаменатель Penetration, а не только результат."""
    c = counts or funnel.empty_counts()
    r = funnel.rates(c, plan)

    def pct(key):
        # None (знаменателя не было) — пустая ячейка. Ноль значит «делали и не
        # вышло», и подменять его нулевым процентом нельзя.
        return "" if r.get(key) is None else r[key] / 100

    return [level, name, country, office, plan or "", workdays, c["visits"],
            pct("penetration"),
            c["carded"], c["visits"] - c["carded"],
            c["contacts"], pct("contact_rate"), c["dead"],
            c["rpc"], pct("rpc"),
            c["third"], c["relatives"],
            c["ptp"], pct("ptp"), c["promise_third"],
            c["ptp_persons"], c["kept"], pct("kp"), c["broken"], pct("bp"),
            c["pending"], c["nodate"]]


PROMISE_COLUMNS = (
    ("Дата выезда", 12), ("Выездов с обещанием", 12),
    ("Республика", 14), ("Офис", 14), ("Сотрудник", 28),
    ("Клиент", 32), ("Договоров", 10), ("С кем был контакт", 24),
    ("Кто на адресе", 30), ("Договорённость", 28),
    ("Дата обещания", 13), ("Сумма обещания", 15), ("Валюта", 11),
    ("Группа по сумме", 16),
    ("Долг", 18), ("Исход обещания", 14), ("Дней просрочки", 13),
    ("Группа по просрочке", 16), ("Последний платёж", 15),
    ("Сумма платежа", 15), ("Исход выезда", 20), ("В зачёт KP/BP", 13),
)

# Тип «money» — колонка денег: формат ячейки берётся из валюты СТРОКИ, а не
# прибивается к рублю. Подпись без знака валюты намеренно: строки листа
# разложены по валютам, и знак живёт в колонке «Валюта» и в формате ячейки.
RANGE_COLUMNS = (
    ("Группа по сумме обещания", 24), ("Валюта", 11),
    ("Клиентов с обещанием", 15),
    ("Исполнено (KP)", 12), ("KP, %", 10, "pct"),
    ("Сорвано (BP)", 12), ("BP, %", 10, "pct"),
    ("Ждём срока", 11), ("Без даты", 11),
    ("Обещано", 16, "money"), ("Сорвано обещаний", 18, "money"),
    ("Долг сорвавших", 18, "money"),
    ("Средняя просрочка, дн", 15), ("Худшая просрочка, дн", 15),
)

# Валюта долга, которой нет (пустой контрагент) или которых несколько. Пустая
# ячейка тут читается как «нет данных», а это разные вещи: сложить такие суммы
# с рублёвыми нельзя ни в том, ни в другом случае.
UNIT_UNKNOWN = "не указана"
UNIT_MIXED = "разные"


def _promise_unit(p: dict) -> tuple[str, str]:
    """(валюта для формата ячейки, подпись) долга должника.

    Сумма обещания в анкете — голое число без единицы, и единственная честная
    единица для неё это валюта долга того же человека. Должник с договорами
    в РАЗНЫХ валютах помечается отдельно: складывать его обещание ни с теми,
    ни с другими нельзя.
    """
    labels = {str(x.get("unit") or "") for x in (p.get("totals") or ())}
    if len(labels) > 1:
        return "", UNIT_MIXED
    unit = labels.pop() if labels else debt.unit(p.get("partners") or [])
    return unit, (unit or UNIT_UNKNOWN)


def _promise_rows(rows: list[dict], names: dict) -> list[dict]:
    """Обещания клиентов, взятые на выездах окна, — сырьё листов «Обещания»
    и «Ранжирование». Одна строка = ОДИН ДОЛЖНИК (решение владельца
    22.09.2026), а не один выезд.

    Результат, сумму и дату обещания даёт ПОСЛЕДНИЙ выезд к должнику: его
    анкета и лежит сейчас в реестре. Раньше строка печаталась на каждый выезд,
    и SUM колонки «Сумма обещания» показывал 10 100 000 там, где обещано
    9 900 000: должник, к которому съездили дважды, считался дважды.

    Ключ дедупликации — пара (сотрудник, должник), а не один номер договора:
    номер КД дублируется между республиками (случай 47892257), и по голому
    номеру работа одного выездника затёрла бы работу другого.
    """
    last: dict[tuple[str, str], tuple[dict, dict]] = {}
    seen: dict[tuple[str, str], int] = {}
    for r in rows:
        for e in r["_entries"]:
            if funnel.contact_kind(e.get("contact_with")) != "client":
                continue
            if funnel.promise_kind(e.get("agreed")) != "client":
                continue
            # «_entries» отсортированы по дате — побеждает последний выезд.
            key = (r["employee"], e["person"]["rep"])
            last[key] = (r, e)
            seen[key] = seen.get(key, 0) + 1
    out = []
    for key, (r, e) in last.items():
        p, pr = e["person"], e["promise"]
        last_day = _last_payment_day(p)
        debt_val, debt_unit = _units_num(p["totals"])
        promise_unit, promise_unit_text = _promise_unit(p)
        out.append({
            "date": e["date"], "country": names.get(r["country"], ""),
            "office": r.get("office") or "", "employee": r["employee"],
            "client": p["client"], "contracts": p["n"],
            "visits": seen[key],
            "unit": promise_unit, "unit_text": promise_unit_text,
            "contact_with": e.get("contact_with") or "",
            "who_at_address": e.get("who_at_address") or "",
            "agreed": e.get("agreed") or "",
            "promise_day": pr["day"], "sum": pr["sum"],
            "bucket": funnel.amount_bucket(pr["sum"]),
            "debt": _persons_total([p]),
            "debt_num": sum(x["sum"] for x in p["totals"]),
            # Та же пара, что и у остальных денег книги: числом — только
            # одну валюту (см. _units_num), иначе текст.
            "debt_val": debt_val, "debt_unit": debt_unit,
            "state": pr["state"],
            "days_overdue": pr["days_overdue"],
            "overdue_bucket": funnel.overdue_bucket(pr["days_overdue"]),
            "last_payment": last_day.isoformat() if last_day else "",
            "last_payment_sum": _v(p["contracts"], "last_payment_sum") or "",
            "status": e["status"], "counted": bool(pr.get("counted")),
        })
    out.sort(key=lambda x: (x["date"], x["employee"], x["client"]))
    return out


def _range_line(title: str, items: list[dict], unit_text: str = "") -> list:
    """Строка ранжирования: что стало с обещаниями одного размера И одной
    валюты. Валюта в строке обязательна: до 22.09.2026 лист складывал суммы
    разных валют в одну колонку с подписью «Обещано, ₽» — 900 000 + 9 000 000
    давали «9 900 000 ₽», которых не существует."""
    kept = [x for x in items if x["state"] == funnel.PROMISE_KEPT]
    broken = [x for x in items if x["state"] == funnel.PROMISE_BROKEN]
    base = len(items)
    kp = funnel.rate(len(kept), len(items))
    bp = funnel.rate(len(broken), len(items))
    days = [x["days_overdue"] for x in broken]
    return [title, unit_text, base,
            len(kept), "" if kp is None else kp / 100,
            len(broken), "" if bp is None else bp / 100,
            sum(1 for x in items if x["state"] == funnel.PROMISE_PENDING),
            sum(1 for x in items if x["state"] == funnel.PROMISE_NODATE),
            sum(x["sum"] for x in items),
            sum(x["sum"] for x in broken),
            sum(x["debt_num"] for x in broken),
            round(sum(days) / len(days)) if days else "",
            max(days) if days else ""]


def _office_country(rows: list[dict], names: dict) -> str:
    """Республика офиса — по республикам его сотрудников.

    У офисных строк колонка «Республика» пустовала, и фильтр по республике
    выбрасывал их из книги целиком: руководитель направления, оставив свою
    республику, переставал видеть собственные офисы. Офис почти всегда в одной
    республике; если в нём люди из разных — перечисляем обе, а не молчим.
    """
    labels = sorted({names.get(r.get("country"), "") for r in rows} - {""})
    return ", ".join(labels)


def _funnel_sheets(bk, data: dict, rows: list[dict], names: dict,
                   rng: dict) -> None:
    """Листы воронки — в stats_book (книги пишутся xlsxwriter с нативными
    типами ячеек, см. book.py). Импорт ленивый: stats_book сам зовёт сюда."""
    import stats_book
    stats_book.funnel_sheets(bk, data, rows, names, rng)


def stats_xlsx(mgr: dict, rng: dict, employees: list[str] | None = None) -> bytes:
    """Статистика книгой Excel — «Сводка», «Сотрудники», «Республики», «Офисы»,
    «Выезды», «По дням» и воронка. Пишет stats_book (нативные типы ячеек)."""
    import stats_book
    return stats_book.stats_xlsx(mgr, rng, employees)


# --- Сводный отчёт руководителю: закреплено / сегодня / всего ----------------
# Просьба руководителей 08.09.2026: «общей таблицей по всем сотрудникам —
# закреплено, отработано сегодня, отработано всего по нарастающей», причём
# Таджикистан и Узбекистан вместе. Вместе они и так: направление «СНГ» видит
# обе республики, а колонка «Республика» разделяет их внутри одной таблицы.
DAILY_REPORT_COLUMNS = (
    ("employee", "Сотрудник", 28),
    ("country_name", "Республика", 14),
    ("assigned_persons", "Закреплено должников", 14),
    ("assigned_contracts", "Закреплено договоров", 14),
    ("assigned_money", "Долг закреплённый", 18),
    ("today_persons", "Отработано сегодня", 14),
    ("today_visits", "Выездов сегодня", 13),
    ("today_paid", "Оплачен сегодня", 13),
    ("today_promised", "Обещают сегодня", 13),
    ("today_photo", "С фото сегодня", 13),
    ("overdue", "Просрочено обещаний", 14),
    ("confirmed", "На адресе подтверждено", 14),
    ("far", "У адреса не был", 13),
    ("unverified", "Нечем проверить", 13),
    ("total_persons", "Отработано всего", 13),
    ("total_visits", "Выездов всего", 12),
    ("first_day", "Первый выезд", 13),
)
# «Всего» считается с этой даты — раньше боевых выездов не было, а вычитать
# демо-строки из отчёта руководителя не за чем.
TOTALS_SINCE = "2026-01-01"


def daily_report_data(mgr: dict, on_date: str,
                      employees: list[str] | None = None) -> dict:
    """Строки вечерней сводки: закреплено / сегодня / всего. Это та же сводка
    за окно, что лист «Сводка» книги за период (stats_book.period_report_data),
    с окном в один день — два расчёта одной таблицы разошлись бы."""
    import stats_book
    return stats_book.daily_report_data(mgr, on_date, employees)


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
    # Подтверждение выезда — одной строкой: на адресе / не был / нечем
    # проверить. Ноль по всем трём — строки нет (старые оболочки).
    if t.get("confirmed") or t.get("far") or t.get("unverified"):
        parts.append(f"🛡 На адресе подтверждено: <b>{t.get('confirmed', 0)}</b> · "
                     f"у адреса не был: <b>{t.get('far', 0)}</b> · "
                     f"нечем проверить: {t.get('unverified', 0)}"
                     + (f" · заполнено не на адресе: {t['saved_away']}"
                        if t.get("saved_away") else "")
                     + (f" · признаки серии: {t['series']}" if t.get("series") else ""))
    parts.append("Таблица по каждому сотруднику — в файле; "
                 "разбор по дням и карточки — во вкладке «Сводка» приложения.")
    return "\n".join(parts)


def daily_report_xlsx(mgr: dict, on_date: str,
                      employees: list[str] | None = None,
                      data: dict | None = None) -> bytes:
    """Вечерняя книга (Сводка, Исходы за день, воронка дня) — пишет stats_book."""
    import stats_book
    return stats_book.daily_report_xlsx(mgr, on_date, employees, data)


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
               "outcomes": r["outcomes"],
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
        outcomes: dict[str, int] = {}
        for x in mine:
            for o in x["outcomes"]:
                outcomes[o["status"]] = outcomes.get(o["status"], 0) + o["n"]
        countries.append({
            "outcomes": [{"status": s_, "n": n_, "emoji": _st_emoji(s_)}
                         for s_, n_ in sorted(outcomes.items(), key=lambda kv: (-kv[1], kv[0]))],
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
