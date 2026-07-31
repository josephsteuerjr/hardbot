"""Кого система считает ОДНИМ человеком — и почему это блокер, а не мелочь.

Найдено адверсарным разбором 31.07.2026. Ключ человека строился как
(ФИО, birth_date or ''), то есть пустая дата рождения превращалась в полноценное
значение ключа. Два РАЗНЫХ должника с одинаковым ФИО у одного выездника сливались
в одного: в карточке два адреса и два чужих телефона, долг складывался, а главное —
`person_contracts` отдавал оба договора, и закрытие визита по одному писало снимок
во второй. Результат выезда физически уезжал в чужую строку офисного реестра.

Просто разделить всех без даты рождения тоже нельзя: несколько займов на одного
человека — норма. На реальном реестре (17 075 строк, 17.7% без ДР) таких групп 331
на 1 382 договора, и у 320 из них адрес совпадает. Поэтому без ДР объединяем только
при совпадающем опознавательном признаке — адресе, иначе телефоне.
"""
import db

EMP = "Идентов Тест"


def _seed(*rows):
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'PI-%'")
        conn.commit()
    grid = [[f.title for f in db.FIELDS], [f.source for f in db.FIELDS]]
    for row in rows:
        full = {"responsible": EMP, **row}
        grid.append([full.get(f.key) for f in db.FIELDS])
    rep = db.import_grid(grid, source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    assert not rep.get("blocked"), rep.get("error")
    return rep


def _people():
    return {(p["client"], tuple(sorted(r["contract_id"] for r in p["contracts"])))
            for p in db.persons_for_employee(EMP)}


# --- ради чего всё: разные люди не смешиваются -------------------------------
def test_same_name_different_address_are_two_people():
    _seed({"contract_id": "PI-1", "client": "Каримов Ж. А.", "address": "Худжанд, ул. А, 1",
           "phone": "+992900000001", "debt_total": "100000"},
          {"contract_id": "PI-2", "client": "Каримов Ж. А.", "address": "Душанбе, ул. Б, 2",
           "phone": "+992900000002", "debt_total": "700000"})
    assert _people() == {("Каримов Ж. А.", ("PI-1",)), ("Каримов Ж. А.", ("PI-2",))}


def test_visit_does_not_leak_to_the_namesake():
    """Самое дорогое последствие: снимок визита уходил в чужой договор."""
    _seed({"contract_id": "PI-1", "client": "Каримов Ж. А.", "address": "Худжанд, ул. А, 1"},
          {"contract_id": "PI-2", "client": "Каримов Ж. А.", "address": "Душанбе, ул. Б, 2"})
    assert [r["contract_id"] for r in db.person_contracts("PI-1")] == ["PI-1"]


def test_debt_of_two_namesakes_is_not_summed():
    _seed({"contract_id": "PI-1", "client": "Каримов Ж. А.", "address": "Худжанд, ул. А, 1",
           "debt_total": "100000"},
          {"contract_id": "PI-2", "client": "Каримов Ж. А.", "address": "Душанбе, ул. Б, 2",
           "debt_total": "700000"})
    totals = sorted(p["total"] for p in db.persons_for_employee(EMP))
    assert totals == [100_000, 700_000]


# --- и при этом настоящий человек с двумя займами остаётся одним --------------
def test_same_person_two_contracts_still_merges_by_birth_date():
    _seed({"contract_id": "PI-3", "client": "Рахимов Р.", "birth_date": "1990-01-01",
           "address": "Исфара, ул. В, 3"},
          {"contract_id": "PI-4", "client": "Рахимов Р.", "birth_date": "1990-01-01",
           "address": "Исфара, ул. В, 3"})
    assert _people() == {("Рахимов Р.", ("PI-3", "PI-4"))}


def test_same_person_merges_without_birth_date_when_address_matches():
    """320 из 331 таких групп в реальном реестре — именно этот случай."""
    _seed({"contract_id": "PI-5", "client": "Салимов С.", "address": "Конибодом, ул. Г, 5"},
          {"contract_id": "PI-6", "client": "Салимов С.", "address": "Конибодом, ул. Г, 5"})
    assert _people() == {("Салимов С.", ("PI-5", "PI-6"))}
    assert [r["contract_id"] for r in db.person_contracts("PI-5")] == ["PI-5", "PI-6"]


def test_phone_identifies_when_address_is_empty():
    _seed({"contract_id": "PI-7", "client": "Назаров Н.", "phone": "+992911111111"},
          {"contract_id": "PI-8", "client": "Назаров Н.", "phone": "+992911111111"})
    assert _people() == {("Назаров Н.", ("PI-7", "PI-8"))}


def test_no_identifying_evidence_at_all_splits():
    """Ни ДР, ни адреса, ни телефона — оснований считать это одним человеком нет.
    Ошибка в сторону разделения безопасна, в сторону объединения — нет."""
    _seed({"contract_id": "PI-9", "client": "Безликов Б."},
          {"contract_id": "PI-10", "client": "Безликов Б."})
    assert _people() == {("Безликов Б.", ("PI-9",)), ("Безликов Б.", ("PI-10",))}


def test_birth_date_wins_over_address():
    """Разные адреса, но одна дата рождения — человек переехал, это он же."""
    _seed({"contract_id": "PI-11", "client": "Юсупов Ю.", "birth_date": "1985-05-05",
           "address": "Худжанд, старый адрес"},
          {"contract_id": "PI-12", "client": "Юсупов Ю.", "birth_date": "1985-05-05",
           "address": "Душанбе, новый адрес"})
    assert _people() == {("Юсупов Ю.", ("PI-11", "PI-12"))}


def test_grouping_and_sql_lookup_agree():
    """_group_persons и person_contracts обязаны отвечать одинаково: если они
    разойдутся, визит разнесётся по договорам, которых карточка не показывает."""
    _seed({"contract_id": "PI-13", "client": "Один И.", "address": "адрес 1"},
          {"contract_id": "PI-14", "client": "Один И.", "address": "адрес 1"},
          {"contract_id": "PI-15", "client": "Один И.", "address": "адрес 2"})
    for p in db.persons_for_employee(EMP):
        from_group = sorted(r["contract_id"] for r in p["contracts"])
        from_sql = sorted(r["contract_id"] for r in db.person_contracts(p["rep"]))
        assert from_group == from_sql, (from_group, from_sql)


def test_empty_birth_date_is_not_exposed_as_a_key():
    """Служебный суффикс ключа наружу не отдаём — снаружи это по-прежнему «ДР нет»."""
    _seed({"contract_id": "PI-16", "client": "Пустой П.", "address": "адрес"})
    p = db.persons_for_employee(EMP)[0]
    assert p["birth_date"] == ""
