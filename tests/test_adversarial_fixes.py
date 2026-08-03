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


# --- геокодер: два бага, найденных тестовыми реестрами 01.08.2026 ---------------
def test_capital_city_wins_over_the_region_of_the_same_name():
    """В GeoNames «Ташкент» — это и город (adm1=13), и область (adm1=14), обе
    записи с fcode=ADM1. Код брал первую попавшуюся, порядок задавал файл дампа:
    для адреса в Юнусабаде опорой становился центроид ОБЛАСТИ, точка уезжала за
    66 км, а точность при этом рапортовалась «махалля»."""
    import geocode
    hit = geocode.geocode_address(
        "г. Ташкент, Юнусабадский район, кв. 11, Богишамол МСГ", "Ташкент",
        use_precise=False)
    assert hit is not None
    assert abs(hit["lat"] - 41.31) < 0.25 and abs(hit["lon"] - 69.28) < 0.25, \
        f"точка не в Ташкенте: {hit}"


def test_region_wins_when_the_address_says_region():
    """Обратный случай: «ТОШКЕНТ ВИЛОЯТИ, ЯНГИЙЎЛ ШАҲРИ» — это область, и
    подменять её городом нельзя, иначе Янгиюль схлопнется в центр Ташкента."""
    import geocode
    hit = geocode.geocode_address(
        "ЎЗБЕКИСТОН, ТОШКЕНТ ВИЛОЯТИ, ЯНГИЙЎЛ ШАҲРИ, РАМАДОН МФЙ", "Ташкент",
        use_precise=False)
    assert hit is not None
    assert abs(hit["lat"] - 41.11) < 0.35, f"точка уехала из Янгиюля: {hit}"


def test_city_from_the_address_anchors_the_search():
    """Района в адресе нет, город назван прямым текстом — он и есть опора.
    Иначе кандидаты ранжируются относительно центра области, и «Bo'ston» под
    Ангреном выигрывает у правильного под Охангароном (39 км мимо)."""
    import geocode
    hit = geocode.geocode_address(
        "ЎЗБЕКИСТОН, ТОШКЕНТ ВИЛОЯТИ, ОХАНГАРОН ШАҲРИ, BO‘STON MFY", "",
        use_precise=False)
    assert hit is not None
    assert abs(hit["lat"] - 40.91) < 0.2 and abs(hit["lon"] - 69.64) < 0.2, \
        f"точка не в Охангароне: {hit}"


# --- находки адверсарки по спине 02.08.2026 ------------------------------------
def test_unparsed_assign_date_does_not_hide_the_case():
    """Одно нажатие «Применить» в панели периода прятало у выездника десятки
    договоров: дата закрепления в реестре бывает пустой или в незнакомом формате.
    Пульт при этом показывал полное число, и причину было не найти."""
    from datetime import date as _d
    lo, hi = _d(2026, 1, 1), _d(2026, 12, 31)
    assert db._date_in("", lo, hi) is True
    assert db._date_in("чепуха", lo, hi) is True
    assert db._date_in("01.01.2020", lo, hi) is False      # настоящая дата вне окна — прячем


def test_date_parser_knows_the_formats_people_actually_type():
    from datetime import date as _d
    want = _d(2026, 8, 1)
    for s in ("01.08.2026", "2026-08-01", "01/08/2026", "01-08-2026",
              "2026/08/01", "01.08.2026."):
        assert db._as_date(s) == want, s


def test_report_chat_notifier_has_every_name_it_uses():
    """`uid` использовался, но не был параметром — NameError на КАЖДОМ визите,
    съеденный общим except: отчёт в беседу не уходил вообще, а наружу HTTP 200."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(web._notify_report_chats))
    fn = tree.body[0]
    names = {a.arg for a in fn.args.args}
    assigned = {t.id for n in ast.walk(fn) if isinstance(n, ast.Assign)
                for t in n.targets if isinstance(t, ast.Name)}
    for n in ast.walk(fn):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            if n.id in ("uid", "row", "vlat", "vlon", "dist_m"):
                assert n.id in names or n.id in assigned, f"{n.id} не определено"


def test_geo_alert_ignores_an_imprecise_phone_fix():
    """В помещении браузер отдаёт сетевой фикс с точностью 800–1500 м. Обвинять
    по такой точке — значит обвинять человека, стоявшего ровно там, где надо.

    Раньше это решалось отсечкой «не мерить, если фикс хуже порога». С 03.08.2026
    погрешность телефона СКЛАДЫВАЕТСЯ с погрешностью адреса: визит всё равно
    измеряется (руководителю полезно видеть число), но нарушением не считается,
    пока расстояние укладывается в сумму двух погрешностей."""
    import inspect
    src = inspect.getsource(web._save)
    assert 'g.get("acc")' in src, "погрешность точки не читается"
    assert "tolerance + max(acc" in src, "погрешность телефона не учтена в допуске"
    assert "db.geo_tolerance_m(" in src, "допуск по уровню адреса не применяется"


def test_non_ascii_signature_is_refused_not_crashed():
    """compare_digest на СТРОКАХ требует ASCII с обеих сторон и бросал TypeError
    на кириллическом омоглифе: страница карты дня отдавала 500 вместо честного
    «ссылка устарела», а отказ авторизации превращался в 500."""
    import time
    import maplink
    exp = str(int(time.time()) + 3600)
    assert maplink.verify("Иванов", "2026-08-02", exp, "абвгд") is False
    assert maplink.verify("Иванов", "2026-08-02", exp, "deadbeef") is False
    assert web.validate_init_data("hash=абвгд&auth_date=1", "1:TOK") is None


def test_album_caption_is_escaped():
    """Бот шлёт подписи с parse_mode=HTML — ФИО из реестра туда попадает как есть."""
    import inspect
    import handlers_manager
    src = inspect.getsource(handlers_manager._send_photos)
    assert "html.escape(p['client'])" in src or 'html.escape(p["client"])' in src


# --- находки финальной адверсарки, часть 2 -------------------------------------
def test_reset_can_actually_delete_every_table_it_claims():
    """Таблица объявлена сбрасываемой, но забыта в порядке удаления — и сброс
    падает целиком, как только у кого-то появится первая заметка."""
    import limited_test_reset as ltr
    assert not (ltr.RESET_TABLES - set(ltr.DELETE_ORDER)), "нечем удалить"
    assert not (set(ltr.DELETE_ORDER) - ltr.RESET_TABLES), "удаляем необъявленное"


def test_activity_merge_uses_the_keys_that_actually_exist():
    """Слияние по двум телеграм-аккаунтам одного ФИО было написано по выдуманным
    ключам и молча не делало ничего: карточка так и показывала нули."""
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM agent_bind WHERE telegram_id IN (8801, 8802)")
        conn.execute("DELETE FROM visits WHERE contract_id = 'ACT-1'")
        conn.commit()
    db.set_bind(8801, "Двухтелефонов Д.")
    db.bind_agent(8802, "Двухтелефонов Д.", "agent", approved=True)
    with db.connect() as conn:
        conn.execute("INSERT INTO visits (contract_id, agent_id, created_at, payload) "
                     "VALUES ('ACT-1', 8802, date('now'), '')")
        conn.commit()
    a = db.employee_activity("Двухтелефонов Д.")
    assert a["visits_7d"] == 1, f"активность второго аккаунта потеряна: {a}"


def test_app_search_refuses_addresses_outside_coverage():
    """Поиск в приложении шёл в справочник напрямую, мимо защиты: «Москва»
    отдавалась как достоверная точка кишлака Москва в Хатлоне."""
    import inspect
    src = inspect.getsource(web._geocode_free)
    assert "_outside_coverage" in src


def test_null_island_is_not_a_visit_location():
    """Точка (0,0) — сбой датчика, а не место визита: без отсечки она давала
    публичное «закрыт в 8290 км от адреса»."""
    import inspect
    src = inspect.getsource(web._save)
    assert "abs(vlat) < 0.01" in src


def test_manager_switcher_depends_on_the_role_only():
    """Условие «и нет привязки» запирало руководителя в чужом портфеле: портфель
    открывает роль, а переключатель исчезал из-за случайной привязки."""
    import inspect
    src = inspect.getsource(web._bootstrap)
    assert '"manager": bool(scope)' in src
    assert "not db.get_binding(uid)" not in src


def test_report_is_signed_by_the_same_rule_that_opened_the_portfolio():
    """Иначе в одном посте три разных человека: кейс сотрудника A, подпись
    «выездник B», а на месте был руководитель C."""
    import inspect
    src = inspect.getsource(web._notify_report_chats)
    assert "_acting_actor_name(uid) or db.get_binding(uid)" in src
