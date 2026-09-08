"""Ручка «Сегодня» для пульта и расширенная активность (вход, оболочка, портфель).

09.09.2026: чтобы ответить «у Хайдарова тормозит?», «кто получает карточки?»
и «откуда 342 без ответственного?», пришлось ходить в базу по SSH. Всё это
теперь отдаёт сервер одной ручкой, а пульт показывает на первом экране.
"""
import asyncio
import json
from datetime import date, datetime, timedelta

from aiohttp.test_utils import make_mocked_request

import db
import digest_data as dd
import web

T = date.today().isoformat()
Y = (date.today() - timedelta(days=1)).isoformat()
EMP_A, EMP_B = "Тудпиев А.", "Тудмолчун Б."
TID_A, TID_B = 930001, 930002


def _seed():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM visits WHERE contract_id LIKE 'TD-%'")
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'TD-%'")
        conn.execute("DELETE FROM agent_bind WHERE telegram_id IN (?,?)", (TID_A, TID_B))
        conn.execute("DELETE FROM app_tokens WHERE telegram_id IN (?,?)", (TID_A, TID_B))
        conn.execute("DELETE FROM users WHERE telegram_id IN (?,?)", (TID_A, TID_B))
        conn.execute("DELETE FROM enrichment_ledger WHERE contract_id LIKE 'TD-%'")
        for cid, client, emp in (("TD-1", "К1", EMP_A), ("TD-2", "К2", EMP_A), ("TD-3", "К3", EMP_B)):
            conn.execute("INSERT INTO clients (contract_id, client, responsible, birth_date, "
                         "partner, debt_today) VALUES (?,?,?,?,?,?)",
                         (cid, client, emp, "1990-01-01", "ЗК", "1000"))
        conn.execute("INSERT INTO clients (contract_id, client, responsible, unassigned_reason) "
                     "VALUES (?,?,?,?)", ("TD-9", "Сирота", "", "recalled"))
        conn.execute("INSERT INTO visits (contract_id, agent_id, created_at, payload) VALUES (?,?,?,?)",
                     ("TD-1", TID_A, T, "Выезд (миниапп). Статус: Оплачен; Договорённость: ; "))
        conn.execute("INSERT INTO visits (contract_id, agent_id, created_at, payload) VALUES (?,?,?,?)",
                     ("TD-2", TID_A, Y, "Выезд (миниапп). Статус: Нет контакта; Договорённость: ; "))
        conn.execute("INSERT OR REPLACE INTO users (telegram_id, name, last_seen, shell_version) "
                     "VALUES (?,?,?,?)", (TID_A, "А", f"{T}T09:00:00", 38))
        conn.execute("INSERT INTO app_tokens (token, telegram_id, created_at, expires_at) "
                     "VALUES (?,?,?,?)", ("tok-td-a", TID_A, f"{T}T09:00:00",
                                          (datetime.now() + timedelta(days=10)).isoformat()))
        conn.execute("INSERT INTO enrichment_ledger (contract_id, field_key, value, delivered_at) "
                     "VALUES (?,?,?,?)", ("TD-1", "call_status", "Оплачен", f"{T}T10:00:00"))
        conn.commit()
    db.record_bot_visit("TD-1", {"call_status": "Оплачен"}, T)
    db.bind_agent(TID_A, EMP_A, "agent")
    db.bind_agent(TID_B, EMP_B, "agent")
    db.reset_role_caches()


def _cleanup():
    with db.connect() as conn:
        conn.execute("DELETE FROM visits WHERE contract_id LIKE 'TD-%'")
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'TD-%'")
        conn.execute("DELETE FROM agent_bind WHERE telegram_id IN (?,?)", (TID_A, TID_B))
        conn.execute("DELETE FROM app_tokens WHERE telegram_id IN (?,?)", (TID_A, TID_B))
        conn.execute("DELETE FROM users WHERE telegram_id IN (?,?)", (TID_A, TID_B))
        conn.execute("DELETE FROM enrichment_ledger WHERE contract_id LIKE 'TD-%'")
        conn.commit()
    db.reset_role_caches()


def test_activity_carries_entry_shell_portfolio_and_today():
    _seed()
    try:
        by = {w["telegram_id"]: w for w in db.activity_stats()}
        a, b = by[TID_A], by[TID_B]
        assert a["entry"] == "pwa" and a["shell_version"] == 38
        assert a["last_seen"].startswith(T)
        assert (a["portfolio_contracts"], a["portfolio_persons"]) == (2, 2)
        assert a["visits_today"] == 1
        assert b["entry"] == "none" and b["last_seen"] is None and b["visits_today"] == 0
        assert b["portfolio_contracts"] == 1
    finally:
        _cleanup()


def test_today_overview_has_day_series_silent_delivery_and_registry():
    _seed()
    try:
        d = dd.today_overview(T, days=7)
        assert d["date"] == T and len(d["days"]) == 7
        assert d["days"][-1]["date"] == T and d["days"][-1]["visits"] >= 1
        k = d["kpi"]
        assert k["visits_today"] >= 1 and k["visits_yesterday"] >= 1
        assert k["visits_7d"] >= 2
        assert k["employees_total"] >= 2
        silent = {r["employee"] for r in d["silent"]}
        assert EMP_B in silent and EMP_A not in silent, "молчит тот, у кого портфель и ноль выездов"
        row_b = next(r for r in d["silent"] if r["employee"] == EMP_B)
        assert row_b["portfolio"] == 1 and row_b["entry"] == "none"
        assert set(d["delivery"]) >= {"visit", "digest", "promise", "report_chat", "blocked"}
        r = d["registry"]
        assert r["unassigned"].get("recalled", 0) >= 1
        assert r["clients"] >= r["assigned"] + sum(r["unassigned"].values()) - 1
        e = d["enrichment"]
        assert e["ledger_cells"] >= 1 and e["last_delivered_at"]
        assert isinstance(d["countries"], list)
        for c in d["countries"]:
            assert {"code", "name", "employees", "active", "visits_today", "portfolio"} <= set(c)
        json.dumps(d)
    finally:
        _cleanup()


def test_today_endpoint_requires_admin(monkeypatch):
    _seed()
    try:
        called = {"n": 0}

        def _admin(_r):
            called["n"] += 1
        monkeypatch.setattr(web, "_admin", _admin)
        req = make_mocked_request("GET", "/admin/today")
        resp = asyncio.run(web._admin_today(req))
        j = json.loads(resp.body.decode())
        assert called["n"] == 1 and "kpi" in j and "days" in j
    finally:
        _cleanup()
