"""Контур «Софт»: журнал звонков, форма «Звонок» и портфель по responsible_soft.

Звонок — зеркало визита без гео: та же идемпотентность op_id, тот же канон
обещания, тот же «Прогноз». Отличия проверяются здесь явно: владение по второй
колонке ответственности, «Общий результат» отражает звонок, форма визита
не знает софт-полей, а форма звонка не требует результата визита.
"""
import asyncio
import json
from datetime import date, timedelta

from aiohttp import web as aioweb
from aiohttp.test_utils import make_mocked_request

import db
import web
from fields import FIELDS

EMP_HARD = "Выездников Тест"
EMP_SOFT = "Софтников Тест"
CID = "CALL-1"


def _grid(rows):
    return [[None] * len(FIELDS),
            [f.title for f in FIELDS],
            [f.source for f in FIELDS],
            *[[r.get(f.key) for f in FIELDS] for r in rows]]


def _isolated(tmp_path, monkeypatch, rows=None):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "hardbot.db")
    db.init_db()
    rows = rows or [{"contract_id": CID, "client": "Клиент С.",
                     "responsible": EMP_HARD, "responsible_soft": EMP_SOFT,
                     "debt_total": "50000"}]
    # responsible_soft едет через служебную колонку: в грид она не входит,
    # пишем напрямую upsert-ом импорта нельзя — поэтому отдельная строка
    # с «ответственный Сотрудник СОФТ» как дополнительным заголовком.
    fields = list(FIELDS)
    grid = [[None] * (len(fields) + 1),
            [f.title for f in fields] + ["ответственный Сотрудник СОФТ"],
            [f.source for f in fields] + ["софт"],
            *[[r.get(f.key) for f in fields] + [r.get("responsible_soft")]
              for r in rows]]
    rep = db.import_grid(grid, source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    assert not rep["blocked"], rep.get("warnings")


def _save(monkeypatch, body, emp=EMP_SOFT, uid=4242):
    monkeypatch.setattr(web, "_auth", lambda _r: (uid, emp))
    req = make_mocked_request("POST", "/api/save")

    async def _json():
        return body
    req.json = _json
    resp = asyncio.run(web._save(req))
    return json.loads(resp.body.decode())


def _call_body(values, **kw):
    return {"rep": CID, "values": values, "finish": True, "op_id": "call-op-1",
            "kind": "call",
            "form_version": web.FORM_PROTOCOL_VERSION,
            "input_keys": list(values.keys()), **kw}


def test_import_reads_soft_responsible(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    row = db.get_client(CID)
    assert row["responsible_soft"] == EMP_SOFT
    assert db.soft_portfolio_exists(EMP_SOFT)
    assert not db.soft_portfolio_exists("Никто Неизвестов")
    assert {r["employee"] for r in db.soft_employees()} == {EMP_SOFT}
    assert len(db.clients_for_soft(EMP_SOFT)) == 1
    assert len(db.persons_for_soft(EMP_SOFT)) == 1


def test_call_save_writes_columns_and_journal(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    monkeypatch.setattr(db, "promise_ahead_days", lambda: 0)
    when = (date.today() + timedelta(days=2)).isoformat()
    out = _save(monkeypatch, _call_body({
        "soft_call_status": "Обещает оплатить",
        "soft_comment": "обещал к пятнице",
        "soft_comm_date": (date.today() + timedelta(days=2)).isoformat(),
        "promise_date": when, "forecast_sum": "12 000"}))
    assert out["ok"] and not out.get("rejected"), out
    row = db.get_client(CID)
    assert row["soft_call_status"] == "Обещает оплатить"
    assert row["soft_comment"] == "обещал к пятнице"
    assert row["soft_comm_date"] == db.workday.today().isoformat()
    assert row["soft_next_call_date"] == when
    assert row["promise_date"] == when
    assert row["forecast"] == "Ждём"           # тот же вывод, что у визита
    assert row["overall_result"] == "Обещает оплатить"
    calls = db.calls_for_person(CID)
    assert len(calls) == 1
    assert calls[0]["employee"] == EMP_SOFT
    assert "Обещает оплатить" in calls[0]["payload"]
    assert calls[0]["created_at"] == date.today().isoformat()


def test_call_requires_status_and_is_idempotent(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    # без статуса — 400, звонок не «нулевой»
    monkeypatch.setattr(web, "_auth", lambda _r: (4242, EMP_SOFT))
    req = make_mocked_request("POST", "/api/save")

    async def _j():
        return _call_body({"soft_comment": "без статуса"})
    req.json = _j
    try:
        asyncio.run(web._save(req))
        raised = False
    except aioweb.HTTPBadRequest:
        raised = True
    assert raised
    # повтор с тем же op_id — одна строка журнала
    _save(monkeypatch, _call_body({"soft_call_status": "Нет контакта"}), )
    _save(monkeypatch, _call_body({"soft_call_status": "Нет контакта"}))
    assert len(db.calls_for_person(CID)) == 1


def test_soft_owner_cannot_save_visit_and_hard_owner_cannot_call(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    # софтник на чужом (для него — хардовом) контуре: визит 404
    try:
        _save(monkeypatch, {"rep": CID, "values": {"call_status": "Оплачен"},
                            "finish": True, "kind": "visit",
                            "form_version": web.FORM_PROTOCOL_VERSION,
                            "input_keys": ["call_status"]})
        raised = False
    except aioweb.HTTPNotFound:
        raised = True
    assert raised
    # выездник не может закрыть ЗВОНОК: владение звонка — по responsible_soft
    try:
        _save(monkeypatch, _call_body({"soft_call_status": "В работе"}),
              emp=EMP_HARD)
        raised = False
    except aioweb.HTTPNotFound:
        raised = True
    assert raised


def test_paid_call_sets_forecast_done(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    _save(monkeypatch, _call_body({"soft_call_status": "Оплачен sms"}))
    assert db.get_client(CID)["forecast"] == "Уже"


def test_visit_form_has_no_soft_fields():
    assert "soft_call_status" not in web.FORM_DROPDOWNS_VISIT
    assert "soft_call_status" in web.FORM_DROPDOWNS
    # soft_comment — маркер «сх» реестра (месячная колонка офиса), но в форме
    # звонка поле живёт и принимается сервером (FORM_TEXT).
    assert web.SOFT_FORM_KEYS == frozenset({"soft_call_status", "soft_comm_date", "soft_next_call_date", "meeting_date", "soft_extra_phone"})
    assert "soft_comment" in web.FORM_TEXT


def test_person_payload_carries_soft_state(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    _save(monkeypatch, _call_body({"soft_call_status": "В работе",
                                   "soft_comment": "текст"}))
    data = web._person_full(CID, "ru")
    assert data["soft"]["status"] == "В работе"
    assert data["soft"]["comment"] == "текст"
    assert data["soft"]["responsible"] == EMP_SOFT
    assert len(data["calls"]) == 1
    assert data["calls"][0]["employee"] == EMP_SOFT
    # софт-статус не утёк в форму ВИЗИТА
    assert all(f["key"] != "soft_call_status" for f in data["form"])


def test_bootstrap_soft_mode_queue(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch, rows=[
        {"contract_id": "CALL-1", "client": "Просрочен", "responsible": EMP_HARD,
         "responsible_soft": EMP_SOFT, "debt_total": "1000",
         "soft_call_status": "В работе", "soft_comm_date": "2026-01-01"},
        {"contract_id": "CALL-2", "client": "Без статуса", "responsible": EMP_HARD,
         "responsible_soft": EMP_SOFT, "debt_total": "2000"},
    ])
    db.update_fields("CALL-1", {"soft_next_call_date": "2026-01-01"})
    from datetime import date as _d
    monkeypatch.setattr(web, "_uid", lambda _r: 4242)
    monkeypatch.setattr(web, "_employee", lambda uid, _r: EMP_SOFT)
    monkeypatch.setattr(web, "_remember_from_init", lambda _r: None)
    monkeypatch.setattr(web, "lang_of", lambda uid: "ru")
    req = make_mocked_request("GET", "/api/bootstrap?mode=soft")
    resp = asyncio.run(web._bootstrap(req))
    data = json.loads(resp.body.decode())
    assert data["roles"] and data["roles"]["soft"] is True
    assert [p["client"] for p in data["persons"]][0] == "Просрочен"
    assert data["persons"][0]["overdue"] > 0
    assert data["persons"][1]["sc"] == ""


def test_soft_bootstrap_pools_keep_contract_partner_and_employee_scope(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch, rows=[
        {"contract_id": "POOL-A", "client": "Synthetic client", "birth_date": "2000-01-01",
         "responsible": EMP_HARD, "responsible_soft": EMP_SOFT, "pool": " Pool 2 ", "partner": "Alpha"},
        {"contract_id": "POOL-B", "client": "Synthetic client", "birth_date": "2000-01-01",
         "responsible": EMP_HARD, "responsible_soft": EMP_SOFT, "pool": "Pool 1", "partner": "Beta"},
        {"contract_id": "POOL-C", "client": "Synthetic client", "birth_date": "2000-01-01",
         "responsible": EMP_HARD, "responsible_soft": EMP_SOFT, "pool": "Pool 2", "partner": "Alpha"},
        {"contract_id": "POOL-HIDDEN", "client": "Synthetic client", "birth_date": "2000-01-01",
         "responsible": EMP_HARD, "responsible_soft": "Other operator", "pool": "Hidden", "partner": "Hidden"},
    ])
    monkeypatch.setattr(web, "_uid", lambda _r: 4242)
    monkeypatch.setattr(web, "_employee", lambda uid, _r: EMP_SOFT)
    monkeypatch.setattr(web, "_remember_from_init", lambda _r: None)
    monkeypatch.setattr(web, "lang_of", lambda uid: "ru")
    data = json.loads(asyncio.run(web._bootstrap(make_mocked_request("GET", "/api/bootstrap?mode=soft"))).body)
    assert len(data["persons"]) == 1
    person = data["persons"][0]
    assert person["pools"] == ["Pool 1", "Pool 2"]
    assert person["pool_partners"] == [
        {"pool": "Pool 1", "partner": "Beta"},
        {"pool": "Pool 2", "partner": "Alpha"},
    ]
    assert person["n"] == 3
