"""Лента активности команды и «Контроль» безопасника (Пульты 2, 29.09.2026).

Зачем «Контроль». В Таджикистане выездник берёт у должника наличные «в счёт
долга» и не сдаёт их, либо пишет выезд, которого не было. Прямой улики в данных
нет — есть признаки: выезд далеко от адреса или без точки телефона, выезд,
занесённый задним числом, карточка, заполненная за полминуты, выезд ночью, серия
сохранений из одной точки, «наличные» в тексте, «оплатил», за которым в реестре
не пришло ни копейки. Каждый признак сам по себе ничего не доказывает —
подтверждает подозрение только звонок должнику (контрольный обзвон).

Решение от 03.10.2026 (слово Егора): процентов и сводного балла риска НЕТ —
только счётчики по каждому признаку, отдельно. Адреса в реестре неровные
(большая часть — до города/района), и любая доля обвиняла бы того, у кого
адреса точнее. «Далеко от адреса» считается только по проверяемым выездам
(точный адрес + годная GPS-точка), база показывается рядом.

Всё считается по сохранённым фактам визита (evidence, series, received_at),
правила живут здесь и могут меняться без миграций.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from datetime import date, datetime, timedelta, timezone

import db
import workday
import digest_data
import evidence

TZ = evidence.FIELD_TZ
NO_OFFICE = digest_data.NO_OFFICE
NO_OFFICE_NAME = digest_data.NO_OFFICE_NAME

FAST_FILL_S = 40          # карточку быстрее не заполнить честно
NIGHT_FROM, NIGHT_TO = 21, 7
PAID_LAG_DAYS = 3         # платёж доезжает до реестра не сразу
CALLBACK_SHARE = 0.10
CALLBACK_MIN = 3
CALLBACK_FLAGGED_MAX = 10

# «Наличные» словами выездника: русский, таджикский, узбекский (кириллица и
# латиница). Корни подобраны так, чтобы не ловить «наличие», «передал документы».
_CASH_RE = re.compile(
    r"наличн(?!ост|ие|ия|ии|ый\s+адрес)|налом|кэш|\bкеш\b|cash"
    r"|(?:передал|отдал|вручил|дал)[аи]?\s+(?:мне\s+|нам\s+)?(?:деньг|денег|сумм|\d)"
    r"|деньги\s+(?:у\s+меня|отдал|передал|забрал)|забрал[аи]?\s+деньг"
    r"|пул(?:ро|и)?\s+(?:до?д|супорд|гирифт)|пулро\s+ба\s+ман"
    r"|нақд|накд|naqd|pul(?:ni)?\s+(?:berdi|berdim|oldim|topshir)"
    r"|пул(?:ни)?\s+(?:берди|бердим|олдим|топшир)",
    re.I)

SIGNALS = (
    ("far", "далеко от адреса",
     "точка сохранения дальше допустимого от адреса; судим ТОЛЬКО проверяемые "
     "выезды — адрес в реестре точный и телефон дал годную точку"),
    ("nogeo", "нет годной GPS-точки",
     "телефон не передал координаты или точка грубее 1,5 км — выезд по месту "
     "проверить не удалось"),
    ("late", "внесено позже", "выезды, доехавшие до сервера на следующий день и позже; причиной могла быть работа без связи"),
    ("fast", f"карточка быстрее {FAST_FILL_S} с", "время между открытием и сохранением карточки; короткое время само по себе не подтверждает нарушение"),
    ("night", "ночью", f"выезды, сохранённые с {NIGHT_FROM}:00 до {NIGHT_TO:02d}:00 по времени офисов"),
    ("series", "внесено пачкой", "несколько выездов внесено подряд из одной точки при разных адресах или с невозможной скоростью перемещения"),
    ("cash", "«наличные» в тексте", "выезды, где в комментарии деньги переданы сотруднику"),
    ("paid", "«оплатил» без платежа", f"исход «Оплачен», а платежа в реестре после выезда нет (старше {PAID_LAG_DAYS} дн.)"),
    ("bymgr", "внесено руководителем", "выезды, занесённые со счёта руководителя (act_as), а не с телефона сотрудника: сам по себе не нарушение, но для СБ — где статистику поправили руками"),
    ("dupphoto", "фото использовано дважды", "снимки, приложенные к РАЗНЫМ должникам: один файл переиспользован (хэш sha256)"),
    ("repeat", "ездит без результата", "должники, к которым за окно было 3+ выезда без оплаты и обещания"),
    ("callback", "обзвон: не подтвердилось", "контрольный звонок: выезда не было или деньги отданы сотруднику"),
    ("addr_coarse", "адрес неточный",
     "адрес в реестре известен только до города или района — выезд по месту "
     "проверить нельзя. Это качество данных, а не поведение сотрудника: "
     "в балл риска не входит"),
)
SIGNAL_LABEL = {k: label for k, label, _ in SIGNALS}
# Решение от 03.10.2026 (слово Егора): процентов в «Контроле» НЕ ПОКАЗЫВАЕМ
# ВООБЩЕ — ни долей признака, ни сводного балла риска. Адреса в реестре
# неровные (большая часть — до города/района), и любая доля обвиняла бы того,
# у кого адреса точнее, и хвалила бы того, у кого проверять нечего. Витрина —
# ТОЛЬКО счётчики: сколько выездов с каждым признаком, каждый отдельно.
# «Далеко от адреса» при этом считается по проверяемым выездам (точный адрес
# + годная GPS-точка), и его база показывается рядом.
COUNTER_KEYS = ("far", "nogeo", "addr_coarse", "late", "fast", "night", "series",
                "cash", "paid", "bymgr", "dupphoto", "repeat", "callback")


# --- время ---------------------------------------------------------------------
def _local_dt(value) -> datetime | None:
    """Момент в поясе офисов (+5). received_at — наивное местное время сервера,
    saved_at — миллисекунды или ISO от телефона."""
    if value in (None, ""):
        return None
    ms = evidence.to_ms(value)
    if ms is not None:
        return datetime.fromtimestamp(ms / 1000.0, TZ)
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return dt.astimezone(TZ)


def _hhmm(dt: datetime | None) -> str:
    return dt.strftime("%H:%M") if dt else ""


# --- команда -------------------------------------------------------------------
def team_uids(team: list[str]) -> dict[int, str]:
    """{uid: ФИО} для телефонов команды (одобренные привязки)."""
    if not team:
        return {}
    keys = {db._name_key(e): e for e in team}
    out: dict[int, str] = {}
    with db.connect() as conn:
        for r in conn.execute("SELECT telegram_id, employee FROM agent_bind WHERE approved = 1"):
            emp = keys.get(db._name_key(r["employee"] or ""))
            if emp:
                out[int(r["telegram_id"])] = emp
    return out


def offices_for(team: list[str]) -> tuple[dict[str, str], list[dict]]:
    """({ФИО: код офиса}, чипы офисов). Код «!none» — не закреплён."""
    index = db.employee_office_index()
    of = {e: (db.office_of(e, index) or NO_OFFICE) for e in team}
    counts: dict[str, int] = {}
    for code in of.values():
        counts[code] = counts.get(code, 0) + 1
    chips = [{"code": c, "label": c, "n": n} for c, n in sorted(counts.items())
             if c != NO_OFFICE]
    if NO_OFFICE in counts:
        chips.append({"code": NO_OFFICE, "label": NO_OFFICE_NAME, "n": counts[NO_OFFICE]})
    return of, chips


def _pick(team: list[str], of: dict[str, str], office: str, employee: str) -> list[str]:
    out = [e for e in team if not office or of.get(e) == office]
    if employee:
        out = [e for e in out if e == employee]
    return out


# --- выезды окна -----------------------------------------------------------------
def _payloads(ids: list[int]) -> dict[int, str]:
    if not ids:
        return {}
    out: dict[int, str] = {}
    with db.connect() as conn:
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = ",".join("?" * len(chunk))
            for r in conn.execute(f"SELECT id, payload FROM visits WHERE id IN ({q})", chunk):
                out[int(r["id"])] = r["payload"] or ""
    return out


def _comment_of(payload: str) -> str:
    """Свободный текст выездника — всё после «На адресе: …;» (см. web._save)."""
    text = str(payload or "")
    m = re.search(r"На адресе:[^;]*;\s*(.*)$", text, re.S)
    if m:
        return m.group(1).strip()
    m = re.search(r"Договорённость:[^;]*;\s*(.*)$", text, re.S)
    return (m.group(1).strip() if m else "")


def _last_payment(p: dict) -> date | None:
    best = None
    for c in p["contracts"]:
        d = db._as_date(str(digest_data._cell_of(c, "last_payment_date") or "")[:10])
        if d and (best is None or d > best):
            best = d
    return best


def _visit_flags(e: dict, payload: str, today: date,
                 tids: set[int] | None = None) -> list[str]:
    flags = []
    # Гео-вердикт — три РАЗНЫХ утверждения, которые раньше склеивались в один
    # «нечем проверить»: «далеко» винит человека и живёт только на проверяемых
    # выездах; «нет точки» — телефон сотрудника не дал себя проверить;
    # «адрес неточный» — качество данных реестра, человека не обвиняет.
    lvl = e.get("evidence_level") or ""
    if lvl == "far":
        flags.append("far")
    if lvl in ("nogeo", "nofix") or (not lvl and not e.get("geo")):
        flags.append("nogeo")
    if lvl == "coarse":
        flags.append("addr_coarse")
    if (e.get("late_days") or 0) >= 1:
        flags.append("late")
    fs = e.get("fill_seconds")
    if isinstance(fs, (int, float)) and 0 <= fs < FAST_FILL_S:
        flags.append("fast")
    t = _local_dt(e.get("saved_at") or e.get("received_at"))
    if t and (t.hour >= NIGHT_FROM or t.hour < NIGHT_TO):
        flags.append("night")
    if evidence.is_series(e.get("series")):
        flags.append("series")
    if _CASH_RE.search(_comment_of(payload)):
        flags.append("cash")
    # «Кем закрыто»: визит занесён аккаунтом, не привязанным к этому ФИО, —
    # руководитель через act_as. Для выездника это не нарушение (гео-контроль
    # его и не судит), но СБ должен видеть, где числа занесены чужими руками.
    if tids is not None and e.get("agent_id") is not None \
            and e["agent_id"] not in tids:
        flags.append("bymgr")
    day = db._as_date(e["date"])
    if "Оплачен" in (e.get("status") or "") and day and (today - day).days >= PAID_LAG_DAYS:
        paid = _last_payment(e["person"])
        if paid is None or paid < day:
            flags.append("paid")
    return flags


def _dup_photos(tids: list[int], d_from: str, d_to: str) -> int:
    """Сколько снимков этого человека за окно лежит у РАЗНЫХ должников.

    Один и тот же файл (sha256) у двух КД не бывает честно: это переиспользование
    контрольного фото. Считаем группами по хэшу, а не файлами, — пять копий одного
    снимка это один сигнал, а не пять."""
    if not tids:
        return 0
    hi = (date.fromisoformat(d_to) + timedelta(days=1)).isoformat()
    qs = ",".join("?" * len(tids))
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT sha256, COUNT(DISTINCT contract_id) AS dirs FROM photo_geo "
            f"WHERE telegram_id IN ({qs}) AND sha256 IS NOT NULL "
            "AND created_at >= ? AND created_at < ? "
            "GROUP BY sha256 HAVING dirs > 1",
            (*tids, d_from, hi)).fetchall()
    return len(rows)


def _entries(emp: str, d_from: str, d_to: str) -> list[dict]:
    return digest_data._employee_stats(emp, d_from, d_to, with_photos=False)["_entries"]


def _phones(p: dict) -> list[str]:
    seen, out = set(), []
    for c in p["contracts"]:
        for key in ("phone", "phone_main"):
            raw = str(digest_data._cell_of(c, key) or "")
            for part in re.split(r"[,;/]|\s{2,}", raw):
                digits = re.sub(r"\D", "", part)
                if len(digits) >= 7 and digits not in seen:
                    seen.add(digits)
                    out.append(part.strip())
    return out[:6]


def _address(p: dict) -> str:
    for c in p["contracts"]:
        a = str(digest_data._cell_of(c, "address") or "").strip()
        if a:
            return a
    return ""


def _visit_event(e: dict, emp: str, office: str, payload: str, today: date,
                 tids: set[int] | None = None) -> dict:
    p = e["person"]
    saved = _local_dt(e.get("saved_at"))
    got = _local_dt(e.get("received_at"))
    at = saved or got
    late_h = None
    if saved and got:
        late_h = max(0, round((got - saved).total_seconds() / 3600))
    status = e.get("status") or ""
    detail = _comment_of(payload)
    return {
        "id": f"v{e['_vid']}", "visit_id": e["_vid"], "kind": "visit",
        "at": at.isoformat(timespec="seconds") if at else None,
        "time": _hhmm(at), "day": e["date"],
        "employee": emp, "office": "" if office == NO_OFFICE else office,
        "rep": p["rep"], "debtor": p["client"],
        "title": status or "Выезд", "agreed": e.get("agreed") or "",
        "detail": detail[:280],
        "evidence": e.get("evidence_level") or "",
        "evidence_note": ((e.get("ev") or {}).get("note") or ""),
        "fill_s": e.get("fill_seconds"), "late_days": e.get("late_days"), "late_h": late_h,
        "flags": _visit_flags(e, payload, today, tids),
    }


# --- лента активности -----------------------------------------------------------
_AUDIT_KINDS = {"view_card": "view", "photo": "photo", "app_login": "login",
                "marker": "mark", "promise_set": "promise", "receipt": "receipt",
                "receipt_view": "receipt_view", "save_call": "call", "save_card": "edit"}
_AUDIT_TITLE = {"view": "открыл карточку", "photo": "фото", "login": "вошёл в приложение",
                "mark": "метка на карте", "promise": "записал обещание",
                "receipt": "приложил чек", "receipt_view": "открыл чек",
                "call": "сохранил звонок", "edit": "изменил карточку"}


def activity(mgr: dict, team: list[str], rng: dict, *, office: str = "",
             employee: str = "", kinds: set[str] | None = None,
             before: str = "", limit: int = 200) -> dict:
    kinds = kinds or {"visit", "photo", "view", "login", "mark", "promise", "receipt", "receipt_view", "call", "edit"}
    of, chips = offices_for(team)
    people = _pick(team, of, office, employee)
    today = db._as_date(digest_data.today_iso())
    events: list[dict] = []
    rows: list[dict] = []
    for emp in people:
        ents = _entries(emp, rng["from"], rng["to"])
        pay = _payloads([e["_vid"] for e in ents])
        evs = [_visit_event(e, emp, of[emp], pay.get(e["_vid"], ""), today) for e in ents]
        times = sorted(x["at"] for x in evs if x["at"])
        rows.append({"employee": emp, "office": "" if of[emp] == NO_OFFICE else of[emp],
                     "visits": len(evs),
                     "calls": 0,
                     "first": times[0][11:16] if times else "",
                     "last": times[-1][11:16] if times else "",
                     "far": sum("far" in x["flags"] for x in evs),
                     "late": sum("late" in x["flags"] for x in evs),
                     "flags": sum(bool(x["flags"]) for x in evs),
                     "last_seen": None})
        if "visit" in kinds:
            events.extend(evs)
    # Телефон в руках: открытия карточек, фото, входы — из журнала доступа.
    uids = team_uids(people)
    last_seen: dict[str, str] = {}
    if uids:
        lo = rng["from"] + "T00:00:00"
        hi = (db._as_date(rng["to"]) + timedelta(days=1)).isoformat() + "T00:00:00"
        q = ",".join("?" * len(uids))
        with db.connect() as conn:
            seen = conn.execute(
                f"SELECT actor, MAX(ts) ts FROM audit WHERE actor IN ({q}) GROUP BY actor",
                list(uids)).fetchall()
            wanted = [a for a, k in _AUDIT_KINDS.items() if k in kinds and a not in ("receipt", "save_call")]
            arows = []
            if wanted:
                qa = ",".join("?" * len(wanted))
                arows = conn.execute(
                    f"SELECT id, ts, actor, action, target FROM audit WHERE actor IN ({q}) "
                    f"AND action IN ({qa}) AND ts >= ? AND ts < ? ORDER BY id DESC LIMIT 3000",
                    [*uids, *wanted, lo, hi]).fetchall()
        for r in seen:
            emp = uids.get(int(r["actor"]))
            t = _local_dt(r["ts"])
            if emp and t and (emp not in last_seen or t.isoformat() > last_seen[emp]):
                last_seen[emp] = t.isoformat(timespec="seconds")
        names = _client_names([r["target"] for r in arows if r["target"]])
        for r in arows:
            emp = uids.get(int(r["actor"]))
            if not emp:
                continue
            kind = _AUDIT_KINDS[r["action"]]
            t = _local_dt(r["ts"])
            events.append({
                "id": f"a{r['id']}", "kind": kind,
                "at": t.isoformat(timespec="seconds") if t else None, "time": _hhmm(t),
                "day": (t.date().isoformat() if t else ""), "employee": emp,
                "office": "" if of.get(emp) == NO_OFFICE else of.get(emp, ""),
                "rep": r["target"] or "", "debtor": names.get(r["target"] or "", ""),
                "title": _AUDIT_TITLE[kind], "detail": "", "flags": []})
    # Завершённый звонок и чек — из неизменяемых журналов с автором события,
    # а не из сегодняшней привязки аккаунта. Повтор доставки не плодит действия.
    if people:
        marks = ','.join('?' * len(people))
        with db.connect() as conn:
            journals = []
            journals += [("call", dict(r)) for r in conn.execute(
                    f"SELECT * FROM calls WHERE employee IN ({marks}) AND created_at >= ? AND created_at <= ?",
                    (*people, rng["from"], rng["to"] + "T23:59:59"))]
            if "receipt" in kinds:
                lo = (date.fromisoformat(rng["from"]) - timedelta(days=1)).isoformat()
                hi = (date.fromisoformat(rng["to"]) + timedelta(days=2)).isoformat()
                journals += [("receipt", dict(r)) for r in conn.execute(
                    f"SELECT * FROM payment_evidence WHERE employee IN ({marks}) AND created_at >= ? AND created_at < ?",
                    (*people, lo, hi))]
        names = _client_names([r["contract_id"] for _, r in journals])
        by_employee = {row["employee"]: row for row in rows}
        for kind, r in journals:
            t = _local_dt((r.get("saved_at") or r.get("received_at")) if kind == "call" else r["created_at"])
            if not t or not rng["from"] <= t.date().isoformat() <= rng["to"]:
                continue
            emp = r["employee"]
            title, detail, flags = _AUDIT_TITLE[kind], "", []
            if kind == "call":
                try:
                    values = json.loads(r.get("values_json") or "{}")
                except (ValueError, TypeError):
                    values = {}
                status = str(values.get("soft_call_status") or "")
                title = "Звонок" + (": " + status if status else "")
                detail = str(values.get("soft_comment") or r.get("payload") or "")
                callback = values.get("soft_next_call_date")
                if "overall_result" not in values:  # журнал v6: прежнее имя перезвона
                    callback = values.get("soft_comm_date")
                if callback:
                    detail += " · следующий контакт: " + str(callback)
                received = _local_dt(r.get("received_at"))
                if received and received.date() > db._as_date(r["created_at"]):
                    flags.append("late")
                if t.hour >= NIGHT_FROM or t.hour < NIGHT_TO:
                    flags.append("night")
                if _CASH_RE.search(detail):
                    flags.append("cash")
                summary = by_employee[emp]
                summary["calls"] += 1
                summary["late"] += int("late" in flags)
                summary["flags"] += int(bool(flags))
                hhmm = _hhmm(t)
                summary["first"] = min(summary["first"], hhmm) if summary["first"] else hhmm
                summary["last"] = max(summary["last"], hhmm) if summary["last"] else hhmm
                if kind not in kinds:
                    continue
            if kind == "receipt" and r.get("source") == "manual":
                title = "Записана оплата"
                detail = db.reported_payment_text(r)
            events.append({"id": f"{kind}{r['id']}", "kind": kind,
                "call_id": r["id"] if kind == "call" else None,
                "at": t.isoformat(timespec="seconds"), "time": _hhmm(t),
                "day": t.date().isoformat(), "employee": emp,
                "office": "" if of.get(emp) == NO_OFFICE else of.get(emp, ""),
                "rep": r["contract_id"], "debtor": names.get(r["contract_id"], ""),
                "title": title, "detail": detail, "flags": flags,
                "visit_op": r.get("visit_op"), "op": r.get("op_id") or r.get("op"),
                "received_at": r.get("received_at"), "actor_id": r.get("agent_id")})
    for row in rows:
        row["last_seen"] = last_seen.get(row["employee"])
    events.sort(key=lambda x: (x["at"] or "", x["id"]), reverse=True)
    if before:
        if "|" in before:
            stamp, event_id = before.rsplit("|", 1)
            events = [x for x in events if (x["at"] or "", x["id"]) < (stamp, event_id)]
        else:
            events = [x for x in events if (x["at"] or "") < before]
    limit = max(1, min(500, int(limit or 200)))
    page = events[:limit]
    rows.sort(key=lambda r: (-(r["visits"] + r["calls"]), r["employee"].lower()))
    return {"screen": "activity", "range": rng, "tz": "+05:00", "offices": chips,
            "employees": rows, "events": page,
            "next_before": (page[-1]["at"] + "|" + page[-1]["id"]) if len(events) > limit and page else None,
            "signals": [{"key": k, "label": label} for k, label, _ in SIGNALS],
            "generated_at": datetime.now(TZ).isoformat(timespec="seconds")}


def _client_names(reps: list[str]) -> dict[str, str]:
    reps = sorted({r for r in reps if r})
    if not reps:
        return {}
    out: dict[str, str] = {}
    for r in db.clients_by_ids(reps):
        out[r["contract_id"]] = r["client"] or ""
    return out


# --- контроль: риск по людям --------------------------------------------------------
def _person_signals(ents: list[dict], pay: dict[int, str], today: date,
                    checks: list[dict], tids: set[int] | None = None,
                    d_from: str = "", d_to: str = "") -> tuple[dict, list[dict]]:
    per_visit = [(e, _visit_flags(e, pay.get(e["_vid"], ""), today, tids))
                 for e in ents]
    # Проверяемые выезды: вердикт по месту вообще возможен — адрес точный
    # И телефон дал годную точку (evidence.py confirmed/far). «Далеко от
    # адреса» считаем только среди них и показываем базу рядом с числом:
    # «3 из 12» честнее любого процента, когда у половины портфеля адрес
    # известен до района.
    v_n = sum(1 for e, _ in per_visit
              if (e.get("evidence_level") or "") in ("confirmed", "far"))
    counts = {k: 0 for k in COUNTER_KEYS}
    for _, f in per_visit:
        for k in f:
            if k in counts:
                counts[k] += 1
    counts["verifiable"] = v_n
    if d_from and d_to:
        counts["dupphoto"] = _dup_photos(sorted(tids) if tids else [], d_from, d_to)
    by_person: dict[str, list[dict]] = {}
    for e in ents:
        by_person.setdefault(e["person"]["rep"], []).append(e)
    counts["repeat"] = sum(
        1 for items in by_person.values() if len(items) >= 3 and not any(
            ("Оплачен" in (x.get("status") or "") or "Обещ" in (x.get("status") or "")
             or "Обещ" in (x.get("agreed") or "")) for x in items))
    bad = [c for c in checks if c.get("outcome") in ("no_visit", "cash_to_employee")]
    counts["callback"] = len(bad)
    return counts, per_visit


def overview(mgr: dict, team: list[str], rng: dict, *, office: str = "") -> dict:
    of, chips = offices_for(team)
    people = _pick(team, of, office, "")
    today = db._as_date(digest_data.today_iso())
    checks_all = db.control_checks_between(rng["from"], rng["to"])
    rows = []
    for emp in people:
        ents = _entries(emp, rng["from"], rng["to"])
        pay = _payloads([e["_vid"] for e in ents])
        checks = [c for c in checks_all if c.get("employee") == emp]
        tids = set(db.agent_tids(emp))
        counts, _ = _person_signals(ents, pay, today, checks, tids,
                                    rng["from"], rng["to"])
        done = [c for c in checks if c.get("outcome")]
        rows.append({
            "employee": emp,
            "office": "" if of[emp] == NO_OFFICE else of[emp],
            "visits": len(ents),
            # Качество гео — отдельно от поведения: сколько выездов вообще
            # можно проверить по месту и у скольких адрес неточен.
            "verifiable": counts["verifiable"],
            "addr_coarse": counts["addr_coarse"],
            "counts": counts,
            "checks": {"selected": len(checks), "done": len(done),
                       "no_visit": sum(c["outcome"] == "no_visit" for c in done),
                       "cash": sum(c["outcome"] == "cash_to_employee" for c in done)}})
    # No hidden aggregate score: the reviewer chooses which factual column
    # to sort. Default ordering conveys no judgement about the employee.
    rows.sort(key=lambda x: x["employee"].casefold())
    return {"screen": "control", "range": rng, "offices": chips, "rows": rows,
            "legend": [{"key": k, "label": label, "hint": hint} for k, label, hint in SIGNALS],
            "outcomes": [{"key": k, "label": v} for k, v in db.CONTROL_OUTCOMES.items()],
            # Как читать — одним объяснением на все витрины «Контроля».
            "how": [
                "Здесь только факты: сколько выездов с каждым признаком. Проценты и сводный балл сознательно не показываются — адреса в реестре неровные, и доля обвиняла бы того, у кого адреса точнее.",
                "«Далеко от адреса» — повод проверить обстоятельства. Считается только при точном адресе и годной GPS-точке телефона; точка сохранения сама по себе не доказывает место или отсутствие выезда. База показана рядом.",
                "«Нет годной GPS-точки» и «адрес неточный» — почему выезд не удалось проверить: первое — про телефон сотрудника, второе — про качество данных и в вину не ставится.",
                "Дополнительная проверка — контрольный обзвон по выезду. Ответ должника записывается отдельно и требует сопоставления с остальными данными; автоматически нарушение не устанавливается.",
            ]}


def flags_of(mgr: dict, team: list[str], rng: dict, employee: str) -> dict | None:
    if employee not in team:
        return None
    of, _ = offices_for(team)
    today = db._as_date(digest_data.today_iso())
    ents = _entries(employee, rng["from"], rng["to"])
    pay = _payloads([e["_vid"] for e in ents])
    tids = set(db.agent_tids(employee))
    visits = [_visit_event(e, employee, of[employee], pay.get(e["_vid"], ""), today,
                           tids)
              for e in ents]
    visits.sort(key=lambda x: (x["at"] or "", x["id"]), reverse=True)
    return {"screen": "control_flags", "range": rng, "employee": employee,
            "office": "" if of[employee] == NO_OFFICE else of[employee],
            "visits": visits,
            "labels": {k: label for k, label, _ in SIGNALS}}


# --- контрольный обзвон ------------------------------------------------------------
_STRONG = ("cash", "far", "series", "fast", "night", "dupphoto")


def _rank(visit_id: int, day: str) -> str:
    return hashlib.sha256(f"{day}:{visit_id}".encode()).hexdigest()


def callbacks(mgr: dict, team: list[str], day: str, *, office: str = "") -> dict:
    of, chips = offices_for(team)
    people = _pick(team, of, office, "")
    today = db._as_date(digest_data.today_iso())
    items_by_vid: dict[int, dict] = {}
    for emp in people:
        ents = _entries(emp, day, day)
        pay = _payloads([e["_vid"] for e in ents])
        tids = set(db.agent_tids(emp))
        for e in ents:
            payload = pay.get(e["_vid"], "")
            items_by_vid[e["_vid"]] = {"e": e, "emp": emp, "payload": payload,
                                       "flags": _visit_flags(e, payload, today, tids)}
    chosen = db.control_checks_for(day)
    fresh = [v for v in items_by_vid if v not in chosen]
    if fresh and len(chosen) < len(items_by_vid):
        # Бюджеты — на ДЕНЬ, а не на запрос: иначе каждое открытие экрана
        # добавляло бы в список ещё десяток выездов.
        already_flagged = sum(1 for c in chosen.values()
                              if (c.get("selected_why") or "").startswith("по следам"))
        already_random = sum(1 for c in chosen.values()
                             if (c.get("selected_why") or "").startswith("случайная"))
        flagged = sorted((v for v in fresh if any(f in _STRONG for f in items_by_vid[v]["flags"])),
                         key=lambda v: _rank(v, day))[:max(0, CALLBACK_FLAGGED_MAX - already_flagged)]
        want = max(CALLBACK_MIN, math.ceil(CALLBACK_SHARE * len(items_by_vid)))
        rest = sorted((v for v in fresh if v not in flagged), key=lambda v: _rank(v, day))
        rand = rest[:max(0, want - already_random)]
        picks = []
        for v in flagged:
            picks.append((v, "по следам", items_by_vid[v]["emp"]))
        picks += [(v, "случайная выборка", items_by_vid[v]["emp"]) for v in rand]
        if picks:
            db.control_select(day, picks)
            chosen = db.control_checks_for(day)
    out = []
    for vid, chk in chosen.items():
        it = items_by_vid.get(vid)
        if it is None:
            continue                       # выезд чужого офиса или вне охвата
        e, p = it["e"], it["e"]["person"]
        promise = None
        pd = digest_data._v(p["contracts"], "promise_date")
        ps = digest_data._v(p["contracts"], "forecast_sum")
        if pd or ps:
            promise = {"date": str(pd or "")[:10], "sum": ps}
        out.append({
            "visit_id": vid, "employee": it["emp"],
            "office": "" if of[it["emp"]] == NO_OFFICE else of[it["emp"]],
            "day": e["date"], "time": _hhmm(_local_dt(e.get("saved_at") or e.get("received_at"))),
            "rep": p["rep"], "debtor": p["client"], "phones": _phones(p),
            "address": _address(p), "status": e.get("status") or "",
            "agreed": e.get("agreed") or "", "reported": _comment_of(it["payload"])[:600],
            "promise": promise, "flags": it["flags"],
            "why": chk.get("selected_why") or "",
            "state": "done" if chk.get("outcome") else "todo",
            "outcome": chk.get("outcome") or "", "note": chk.get("note") or "",
            "checked_at": chk.get("checked_at") or ""})
    out.sort(key=lambda x: (x["state"] == "done", x["office"], x["employee"], x["time"]))
    return {"screen": "callbacks", "date": day, "offices": chips, "items": out,
            "outcomes": [{"key": k, "label": v} for k, v in db.CONTROL_OUTCOMES.items()],
            "labels": {k: label for k, label, _ in SIGNALS},
            "script": [
                "Представиться: служба качества, звонок по вашему договору.",
                "Приезжал ли к вам наш сотрудник? Когда примерно?",
                "О чём договорились? Была ли оплата — как именно вы платили?",
                "Передавали ли вы деньги сотруднику в руки? Сколько?",
                "Выдали ли вам какой-нибудь документ об оплате?",
            ]}


def visit_in_scope(team: list[str], visit_id: int) -> bool:
    with db.connect() as conn:
        row = conn.execute("SELECT employee, contract_id FROM visits WHERE id = ?",
                           (int(visit_id),)).fetchone()
    if row is None:
        return False
    emp = row["employee"]
    if not emp:
        c = db.clients_by_ids([row["contract_id"]])
        emp = c[0]["responsible"] if c else ""
    keys = {db._name_key(e) for e in team}
    return db._name_key(emp or "") in keys


# --- выгрузка -----------------------------------------------------------------
# Колонки книги «Статистика»: счётчик на каждый признак, отдельно. Процентов
# и сводного балла в книге нет — решение от 03.10.2026 (см. COUNTER_KEYS).
_XLSX_COUNTERS = (
    ("far", "Далеко от адреса"),
    ("nogeo", "Нет GPS-точки"),
    ("addr_coarse", "Адрес неточный"),
    ("late", "Внесено позже"),
    ("fast", "За полминуты"),
    ("night", "Ночью"),
    ("series", "Внесено пачкой"),
    ("cash", "«Наличные» в тексте"),
    ("paid", "«Оплатил» без платежа"),
    ("bymgr", "Внесено руководителем"),
    ("dupphoto", "Фото использовано дважды"),
    ("repeat", "Без результата 3+ выезда"),
    ("callback", "Обзвон: не подтвердилось"),
)
_OUTCOME_FILL = {"confirmed": "#E3F2E1", "paid_elsewhere": "#E3F2E1",
                 "no_visit": "#FAE3E0", "cash_to_employee": "#FAE3E0"}
_CONTROL_HELP = {
    "Выездов": "Сколько выездов сотрудника попало в период.",
    "Проверяемых": "Выезды, которые вообще можно проверить по месту: адрес в реестре точный "
                   "И телефон дал годную точку. «Далеко от адреса» считается только среди них.",
    "Далеко от адреса": "Выезды, где точка сохранения была дальше допустимого от ТОЧНОГО адреса "
                        "должника. Прямое обвинение — проверить звонком.",
    "Нет GPS-точки": "Выезды, где телефон не передал координаты или точка грубее 1,5 км: "
                     "проверить по месту не удалось.",
    "Адрес неточный": "Выезды, где адрес в реестре известен только до города или района. "
                      "Качество данных, а не поведение сотрудника.",
    "За полминуты": f"Карточки, заполненные быстрее {FAST_FILL_S} с.",
    "Внесено пачкой": "Выезды из серии: несколько сохранений подряд из одной точки при разных адресах "
                      "или с невозможной скоростью перемещения.",
    "Внесено позже": "Выезды, доехавшие до сервера на следующий день и позже.",
    "«Оплатил» без платежа": f"Исход «Оплачен», а платежа в реестре после выезда нет (старше {PAID_LAG_DAYS} дн.).",
    "Внесено руководителем": "Выезды, занесённые со счёта руководителя (act_as), а не с телефона сотрудника.",
    "Фото использовано дважды": "Снимки, приложенные к РАЗНЫМ должникам: один файл переиспользован.",
    "Обзвон: не подтвердилось": "Контрольный звонок: выезда не было или деньги отданы сотруднику.",
    "Проверено звонком": "Сколько выездов сотрудника уже проверили звонком должнику.",
    "Не подтвердилось": "Звонок показал «выезда не было» или «отдал деньги сотруднику».",
    "Почему выбран": "Почему выезд попал в обзвон: случайная выборка или конкретный признак.",
    "Исход": "Что ответил должник.",
    "Когда": "Когда внесён результат звонка.",
}


def control_xlsx(mgr: dict, team: list[str], rng: dict) -> bytes:
    """Статистика признаков по людям и итоги обзвона одной книгой — в том же
    облике, что книга статистики (book.py): счётчик на каждый признак
    отдельной колонкой, процентов и сводного балла нет, исход звонка
    подсвечен, даты — датами. Текст со «=» — текстом: комментарий выездника
    не должен стать формулой у безопасника."""
    import book

    ov = overview(mgr, team, rng)
    period = f"{rng.get('label') or ''} ({rng['from']} — {rng['to']})".strip()
    bk = book.Book(_CONTROL_HELP.get)
    ws = bk.sheet("Статистика", tab="main")
    ws.title(f"Контроль выездов · {period}")
    ws.title("Только факты: сколько выездов с каждым признаком. Проценты и сводный "
             "балл сознательно не показываются: адреса в реестре неровные, и доля "
             "обвиняла бы того, у кого адреса точнее. «Далеко от адреса» считается "
             "только по проверяемым выездам — точный адрес и годная GPS-точка. "
             "Наведите мышь на заголовок — всплывёт пояснение.", italic=True)
    cols = (["Сотрудник", "Офис", "Выездов", "Проверяемых"]
            + [title for _, title in _XLSX_COUNTERS]
            + ["Проверено звонком", "Не подтвердилось"])
    head_at = ws.head(cols)
    kinds = (["text", "text", "int", "int"] + ["int"] * len(_XLSX_COUNTERS)
             + ["int", "int"])
    for row in ov["rows"]:
        c = row["counts"]
        ws.append([row["employee"], row["office"] or NO_OFFICE_NAME, row["visits"],
                   row["verifiable"]]
                  + [c.get(key, 0) for key, _ in _XLSX_COUNTERS]
                  + [row["checks"]["done"],
                     row["checks"]["no_visit"] + row["checks"]["cash"]],
                  kinds)
    ws.widths((30, 16, 9, 12) + (8,) * len(_XLSX_COUNTERS) + (12, 13))
    ws.autofilter(head_at, ws.row - 1, len(cols))
    ws.freeze("B4")

    ws2 = bk.sheet("Обзвон", tab="detail")
    ws2.title(f"Обзвон должников · {period}")
    ws2.title("Одна строка — один выезд, выбранный для проверки звонком.", italic=True)
    cols2 = ["День", "Сотрудник", "Должник", "Договор", "Почему выбран", "Исход",
             "Заметка", "Когда"]
    head2 = ws2.head(cols2)
    keys = {db._name_key(e) for e in team}
    checks = [c for c in db.control_checks_between(rng["from"], rng["to"])
              if db._name_key(c.get("employee") or "") in keys]
    info: dict[int, tuple[str, str]] = {}
    if checks:
        ids = [c["visit_id"] for c in checks]
        q = ",".join("?" * len(ids))
        with db.connect() as conn:
            for v in conn.execute("SELECT v.id, v.contract_id, c.client FROM visits v "
                                  "LEFT JOIN clients c ON c.contract_id = v.contract_id "
                                  f"WHERE v.id IN ({q})", ids):
                info[int(v["id"])] = (v["client"] or "", v["contract_id"] or "")
    ordered = sorted(checks, key=lambda x: (x["selected_on"], x.get("employee") or ""))
    kinds2 = ["date", "text", "text", "text", "text", "text", "text", "datetime"]
    for c in ordered:
        client, cid = info.get(int(c["visit_id"]), ("", ""))
        outcome = c.get("outcome") or ""
        ws2.append([c["selected_on"], c.get("employee") or "", client, cid,
                    c.get("selected_why") or "",
                    db.CONTROL_OUTCOMES.get(outcome, "") or "не звонили",
                    c.get("note") or "", c.get("checked_at") or ""],
                   kinds2, fills={5: _OUTCOME_FILL.get(outcome)})
    ws2.widths((11, 28, 30, 12, 34, 22, 40, 16))
    ws2.autofilter(head2, ws2.row - 1, len(cols2))
    ws2.freeze("C4")
    return bk.close()


# --- журнал закреплений (контур СБ, 01.10.2026) ------------------------------------
# «Любимчики»: кому стабильно приезжают денежные должники. Событие смены
# ответственного снимает импорт (окно факта), заявленная «Дата закрепления»
# хранится рядом и сличается: дата далеко от окна — закрепление задним числом.
CLAIMED_LAG_DAYS = 7


def _money_int(v) -> int:
    """Реестровые суммы приходят строками с пробелами — к целым рублям."""
    digits = re.sub(r"[^\d-]", "", str(v or ""))
    return int(digits) if digits not in ("", "-") else 0


def assignments(mgr: dict, team: list[str], rng: dict) -> dict:
    """Кому что приехало: события журнала закреплений и метрики «любимчиков».

    «Денежность» договора — долг сейчас (debt.contract_debt) плюс отметка
    «платил за 90 дней» (last_payment_date): точной истории платежей у сервера
    нет, и это честная оговорка отчёта. Сравнение — медиана полученных данным
    человеком против медианы ВСЕХ активных договоров команды."""
    import debt as debt_mod

    of, chips = offices_for(team)
    events = db.assignment_events_between(rng["from"], rng["to"], team)
    came_in = [e for e in events if e["employee"]]
    # Экономика договоров — по текущему состоянию строки (долг меняется, для
    # сравнения «кому отдают лучшее» текущего достаточно).
    econ: dict[str, dict] = {}
    pool: list[int] = []
    for emp in team:
        with db.connect() as conn:
            portfolio = conn.execute("SELECT * FROM clients WHERE responsible=? OR responsible_soft=?", (emp, emp)).fetchall()
        for r in portfolio:
            cid = r["contract_id"]
            if cid in econ:
                continue
            paid = db._as_date(r["last_payment_date"]) if "last_payment_date" in r.keys() else None
            recent_pay = bool(
                paid and (workday.today() - paid).days <= 90)
            econ[cid] = {"debt": debt_mod.contract_debt(r) or 0,
                         "recent_pay": recent_pay}
            pool.append(debt_mod.contract_debt(r) or 0)
    office_median = statistics.median(pool) if pool else 0

    rows: dict[str, dict] = {}
    for e in came_in:
        profile = e.get("profile", "hard")
        r = rows.setdefault((e["employee"], profile), {
            "employee": e["employee"],
            "profile": profile,
            "office": "" if of.get(e["employee"]) == NO_OFFICE else of.get(e["employee"], ""),
            "received": 0, "transfers": 0, "debts": [], "paying": 0,
            "backdated": 0, "from_peers": {}})
        c = econ.get(e["contract_id"]) or {"debt": 0, "recent_pay": False}
        r["received"] += 1
        r["debts"].append(c["debt"])
        if c["recent_pay"]:
            r["paying"] += 1
        if e["kind"] == "transfer":
            r["transfers"] += 1
            if e["prev_employee"]:
                r["from_peers"][e["prev_employee"]] = \
                    r["from_peers"].get(e["prev_employee"], 0) + 1
        claimed = db._as_date(e["claimed_at"] or "")
        window = db._as_date(e["window_to"] or "")
        if claimed and window and abs((claimed - window).days) > CLAIMED_LAG_DAYS:
            r["backdated"] += 1

    out_rows = []
    for r in rows.values():
        med = statistics.median(r["debts"]) if r["debts"] else 0
        top_peer, top_peer_n = "", 0
        for name, n in sorted(r["from_peers"].items(),
                              key=lambda kv: (-kv[1], kv[0])):
            top_peer, top_peer_n = name, n
            break
        out_rows.append({
            "employee": r["employee"], "office": r["office"], "profile": r["profile"],
            "received": r["received"], "transfers": r["transfers"],
            "paying": r["paying"], "backdated": r["backdated"],
            "median_debt": med, "office_median": office_median,
            "debt_ratio": round(med / office_median, 2) if office_median else None,
            "top_peer": top_peer, "top_peer_n": top_peer_n})
    out_rows.sort(key=lambda x: (-(x["median_debt"] or 0), x["employee"].lower()))
    # события — свежими сверху, с долгом и метками для витрины
    for e in events:
        e["debt"] = (econ.get(e["contract_id"]) or {}).get("debt")
    return {"screen": "assignments", "range": rng, "offices": chips,
            "rows": out_rows, "events": events[:500],
            "claimed_lag_days": CLAIMED_LAG_DAYS,
            "generated_at": datetime.now(TZ).isoformat(timespec="seconds")}


# --- лента выдачи входов (контур СБ) -----------------------------------------------
# «Кто кому выдал вход» и «кто что отозвал» — действия руководителей и аналитика
# над доступом. Для СБ это половина контроля руководителей: след сам по себе не
# приговор, но «любимчику выдали второй вход в 23:40» — вопрос.
_ACCESS_ACTIONS = {
    "mgr_app_link": "выдал вход сотруднику",
    "mgr_app_revoke": "сбросил вход",
    "pult_enroll": "ввёл пульт на компьютере",
    "pult_revoke": "отозвал пульт",
    "desk_enroll": "ввёл приложение выездника на ПК",
    "desk_revoke": "отозвал ПК-приложение",
    "revoke_access": "отозвал доступ",
    "bind_fix": "перепривязал аккаунт",
    "mgr_rebind": "переставил аккаунт",
    "mgr_unbind": "снял привязку",
    "bind_approved_by_link": "ссылка подтвердила заявку",
}


def access_log(rng: dict, team: list[str] | None = None) -> dict:
    """События доступа за окно, новые сверху. team — фильтр по цели (target =
    ФИО сотрудника) для руководителя; админ видит всё."""
    hi = (date.fromisoformat(rng["to"]) + timedelta(days=1)).isoformat()
    qs = ",".join("?" * len(_ACCESS_ACTIONS))
    wanted = sorted({e for e in (team or []) if e})
    scope = ""
    if team is not None:
        if not wanted:
            return {"screen": "access", "range": rng, "events": []}
        scope = f" AND target IN ({','.join('?' * len(wanted))})"
    with db.connect() as conn:
        mgrs = {int(r["telegram_id"]): (r["name"] or "")
                for r in conn.execute("SELECT telegram_id, name FROM managers")}
        rows = conn.execute(
            f"SELECT id, ts, actor, action, target, detail FROM audit "
            f"WHERE action IN ({qs}) AND ts >= ? AND ts < ? "
            + scope + " ORDER BY id DESC LIMIT 500",
            (*_ACCESS_ACTIONS, rng["from"], hi, *wanted)).fetchall()
    names: dict[int, str] = {}

    def name_of(uid: int) -> str:
        if uid not in names:
            names[uid] = mgrs.get(int(uid or 0), "") or db.get_binding(int(uid or 0)) or ""
        return names[uid]

    wanted = {e for e in (team or []) if e}
    events = []
    for r in rows:
        target = r["target"] or ""
        if wanted and target not in wanted:
            continue
        actor = int(r["actor"] or 0)
        events.append({
            "id": f"a{r['id']}", "ts": r["ts"], "day": str(r["ts"] or "")[:10],
            "actor": actor, "actor_name": name_of(actor),
            "action": r["action"], "label": _ACCESS_ACTIONS.get(r["action"], r["action"]),
            "target": target, "detail": (r["detail"] or "")[:200]})
    return {"screen": "access", "range": rng, "events": events,
            "generated_at": datetime.now(TZ).isoformat(timespec="seconds")}


# --- гео-запись: день сотрудника по времени (03.10.2026) -------------------------
# Режим «На линии» (PWA выездника) и поездки-навигатор пишут точки в track_points.
# Сырой трек — сотни координат, которые человек не читает; «способ чтения по
# времени» — лента дня: отрезки движения с километрами и паузами, выезды с
# вердиктами и сигналы SOS вперемешку, по возрастанию времени.
SEGMENT_GAP_MIN = 10     # разрыв в записи больше этого — новый отрезок
MOVE_MIN_M = 20          # меньше — точка считается той же позицией, не движением
GAP_WARN_MIN = 45        # пауза в записи, о которой стоит сказать явно
TRACK_MAX_ACCURACY_M = 200


def _usable_track(points: list[dict]) -> list[dict]:
    """Сырые точки остаются в БД. Для карты исключаем заведомо грубые замеры."""
    by_time = {}
    for point in points:
        ms = evidence.to_ms(point.get("ts"))
        acc = point.get("acc_m")
        if ms is None or (acc is not None and float(acc) > TRACK_MAX_ACCURACY_M):
            continue
        prior = by_time.get(ms)
        if prior is None or (acc or 0) < (prior.get("acc_m") or 0):
            by_time[ms] = point
    return [by_time[ms] for ms in sorted(by_time)]


def _segments(points: list[dict]) -> list[dict]:
    """Точки → отрезки движения: разрыв по времени разрывает, километраж
    считается по соседним точкам (не «туда-обратно» — сумма длин шагов)."""
    out: list[dict] = []
    cur: dict | None = None
    for p in _usable_track(points):
        ms = evidence.to_ms(p["ts"])
        if ms is None:
            continue
        if (cur is None or (ms - cur["to_ms"]) > SEGMENT_GAP_MIN * 60_000
                or p.get("session_id") != cur.get("session_id")):
            if cur:
                out.append(cur)
            cur = {"from_ms": ms, "to_ms": ms, "n": 1, "km": 0.0,
                   "lat0": p["lat"], "lon0": p["lon"],
                   "lat1": p["lat"], "lon1": p["lon"],
                   "session_id": p.get("session_id"), "acc": p.get("acc_m") or 0}
        else:
            step = evidence.haversine_m(cur["lat1"], cur["lon1"], p["lat"], p["lon"])
            uncertainty = max(MOVE_MIN_M, cur["acc"] + (p.get("acc_m") or 0))
            if step >= uncertainty:
                cur["km"] += step / 1000.0
                cur["lat1"], cur["lon1"] = p["lat"], p["lon"]
                cur["acc"] = p.get("acc_m") or 0
            cur["to_ms"] = ms
            cur["n"] += 1
    if cur:
        out.append(cur)
    for s in out:
        s["from"] = _hhmm(datetime.fromtimestamp(s["from_ms"] / 1000.0, TZ))
        s["to"] = _hhmm(datetime.fromtimestamp(s["to_ms"] / 1000.0, TZ))
        s["min"] = max(1, round((s["to_ms"] - s["from_ms"]) / 60000))
        s["km"] = round(s["km"], 1)
    return out


def _day_gaps(points_ms: list[int]) -> list[dict]:
    """Паузы записи ВНУТРИ дня: между соседними точками больше GAP_WARN_MIN
    минут. Это не «человек пропал» — это «телефон не писал»: гео выключили,
    села батарея, приложение закрыто. Для СБ — где день перестал быть виден."""
    gaps = []
    for a, b in zip(points_ms, points_ms[1:]):
        gap_min = (b - a) / 60000
        if gap_min >= GAP_WARN_MIN:
            gaps.append({"from": _hhmm(datetime.fromtimestamp(a / 1000.0, TZ)),
                         "to": _hhmm(datetime.fromtimestamp(b / 1000.0, TZ)),
                         "min": round(gap_min)})
    return gaps


def day_story(mgr: dict, team: list[str], employee: str, day: str) -> dict | None:
    """Один день одного человека по времени. Читается сверху вниз: когда вышел
    (первая точка), как двигался, где был выезд и чем он подтверждён, когда
    запись прервалась. Точки — местный день (пояс +5): ts на сервере в UTC."""
    if employee not in team:
        return None
    if db._as_date(day) is None:
        return None
    lo_local = datetime.fromisoformat(day + "T00:00:00").replace(tzinfo=TZ)
    hi_local = lo_local + timedelta(days=1)
    pts = db.track_points_between(
        employee, lo_local.astimezone(timezone.utc).isoformat(),
        hi_local.astimezone(timezone.utc).isoformat())
    raw_points = len(pts)
    pts = _usable_track(pts)
    pts_ms = [evidence.to_ms(p["ts"]) for p in pts]
    pts_ms = [m for m in pts_ms if m is not None]
    segments = _segments(pts)
    today = db._as_date(digest_data.today_iso())
    ents = _entries(employee, day, day)
    pay = _payloads([e["_vid"] for e in ents])
    tids = set(db.agent_tids(employee))
    events: list[dict] = []
    for e in ents:
        ev = _visit_event(e, employee, "", pay.get(e["_vid"], ""), today, tids)
        # Координаты в ленту дня: точка закрытия и точка адреса — плеер дня
        # рисует выезды на треке, а не только текстом.
        pt, addr = e.get("_pt"), e.get("_addr")
        ev["lat"] = pt[0] if pt and pt[0] is not None else None
        ev["lon"] = pt[1] if pt and pt[1] is not None else None
        ev["alat"] = addr[0] if addr and addr[0] is not None else None
        ev["alon"] = addr[1] if addr and addr[1] is not None else None
        events.append({"kind": "visit", "at": ev["at"], "event": ev})
    for s in segments:
        at = datetime.fromtimestamp(s["from_ms"] / 1000.0, TZ)
        events.append({"kind": "move",
                       "at": at.isoformat(timespec="seconds"), "event": s})
    actions = activity(mgr, team, {"from": day, "to": day}, employee=employee,
                       kinds={"photo", "view", "login", "mark", "promise", "receipt", "receipt_view", "call", "edit"}, limit=500)
    for action in actions["events"]:
        events.append({"kind": "action", "at": action["at"], "event": action})
    events.sort(key=lambda x: x["at"] or "")
    # Сырой трек для плеера (перемотка по времени): точек за смену при шаге
    # ≥25 м — сотни, не тысячи; на всякий случай режем с конца (последние).
    track = [{"t": m, "lat": p["lat"], "lon": p["lon"], "acc": p.get("acc_m"),
              "session": p.get("session_id")}
             for p, m in zip(pts, pts_ms) if m is not None]
    return {"screen": "day", "employee": employee, "day": day,
            "points": len(pts), "km": round(sum(s["km"] for s in segments), 1),
            "raw_points": raw_points, "excluded_points": raw_points - len(pts),
            "first": segments[0]["from"] if segments else "",
            "last": segments[-1]["to"] if segments else "",
            "segments": segments, "gaps": _day_gaps(pts_ms),
            "events": events, "track": track[-4000:],
            "generated_at": datetime.now(TZ).isoformat(timespec="seconds")}


def geo_days(mgr: dict, team: list[str], rng: dict, employee: str) -> dict:
    """В какие дни окна у человека есть гео-запись или выезды — чипы для
    выбора дня в «Гео по времени». День — местный (пояс +5)."""
    if employee not in team:
        return {"screen": "geo_days", "employee": employee, "days": []}
    lo_utc = (datetime.fromisoformat(rng["from"] + "T00:00:00").replace(tzinfo=TZ)
              - timedelta(days=1)).astimezone(timezone.utc).isoformat()
    hi_utc = (datetime.fromisoformat(
        (db._as_date(rng["to"]) + timedelta(days=1)).isoformat() + "T00:00:00"
    ).replace(tzinfo=TZ) + timedelta(days=1)).astimezone(timezone.utc).isoformat()
    pts: dict[str, int] = {}
    for p in db.track_points_between(employee, lo_utc, hi_utc):
        t = _local_dt(p["ts"])
        if t:
            pts[t.date().isoformat()] = pts.get(t.date().isoformat(), 0) + 1
    visits: dict[str, int] = {}
    for v in db.visits_for_employee(employee, rng["from"], rng["to"]):
        d = str(v["created_at"] or "")[:10]
        if d:
            visits[d] = visits.get(d, 0) + 1
    days = [{"day": iso, "points": pts.get(iso, 0), "visits": visits.get(iso, 0)}
            for iso in sorted(set(pts) | set(visits), reverse=True)]
    return {"screen": "geo_days", "employee": employee, "days": days,
            "range": rng, "generated_at": datetime.now(TZ).isoformat(timespec="seconds")}
