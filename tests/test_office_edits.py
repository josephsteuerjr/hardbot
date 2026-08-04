"""Якорный шлюз published master → hardbot.

Офисная правка проходит personal → registry_cycle → published master. Итог из
Excel ``overall_result`` reverse-map-ится во внутренний visit-result
``call_status``; настоящие call_status/call_comment из master изолированы.
"""

import json
from datetime import date

import pytest

import db
from pult import enrich


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "hardbot.db")
    db.init_db()


KEYS = [
    "contract_id",
    "client",
    "responsible",
    "city",
    "visit_date",
    "overall_result",
    "call_status",
    "visit_comment",
    "call_comment",
    "contact_with",
    "agreed_payment",
    "who_at_address",
    "client_location",
    "purpose_rf",
]


def _import(*rows):
    titles = [field.title for field in db.FIELDS]
    # Адрес проставляем по умолчанию: без даты рождения именно он отличает
    # однофамильцев друг от друга (db.person_key), и в реальном реестре он есть
    # почти всегда. Строка вообще без опознавательных признаков — не тот случай,
    # который эти тесты описывают.
    rows = [{"address": f"адрес {row.get('client') or ''}", **row} for row in rows]
    return db.import_grid(
        [titles, [field.source for field in db.FIELDS]]
        + [[row.get(field.key) for field in db.FIELDS] for row in rows],
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
    )


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


def test_office_edit_is_accepted_from_a_pult_import_too():
    """Происхождение файла больше не решает — решает якорь.

    05.08.2026. Раньше офисная правка принималась только из опубликованного
    мастера. На практике реестр грузят из пульта, метка всегда test_subset, и
    замок не открывался НИ РАЗУ: во всех расписках office_cells = 0. Офис правил
    «Общий результат» прямо в книгах, бот об этом не узнавал, и очередное
    обогащение возвращало туда своё значение — так пропали правки по 28
    договорам Уткирова за 03.08.

    Проверка подлинности осталась прежней и живёт в якоре (см. соседние тесты):
    совпавшая дата выезда доказывает, что файл видел результат бота.
    """
    today = date.today().isoformat()
    _import({"contract_id": "OF-SUBSET", "client": "Из пульта",
             "responsible": "Агент"})
    _complete_visit("OF-SUBSET", today, call_status="Оплачен")
    values = {
        "contract_id": "OF-SUBSET",
        "client": "Из пульта",
        "responsible": "Агент",
        "visit_date": today,
        "overall_result": "Доп. Обогащение",
    }
    grid = [
        [field.title for field in db.FIELDS],
        [values.get(field.key) for field in db.FIELDS],
    ]
    rep = db.import_grid(grid, source_kind=db.IMPORT_SOURCE_TEST_SUBSET)
    assert rep["office_cells"] >= 1, "правка офиса снова не доезжает до бота"
    assert db.bot_visit_snapshot(
        db.get_client("OF-SUBSET"))["call_status"] == "Доп. Обогащение"


def test_pult_import_with_a_wrong_date_is_still_rejected():
    """Якорь остался единственной, но настоящей защитой.

    Файл без совпавшей даты выезда — это старая копия, а не правка офиса.
    Снятие требования «опубликованный мастер» не должно её пропустить.
    """
    today = date.today().isoformat()
    _import({"contract_id": "OF-STALE-SUBSET", "client": "Старая копия",
             "responsible": "Агент"})
    _complete_visit("OF-STALE-SUBSET", today, call_status="Оплачен")
    values = {
        "contract_id": "OF-STALE-SUBSET",
        "client": "Старая копия",
        "responsible": "Агент",
        "visit_date": "2020-01-01",           # файл не видел этого визита
        "overall_result": "Доп. Обогащение",
    }
    grid = [
        [field.title for field in db.FIELDS],
        [values.get(field.key) for field in db.FIELDS],
    ]
    rep = db.import_grid(grid, source_kind=db.IMPORT_SOURCE_TEST_SUBSET)
    assert rep["office_cells"] == 0
    assert rep["protected_cells"] >= 2
    assert db.bot_visit_snapshot(
        db.get_client("OF-STALE-SUBSET"))["call_status"] == "Оплачен"


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
    assert rep["protected_cells"] == 2          # дата+visit_comment не вошли
    assert rep["isolated_call_cells"] == 1      # call_status — отдельный звонок
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
                   "visit_date": today, "overall_result": "CONSUMER"})
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
                   "visit_date": today, "overall_result": "Нет контакта"})
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
    assert rep["isolated_call_cells"] == 2
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
                   "visit_date": today, "overall_result": "оплачен."})
    assert rep["office_cells"] == 1
    assert rep["office_applied"]["OF-B"]["call_status"] == "Оплачен"
    assert db.get_client("OF-B")["call_status"] == "Оплачен"


def test_published_master_reverse_map_survives_two_enrichment_cycles():
    """Полный контракт без personal→bot gateway.

    safe export → personal enrichment → registry publication (представлена тем
    же master-row) → explicit master import повторяется дважды. Офисный итог
    сохраняется, а самостоятельные поля звонка не трогаются.
    """
    today = date.today().isoformat()
    _import({
        "contract_id": "OF-CYCLE",
        "client": "Два цикла",
        "responsible": "Агент",
    })
    _complete_visit(
        "OF-CYCLE",
        today,
        call_status="Нет контакта",
        visit_comment="бот",
    )

    keys = [field.key for field in db.FIELDS]
    initial = {
        "contract_id": "OF-CYCLE",
        "call_status": "В работе",
        "call_comment": "звонок офиса",
    }
    grid = [
        [db.BY_KEY[key].title for key in keys],
        [initial.get(key) for key in keys],
    ]

    def apply_safe_export():
        safe = [
            row
            for row in db.enrichment_rows()
            if row["contract_id"] == "OF-CYCLE"
        ]
        plan = enrich.build_plan(grid, db.export_schema(), safe)
        for row, column, value, _is_date in plan["writes"]:
            grid[row][column] = value
        return plan

    first = apply_safe_export()
    assert first["cells_written"] == 3
    assert grid[1][keys.index("overall_result")] == "Нет контакта"
    assert grid[1][keys.index("call_status")] == "В работе"
    assert grid[1][keys.index("call_comment")] == "звонок офиса"

    # Ручная правка в personal прошла registry_cycle и появилась в master.
    grid[1][keys.index("overall_result")] = "Доп. Обогащение"
    published = dict(zip(keys, grid[1], strict=True))
    rep = _import({**published, "client": "Два цикла", "responsible": "Агент"})
    assert rep["office_applied"]["OF-CYCLE"]["call_status"] == "Доп. Обогащение"
    assert rep["isolated_call_cells"] == 2

    raw = db.get_client("OF-CYCLE")
    assert json.loads(raw["bot_visit_data"])["call_status"] == "Нет контакта"
    assert db.bot_visit_snapshot(raw)["call_status"] == "Доп. Обогащение"
    assert raw["call_comment"] is None

    second = apply_safe_export()
    assert grid[1][keys.index("overall_result")] == "Доп. Обогащение"
    assert grid[1][keys.index("call_status")] == "В работе"
    assert grid[1][keys.index("call_comment")] == "звонок офиса"
    assert second["cells_written"] == 0
    assert second["cells_already"] >= 3

    published_again = dict(zip(keys, grid[1], strict=True))
    echo = _import({
        **published_again,
        "client": "Два цикла",
        "responsible": "Агент",
    })
    assert echo["office_cells"] == 0
    assert db.bot_visit_snapshot(
        db.get_client("OF-CYCLE")
    )["call_status"] == "Доп. Обогащение"


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
