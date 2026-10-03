"""Обещание выездника (контракт v3): форма, снимок, якорный шлюз.

На контракте v2 эти тесты ПРОПУСКАЮТСЯ (типизированных полей формы нет) —
включаются автоматически после _local/apply_contract_v3.py --apply. Сам бамп
покрыт tests/test_contract_v3_bump.py всегда.
"""
import asyncio
import json
from datetime import date, timedelta

import pytest
from aiohttp.test_utils import make_mocked_request

import db
import web
from fields import FIELDS

pytestmark = pytest.mark.skipif(
    not db.FORM_TYPED_KEYS, reason="контракт v2: типизированных полей формы нет")

TODAY = date.today().isoformat()
EMP = "Обещалкин Тест"
CID = "PV-1"



@pytest.fixture(autouse=True)
def _no_promise_limit(monkeypatch):
    """Здесь проверяется МЕХАНИКА обещаний, а не предел офиса.

    Предел («не дальше трёх дней от выезда») живёт в своём файле
    test_promise_window_limit.py. Без этой фикстуры каждая дата «через неделю»
    в тестах ниже стала бы проверкой чужого правила и молча съедала бы поле.
    """
    monkeypatch.setattr(db, "promise_ahead_days", lambda: 0)

def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "hardbot.db")
    db.init_db()
    _imp([_master_row()])


def _save(monkeypatch, body):
    monkeypatch.setattr(web, "_auth", lambda _r: (42, EMP))
    req = make_mocked_request("POST", "/api/save")

    async def _json():
        return body
    req.json = _json
    resp = asyncio.run(web._save(req))
    return json.loads(resp.body.decode())


def _finish_body(values, **kw):
    return {"rep": CID, "values": values, "finish": True, "op_id": "op1",
            "form_version": web.FORM_PROTOCOL_VERSION,
            "input_keys": list(values.keys()), **kw}


def _master_row(**extra):
    base = {"contract_id": CID, "client": "Клиент П.", "responsible": EMP,
            "address": "адрес", "debt_total": "10000"}
    base.update(extra)
    return base


def _grid(rows, *, skip_key=None):
    fields = [f for f in FIELDS if f.key != skip_key]
    return [[None] * len(fields),
            [f.title for f in fields],
            [f.source for f in fields],
            *[[r.get(f.key) for f in fields] for r in rows]]


def _imp(rows, **kw):
    return db.import_grid(_grid(rows, **kw),
                          source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)


def test_typed_keys_are_canonical_constants():
    # soft_comm_date (дата следующей коммуникации) — типизированное поле
    # формы ЗВОНКА контура «Софт», того же канона даты.
    assert db.FORM_TYPED_KEYS == frozenset(
        {"promise_date", "forecast_sum", "soft_comm_date"})
    assert set(db.FORM_AMOUNT_KEYS) == {"forecast_sum"}
    assert set(db.FORM_DATE_KEYS) == {"promise_date", "soft_comm_date"}
    # Обе колонки — ЖИВЫЕ поля реестра: сумма едет в личные книги и
    # возвращается офисной правкой (решение владельца 26.08.2026).
    assert {"promise_date", "forecast_sum"} <= {f.key for f in FIELDS}
    # Оба уезжают в личные книги: BOT_VISIT_KEYS чистит сырые значения в
    # enrichment_rows, а сам список источников живёт в контракте.
    assert {"promise_date", "forecast_sum"} <= set(db.BOT_VISIT_KEYS)
    import exchange_contract as xc
    assert {"promise_date", "forecast_sum"} <= set(xc.ENRICHMENT_SOURCE_KEYS)
    assert {"promise_date", "forecast_sum"} <= set(xc.OFFICE_DIRECT_EDIT_KEYS)


def test_save_accepts_promise_and_snapshot_holds_it(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    when = (date.today() + timedelta(days=7)).isoformat()
    out = _save(monkeypatch, _finish_body(
        {"call_status": "Обещает оплатить", "promise_date": when,
         "forecast_sum": "15 000"}))
    assert out["ok"] and not out.get("rejected")
    row = db.get_client(CID)
    snap = db.bot_visit_snapshot(row) or {}
    assert snap["promise_date"] == when
    assert snap["forecast_sum"] == "15000"          # канон: без пробелов
    assert row["promise_date"] == when              # сырое поле — киллер-фильтру
    assert row["forecast_sum"] == "15000"


def test_garbage_sum_drops_the_field_not_the_visit(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    out = _save(monkeypatch, _finish_body(
        {"call_status": "В работе", "forecast_sum": "сто тыщ"}))
    assert out["ok"]                                # 200, визит записан
    assert out["rejected"] == {"forecast_sum": "format"}
    assert db.has_bot_visit(CID)


def test_draft_does_not_write_typed_fields(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    when = (date.today() + timedelta(days=3)).isoformat()
    _save(monkeypatch, {"rep": CID, "finish": False,
                        "values": {"promise_date": when}})
    assert not (db.get_client(CID)["promise_date"] or "")


def test_empty_without_cleared_keys_is_not_a_clear(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    when = (date.today() + timedelta(days=7)).isoformat()
    _save(monkeypatch, _finish_body(
        {"call_status": "Обещает оплатить", "promise_date": when}))
    _save(monkeypatch, _finish_body(
        {"call_status": "Обещает оплатить", "promise_date": ""}, op_id="op2"))
    assert db.get_client(CID)["promise_date"] == when


def test_clear_own_promise_via_explicit_button(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    when = (date.today() + timedelta(days=7)).isoformat()
    _save(monkeypatch, _finish_body(
        {"call_status": "Обещает оплатить", "promise_date": when}))
    _save(monkeypatch, {**_finish_body(
        {"call_status": "Обещает оплатить", "promise_date": ""}, op_id="op2"),
        "cleared_keys": ["promise_date"]})
    assert not (db.get_client(CID)["promise_date"] or "")
    snap = db.bot_visit_snapshot(db.get_client(CID)) or {}
    assert not (snap.get("promise_date") or "")


def test_promise_survives_a_visit_that_did_not_mention_it(tmp_path, monkeypatch):
    """Повторный визит, не сказавший про обещание, его НЕ отменяет: молчание
    формы — не ответ. Отменяет только кнопка «Убрать»."""
    _isolated(tmp_path, monkeypatch)
    when = (date.today() + timedelta(days=7)).isoformat()
    _save(monkeypatch, _finish_body(
        {"call_status": "Обещает оплатить", "promise_date": when}))
    _save(monkeypatch, _finish_body({"call_status": "В работе"}, op_id="op2"))
    assert db.get_client(CID)["promise_date"] == when
    out = _save(monkeypatch, {**_finish_body(
        {"call_status": "В работе", "promise_date": ""}, op_id="op3"),
        "cleared_keys": ["promise_date"]})
    assert not out.get("rejected")
    assert not (db.get_client(CID)["promise_date"] or "")


def test_field_agent_removes_the_office_promise(tmp_path, monkeypatch):
    """Выездник снимает ЛЮБОЕ обещание, не только своё: он стоит перед
    должником. Запрет на чужое означал бы, что должника со сорвавшимся
    обещанием не вернёт в маршрут никто."""
    _isolated(tmp_path, monkeypatch)
    _imp([_master_row(promise_date="01.09.2026")])          # обещание офиса
    out = _save(monkeypatch, {**_finish_body(
        {"call_status": "В работе", "promise_date": ""}),
        "cleared_keys": ["promise_date"]})
    assert not out.get("rejected")
    assert not (db.get_client(CID)["promise_date"] or "")


def test_removed_promise_survives_a_previsit_copy(tmp_path, monkeypatch):
    """Снятие — это ОТВЕТ, а не молчание: копия реестра, которая визита не
    видела, не возвращает снятое обещание. Иначе выездник снимал бы его каждый
    день, а ночной перенос каждую ночь возвращал."""
    _isolated(tmp_path, monkeypatch)
    _imp([_master_row(promise_date="01.09.2026")])
    _save(monkeypatch, {**_finish_body(
        {"call_status": "В работе", "promise_date": ""}),
        "cleared_keys": ["promise_date"]})
    _imp([_master_row(promise_date="01.09.2026")])          # довизитная копия
    assert not (db.get_client(CID)["promise_date"] or "")


def test_office_may_set_a_new_promise_after_the_visit(tmp_path, monkeypatch):
    """А обзвон ПОСЛЕ выезда — может: строка доказала, что видела этот визит
    (дата выезда + эхо поля визита), и новая дата офиса принимается."""
    _isolated(tmp_path, monkeypatch)
    _imp([_master_row(promise_date="01.09.2026")])
    _save(monkeypatch, _finish_body(
        {"call_status": "В работе", "contact_with": "Должник",
         "promise_date": ""}, cleared_keys=["promise_date"]))
    _imp([_master_row(visit_date=TODAY, contact_with="Должник",
                      promise_date="20.09.2026")])
    merged = db._merged_visit(db.get_client(CID)) or {}
    assert merged.get("promise_date") == "2026-09-20"


# --- якорный шлюз импорта -----------------------------------------------------
def test_office_promise_flows_before_any_visit(tmp_path, monkeypatch):
    """Пока бот не высказался про обещание — офисная дата идёт обычным
    upsert'ом: office_cells/protected_cells не растут, киллер-фильтр кормится."""
    _isolated(tmp_path, monkeypatch)
    db.record_bot_visit(CID, {"call_status": "В работе"}, TODAY)
    rep = _imp([_master_row(promise_date="15.09.2026", visit_date=TODAY)])
    assert db.get_client(CID)["promise_date"] == "2026-09-15"   # канонизирована
    assert rep["office_cells"] == 0 and rep["protected_cells"] == 0


def test_promise_echo_does_not_unlock_the_result(tmp_path, monkeypatch):
    """Совпавшая дата обещания НЕ считается эхом и не открывает замок «Общего
    результата»: довизитная копия реестра не должна откатывать доказанный
    статус только потому, что в ней совпала дата обещания."""
    _isolated(tmp_path, monkeypatch)
    when = (date.today() + timedelta(days=7)).isoformat()
    db.record_bot_visit(CID, {"call_status": "Оплачен",
                              "promise_date": when}, TODAY)
    db.update_fields(CID, {"call_status": "Оплачен", "promise_date": when})
    _imp([_master_row(promise_date=when, visit_date=TODAY,
                      overall_result="В работе")])
    merged = db._merged_visit(db.get_client(CID)) or {}
    assert merged.get("call_status") == "Оплачен"   # результат не откатился


def test_office_clears_promise_only_with_proof_and_column(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    when = (date.today() + timedelta(days=7)).isoformat()
    db.record_bot_visit(CID, {"call_status": "Обещает оплатить",
                              "contact_with": "Должник",
                              "promise_date": when}, TODAY)
    db.update_fields(CID, {"contact_with": "Должник", "promise_date": when})
    # строка доказала визит (дата + эхо contact_with), колонка обещания в книге
    # есть, ячейка пуста → офис СНЯЛ обещание
    _imp([_master_row(visit_date=TODAY, contact_with="Должник",
                      promise_date="")])
    merged = db._merged_visit(db.get_client(CID)) or {}
    assert not (merged.get("promise_date") or "")
    # а книга БЕЗ колонки обещания снять не может ни при каком якоре
    db.record_bot_visit(CID, {"call_status": "Обещает оплатить",
                              "contact_with": "Должник",
                              "promise_date": when}, TODAY)
    db.update_fields(CID, {"promise_date": when})
    _imp([_master_row(visit_date=TODAY, contact_with="Должник")],
         skip_key="promise_date")
    merged2 = db._merged_visit(db.get_client(CID)) or {}
    assert merged2.get("promise_date") == when


def test_overdue_promises_carry_the_snapshot_sum(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    past = (date.today() - timedelta(days=3)).isoformat()
    db.record_bot_visit(CID, {"call_status": "Обещает оплатить",
                              "promise_date": past,
                              "forecast_sum": "20000"}, TODAY)
    db.update_fields(CID, {"promise_date": past, "forecast_sum": "20000"})
    ps = db.overdue_promises(EMP, TODAY)
    assert ps and ps[0]["promised_sum"] == 20000


def test_office_correction_of_the_sum_comes_back(tmp_path, monkeypatch):
    """Главное требование владельца: сумма едет в реестр И РЕДАКТИРУЕТСЯ.

    Выездник записал «обещал 15 000», обзвон уточнил до 20 000 — руководитель
    обязан видеть уточнённую сумму, иначе бот навсегда показывает цифру с
    порога должника."""
    _isolated(tmp_path, monkeypatch)
    when = (date.today() + timedelta(days=7)).isoformat()
    db.record_bot_visit(CID, {"call_status": "Обещает оплатить",
                              "contact_with": "Должник",
                              "promise_date": when,
                              "forecast_sum": "15000"}, TODAY)
    db.update_fields(CID, {"contact_with": "Должник", "promise_date": when,
                           "forecast_sum": "15000"})
    _imp([_master_row(visit_date=TODAY, contact_with="Должник",
                      promise_date=when, forecast_sum="20 000")])
    merged = db._merged_visit(db.get_client(CID)) or {}
    assert merged.get("forecast_sum") == "20000"    # канонизирована при приёме
    assert merged.get("promise_date") == when       # дата не пострадала


def test_only_the_proven_sum_rides_into_the_books(tmp_path, monkeypatch):
    """Обогащение везёт сумму ТОЛЬКО из доказанного визита: сырую офисную
    «Сумму прогноза» бот не имеет права возить обратно в личные книги —
    это была бы его подпись под чужим числом."""
    _isolated(tmp_path, monkeypatch)
    _imp([_master_row(forecast_sum="90000")])       # офисный прогноз, визита нет
    row = [r for r in db.enrichment_rows() if r["contract_id"] == CID][0]
    assert row[db.BOT_VISIT_MARKER] is False
    assert not (row.get("forecast_sum") or "")
    db.record_bot_visit(CID, {"call_status": "Обещает оплатить",
                              "forecast_sum": "15000"}, TODAY)
    row2 = [r for r in db.enrichment_rows() if r["contract_id"] == CID][0]
    assert row2[db.BOT_VISIT_MARKER] is True
    assert row2["forecast_sum"] == "15000"


# --- «Прогноз» по итогу выезда ------------------------------------------------
def _fc(cid=CID):
    return (db._merged_visit(db.get_client(cid)) or {}).get("forecast") or ""


def test_promise_sets_waiting(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    when = (date.today() + timedelta(days=7)).isoformat()
    _save(monkeypatch, _finish_body(
        {"call_status": "Обещает оплатить", "promise_date": when}))
    assert _fc() == db.FORECAST_WAITING
    assert db.get_client(CID)["forecast"] == db.FORECAST_WAITING


def test_paid_visit_sets_already(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    when = (date.today() + timedelta(days=7)).isoformat()
    _save(monkeypatch, _finish_body(
        {"call_status": "Оплачен", "promise_date": when}))
    assert _fc() == db.FORECAST_PAID     # платёж старше обещания


def test_visit_without_a_promise_says_nothing(tmp_path, monkeypatch):
    """Ни обещания сейчас, ни обещания до выезда — прогноз не наш: значение
    аналитика («ОК», «Нет», «Сомнение») бот трогать не вправе."""
    _isolated(tmp_path, monkeypatch)
    db.update_fields(CID, {"forecast": "Сомнение"})
    _save(monkeypatch, _finish_body({"call_status": "Нет контакта"}))
    assert db.get_client(CID)["forecast"] == "Сомнение"


def test_own_promise_removed_is_a_breakdown(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    when = (date.today() + timedelta(days=7)).isoformat()
    _save(monkeypatch, _finish_body(
        {"call_status": "Обещает оплатить", "promise_date": when}))
    _save(monkeypatch, {**_finish_body(
        {"call_status": "В работе", "promise_date": ""}, op_id="op2"),
        "cleared_keys": ["promise_date"]})
    assert _fc() == db.FORECAST_BROKEN


def test_office_promise_broken_at_the_visit(tmp_path, monkeypatch):
    """Ровно случай владельца: обещание было ДО выезда (его вёл офис), на
    выезде должник его не подтвердил. Дата снимается, а факт срыва уезжает
    офису статусом."""
    _isolated(tmp_path, monkeypatch)
    _imp([_master_row(promise_date="01.08.2026")])   # офисное обещание
    _save(monkeypatch, {**_finish_body(
        {"call_status": "В работе", "promise_date": ""}),
        "cleared_keys": ["promise_date"]})
    assert not (db.get_client(CID)["promise_date"] or "")
    assert _fc() == db.FORECAST_BROKEN


def test_analyst_edit_of_the_forecast_comes_back(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    when = (date.today() + timedelta(days=7)).isoformat()
    db.record_bot_visit(CID, {"call_status": "Обещает оплатить",
                              "contact_with": "Должник",
                              "promise_date": when,
                              "forecast": db.FORECAST_WAITING}, TODAY)
    db.update_fields(CID, {"contact_with": "Должник", "promise_date": when,
                           "forecast": db.FORECAST_WAITING})
    _imp([_master_row(visit_date=TODAY, contact_with="Должник",
                      promise_date=when, forecast="ОК")])
    assert _fc() == "ОК"


def test_forecast_in_the_book_is_not_an_echo(tmp_path, monkeypatch):
    """«Ждём» мог стоять в книге и до выезда — от аналитика. Если считать его
    эхом, довизитная копия реестра откатывала бы доказанный результат."""
    _isolated(tmp_path, monkeypatch)
    when = (date.today() + timedelta(days=7)).isoformat()
    db.record_bot_visit(CID, {"call_status": "Оплачен",
                              "promise_date": when,
                              "forecast": db.FORECAST_WAITING}, TODAY)
    db.update_fields(CID, {"call_status": "Оплачен", "promise_date": when,
                           "forecast": db.FORECAST_WAITING})
    _imp([_master_row(visit_date=TODAY, promise_date=when,
                      forecast=db.FORECAST_WAITING,
                      overall_result="В работе")])
    merged = db._merged_visit(db.get_client(CID)) or {}
    assert merged.get("call_status") == "Оплачен"    # откат не прошёл
