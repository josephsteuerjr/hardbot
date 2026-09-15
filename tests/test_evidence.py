"""Подтверждение выезда (evidence.py) — чистая логика без базы.

Вердикт «был ли у адреса» складывается из ВСЕХ следов телефона за карточку, а
не из одной точки закрытия; адрес, известный до города, не судится вовсе;
серии («пачка», «с одной точки», «телепортация») считаются по фактам дня.
"""
import evidence

ADDR = (40.2800, 69.6200)
T0 = 1_758_000_000_000          # мс эпохи, произвольный полдень


def _s(dt_s, lat=40.2800, lon=69.6200, acc=15, k="sample", on=1):
    return {"ts": T0 + dt_s * 1000, "lat": lat, "lon": lon, "acc": acc, "k": k, "on": on}


def _build(samples, tolerance=300, precision="street", **kw):
    return evidence.build(evidence.normalize_samples(samples), ADDR, tolerance,
                          precision=precision, **kw)


# --- уровни вердикта ----------------------------------------------------------
def test_confirmed_when_a_usable_sample_is_within_tolerance():
    ev = _build([_s(0, k="open"), _s(600), _s(900, k="save")],
                opened_ms=T0, saved_ms=T0 + 900_000, received_ms=T0 + 930_000)
    assert ev["level"] == "confirmed" and ev["at_address"] is True
    assert ev["near_min"] == 15 and ev["near_n"] == 3
    assert ev["online_save"] is True, "принят через 30 с после нажатия — связь была"
    assert "На адресе" in ev["note"] and "связь была" in ev["note"]


def test_far_when_all_samples_are_outside_tolerance_plus_accuracy():
    ev = _build([_s(0, lat=40.30, lon=69.70, k="open"), _s(300, lat=40.31, lon=69.71, k="save")],
                saved_ms=T0 + 300_000, received_ms=T0 + 8 * 3600 * 1000)
    assert ev["level"] == "far" and ev["at_address"] is False
    assert ev["min_dist_m"] > 5000
    assert ev["online_save"] is False, "доехал через 8 часов — связи на месте не было"
    assert "не был" in ev["note"] and "доехал позже" in ev["note"]


def test_phone_accuracy_widens_tolerance_not_blame():
    """Сетевой фикс в подъезде даёт 900 м — точка в 700 м от дома НЕ нарушение."""
    ev = _build([_s(0, lat=40.2863, lon=69.6200, acc=900, k="save")])
    assert ev["level"] == "confirmed"


def test_hopeless_accuracy_is_not_judged():
    ev = _build([_s(0, acc=4000, k="save")])
    assert ev["level"] == "nofix"
    assert "неточная" in ev["note"]


def test_no_points_at_all_is_nogeo():
    ev = _build([{"ts": T0, "on": 1}])
    assert ev["level"] == "nogeo"


def test_coarse_address_is_never_judged_even_if_far():
    ev = _build([_s(0, lat=41.0, lon=70.0, k="save")], tolerance=None, precision="city")
    assert ev["level"] == "coarse" and ev["at_address"] is None
    assert "до города" in ev["note"]


def test_missing_address_point_is_coarse_too():
    ev = evidence.build(evidence.normalize_samples([_s(0)]), None, None)
    assert ev["level"] == "coarse"
    assert "нет" in ev["note"]


def test_curiosity_open_far_away_does_not_spoil_confirmation():
    """Карточку открыли в офисе, приехали через час, стояли у двери 10 минут."""
    ev = _build([_s(0, lat=40.30, lon=69.70, k="open"), _s(3600), _s(4200, k="save")],
                opened_ms=T0, saved_ms=T0 + 4_200_000, received_ms=T0 + 4_210_000)
    assert ev["level"] == "confirmed"
    assert ev["near_min"] == 10, "время у адреса — от первого близкого следа, не от открытия"
    assert ev["open_dist_m"] > 5000 and ev["card_min"] == 70


def test_was_at_address_but_saved_elsewhere_is_flagged_not_demoted():
    """Стоял у двери 12 минут, а «Сохранить выезд» нажал в трёх километрах —
    выезд был, но руководству важно и то, ГДЕ заполнили карточку."""
    ev = _build([_s(0, k="open"), _s(720), _s(2400, lat=40.30, lon=69.65, k="save")],
                saved_ms=T0 + 2_400_000, received_ms=T0 + 2_410_000)
    assert ev["level"] == "confirmed" and ev["saved_away"] is True
    assert ev["save_dist_m"] > 2000
    assert "заполнен не на адресе" in ev["note"] and "На адресе" in ev["note"]
    # сохранил там же, где стоял, — признака нет
    ev2 = _build([_s(0), _s(600, k="save")])
    assert ev2["saved_away"] is False
    # у адреса не был — признак не ставится (вердикт и так «не был»)
    ev3 = _build([_s(0, lat=40.30, lon=69.65, k="save")])
    assert ev3["level"] == "far" and ev3["saved_away"] is None


def test_served_draft_points_are_counted_as_online_proof():
    ev = _build([_s(0, k="draft"), _s(20, k="draft"), _s(60, k="save")])
    assert ev["served"] == 2


def test_photo_near_is_reported():
    ev = _build([_s(0, k="photo"), _s(30, lat=40.30, lon=69.70, k="save")])
    assert ev["level"] == "confirmed" and ev["photo_near"] is True
    assert "фото на месте" in ev["note"]


# --- нормализация --------------------------------------------------------------
def test_normalize_drops_garbage_and_sorts():
    raw = [{"ts": T0 + 5000, "lat": 40.28, "lon": 69.62},
           {"ts": T0, "lat": "x", "lon": 69.62},           # координата мусор → без точки
           {"ts": None, "lat": 40.28, "lon": 69.62},        # без момента → выброшен
           {"ts": T0 + 1000, "lat": 0, "lon": 0},           # нулевой остров → без точки
           {"ts": "2026-09-15T09:00:00", "lat": 40.28, "lon": 69.62, "acc": "12", "on": "1"}]
    out = evidence.normalize_samples(raw)
    assert [s["ts"] for s in out] == sorted(s["ts"] for s in out)
    assert len(out) == 4
    assert sum(1 for s in out if s["lat"] is not None) == 2
    iso = next(s for s in out if s["acc"] == 12)
    assert iso["on"] is True


def test_local_clock_is_field_time_not_server_utc():
    # 09:00 UTC → 14:00 в Душанбе и Ташкенте
    assert evidence.local_hhmm("2026-09-15T09:00:00") == "14:00"


# --- серии ---------------------------------------------------------------------
def test_batch_flag_marks_three_saves_in_ten_minutes():
    day = [{"id": i, "ms": T0 + i * 120_000, "lat": 40.28 + i * 0.01, "lon": 69.62,
            "alat": 40.28 + i * 0.01, "alon": 69.62} for i in range(3)]
    flags = evidence.series_flags(day)
    assert all(flags[i]["batch"] for i in range(3))
    assert not any(flags[i]["same_spot"] for i in range(3)), "адреса совпадают с точками"


def test_same_spot_flag_needs_distant_addresses():
    day = [{"id": 1, "ms": T0, "lat": 40.28, "lon": 69.62, "alat": 40.28, "alon": 69.62},
           {"id": 2, "ms": T0 + 20 * 60_000, "lat": 40.2801, "lon": 69.62,
            "alat": 40.40, "alon": 69.62}]
    flags = evidence.series_flags(day)
    assert flags[1]["same_spot"] and flags[2]["same_spot"]
    assert not flags[2]["batch"]
    assert evidence.is_series(flags[2]) and "с одной точки" in evidence.series_text(flags[2])


def test_two_debtors_in_one_house_are_not_a_series():
    day = [{"id": 1, "ms": T0, "lat": 40.28, "lon": 69.62, "alat": 40.28, "alon": 69.62},
           {"id": 2, "ms": T0 + 20 * 60_000, "lat": 40.2801, "lon": 69.62,
            "alat": 40.2802, "alon": 69.6201}]
    flags = evidence.series_flags(day)
    assert not evidence.is_series(flags[1]) and not evidence.is_series(flags[2])


def test_teleport_flag_on_impossible_speed():
    day = [{"id": 1, "ms": T0, "lat": 40.28, "lon": 69.62},
           {"id": 2, "ms": T0 + 60_000, "lat": 40.38, "lon": 69.62}]   # 11 км за минуту
    flags = evidence.series_flags(day)
    assert flags[2]["teleport"] and flags[2]["speed_kmh"] > 150
    assert "км/ч" in evidence.series_text(flags[2])


def test_line_and_parse_roundtrip():
    ev = _build([_s(0, k="save")])
    text = evidence.dumps(ev)
    back = evidence.parse(text)
    assert back["level"] == "confirmed"
    assert evidence.line(back).startswith("🛡")
    assert evidence.parse("мусор") is None and evidence.parse("") is None
