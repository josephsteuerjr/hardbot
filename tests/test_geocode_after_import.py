"""Геокодинг после импорта не должен знать только один регион.

Регрессия 01.08.2026: `geocode_city_centroids` спрашивала только `parse_city` —
курируемый список городов Согда. Узбекские адреса оставались без координат, и у
половины сотрудников не было ни карты, ни маршрута дня, ни гео-контроля.
"""
import db
import scoring


def _seed(rows):
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'GEO-%'")
        conn.commit()
    grid = [[f.title for f in db.FIELDS], [f.source for f in db.FIELDS]]
    for cid, address, city in rows:
        grid.append([{"contract_id": cid, "client": f"Клиент {cid}",
                      "birth_date": f"199{cid[-1]}-01-0{cid[-1]}",
                      "responsible": "Тестов", "address": address,
                      "city": city}.get(f.key) for f in db.FIELDS])
    db.import_grid(grid, source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)


def _coords(cid):
    with db.connect() as conn:
        r = conn.execute(
            "SELECT geo_lat, geo_lon, geo_precision FROM clients WHERE contract_id=?",
            (cid,)).fetchone()
    return (r["geo_lat"], r["geo_lon"], r["geo_precision"]) if r else (None, None, None)


def test_uzbek_address_gets_a_point_not_only_tajik():
    """Ташкент и Самарканд обязаны геокодиться так же, как Худжанд."""
    _seed([
        ("GEO-1", "в.Сугд, ш.Хучанд, к.Хамидов, хавли 20", ""),
        ("GEO-2", "г. Ташкент, Чиланзарский район, мас. Кизил Шарк", "Ташкент"),
        ("GEO-3", "УЗБЕКИСТАН, САМАРКАНДСКАЯ ОБЛАСТЬ, НАРПАЙСКИЙ, КАДИМ ҚФЙ", "Самарканд"),
    ])
    scoring.geocode_city_centroids()
    for cid in ("GEO-1", "GEO-2", "GEO-3"):
        lat, lon, _ = _coords(cid)
        assert lat is not None and lon is not None, f"{cid} остался без координат"


def test_precision_is_recorded():
    """Без precision гео-контроль считает расстояние до центра области и ругается
    на честного выездника — колонка обязана заполняться."""
    _seed([("GEO-4", "в.Сугд, ш.Хучанд, к.Хамидов, хавли 20", "")])
    scoring.geocode_city_centroids()
    _lat, _lon, precision = _coords("GEO-4")
    assert precision, "точность не записана"


def test_manual_point_is_never_overwritten():
    """Метку поставил доехавший выездник — она дороже любого справочника."""
    _seed([("GEO-5", "г. Ташкент, Чиланзарский район", "Ташкент")])
    with db.connect() as conn:
        conn.execute("UPDATE clients SET geo_lat=1.0, geo_lon=2.0, geo_src='manual' "
                     "WHERE contract_id='GEO-5'")
        conn.commit()
    scoring.geocode_city_centroids(overwrite=True)
    lat, lon, _ = _coords("GEO-5")
    assert (lat, lon) == (1.0, 2.0)


def test_coarse_points_are_spread_apart():
    """Десять кейсов одного города не должны слипаться в одну точку на карте."""
    _seed([(f"GEO-1{i}", "в.Сугд, ш.Хучанд", "") for i in range(4)])
    scoring.geocode_city_centroids()
    points = {_coords(f"GEO-1{i}")[:2] for i in range(4)}
    assert len(points) == 4, "точки одного города совпали"


def test_queue_keeps_the_stored_point_instead_of_guessing_the_city():
    """Выдача очереди обязана отдавать точку из БД.

    Раньше compute_priority выбрасывала координаты и заново угадывала город через
    parse_city — курируемый список Согда. У Ташкента и Самарканда точки не было
    вовсе: ни карты, ни маршрута дня, ни порядка обхода.
    """
    _seed([("GEO-6", "г. Ташкент, Чиланзарский район, мас. Кизил Шарк", "Ташкент")])
    scoring.geocode_city_centroids()
    items = [i for i in scoring._items_from_db() if i["rep"] == "GEO-6"]
    assert items and items[0]["lat"] is not None, "точка не доехала из БД в item"
    out = scoring.compute_priority(items, {"debt": 50, "cluster": 30, "fresh": 20})
    assert out["items"][0]["lat"] is not None, "очередь потеряла точку"
    assert out["cities"] and out["cities"][0]["lat"] is not None, \
        "у кластера вне Согда нет центра — маршрут дня не построится"


def test_foreign_address_is_refused_not_guessed():
    """Справочник знает только Таджикистан и Узбекистан, и на чужой стране он
    раньше не молчал, а уверенно врал: «Самара» → Samariy (Узбекистан) с
    точностью «город», «Самарская область, Тольятти» → Rudaki District
    (Таджикистан). Точка уезжала за тысячи километров, а карточка утверждала,
    что всё в порядке."""
    import geocode
    for addr in ("Самара, улица Ново-Садовая, 106",
                 "Самарская область, Тольятти, Ленинский проспект 1",
                 "Россия, Москва, Тверская 1",
                 "г. Екатеринбург"):
        assert geocode.geocode_address(addr, "", use_precise=False) is None, addr


def test_the_guard_does_not_eat_samarkand():
    """«Самарканд» начинается как «Самара» — защита не должна его срезать."""
    import geocode
    for addr, city in (("УЗБЕКИСТАН, САМАРКАНДСКАЯ ОБЛАСТЬ, НАРПАЙСКИЙ", "Самарканд"),
                       ("САМАРҚАНД ВИЛОЯТИ, КАТТАҚЎРҒОН ШАҲРИ", "")):
        assert geocode.geocode_address(addr, city, use_precise=False) is not None, addr
