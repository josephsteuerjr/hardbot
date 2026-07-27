"""Якорный шлюз офисных правок: сотрудники дописывают в реестрах и в офисе,
импорт принимает эти правки ПОСЛЕ визита бота, только если файл доказал, что
видел обогащение («Дата выезда» строки совпала с датой визита бота). Снимок
миниаппа (bot_visit_data) неприкосновенен; устаревший файл по-прежнему не
может затереть результат выездника."""

import json
from datetime import date

import pytest

import db


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "hardbot.db")
    db.init_db()


KEYS = ["contract_id", "client", "responsible", "city",
        "visit_date", "call_status", "visit_comment"]


def _import(*rows):
    titles = [db.BY_KEY[k].title for k in KEYS]
    return db.import_grid([titles] + [[r.get(k) for k in KEYS] for r in rows])


def _complete_visit(cid, today, **values):
    db.update_person(cid, {"visit_date": today, **values})
    db.log_visit(cid, 101, "визит", op_id=f"op-{cid}")
    assert db.record_bot_visit(cid, values, today) >= 1


def test_office_edit_accepted_with_anchor():
    today = date.today().isoformat()
    _import({"contract_id": "OF-1", "client": "Человек", "responsible": "Агент",
             "city": "Ташкент"})
    _complete_visit("OF-1", today, call_status="Оплачен", visit_comment="бот")

    rep = _import({"contract_id": "OF-1", "client": "Человек", "responsible": "Агент",
                   "city": "Ташкент", "visit_date": today, "call_status": "Оплачен",
                   "visit_comment": "бот, офис дополнил"})
    assert rep["office_cells"] == 1
    assert rep["office_contracts"] == 1
    assert rep["office_applied"] == {"OF-1": {"visit_comment": "бот, офис дополнил"}}
    assert rep["protected_cells"] == 0

    raw = db.get_client("OF-1")
    assert raw["visit_comment"] == "бот, офис дополнил"     # raw: карточка/дайджест
    snapshot = json.loads(raw["bot_visit_data"])
    assert snapshot["visit_comment"] == "бот"               # снимок миниаппа нетронут
    merged = db.bot_visit_snapshot(raw)
    assert merged["visit_comment"] == "бот, офис дополнил"  # публичный результат
    safe = next(r for r in db.enrichment_rows() if r["contract_id"] == "OF-1")
    assert safe["visit_comment"] == "бот, офис дополнил"    # обогащение разнесёт
    assert safe[db.BOT_VISIT_MARKER] is True


def test_office_edit_reimport_echo_is_silent():
    """Повторный импорт того же файла стабилен: принятая правка стала «эхом»."""
    today = date.today().isoformat()
    _import({"contract_id": "OF-2", "client": "Тихий", "responsible": "Агент"})
    _complete_visit("OF-2", today, call_status="Оплачен")
    row = {"contract_id": "OF-2", "client": "Тихий", "responsible": "Агент",
           "visit_date": today, "call_status": "Оплачен", "visit_comment": "офис"}
    rep1 = _import(row)
    assert rep1["office_cells"] == 1
    rep2 = _import(row)
    assert rep2["office_cells"] == 0
    assert rep2["protected_cells"] == 0
    assert db.bot_visit_snapshot(db.get_client("OF-2"))["visit_comment"] == "офис"


def test_stale_file_still_rejected():
    """Старая копия без свежей даты выезда — как раньше: ничего не затирает."""
    today = date.today().isoformat()
    _import({"contract_id": "OF-3", "client": "Защищённый", "responsible": "Агент"})
    _complete_visit("OF-3", today, call_status="Оплачен")
    rep = _import({"contract_id": "OF-3", "client": "Защищённый", "responsible": "Агент",
                   "visit_date": "531", "call_status": "Нет контакта",
                   "visit_comment": "древняя копия"})
    assert rep["office_cells"] == 0
    assert rep["protected_cells"] == 3          # дата+статус+комментарий не вошли
    assert rep["protected_contracts"] == 1
    raw = db.get_client("OF-3")
    assert raw["call_status"] == "Оплачен"
    assert raw["visit_comment"] is None


def test_office_dropdown_must_be_canonical():
    """Даже с якорем dropdown принимает только канонические значения — легаси-мусор
    вроде CONSUMER не просачивается (защита от повторного отравления)."""
    today = date.today().isoformat()
    _import({"contract_id": "OF-4", "client": "Каноничный", "responsible": "Агент"})
    _complete_visit("OF-4", today, call_status="Оплачен")
    rep = _import({"contract_id": "OF-4", "client": "Каноничный", "responsible": "Агент",
                   "visit_date": today, "call_status": "CONSUMER"})
    assert rep["office_cells"] == 0
    assert rep["protected_cells"] == 1
    assert db.get_client("OF-4")["call_status"] == "Оплачен"


def test_office_edit_of_visit_date_never_accepted():
    """«Дата выезда» — якорь, её ставит только бот: правка даты валит якорь и
    отклоняет всю строку визита."""
    today = date.today().isoformat()
    _import({"contract_id": "OF-5", "client": "Дата", "responsible": "Агент"})
    _complete_visit("OF-5", today, call_status="Оплачен")
    rep = _import({"contract_id": "OF-5", "client": "Дата", "responsible": "Агент",
                   "visit_date": "2020-01-01", "visit_comment": "офис"})
    assert rep["office_cells"] == 0
    assert rep["protected_cells"] == 2
    assert db.get_client("OF-5")["visit_date"] == today


def test_enriched_copy_between_same_day_visits_does_not_roll_back():
    """Файл, обогащённый МЕЖДУ двумя визитами одного дня, содержит значения
    первого снимка. Журнал visit_value_log знает их — они «эхо», а не офисная
    правка, и результат повторного выезда не откатывается."""
    today = date.today().isoformat()
    _import({"contract_id": "OF-8", "client": "Дважды", "responsible": "Агент"})
    _complete_visit("OF-8", today, call_status="Нет контакта")   # визит 11:00
    # обогащение в 12:00 записало «Нет контакта» в файл; в 15:00 повторный визит
    _complete_visit("OF-8", today, call_status="Оплачен")
    rep = _import({"contract_id": "OF-8", "client": "Дважды", "responsible": "Агент",
                   "visit_date": today, "call_status": "Нет контакта"})
    assert rep["office_cells"] == 0                 # эхо первого снимка, не правка
    assert rep["protected_cells"] == 0
    assert db.get_client("OF-8")["call_status"] == "Оплачен"
    assert db.bot_visit_snapshot(db.get_client("OF-8"))["call_status"] == "Оплачен"


def test_poison_signature_call_comment_not_accepted():
    """Июльский яд: бот-текст, разнесённый старым обогащением в «комментарий
    звонка» файлов. Совпадение call_comment файла с серверным visit_comment —
    сигнатура яда: не офисная правка и не «отклонено», просто мимо. Серверный
    call_comment остаётся пустым, и анти-яд обогащения вычистит файл."""
    today = date.today().isoformat()
    _import({"contract_id": "OF-9", "client": "Отравленный", "responsible": "Агент"})
    _complete_visit("OF-9", today, call_status="Оплачен", visit_comment="говорил лично")
    rep = _import({"contract_id": "OF-9", "client": "Отравленный",
                   "responsible": "Агент", "visit_date": today,
                   "call_status": "Оплачен", "call_comment": "говорил лично"})
    assert rep["office_cells"] == 0
    assert rep["protected_cells"] == 0
    raw = db.get_client("OF-9")
    assert raw["call_comment"] is None              # яд не легализован
    safe = next(r for r in db.enrichment_rows() if r["contract_id"] == "OF-9")
    assert safe["call_comment"] is None             # анти-яд обогащения сработает


def test_office_edit_propagates_to_person_contracts():
    """Выезд — на человека: правка, принятая по одной строке файла, видна во
    всех договорах человека (карточка/дайджест читают первый КД)."""
    today = date.today().isoformat()
    _import({"contract_id": "OF-A1", "client": "Двойной", "responsible": "Агент"},
            {"contract_id": "OF-A2", "client": "Двойной", "responsible": "Агент"})
    _complete_visit("OF-A1", today, call_status="Оплачен")
    rep = _import({"contract_id": "OF-A2", "client": "Двойной", "responsible": "Агент",
                   "visit_date": today, "visit_comment": "офис дополнил"})
    assert rep["office_cells"] == 1
    assert set(rep["office_applied"]) == {"OF-A1", "OF-A2"}
    for cid in ("OF-A1", "OF-A2"):
        raw = db.get_client(cid)
        assert raw["visit_comment"] == "офис дополнил"
        assert db.bot_visit_snapshot(raw)["visit_comment"] == "офис дополнил"


def test_office_dropdown_normalized_to_canonical():
    """«оплачен.» после автозамены Excel — это «Оплачен», а не отказ."""
    today = date.today().isoformat()
    _import({"contract_id": "OF-B", "client": "Регистр", "responsible": "Агент"})
    _complete_visit("OF-B", today, call_status="Нет контакта")
    rep = _import({"contract_id": "OF-B", "client": "Регистр", "responsible": "Агент",
                   "visit_date": today, "call_status": "оплачен."})
    assert rep["office_cells"] == 1
    assert rep["office_applied"]["OF-B"]["call_status"] == "Оплачен"
    assert db.get_client("OF-B")["call_status"] == "Оплачен"


def test_stale_overlay_from_code_rollback_is_ignored():
    """Оверлей привязан к визиту (visit_at): если во время отката кода старый
    record_bot_visit записал новый снимок, не обнулив office_edit_data, протухшая
    правка не ложится поверх свежего результата."""
    import json as _json
    today = date.today().isoformat()
    _import({"contract_id": "OF-C", "client": "Откат", "responsible": "Агент"})
    _complete_visit("OF-C", today, call_status="Оплачен")
    with db.connect() as conn:
        conn.execute("UPDATE clients SET office_edit_data=? WHERE contract_id=?",
                     (_json.dumps({"at": today, "visit_at": "2020-01-01",
                                   "fields": {"call_status": "Нет контакта"}}),
                      "OF-C"))
        conn.commit()
    assert db.bot_visit_snapshot(db.get_client("OF-C"))["call_status"] == "Оплачен"


def test_new_bot_visit_supersedes_office_edits():
    """Повторный выезд перекрывает прежние офисные правки: старый оверлей не должен
    лечь поверх свежего снимка нового визита."""
    today = date.today().isoformat()
    _import({"contract_id": "OF-7", "client": "Повторный", "responsible": "Агент"})
    _complete_visit("OF-7", today, call_status="Оплачен")
    _import({"contract_id": "OF-7", "client": "Повторный", "responsible": "Агент",
             "visit_date": today, "visit_comment": "офисная правка"})
    assert db.bot_visit_snapshot(db.get_client("OF-7"))["visit_comment"] == "офисная правка"

    _complete_visit("OF-7", today, call_status="Оплачен", visit_comment="новый выезд")
    raw = db.get_client("OF-7")
    assert raw["office_edit_data"] is None
    assert db.bot_visit_snapshot(raw)["visit_comment"] == "новый выезд"


def test_reset_visits_clears_office_edits():
    today = date.today().isoformat()
    _import({"contract_id": "OF-6", "client": "Сброс", "responsible": "Агент"})
    _complete_visit("OF-6", today, call_status="Оплачен")
    _import({"contract_id": "OF-6", "client": "Сброс", "responsible": "Агент",
             "visit_date": today, "visit_comment": "офис"})
    assert db.get_client("OF-6")["office_edit_data"]
    db.reset_visits()
    assert db.get_client("OF-6")["office_edit_data"] is None
