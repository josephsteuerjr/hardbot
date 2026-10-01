"""Лента активности команды и «Контроль» безопасника (Пульты 2, 29.09.2026).

Зачем «Контроль». В Таджикистане выездник берёт у должника наличные «в счёт
долга» и не сдаёт их, либо пишет выезд, которого не было. Прямой улики в данных
нет — есть следы: выезд далеко от адреса или без точки телефона, выезд,
занесённый задним числом, карточка, заполненная за полминуты, выезд ночью, серия
сохранений из одной точки, «наличные» в тексте, «оплатил», за которым в реестре
не пришло ни копейки. Каждый след сам по себе ничего не доказывает, поэтому
человек сравнивается со своим офисом, а подтверждает подозрение только звонок
должнику — контрольный обзвон.

Всё считается по сохранённым фактам визита (evidence, series, received_at),
правила живут здесь и могут меняться без миграций.
"""

from __future__ import annotations

import hashlib
import math
import re
import statistics
from datetime import date, datetime, timedelta

import db
import digest_data
import evidence

TZ = evidence.FIELD_TZ
NO_OFFICE = digest_data.NO_OFFICE
NO_OFFICE_NAME = digest_data.NO_OFFICE_NAME

FAST_FILL_S = 40          # карточку быстрее не заполнить честно
NIGHT_FROM, NIGHT_TO = 21, 7
PAID_LAG_DAYS = 3         # платёж доезжает до реестра не сразу
MIN_VISITS_FOR_SHARE = 5  # у кого выездов меньше — доли не судим, это шум
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
    ("far", "далеко от адреса", "доля выездов, где телефон был дальше допуска от адреса должника"),
    ("nogeo", "без точки телефона", "доля выездов, которые нечем проверить: нет точки или она слишком грубая"),
    ("late", "занесён позже", "доля выездов, доехавших до сервера на следующий день и позже"),
    ("fast", "карточка за полминуты", f"доля карточек, заполненных быстрее {FAST_FILL_S} с"),
    ("night", "ночью", f"доля выездов, сохранённых с {NIGHT_FROM}:00 до {NIGHT_TO:02d}:00"),
    ("series", "серия", "доля выездов с признаками серии: несколько сохранений подряд из одной точки или невозможная скорость"),
    ("cash", "«наличные» в тексте", "выезды, где в комментарии деньги переданы сотруднику"),
    ("paid", "«оплатил» без платежа", f"исход «Оплачен», а платежа в реестре после выезда нет (старше {PAID_LAG_DAYS} дн.)"),
    ("bymgr", "закрыто руководителем", "выезды, занесённые со счёта руководителя (act_as), а не с телефона сотрудника: сам по себе не нарушение, но для СБ — где статистику поправили руками"),
    ("dupphoto", "фото использовано дважды", "снимки, приложенные к РАЗНЫМ должникам: один файл переиспользован (хэш sha256)"),
    ("repeat", "ездит без результата", "должники, к которым за окно было 3+ выезда без оплаты и обещания"),
    ("callback", "обзвон: не подтвердилось", "контрольный звонок: выезда не было или деньги отданы сотруднику"),
)
SIGNAL_LABEL = {k: label for k, label, _ in SIGNALS}
# «Оплачен» без платежа — тоже доля против офиса: реестр платежей доезжает
# с опозданием у всех одинаково, и абсолютный счёт судил бы задержку, а не человека.
_SHARE_KEYS = ("far", "nogeo", "late", "fast", "night", "series", "paid")
_SHARE_WEIGHT = {"far": 22, "nogeo": 10, "late": 14, "fast": 16, "night": 12, "series": 18,
                 "paid": 16}
# bymgr — лёгкий вес: закрыть карточку за сотрудника законно (act_as журналируется);
# dupphoto — тяжёлый: один файл у двух должников не бывает честно.
_COUNT_WEIGHT = {"cash": (18, 36), "repeat": (4, 16), "callback": (35, 70),
                 "bymgr": (6, 18), "dupphoto": (25, 50)}


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
    lvl = e.get("evidence_level") or ""
    if lvl == "far":
        flags.append("far")
    elif not e.get("geo") or lvl in evidence.UNVERIFIED:
        flags.append("nogeo")
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
                "marker": "mark", "promise_set": "promise"}
_AUDIT_TITLE = {"view": "открыл карточку", "photo": "фото", "login": "вошёл в приложение",
                "mark": "метка на карте", "promise": "записал обещание"}


def activity(mgr: dict, team: list[str], rng: dict, *, office: str = "",
             employee: str = "", kinds: set[str] | None = None,
             before: str = "", limit: int = 200) -> dict:
    kinds = kinds or {"visit", "photo", "view", "login", "mark", "promise"}
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
            wanted = [a for a, k in _AUDIT_KINDS.items() if k in kinds]
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
    for row in rows:
        row["last_seen"] = last_seen.get(row["employee"])
    events.sort(key=lambda x: (x["at"] or "", x["id"]), reverse=True)
    if before:
        events = [x for x in events if (x["at"] or "") < before]
    limit = max(1, min(500, int(limit or 200)))
    page = events[:limit]
    rows.sort(key=lambda r: (-(r["visits"]), r["employee"].lower()))
    return {"screen": "activity", "range": rng, "tz": "+05:00", "offices": chips,
            "employees": rows, "events": page,
            "next_before": page[-1]["at"] if len(events) > limit and page else None,
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
                    d_from: str = "", d_to: str = "") -> tuple[dict, dict, list[dict]]:
    n = len(ents)
    per_visit = [(e, _visit_flags(e, pay.get(e["_vid"], ""), today, tids))
                 for e in ents]
    shares = {k: (sum(k in f for _, f in per_visit) / n if n else 0.0) for k in _SHARE_KEYS}
    counts = {"cash": sum("cash" in f for _, f in per_visit)}
    if tids is not None:
        counts["bymgr"] = sum("bymgr" in f for _, f in per_visit)
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
    return shares, counts, per_visit


def overview(mgr: dict, team: list[str], rng: dict, *, office: str = "") -> dict:
    of, chips = offices_for(team)
    people = _pick(team, of, office, "")
    today = db._as_date(digest_data.today_iso())
    checks_all = db.control_checks_between(rng["from"], rng["to"])
    raw = []
    for emp in people:
        ents = _entries(emp, rng["from"], rng["to"])
        pay = _payloads([e["_vid"] for e in ents])
        checks = [c for c in checks_all if c.get("employee") == emp]
        tids = set(db.agent_tids(emp))
        shares, counts, _ = _person_signals(ents, pay, today, checks, tids,
                                            rng["from"], rng["to"])
        raw.append({"employee": emp, "office": of[emp], "visits": len(ents),
                    "shares": shares, "counts": counts, "checks": checks})
    # Медиана по офису (у кого выездов достаточно); офис из одного человека
    # сравнивается со всей командой.
    def med(key: str, pool: list[dict]) -> float:
        vals = [r["shares"][key] for r in pool if r["visits"] >= MIN_VISITS_FOR_SHARE]
        return statistics.median(vals) if vals else 0.0
    judged = [r for r in raw if r["visits"] >= MIN_VISITS_FOR_SHARE]
    rows = []
    for r in raw:
        pool = [x for x in judged if x["office"] == r["office"]]
        if len(pool) < 3:
            pool = judged
        score = 0.0
        signals = []
        for key in _SHARE_KEYS:
            v, peer = r["shares"][key], med(key, pool)
            if r["visits"] >= MIN_VISITS_FOR_SHARE and v > 0:
                excess = max(0.0, v - peer)
                pts = _SHARE_WEIGHT[key] * min(1.0, excess / 0.3)
                score += pts
                if excess >= 0.1 or (v >= 0.5 and pts > 0):
                    signals.append({"key": key, "label": SIGNAL_LABEL[key],
                                    "value": round(v, 3), "peer": round(peer, 3),
                                    "points": round(pts)})
        for key, (w, cap) in _COUNT_WEIGHT.items():
            c = r["counts"].get(key, 0)
            if c:
                pts = min(cap, w * c)
                score += pts
                signals.append({"key": key, "label": SIGNAL_LABEL[key], "n": c,
                                "points": round(pts)})
        risk = int(min(100, round(score)))
        signals.sort(key=lambda s: -s["points"])
        done = [c for c in r["checks"] if c.get("outcome")]
        rows.append({
            "employee": r["employee"],
            "office": "" if r["office"] == NO_OFFICE else r["office"],
            "visits": r["visits"], "risk": risk,
            "level": "high" if risk >= 50 else "mid" if risk >= 25 else "low",
            "judged": r["visits"] >= MIN_VISITS_FOR_SHARE,
            "signals": signals,
            "checks": {"selected": len(r["checks"]), "done": len(done),
                       "confirmed": sum(c["outcome"] == "confirmed" for c in done),
                       "no_visit": sum(c["outcome"] == "no_visit" for c in done),
                       "cash": sum(c["outcome"] == "cash_to_employee" for c in done)}})
    rows.sort(key=lambda x: (-x["risk"], -x["visits"], x["employee"].lower()))
    return {"screen": "control", "range": rng, "offices": chips, "rows": rows,
            "legend": [{"key": k, "label": label, "hint": hint} for k, label, hint in SIGNALS],
            "outcomes": [{"key": k, "label": v} for k, v in db.CONTROL_OUTCOMES.items()]}


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
_RISK_FILL = {"high": "#FAE3E0", "mid": "#FBF0D0", "low": "#E3F2E1"}
_OUTCOME_FILL = {"confirmed": "#E3F2E1", "paid_elsewhere": "#E3F2E1",
                 "no_visit": "#FAE3E0", "cash_to_employee": "#FAE3E0"}
_CONTROL_HELP = {
    "Выездов": "Сколько выездов сотрудника попало в период.",
    "Риск": "Балл от 0 до 100: чем выше, тем сильнее выезды сотрудника отличаются "
            "от выездов его офиса. 50 и выше — высокий, 25–49 — средний.",
    "Уровень": "Высокий, средний или низкий — по баллу риска.",
    "Следы": "Из чего сложился балл: доля выездов с признаком у сотрудника и в "
             "среднем по офису, а также счётчики (наличные в комментариях, повторы).",
    "Проверено звонком": "Сколько выездов сотрудника уже проверили звонком должнику.",
    "Не подтвердилось": "Звонок показал «выезда не было» или «отдал деньги сотруднику».",
    "Почему выбран": "Почему выезд попал в обзвон: случайная выборка или конкретный признак.",
    "Исход": "Что ответил должник.",
    "Когда": "Когда внесён результат звонка.",
}


def control_xlsx(mgr: dict, team: list[str], rng: dict) -> bytes:
    """Риск по людям и итоги обзвона одной книгой — в том же облике, что
    книга статистики (book.py): шапка с подсказками, уровень риска и исход
    звонка подсвечены, даты — датами. Текст со «=» — текстом: комментарий
    выездника не должен стать формулой у безопасника."""
    import book

    ov = overview(mgr, team, rng)
    period = f"{rng.get('label') or ''} ({rng['from']} — {rng['to']})".strip()
    bk = book.Book(_CONTROL_HELP.get)
    ws = bk.sheet("Риск", tab="main")
    ws.title(f"Контроль выездов · {period}")
    ws.title("Балл риска сравнивает сотрудника с его же офисом. Высокий балл — повод "
             "проверить выезды звонком, а не доказательство. Наведите мышь на "
             "заголовок — всплывёт пояснение.", italic=True)
    cols = ["Сотрудник", "Офис", "Выездов", "Риск", "Уровень", "Следы",
            "Проверено звонком", "Не подтвердилось"]
    head_at = ws.head(cols)
    level = {"high": "высокий", "mid": "средний", "low": "низкий"}
    kinds = ["text", "text", "int", "int", "text", "text", "int", "int"]
    wrap = bk.fmt(text_wrap=True, valign="top")
    for row in ov["rows"]:
        sig = "; ".join(
            f"{s['label']} {round(s['value'] * 100)}% (офис {round(s['peer'] * 100)}%)"
            if "value" in s else f"{s['label']}: {s['n']}" for s in row["signals"])
        at = ws.append([row["employee"], row["office"] or NO_OFFICE_NAME, row["visits"],
                        row["risk"], level[row["level"]], "",
                        row["checks"]["done"],
                        row["checks"]["no_visit"] + row["checks"]["cash"]],
                       kinds, fills={3: _RISK_FILL[row["level"]],
                                     4: _RISK_FILL[row["level"]]})
        ws.ws.write_string(at, 5, sig, wrap)
    ws.widths((30, 16, 9, 7, 10, 80, 12, 13))
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
        for r in db.contracts_of_responsible(emp):
            cid = r["contract_id"]
            paid = db._as_date(r["last_payment_date"]) if "last_payment_date" in r.keys() else None
            recent_pay = bool(
                paid and (date.today() - paid).days <= 90)
            econ[cid] = {"debt": debt_mod.contract_debt(r) or 0,
                         "recent_pay": recent_pay}
            pool.append(debt_mod.contract_debt(r) or 0)
    office_median = statistics.median(pool) if pool else 0

    rows: dict[str, dict] = {}
    for e in came_in:
        r = rows.setdefault(e["employee"], {
            "employee": e["employee"],
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
            "employee": r["employee"], "office": r["office"],
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
