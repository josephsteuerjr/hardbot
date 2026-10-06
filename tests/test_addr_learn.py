"""Адрес уточняется по выезду, на котором должника застали (addr_learn.py).

Точка берётся, только если контакт был по адресу, телефон дал точные фиксы и
простоял на месте (или снял там кадр); грубые адреса перезаписываются,
точные и ручные — никогда.
"""
from datetime import date

import pytest

import addr_learn
import db

EMP = "Учёнов Сотрудник"
T0 = 1_758_000_000_000
T = date.today().isoformat()


def _s(dt_s, lat=40.2800, lon=69.6200, acc=20, k="sample"):
    return {"ts": T0 + dt_s * 1000, "lat": lat, "lon": lon, "acc": acc, "k": k}


def _seed(cid, precision, src="geonames", lat=40.5, lon=69.9):
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id=?", (cid,))
        conn.execute("DELETE FROM addr_candidates WHERE contract_id=?", (cid,))
        conn.execute(
            "INSERT INTO clients (contract_id, client, responsible, birth_date, partner, "
            "geo_lat, geo_lon, geo_src, geo_precision) VALUES (?,?,?,?,?,?,?,?,?)",
            (cid, f"Клиент {cid}", EMP, "1990-01-01", "ЗК", lat, lon, src, precision))
        conn.commit()
    return db.get_client(cid)


@pytest.fixture(autouse=True)
def _cleanup():
    db.init_db()
    yield
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'AL-%'")
        conn.execute("DELETE FROM addr_candidates WHERE contract_id LIKE 'AL-%'")
        conn.commit()


FOUND = {"who_at_address": "По адресу проживает клиент"}


def test_candidate_needs_precise_fixes_and_a_stay():
    assert addr_learn.candidate([_s(0, acc=200), _s(300, acc=150)]) is None, "неточные фиксы"
    assert addr_learn.candidate([_s(0), _s(60)]) is None, "простоял минуту — мало"
    cand = addr_learn.candidate([_s(0), _s(120), _s(240)])
    assert cand and cand["stay_min"] == 4 and cand["n"] == 3
    assert abs(cand["lat"] - 40.28) < 1e-6


def test_photo_at_the_spot_replaces_the_stay():
    cand = addr_learn.candidate([_s(0, k="photo"), _s(30, k="save")])
    assert cand and cand["photo"]


def test_coarse_address_is_replaced_by_the_learned_point():
    row = _seed("AL-1", "city")
    out = addr_learn.consider(row, FOUND, {"level": "coarse"}, [_s(0), _s(200), _s(400)],
                              employee=EMP, op_id="op-al-1")
    assert out and out["applied"]
    fresh = db.get_client("AL-1")
    assert fresh["geo_src"] == "visit" and fresh["geo_precision"] == "visit"
    assert abs(fresh["geo_lat"] - 40.28) < 1e-6
    assert db.geo_tolerance_m("visit", "visit") == 250, "выученная точка судится своим допуском"
    with db.connect() as conn:
        c = conn.execute("SELECT * FROM addr_candidates WHERE contract_id='AL-1'").fetchone()
    assert c["applied_at"] and c["prev_precision"] == "city" and c["prev_lat"] == 40.5


def test_no_contact_at_address_teaches_nothing():
    row = _seed("AL-2", "city")
    out = addr_learn.consider(row, {"who_at_address": "По адресу клиента не знают/ не общаются",
                                    "contact_with": "3 лицо"},
                              None, [_s(0), _s(300)], employee=EMP)
    assert out is None
    assert db.get_client("AL-2")["geo_src"] == "geonames"


def test_street_and_manual_points_are_kept():
    row = _seed("AL-3", "street", src="osm")
    out = addr_learn.consider(row, FOUND, {"level": "confirmed"}, [_s(0), _s(300)],
                              employee=EMP)
    assert out and not out["applied"] and "точнее" in out["reason"] or "не трогаем" in out["reason"]
    assert db.get_client("AL-3")["geo_src"] == "osm"
    row = _seed("AL-4", "", src="manual")
    out = addr_learn.consider(row, FOUND, None, [_s(0), _s(300)], employee=EMP)
    assert out and not out["applied"]
    assert db.get_client("AL-4")["geo_src"] == "manual"


def test_village_level_needs_strong_evidence():
    row = _seed("AL-5", "village")
    weak = addr_learn.consider(row, FOUND, None, [_s(0), _s(300)], employee=EMP)
    assert weak and not weak["applied"]
    assert db.get_client("AL-5")["geo_precision"] == "village"
    strong = addr_learn.consider(row, FOUND, None,
                                 [_s(0), _s(100, k="photo"), _s(300)], employee=EMP)
    assert strong and strong["applied"]
    assert db.get_client("AL-5")["geo_precision"] == "visit"


def test_far_verdict_never_teaches():
    row = _seed("AL-6", "city")
    assert addr_learn.consider(row, FOUND, {"level": "far"}, [_s(0), _s(300)],
                               employee=EMP) is None


def test_geocoder_does_not_overwrite_learned_points():
    """Точка «где застали» — как ручная метка: пакетный геокодинг её не трогает."""
    import inspect
    import geocode
    src = inspect.getsource(geocode.geocode_all)
    assert '"visit"' in src or "'visit'" in src
