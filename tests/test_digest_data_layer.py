"""Расщепление дайджеста: digest_data считает, digest рисует.

Ценность расщепления ровно в том, что бот и вкладка приложения считают ОДНО И
ТО ЖЕ — эти проверки не дают им разойтись первой же правкой.
"""
import json
from datetime import date

import db
import digest
import digest_data

TODAY = date.today().isoformat()
MGR_TID = 710001
OWNER_TID = 710099


def _add(cid, client, resp, **f):
    visit = f.pop("_visit", None)
    row = dict(contract_id=cid, client=client, responsible=resp,
               address=f.pop("address", f"адрес {client}"),
               partner="ЗК", debt_today=f.pop("debt", "1 000 000"))
    row.update(f)
    with db.connect() as conn:
        keys = ", ".join(f'"{k}"' for k in row)
        qs = ", ".join("?" for _ in row)
        conn.execute(f"INSERT OR REPLACE INTO clients ({keys}) VALUES ({qs})",
                     tuple(row.values()))
        conn.commit()
    if visit:
        db.update_fields(cid, {k: v for k, v in visit.items() if v})
        db.record_bot_visit(cid, visit, TODAY)


def _seed():
    db.init_db()
    _add("L-1", "Абдуллаев А.", "Слоев Дата",
         overall_result="Оплачен",
         _visit={"call_status": "Нет контакта", "contact_with": "Должник"})
    _add("L-2", "Каримова Г.", "Слоев Дата",
         _visit={"call_status": "Рассрочка"})
    db.set_manager(MGR_TID, "Рустам", ["Душанбе"],
                   ["Слоев Дата", "Слоев Бахтиёр"])


def test_summary_view_is_a_thin_wrapper():
    _seed()
    mgr = db.get_manager(MGR_TID)
    d = digest.summary_data(mgr, TODAY)
    text, _ = digest.summary_view(mgr, TODAY)
    assert str(d["totals"]["persons"]) in text
    for r in d["display"]:
        assert r["employee"] in text


def test_sorted_employees_is_computed_once(monkeypatch):
    _seed()
    mgr = db.get_manager(MGR_TID)
    calls = {"n": 0}
    orig = db.manager_employees

    def counting(*a, **kw):
        calls["n"] += 1
        return orig(*a, **kw)

    monkeypatch.setattr(digest_data.db, "manager_employees", counting)
    digest_data.summary_data(mgr, TODAY)
    assert calls["n"] == 1


def test_data_layer_carries_no_telegram_markup():
    _seed()
    mgr = db.get_manager(MGR_TID)
    payloads = [digest_data.summary_data(mgr, TODAY),
                digest_data.employee_data(mgr, TODAY, "Слоев Дата"),
                digest_data.person_data(mgr, TODAY, "Слоев Дата", "L-1"),
                digest_data.period_data(mgr, TODAY, 7),
                digest_data.promises_data(mgr, TODAY)]
    blob = json.dumps([digest_data._public(p) for p in payloads], ensure_ascii=False)
    for bad in ("<b>", "</", "&amp;", "━"):
        assert bad not in blob


def test_person_data_reads_the_proven_snapshot():
    _seed()
    mgr = db.get_manager(MGR_TID)
    d = digest_data.person_data(mgr, TODAY, "Слоев Дата", "L-1")
    assert d["outcome"] == "Нет контакта"          # снимок сильнее реестра
    # и телеграм-карточка на том же кейсе говорит то же самое
    emps = digest.sorted_employees(mgr)
    ei = emps.index("Слоев Дата")
    persons = db.persons_processed("Слоев Дата", TODAY)
    ci = next(i for i, p in enumerate(persons) if p["rep"] == "L-1")
    text, _ = digest.person_card(mgr, TODAY, ei, ci)
    assert "Нет контакта" in text


def test_registry_divergence_is_shown_not_hidden():
    """Правка после «Сохранить выезд» ложится в сырую колонку и не прячется."""
    _seed()
    db.update_fields("L-1", {"contact_with": "3 лицо"})   # правка после finish
    mgr = db.get_manager(MGR_TID)
    d = digest_data.person_data(mgr, TODAY, "Слоев Дата", "L-1")
    item = next(x for x in d["visit"] if x["key"] == "contact_with")
    assert item["value"] == "Должник"        # доказанный снимок
    assert item["registry"] == "3 лицо"      # расхождение видно второй величиной


def test_person_data_falls_back_to_raw_when_snapshot_lacks_key():
    _seed()
    # у L-2 в снимке нет tips; сырая колонка непуста → отдаётся сырая
    db.update_fields("L-2", {"tips": "второй адрес"})
    mgr = db.get_manager(MGR_TID)
    d = digest_data.person_data(mgr, TODAY, "Слоев Дата", "L-2")
    assert d["tips"] == "второй адрес"
    # record_bot_visit с пустым tips не создаёт ключ в снимке — настоящий инвариант
    db.record_bot_visit("L-2", {"call_status": "Рассрочка", "tips": ""}, TODAY)
    snap = db.bot_visit_snapshot(db.get_client("L-2")) or {}
    assert "tips" not in snap


def test_totals_are_split_by_unit_not_summed():
    # Чистая функция: показ сейчас одновалютный (debt.unit — рубли для всех),
    # но правило «валюты не складываются» обязано пережить возврат сумов.
    fake = [{"totals": [{"unit": "₽", "sum": 100}]},
            {"totals": [{"unit": "сум", "sum": 200}, {"unit": "₽", "sum": 1}]}]
    units = {x["unit"]: x["sum"] for x in digest_data._totals_units(fake)}
    assert units == {"₽": 101, "сум": 200}
    assert " · " in digest_data._persons_total(fake)


def test_units_persons_vs_contracts():
    _seed()
    _add("L-5", "Многодоговорный М.", "Слоев Бахтиёр",
         address="один адрес", _visit={"call_status": "В работе"})
    _add("L-6", "Многодоговорный М.", "Слоев Бахтиёр",
         address="один адрес", _visit={"call_status": "В работе"})
    mgr = db.get_manager(MGR_TID)
    d = digest_data.employee_data(mgr, TODAY, "Слоев Бахтиёр")
    p = next(c for c in d["cases"] if c["client"] == "Многодоговорный М.")
    assert p["n"] == 2                            # договоров 2, должник 1


def test_promises_data_is_not_truncated():
    """Слой данных отдаёт ВСЕХ, срез до 12 строк — дело рисовальщика.

    Окно списка на время проверки снимаем: по умолчанию в рассылку идёт неделя
    и только обещания выездника (db.promise_window), а тут важно, что двадцать
    позиций доходят до рисовальщика целиком."""
    _seed()
    for i in range(20):
        _add(f"P-{i}", f"Обещал {i}", "Слоев Дата",
             promise_date="2026-01-01")
    mgr = db.get_manager(MGR_TID)
    db.set_setting("promise_max_days", "0")
    db.set_setting("promise_agent_only", "0")
    try:
        d = digest_data.promises_data(mgr, TODAY)
        emp = next(e for e in d["employees"] if e["employee"] == "Слоев Дата")
        assert len(emp["items"]) == 20           # телеграм-срез 12 — в рисовальщике
        text, _ = digest.promises_view(mgr, TODAY)
        assert "…и ещё" in text
    finally:
        db.set_setting("promise_max_days", "")
        db.set_setting("promise_agent_only", "")


def test_manager_scope_synthesizes_the_owner():
    _seed()
    assert digest.manager_scope(OWNER_TID, is_owner=False) is None
    mgr = digest.manager_scope(OWNER_TID, is_owner=True)
    assert mgr is not None
    assert mgr["direction"] == ""
    assert mgr["employees"]                       # все сотрудники реестра
    assert digest.owner_scope(OWNER_TID)["name"] == "Владелец"
