"""Руководитель направления: список сотрудников считается живьём.

Замороженный список устаревал бы молча — новый сотрудник не появлялся бы в
сводке, а уволенный оставался бы в ней навсегда.
"""
import db


def _import(rows):
    grid = [[f.title for f in db.FIELDS], [f.source for f in db.FIELDS]]
    for cid, responsible, city in rows:
        grid.append([{"contract_id": cid, "client": "К", "birth_date": "1990-01-01",
                      "responsible": responsible, "address": city, "city": city,
                      "debt_total": "1000"}.get(f.key) for f in db.FIELDS])
    db.import_grid(grid, source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)


def _setup():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'DIR-%'")
        conn.execute("DELETE FROM managers WHERE telegram_id IN (601, 602, 603)")
        conn.commit()
    _import([("DIR-1", "Таджиков Т.", "Худжанд"),
             ("DIR-2", "Узбеков У.", "Ташкент"),
             ("DIR-3", "Самаркандов С.", "Самарканд")])


def test_direction_scopes_are_separate():
    _setup()
    db.set_manager(601, "Рук РТ", ["Таджикистан"], [], direction="tj")
    db.set_manager(602, "Рук УЗ", ["Узбекистан"], [], direction="uz")
    assert db.manager_employees(601) == ["Таджиков Т."]
    assert db.manager_employees(602) == ["Samarkandov" if False else "Самаркандов С.",
                                         "Узбеков У."]


def test_cis_manager_sees_both():
    _setup()
    db.set_manager(603, "Рук СНГ", ["Таджикистан", "Узбекистан"], [], direction="all")
    # Проверяем ВХОЖДЕНИЕ, а не длину: «СНГ» — это все сотрудники базы, и точное
    # число зависит от того, что оставили после себя соседние тесты.
    assert {"Таджиков Т.", "Узбеков У.", "Самаркандов С."} <= set(
        db.manager_employees(603))


def test_cis_manager_sees_russia_too():
    """СНГ — это ВСЕ, а не «РТ плюс УЗ».

    Появился портфель по Самарской области. Пока «all» означало объединение двух
    стран, сотрудник с одними российскими кейсами не попадал НИ В ОДНО направление
    и пропадал из сводки руководителя СНГ молча — как будто не работает.
    """
    _setup()
    _import([("DIR-RU", "Самарский С.", "Самара")])
    db.set_manager(603, "Рук СНГ", list(db.DIRECTIONS["all"][1]), [], direction="all")
    assert "Самарский С." in db.manager_employees(603)
    # …и при этом не протёк в страновые направления
    db.set_manager(601, "Рук РТ", ["Таджикистан"], [], direction="tj")
    db.set_manager(602, "Рук УЗ", ["Узбекистан"], [], direction="uz")
    assert "Самарский С." not in db.manager_employees(601)
    assert "Самарский С." not in db.manager_employees(602)


def test_new_employee_appears_without_touching_the_role():
    """Главное свойство направления: состав не надо править руками."""
    _setup()
    db.set_manager(601, "Рук РТ", ["Таджикистан"], [], direction="tj")
    assert db.manager_employees(601) == ["Таджиков Т."]
    _import([("DIR-4", "Новичок Н.", "Душанбе")])
    assert db.manager_employees(601) == ["Новичок Н.", "Таджиков Т."]


def test_explicit_employee_list_still_works():
    """Точечный руководитель (список из пульта) не должен пострадать."""
    _setup()
    db.set_manager(601, "Точечный", ["Худжанд"], ["Таджиков Т."])
    assert db.manager_employees(601) == ["Таджиков Т."]
    _import([("DIR-5", "Ещё Один", "Худжанд")])
    assert db.manager_employees(601) == ["Таджиков Т."], "точечный список расширился сам"
