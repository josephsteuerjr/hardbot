"""Проверка конкретного звонка; журнал сотрудника остаётся неизменяемым."""
import json
from datetime import datetime, timezone

import db

STATES = {"open", "checking", "closed"}
SOURCES = {"employee_report", "customer_callback", "telephony", "registry", "other"}
DECISIONS = {"", "confirmed", "disputed", "inconclusive", "no_contact"}


def init_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS call_reviews (
        call_id INTEGER PRIMARY KEY REFERENCES calls(id), revision INTEGER NOT NULL,
        checker_uid INTEGER NOT NULL, due TEXT NOT NULL, state TEXT NOT NULL,
        source TEXT NOT NULL, source_ref TEXT NOT NULL, decision TEXT NOT NULL,
        note TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS call_review_history (
        id INTEGER PRIMARY KEY, call_id INTEGER NOT NULL, op_id TEXT NOT NULL UNIQUE,
        actor INTEGER NOT NULL, at TEXT NOT NULL, values_json TEXT NOT NULL)""")


def read(call_id: int, team: list[str]) -> dict | None:
    with db.connect() as conn:
        call = conn.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
        if not call or call["employee"] not in team:
            return None
        review = conn.execute("SELECT * FROM call_reviews WHERE call_id=?", (call_id,)).fetchone()
        history = [dict(r) for r in conn.execute(
            "SELECT actor,at,values_json FROM call_review_history WHERE call_id=? ORDER BY id", (call_id,))]
    for h in history:
        h["values"] = json.loads(h.pop("values_json"))
    return {"call": dict(call), "review": dict(review) if review else None, "history": history,
            "source_notice": "Источник указан проверяющим; автоматического подтверждения телефонии нет."}


def update(call_id: int, team: list[str], actor: int, body: dict) -> dict | None:
    state, source = str(body.get("state") or "open"), str(body.get("source") or "employee_report")
    decision, note = str(body.get("decision") or ""), str(body.get("note") or "").strip()
    due, ref, op = (str(body.get(k) or "").strip() for k in ("due", "source_ref", "op_id"))
    if state not in STATES or source not in SOURCES or decision not in DECISIONS:
        raise ValueError("state/source/decision")
    if not db._as_date(due) or not 8 <= len(op) <= 160 or len(note) > 10000 or len(ref) > 1000:
        raise ValueError("due/op_id/note/source_ref")
    if state == "closed" and (not decision or not note or (decision in {"confirmed", "disputed"}
            and (source == "employee_report" or not ref))):
        raise ValueError("Для решения нужны комментарий и ссылка на самостоятельный источник проверки")
    now = datetime.now(timezone.utc).isoformat()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        call = conn.execute("SELECT employee FROM calls WHERE id=?", (call_id,)).fetchone()
        if not call or call["employee"] not in team:
            return None
        old = conn.execute("SELECT * FROM call_reviews WHERE call_id=?", (call_id,)).fetchone()
        duplicate = conn.execute("SELECT call_id,actor,values_json FROM call_review_history WHERE op_id=?", (op,)).fetchone()
        revision = int(body.get("revision") or 0)
        values = {"state": state, "source": source, "decision": decision, "due": due,
                  "source_ref": ref, "note": note, "checker_uid": actor}
        if duplicate:
            if duplicate["call_id"] != call_id or duplicate["actor"] != actor or json.loads(duplicate["values_json"]) != values:
                raise ValueError("op_id уже относится к другой записи")
        else:
            if revision != (old["revision"] if old else 0):
                raise RuntimeError("Проверку уже изменили. Обновите карточку перед записью")
            conn.execute("INSERT OR REPLACE INTO call_reviews VALUES(?,?,?,?,?,?,?,?,?,?)",
                (call_id, revision + 1, actor, due, state, source, ref, decision, note, now))
            conn.execute("INSERT INTO call_review_history(call_id,op_id,actor,at,values_json) VALUES(?,?,?,?,?)",
                         (call_id, op, actor, now, json.dumps(values, ensure_ascii=False)))
            conn.commit()
    return read(call_id, team)
