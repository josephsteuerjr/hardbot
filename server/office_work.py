"""Офисные факты Харда и собственное рабочее место сотрудника.

Офисный журнал дополняет карточку, не изменяя импорт, calls или visits.
Все проверки записи выполняются внутри одной writer-транзакции.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import db
import debt
import workday
from fields import DROPDOWNS

ACTIONS = {
    "contact": "Контакт состоялся", "attempt": "Попытка контакта",
    "information": "Получена информация", "note": "Заметка",
    "promise": "Обещание оплаты", "payment": "Сообщена оплата",
    "cancel_promise": "Отмена офисного обещания",
    "month_comment": "Комментарий месяца",
}
FIELDS = {"note", "contact", "address", "next_date", "contract_id",
          "promise_date", "amount", "payment_date", "target_op", "result",
          "contact_with", "client_location", "agreed_payment"}
CHOICES = {k: DROPDOWNS["overall_result" if k == "result" else k]
           for k in ("result", "contact_with", "client_location", "agreed_payment")}
TZ = timezone(timedelta(hours=5))


class OfficeError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def init_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS office_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, op_id TEXT NOT NULL UNIQUE,
        actor_uid INTEGER NOT NULL, employee TEXT NOT NULL, rep TEXT NOT NULL,
        occurred_at TEXT NOT NULL, event_day TEXT NOT NULL, received_at TEXT NOT NULL,
        action TEXT NOT NULL, values_json TEXT NOT NULL, contracts_json TEXT NOT NULL,
        client TEXT NOT NULL, currency TEXT NOT NULL, fingerprint TEXT NOT NULL,
        revision_before TEXT NOT NULL, person_key_json TEXT NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_office_employee ON office_actions(employee,event_day)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_office_rep ON office_actions(rep,id)")
    conn.execute("""CREATE TABLE IF NOT EXISTS office_action_contracts (
        op_id TEXT NOT NULL, contract_id TEXT NOT NULL,
        PRIMARY KEY(op_id,contract_id))""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_office_contract ON office_action_contracts(contract_id,op_id)")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def portfolio_totals(rows):
    """Keep known zero balances and cents; an absent balance is not zero."""
    amounts = {}
    for row in rows:
        source = debt._cell(row, "debt_today")
        if source in (None, ""):
            source = debt._cell(row, "debt_total")
        amount = db.registry_payment_amount(source)
        if amount is not None:
            unit = debt.unit([debt._cell(row, "partner")])
            amounts[unit] = amounts.get(unit, Decimal(0)) + Decimal(str(amount))
    return [{"unit": unit, "sum": float(amount)} for unit, amount in sorted(amounts.items())]


def _group(conn, rep, employee, profile="hard"):
    # Column names are constants; the caller's profile is validated by the API.
    column = "responsible_soft" if profile == "soft" else "responsible"
    row = conn.execute(f"SELECT * FROM clients WHERE contract_id=? AND {column}=?",
                       (rep, employee)).fetchone()
    if row is None:
        raise OfficeError("Договор больше не в вашем портфеле", 404)
    candidates = conn.execute(f"SELECT * FROM clients WHERE client IS ? AND {column}=? ORDER BY contract_id",
                              (row["client"], employee)).fetchall()
    return [r for r in candidates if db.person_key(r) == db.person_key(row)]


def _events(conn, ids):
    # JSON membership avoids leaking or dropping history when the representative
    # contract was reassigned while another contract remains in the card.
    if not ids:
        return []
    found = {}
    for start in range(0, len(ids), 250):
        chunk = ids[start:start + 250]
        marks = ",".join("?" for _ in chunk)
        for event in conn.execute(f"""SELECT DISTINCT a.* FROM office_actions a
            JOIN office_action_contracts j ON j.op_id=a.op_id
            WHERE j.contract_id IN ({marks}) ORDER BY a.id""", chunk):
            found[event["id"]] = dict(event)
    return sorted(found.values(), key=lambda e: (e["occurred_at"], e["id"]))


def _revision(rows, events):
    keys = [f.key for f in db.FIELDS] + ["bot_visit_data", "bot_call_data", "bot_visit_event_at", "bot_call_at", "payment_status_data"]
    data = [{k: debt._cell(r, k) for k in keys} for r in sorted(rows, key=lambda r: r["contract_id"])]
    return hashlib.sha256(_json([data, sorted(e["id"] for e in events)]).encode()).hexdigest()


def _public(event, visible=None):
    values = json.loads(event["values_json"])
    ids = json.loads(event["contracts_json"])
    if visible is not None:
        ids = [cid for cid in ids if cid in visible]
        if values.get("contract_id") and values["contract_id"] not in visible:
            for key in ("contract_id", "amount", "promise_date", "payment_date", "target_op"):
                values.pop(key, None)
    return {"id": event["id"], "op_id": event["op_id"], "employee": event["employee"],
            "rep": event["rep"] if event["rep"] in ids else (ids[0] if ids else ""),
            "client": event["client"], "at": event["occurred_at"], "day": event["event_day"],
            "received_at": event["received_at"], "action": event["action"],
            "title": ACTIONS[event["action"]], "values": values, "contracts": ids,
            "currency": event["currency"] if values.get("amount") else ""}


def person_data(rows):
    ids = [r["contract_id"] for r in rows]
    with db.connect() as conn:
        events = _events(conn, ids)
        result = {"revision": _revision(rows, events), "actions": ACTIONS, "choices": CHOICES,
                  "history": [_public(e, set(ids)) for e in reversed(events[-100:])],
                  "history_count": len(events), "contacts": [], "addresses": [], "next_date": ""}
    for event in events:
        values = json.loads(event["values_json"])
        if "next_date" in values:
            result["next_date"] = values["next_date"]
        for key, target in (("contact", "contacts"), ("address", "addresses")):
            if values.get(key) and values[key] not in result[target]:
                result[target].append(values[key])
    result["promises"] = hard_promises(rows, events)
    return result


def _date(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise OfficeError(f"Проверьте дату: {label}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise OfficeError(f"Проверьте дату: {label}") from exc


def _amount(value):
    try:
        text = str(value).replace(" ", "").replace(",", ".")
        if not re.fullmatch(r"\d+(?:\.\d{1,2})?", text):
            raise ValueError
        amount = Decimal(text)
        if not amount.is_finite() or not 0 < amount <= Decimal("1000000000"):
            raise ValueError
        return format(amount, ".2f")
    except (InvalidOperation, ValueError) as exc:
        raise OfficeError("Сумма: от 0,01 до 1 000 000 000 с точностью до копеек") from exc


def save(uid, employee, body):
    if not isinstance(body, dict) or body.get("kind") != "office" or body.get("finish") is not True:
        raise OfficeError("Подтвердите офисную запись")
    op = body.get("op_id")
    if not isinstance(op, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", op):
        raise OfficeError("Нет идентификатора операции")
    rep, action, raw = body.get("rep"), body.get("action"), body.get("values")
    if not isinstance(rep, str) or action not in ACTIONS or not isinstance(raw, dict) or set(raw) - FIELDS:
        raise OfficeError("Проверьте тип действия и поля записи")
    if not isinstance(body.get("revision"), str):
        raise OfficeError("Откройте актуальную карточку", 409)
    ms = body.get("occurred_at")
    if isinstance(ms, bool) or not isinstance(ms, (int, float)):
        raise OfficeError("Нет исходного времени операции")
    try:
        event_at = datetime.fromtimestamp(ms / 1000, timezone.utc)
    except (ValueError, OSError, OverflowError) as exc:
        raise OfficeError("Проверьте время операции") from exc
    now = datetime.now(timezone.utc)
    if event_at > now + timedelta(minutes=5) or event_at < now - timedelta(days=31):
        raise OfficeError("Время операции вне допустимого окна 31 день")
    event_day = event_at.astimezone(TZ).date()
    values = {}
    cleared = body.get("cleared_keys") or []
    if not isinstance(cleared, list) or set(cleared) - ({"next_date", "note"} if action == "month_comment" else {"next_date"}):
        raise OfficeError("Недопустимая очистка")
    for key, value in raw.items():
        if not isinstance(value, str) or len(value) > (4000 if key in ("note", "contact", "address") else 200):
            raise OfficeError(f"Проверьте поле: {key}")
        value = value.strip()
        if value or key in cleared:
            values[key] = value
    for key, choices in CHOICES.items():
        if key in values and values[key] not in choices:
            raise OfficeError(f"Недопустимый вариант: {key}")
    if action in ("promise", "payment"):
        values["amount"] = _amount(values.get("amount"))
        key = "promise_date" if action == "promise" else "payment_date"
        day = _date(values.get(key), key)
        limit = db.promise_ahead_days()
        if action == "promise" and (day < event_day or (limit and day > event_day + timedelta(days=limit))):
            raise OfficeError(f"Обещание: от дня записи до {db.promise_ahead_days()} дней включительно")
        if action == "payment" and day > event_day:
            raise OfficeError("Дата сообщённой оплаты не позднее дня записи")
    elif any(k in values for k in ("amount", "promise_date", "payment_date")):
        raise OfficeError("Сумма и дата оплаты доступны только в соответствующем действии")
    if values.get("next_date"):
        _date(values["next_date"], "следующее действие")
    if action == "cancel_promise" and not (values.get("target_op") and values.get("note")):
        raise OfficeError("Выберите отменяемое обещание и укажите причину")
    if action in ("note", "information", "contact") and not any(values.get(k) for k in ("note", "contact", "address", "result", "contact_with", "agreed_payment")):
        raise OfficeError("Добавьте заметку, контакт или адрес")
    if action == "month_comment" and "note" not in values:
        raise OfficeError("Введите комментарий или явно подтвердите очистку")
    if action == "month_comment" and set(values) != {"note"}:
        raise OfficeError("Комментарий месяца принимает только текст комментария")
    fingerprint = hashlib.sha256(_json([uid, employee, rep, action, values, ms, body["revision"]]).encode()).hexdigest()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = _group(conn, rep, employee)
        ids = [r["contract_id"] for r in rows]
        prior = conn.execute("SELECT * FROM office_actions WHERE op_id=?", (op,)).fetchone()
        if prior:
            # A lost acknowledgement may be retried after configuring this
            # same person's Hard label. Preserve the original author and hash;
            # accept only the identical request by the same stable uid.
            import staff_roster
            same_employee = (prior['employee'] == employee or
                             staff_roster.product_employee(prior['employee'], 'hard') == employee)
            original_fingerprint = hashlib.sha256(_json([
                uid, prior['employee'], rep, action, values, ms, body['revision']]).encode()).hexdigest()
            if prior["actor_uid"] != uid or not same_employee or prior["fingerprint"] != original_fingerprint:
                raise OfficeError("Идентификатор уже использован для другой записи", 409)
            return {"ok": True, "duplicate": True, "event": _public(dict(prior), set(ids)),
                    "revision": _revision(rows, _events(conn, ids))}
        events = _events(conn, ids)
        if body["revision"] != _revision(rows, events):
            raise OfficeError("Карточка изменилась. Обновите её и сверьте ввод перед повтором", 409)
        if action == "month_comment" and all(str(r["visit_comment"] or "").strip() == values["note"] for r in rows):
            raise OfficeError("Комментарий не изменился")
        currency = ""
        cid = values.get("contract_id")
        if cid and cid not in ids:
            raise OfficeError("Выберите договор из этой карточки", 404)
        if action in ("promise", "payment", "cancel_promise"):
            if not cid:
                raise OfficeError("Выберите один договор для суммы или отмены")
            currency = debt.unit([next(r["partner"] for r in rows if r["contract_id"] == cid)])
            if action in ("promise", "payment") and not currency:
                raise OfficeError("Валюта договора не определена. Уточните реквизиты у аналитика")
            if action == "cancel_promise":
                active = [p for p in hard_promises(rows, events) if p["op_id"] == values["target_op"] and p["contract_id"] == cid]
                if not active:
                    raise OfficeError("Обещание уже изменилось. Обновите карточку", 409)
        conn.execute("""INSERT INTO office_actions(op_id,actor_uid,employee,rep,occurred_at,event_day,
            received_at,action,values_json,contracts_json,client,currency,fingerprint,revision_before,person_key_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (op, uid, employee, rep, event_at.isoformat(), event_day.isoformat(),
            now.isoformat(), action, _json(values), _json(ids), rows[0]["client"] or "", currency, fingerprint, body["revision"], _json(db.person_key(rows[0]))))
        event = dict(conn.execute("SELECT * FROM office_actions WHERE op_id=?", (op,)).fetchone())
        conn.executemany("INSERT INTO office_action_contracts(op_id,contract_id) VALUES(?,?)",
                         [(event["op_id"], cid) for cid in ids])
        if action == "month_comment":
            conn.executemany("UPDATE clients SET visit_comment=?, app_visit_edited_at=? WHERE contract_id=?",
                [(values["note"], event_at.isoformat(), r["contract_id"]) for r in rows
                 if str(r["visit_comment"] or "").strip() != values["note"]])
            rows = _group(conn, rep, employee)
        conn.commit()
        db._bump_version()
        return {"ok": True, "event": _public(event), "revision": _revision(rows, events + [event])}


def hard_promises(rows, events, asof=None):
    """Office promises have their own scope; later phone facts supersede them.

    Payment reconciliation uses observed registry payments. A manual office
    payment never makes a promise fulfilled or contributes to existing KP.
    """
    asof = asof or workday.today().isoformat()
    visible = {r["contract_id"]: r for r in rows}
    latest = {}
    for event in events:
        if event["event_day"] > asof:
            continue
        values = json.loads(event["values_json"])
        cid = values.get("contract_id")
        if cid in visible and event["action"] in ("promise", "cancel_promise"):
            latest[cid] = event if event["action"] == "promise" else None
    payments = db.registry_payments_for(visible)
    out = []
    for cid, event in latest.items():
        if event is None:
            continue
        row, values = visible[cid], json.loads(event["values_json"])
        # Do not present a superseded office agreement as current after a new
        # phone promise. The immutable office event remains in history/reports.
        common = db.common_result(row)
        phone_at = (db.call_field_at(row, "promise_date") if db.call_owns_field(row, "promise_date")
                    else str(debt._cell(row, "bot_visit_event_at") or debt._cell(row, "bot_visit_at") or ""))
        if "promise_date" in common and phone_at and phone_at[:19] > event["occurred_at"][:19]:
            continue
        received = sum((Decimal(str(p["amount"])) for p in payments
                        if p["contract_id"] == cid and p["currency"] == event["currency"]
                        and event["event_day"] <= p["paid_at"] <= asof), Decimal(0))
        amount = Decimal(values["amount"])
        state = "kept" if received >= amount else "partial" if received > 0 else "overdue" if values["promise_date"] < asof else "pending"
        out.append({"op_id": event["op_id"], "rep": cid, "contract_id": cid, "client": row["client"],
                    "author": event["employee"], "date": event["event_day"], "due": values["promise_date"],
                    "amount": float(amount), "unit": event["currency"], "received": float(received),
                    "state": state, "source": "Офис Харда"})
    return out


def decorate(persons, grouped):
    import soft_reporting
    all_ids = [r["contract_id"] for person in grouped for r in person["contracts"]]
    with db.connect() as conn:
        events = _events(conn, all_ids)
    current = hard_promises([r for person in grouped for r in person["contracts"]], events)
    promise_by_cid = {p["contract_id"]: p for p in current}
    by_cid = {}
    for event in events:
        for cid in json.loads(event["contracts_json"]):
            by_cid.setdefault(cid, []).append(event)
    for brief, person in zip(persons, grouped):
        rows = person["contracts"]
        brief["totals"] = portfolio_totals(rows)
        brief["debt_known"] = bool(brief["totals"])
        found = {e["id"]: e for r in rows for e in by_cid.get(r["contract_id"], [])}
        own_events = sorted(found.values(), key=lambda e: (e["occurred_at"], e["id"]))
        brief["address"] = next((r["address"] for r in rows if r["address"]), "")
        brief["city"] = brief.get("city") or next((r["city"] for r in rows if r["city"]), "")
        brief["office"] = {"last": _public(own_events[-1], set(brief["ids"])) if own_events else None,
                          "next_date": "", "contacts": [],
                          "promises": [promise_by_cid[r["contract_id"]] for r in rows if r["contract_id"] in promise_by_cid]}
        for event in own_events:
            values = json.loads(event["values_json"])
            if "next_date" in values:
                brief["office"]["next_date"] = values["next_date"]
            if values.get("contact"):
                brief["office"]["contacts"].append(values["contact"])
    return persons


def portfolio_rows(employee, profile):
    column = "responsible_soft" if profile == "soft" else "responsible"
    with db.connect() as conn:
        return conn.execute(f"SELECT * FROM clients WHERE {column}=? ORDER BY contract_id", (employee,)).fetchall()


def promises(employee, profile):
    rows = portfolio_rows(employee, profile)
    today = workday.today().isoformat()
    if profile == "hard":
        with db.connect() as conn:
            events = _events(conn, [r["contract_id"] for r in rows])
        return hard_promises(rows, events, today)
    import soft_reporting as reporting
    visible = {r["contract_id"]: r for r in rows}
    history = db.calls_for_contracts(visible, today)
    ids = {cid for call in history for cid in reporting.contract_ids(call)}
    all_rows = {r["contract_id"]: r for r in db.clients_by_ids(ids)} if ids else {}
    candidates = [reporting._promise(c, all_rows) for c in history if reporting.status(c) == "Обещает оплатить"]
    latest = {}
    for promise in sorted(candidates, key=lambda p: (p["day"], p["at"], p["id"])):
        for cid in promise["ids"]:
            latest[cid] = promise
    for payment in db.registry_payments_for(ids):
        before = [p for p in candidates if payment["contract_id"] in p["ids"] and p["day"] <= payment["paid_at"] <= today]
        if before:
            p = max(before, key=lambda p: (p["day"], p["at"], p["id"]))
            if p["valid"] and payment["currency"] == p["unit"]:
                p["received"] += Decimal(str(payment["amount"]))
    selected = {p["id"]: p for cid, p in latest.items() if cid in visible}
    result = []
    for p in selected.values():
        own_ids = [cid for cid in p["ids"] if cid in visible]
        if not own_ids:
            continue
        state = "kept" if p["valid"] and p["received"] >= p["amount"] else "partial" if p["received"] else "overdue" if p["due"] and p["due"] < today else "pending" if p["valid"] else "incomplete"
        result.append({"call_id": p["id"], "rep": own_ids[0], "contracts": own_ids,
                       "client": visible[own_ids[0]]["client"], "author": p["employee"], "date": p["day"],
                       "due": p["due"], "amount": float(p["amount"]), "unit": p["unit"],
                       "received": float(p["received"]), "state": state, "source": "Звонок Софта"})
    return result


def report(employee, profile, d_from, d_to):
    rng = {"from": d_from, "to": d_to, "days": (date.fromisoformat(d_to) - date.fromisoformat(d_from)).days + 1,
           "kind": "custom", "title": f"{d_from} — {d_to}"}
    rows = portfolio_rows(employee, profile)
    import soft_reporting
    result = {"ok": True, "employee": employee, "profile": profile, "from": d_from, "to": d_to,
              "today": workday.today().isoformat(), "portfolio_contracts": len(rows),
              "portfolio_persons": len({db.person_key(r) for r in rows}), "money": portfolio_totals(rows),
              "promises": promises(employee, profile)}
    if profile == "soft":
        import digest_data
        stats = digest_data._soft_employee_stats(employee, rng, details=True)
        visible = {r["contract_id"]: r for r in rows}
        own = set(visible)
        calls = {c["id"]: c for c in db.soft_calls_for_employee(employee, d_from, d_to)}
        result["stats"] = {k: stats[k] for k in ("calls", "persons", "rpc", "ptp", "kp", "rpc_rate", "ptp_rate", "kp_rate", "operation")}
        result["events"] = []
        for case in stats.get("cases", []):
            ids = soft_reporting.contract_ids(calls[case["id"]])
            rep = next((cid for cid in ids if cid in own), "")
            result["events"].append({"id": case["id"], "rep": rep, "contracts": ids,
                "client": visible[rep]["client"] if rep else "Прежний договор",
                "day": case["date"], "title": case["outcome"], "note": case["comment"], "source": "Звонок Софта"})
        # Promise metrics use historical contracts internally; do not send those
        # foreign contract keys back to an employee's renderer.
        for p in result["stats"]["operation"].get("promises", []):
            if p.get("rep") not in own:
                p["rep"] = ""
    else:
        with db.connect() as conn:
            import staff_roster
            authors = {employee}
            for person in staff_roster.rows(conn, active_only=False):
                hard_name = json.loads(person['product_names']).get('hard', person['employee'])
                if db._name_key(hard_name) == db._name_key(employee):
                    authors.update([person['employee'], hard_name])
            authors = sorted(authors)
            marks = ','.join('?' for _ in authors)
            events = conn.execute(f"SELECT * FROM office_actions WHERE employee IN ({marks}) AND event_day BETWEEN ? AND ? ORDER BY occurred_at DESC,id DESC",
                                  (*authors, d_from, d_to)).fetchall()
            visits = conn.execute(f"SELECT * FROM visits WHERE employee IN ({marks}) AND created_at BETWEEN ? AND ? ORDER BY created_at DESC,id DESC",
                                  (*authors, d_from, d_to)).fetchall()
        own = {r["contract_id"] for r in rows}
        result["events"] = [{**_public(dict(e)), "source": "Офис Харда"} for e in events]
        for e in result["events"]:
            if e["rep"] not in own:
                e["rep"] = ""
        result["visits"] = [{"id": v["id"], "day": v["created_at"], "rep": v["contract_id"] if v["contract_id"] in own else "",
                             "note": v["payload"], "source": "Реальный выезд"} for v in visits]
        result["stats"] = {"office": len(events), "persons": len({e["person_key_json"] for e in events}),
                           "visits": len(visits), "promised": sum(e["action"] == "promise" for e in events)}
    return result
