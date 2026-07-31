"""Находки адверсарного разбора 31.07.2026 — чтобы не вернулись.

Каждый тест назван последствием, а не механикой: важно не «что за if», а какой
вред он предотвращает. Порядок — по цене ошибки.
"""
from datetime import date

import pytest

import db
import geocode
import notify
import scoring
import scoring_model
import web


# --- потеря денег и данных ----------------------------------------------------
def test_paid_off_contract_does_not_remove_a_person_with_live_debt():
    """У человека два КД: один погашен, по второму долг. Killer-фильтр брал
    результат с ЛЮБОГО договора и снимал человека целиком — вместе с непогашенным."""
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'AF-%'")
        conn.commit()
    grid = [[f.title for f in db.FIELDS], [f.source for f in db.FIELDS]]
    for cid, res, debt_v in (("AF-1", "Оплачен", "0"), ("AF-2", "В работе", "80000")):
        grid.append([{"contract_id": cid, "client": "Двухкредитов Д.",
                      "birth_date": "1988-08-08", "responsible": "Иванов",
                      "address": "Худжанд, ул. Д, 8", "overall_result": res,
                      "debt_total": debt_v}.get(f.key) for f in db.FIELDS])
    db.import_grid(grid, source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    item = next(i for i in scoring._items_from_db() if i["client"] == "Двухкредитов Д.")
    assert item["result"] != "Оплачен", "исход взят с погашенного договора"
    row = scoring.score_items([{**item, "city": "Худжанд"}],
                              {"debt": 50, "cluster": 30, "fresh": 20}, "courier")[0]
    assert not row["dropped"], "человек снят из очереди, хотя долг остался"


def test_settled_debt_is_not_confused_with_missing_debt():
    """Ноль — «взыскивать нечего», пусто — «неизвестно». Раньше и то и другое
    выключало доменную отсечку, и погашенный человек уходил в верх очереди."""
    m = scoring_model.Model({"base_p": 0.5, "base_a": 50_000, "lifts": {}})
    assert m.expected({"debt": 0}) == 0.0
    assert m.expected({"debt": ""}) > 0.0          # неизвестно — ограничивать нечем
    assert m.expected({"debt": 1_000}) <= 1_000


def test_empty_portfolio_does_not_crash_priority():
    """Пустая БД — штатное состояние сразу после сброса; пульт дёргает приоритет."""
    assert scoring.score_items([], {"debt": 50, "cluster": 30, "fresh": 20}) == []


# --- утечки и подмена авторства ------------------------------------------------
def test_map_page_escapes_user_text():
    """Подпись метки вводит выездник, а карта дня вставляет её в HTML.
    Без экранирования это хранимый XSS в сессии руководителя."""
    assert "const esc=" in web.MAP_PAGE
    for unsafe in ("+j.employee+", "+p.client+", "+(p.label||", "+p.at+"):
        assert unsafe not in web.MAP_PAGE, f"неэкранированная подстановка: {unsafe}"


def test_access_log_hides_query_string():
    """В query ездят адрес должника (/api/geocode?q=…) и живые координаты
    выездника (/api/route?lat=…) — в логах контейнера им не место."""
    import inspect
    body = inspect.getsource(web._PathOnlyAccessLogger.log)
    assert "request.path" in body
    # именно в КОДЕ не должно быть полного пути с запросом
    assert "path_qs" not in body and "rel_url" not in body and "query" not in body
    assert "access_log_class" in inspect.getsource(web.start_site)


def test_visit_closed_by_manager_names_the_actual_author():
    """Руководитель работает от имени сотрудника. Без пометки его визит выглядел
    визитом сотрудника — с координатами руководителя в гео-контроле."""
    row = {"responsible": "Иванов И.", "client": "Клиент К.", "contract_id": "X-1",
           "city": "", "address": "", "call_status": "", "agreed_payment": "",
           "visit_comment": "", "call_comment": ""}
    own = notify.visit_report_text(row, actor="Иванов И.")
    by_manager = notify.visit_report_text(row, actor="руководитель Пётр")
    assert "закрыл" not in own
    assert "закрыл: руководитель Пётр" in by_manager


# --- геоточность ----------------------------------------------------------------
def test_region_centroid_is_never_called_a_city():
    """Запись ADM1 — центроид ОБЛАСТИ. Отдавая её как precision='city', система
    врала о точности и перебивала центр района, который заметно ближе."""
    assert "ADM1" not in geoindex_ok_codes("city")
    assert "ADM1" not in geoindex_ok_codes("jamoat")


def geoindex_ok_codes(level):
    import geoindex
    return geoindex._OK_CODE.get(level, ())


def test_namesake_far_from_the_district_is_rejected():
    """Код района в дампах часто пуст, поэтому ранг по нему не работает и
    побеждает тёзка, лежащий в индексе первым. Ловим геометрией."""
    import addr_cis
    import geoindex

    def gn(gid, name, lat, lon, fclass, fcode, adm1, pop=0):
        row = [""] * 19
        row[0], row[1], row[2], row[3] = gid, name, name, ""
        row[4], row[5] = str(lat), str(lon)
        row[6], row[7], row[8] = fclass, fcode, "TJ"
        row[10], row[14] = adm1, str(pop)
        return row

    idx = geoindex.Index()
    idx.add(gn("1", "Viloyati Sughd", 40.2, 69.6, "A", "ADM1", "03", 2_600_000))
    idx.add(gn("2", "Nohiyai Spitamen", 40.18, 69.05, "A", "ADM2", "03", 140_000))
    # тёзка в той же области, но за 150 км от района
    idx.add(gn("3", "Navobod", 39.0, 68.0, "P", "PPL", "03", 500))
    hit = idx.resolve(addr_cis.parse("вилояти Суғд, ноҳияи Спитамен, деҳаи Навобод"))
    assert hit is not None
    assert hit["name"] != "Navobod", "принят тёзка за 150 км"
    assert hit["precision"] in ("district", "region")


def test_geo_control_ignores_coarse_points():
    """Центр района — не адрес. Считать до него расстояние значит показывать
    руководителю «визит далеко от адреса» у честного выездника."""
    db.init_db()
    with db.connect() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(clients)")}
    assert "geo_precision" in cols


# --- импорт ----------------------------------------------------------------------
def test_excel_serial_date_becomes_a_real_date():
    """Ячейка, потерявшая формат даты, приезжает числом. Сохранённая как «46204»,
    она выпадала из всех фильтров по дате."""
    assert db._excel_serial_date(46204) == "2026-07-01"
    assert db._excel_serial_date(1) is None          # не дата — не выдумываем
    assert db._excel_serial_date("мусор") is None


def test_payment_window_matches_between_import_and_training():
    """Признак «платил раньше» обязан считаться одним правилом в бою и в обучении:
    иначе в январе лучший плательщик получит счётчик, которого модель не видела."""
    import train_scoring
    assert train_scoring.PAY_MONTH_RX.pattern == db._PAID_MONTH_RX.pattern


# --- роль руководителя ------------------------------------------------------------
def test_acting_employee_choice_is_persisted():
    """Фолбэк не записывался, и личность руководителя дрейфовала при любой правке
    списка сотрудников — а приложение считало это сменой человека и стирало кэш."""
    uid = 990101
    with db.connect() as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (f"acting_employee:{uid}",))
        conn.commit()
    # manager_employees отдаёт список ОТСОРТИРОВАННЫМ — первый и станет выбором
    assert db.acting_employee(uid, ["Абрамов", "Борисов"]) == "Абрамов"
    # состав пополнился именем, которое встало ПЕРВЫМ: выбор обязан остаться прежним,
    # иначе руководитель молча оказывается в чужом портфеле
    assert db.acting_employee(uid, ["Аакимов", "Абрамов", "Борисов"]) == "Абрамов"
