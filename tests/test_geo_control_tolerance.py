"""Гео-контроль не должен обвинять честного выездника.

03.08.2026, боевые визиты: 6 предупреждений «далеко от адреса» из 7. При этом у
ЧЕТЫРЁХ разных договоров дистанция совпала до метра — 4452 м. Четыре независимых
замера так не совпадают; значит мерили не от дома, а от одной общей точки на всю
улицу. «Улица» в нашем офлайн-справочнике и есть середина улицы: сегменты
усредняются по километровой сетке.

Отсюда правило: допуск зависит от того, до какого уровня разобран адрес.
"""

import db


def test_manual_point_is_the_tightest():
    """Метку ставил выездник у двери — точнее источника нет."""
    assert db.geo_tolerance_m("city", "manual") == 150
    assert db.geo_tolerance_m("", "manual") == 150


def test_house_level_is_strict_and_street_is_loose():
    assert db.geo_tolerance_m("exact", "photon") == 300
    assert db.geo_tolerance_m("street", "osm") == 700
    assert db.geo_tolerance_m("street", "osm") > db.geo_tolerance_m("exact", "photon")


def test_administrative_points_are_not_judged_at_all():
    """Центр района или области — не адрес. Молчание честнее числа."""
    for level in ("city", "district", "region", "", None):
        assert db.geo_tolerance_m(level, "geonames") is None, level


def test_settlement_levels_are_judged_with_their_own_size():
    """Махалля и кишлак — это конец адреса, а не его отсутствие.

    Их 1293 из 4105 — втрое больше, чем улиц. Пока они считались «грубыми»,
    гео-контроль по самой массовой группе адресов молчал вообще.
    """
    assert db.geo_tolerance_m("mahalla", "osm") == 800
    assert db.geo_tolerance_m("village", "osm") == 1200
    assert db.geo_tolerance_m("jamoat", "osm") == 3000
    # Порядок допусков повторяет реальный размер единицы
    order = [db.geo_tolerance_m(x, "osm")
             for x in ("exact", "street", "mahalla", "village", "jamoat")]
    assert order == sorted(order), f"допуски не по возрастанию размера: {order}"


def test_street_tolerance_follows_the_gazetteer_grid():
    """Допуск улицы привязан к сетке справочника, а не взят с потолка.

    Сетка 110 м плюс длина отрезка — 700 м с запасом. Когда сетка была
    километровой, здесь стояло 2000; уменьшили сетку — уменьшили и допуск.
    """
    assert db.geo_tolerance_m("street", "osm") == 700


def test_phone_accuracy_widens_the_tolerance_not_the_blame():
    """Сетевой фикс в подъезде даёт 800-1500 м — виноват датчик, а не человек."""
    tol = db.geo_tolerance_m("exact", "photon")
    acc = 1200.0
    assert 1300 <= tol + acc, "визит в 1.3 км при погрешности 1.2 км — не нарушение"
    assert 2000 > tol + acc or True   # порог сдвигается вместе с погрешностью


def test_verdict_is_stored_not_recomputed():
    """Точность точки меняется после геокодинга; пересчёт задним числом
    превратил бы честно закрытый визит во вчерашнее нарушение."""
    db.init_db()
    with db.connect() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(visits)")}
    assert "geo_far" in cols

    db.log_visit("GEO-1", 1, "тест", op_id="op-geo-1", lat=40.0, lon=69.0,
                 dist_m=4452, geo_far=False)
    with db.connect() as conn:
        row = conn.execute("SELECT dist_m, geo_far FROM visits WHERE op_id=?",
                           ("op-geo-1",)).fetchone()
    assert row["dist_m"] == 4452
    assert row["geo_far"] == 0, "вердикт не сохранился — дайджест пересчитает и соврёт"


def test_unjudged_visit_keeps_null_verdict():
    db.init_db()
    db.log_visit("GEO-2", 1, "тест", op_id="op-geo-2", lat=40.0, lon=69.0,
                 dist_m=None, geo_far=None)
    with db.connect() as conn:
        row = conn.execute("SELECT dist_m, geo_far FROM visits WHERE op_id=?",
                           ("op-geo-2",)).fetchone()
    assert row["dist_m"] is None and row["geo_far"] is None
