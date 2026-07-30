"""Запись трека поездки (`/api/track` → `track_points`).

До этого от всей поездки в БД оставались две точки: координата фотографии и место
закрытия визита. Трек нужен, чтобы получить фактическое время в пути (движок
сейчас множит оценку OSRM на взятый с потолка коэффициент), понять, какие дороги
реально проезжаемы, и померить длительность визита.

Главное требование — НЕ ТЕРЯТЬ и НЕ ДУБЛИРОВАТЬ: в поле сеть рвётся, клиент
переотправляет одни и те же пачки, а телефон присылает мусорные координаты.
"""
import asyncio
import json

import pytest
from aiohttp.test_utils import make_mocked_request

import db
import web

EMP = "Трекеров Тест"
SESS = "sess-001"


def _post(body, monkeypatch, auth=(777, EMP)):
    monkeypatch.setattr(web, "_auth", lambda _r: auth)
    req = make_mocked_request("POST", "/api/track")

    async def _json():
        return body
    req.json = _json
    return json.loads(asyncio.run(web._track(req)).body.decode())


def _pt(ts, lat=40.28, lon=69.62, **kw):
    return {"ts": ts, "lat": lat, "lon": lon, **kw}


def _clean():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM track_points WHERE employee = ?", (EMP,))
        conn.commit()


def test_points_are_stored(monkeypatch):
    _clean()
    r = _post({"session_id": SESS,
               "points": [_pt("2026-07-30T10:00:00Z", acc=12, speed=8.5, heading=270),
                          _pt("2026-07-30T10:00:20Z", 40.29, 69.63)]}, monkeypatch)
    assert r["ok"] and r["added"] == 2
    rows = db.track_points(SESS)
    assert len(rows) == 2
    assert rows[0]["employee"] == EMP and rows[0]["telegram_id"] == 777
    assert rows[0]["acc_m"] == 12 and rows[0]["speed"] == 8.5


def test_resend_of_same_batch_adds_nothing(monkeypatch):
    """Оффлайн-очередь досылает пачку повторно — дублей быть не должно."""
    _clean()
    batch = {"session_id": SESS, "points": [_pt("2026-07-30T11:00:00Z")]}
    assert _post(batch, monkeypatch)["added"] == 1
    assert _post(batch, monkeypatch)["added"] == 0
    assert len(db.track_points(SESS)) == 1


def test_broken_coordinates_are_dropped_not_fatal(monkeypatch):
    """Телефон присылает NaN, нули и строки. Поездка из-за этого падать не должна."""
    _clean()
    r = _post({"session_id": SESS, "points": [
        _pt("2026-07-30T12:00:00Z"),
        {"ts": "2026-07-30T12:00:01Z", "lat": None, "lon": 69.6},
        {"ts": "2026-07-30T12:00:02Z", "lat": "мусор", "lon": "мусор"},
        {"ts": "2026-07-30T12:00:03Z", "lat": 0, "lon": 0},        # «нулевой остров»
        {"ts": "2026-07-30T12:00:04Z", "lat": 200, "lon": 500},
        {"ts": "", "lat": 40.3, "lon": 69.6},                      # без времени
    ]}, monkeypatch)
    assert r["added"] == 1


def test_foreign_contract_keeps_track_but_drops_link(monkeypatch):
    """rep приходит от клиента. Чужой договор не привязываем, но и поездку не
    выбрасываем: потеря сети не должна становиться потерей данных."""
    _clean()
    r = _post({"session_id": SESS, "rep": "НЕТ-ТАКОГО",
               "points": [_pt("2026-07-30T13:00:00Z")]}, monkeypatch)
    assert r["added"] == 1
    assert db.track_points(SESS)[0]["contract_id"] is None


def test_session_id_required(monkeypatch):
    with pytest.raises(web.web.HTTPBadRequest):
        _post({"points": [_pt("2026-07-30T14:00:00Z")]}, monkeypatch)


def test_points_must_be_a_list(monkeypatch):
    with pytest.raises(web.web.HTTPBadRequest):
        _post({"session_id": SESS, "points": "не список"}, monkeypatch)


def test_batch_is_capped(monkeypatch):
    """Час без сети — сотни точек в одной отправке. Режем, чтобы не встало колом."""
    _clean()
    pts = [_pt(f"2026-07-30T15:{i // 60:02d}:{i % 60:02d}Z") for i in range(600)]
    r = _post({"session_id": SESS, "points": pts}, monkeypatch)
    assert r["added"] == web.TRACK_MAX_POINTS


def test_sessions_summary(monkeypatch):
    _clean()
    _post({"session_id": "s-a", "points": [_pt("2026-07-30T16:00:00Z"),
                                           _pt("2026-07-30T16:05:00Z")]}, monkeypatch)
    _post({"session_id": "s-b", "points": [_pt("2026-07-30T17:00:00Z")]}, monkeypatch)
    by = {s["session_id"]: s for s in db.track_sessions(EMP)}
    assert by["s-a"]["n"] == 2
    assert by["s-a"]["started"] == "2026-07-30T16:00:00Z"
    assert by["s-a"]["finished"] == "2026-07-30T16:05:00Z"
    assert by["s-b"]["n"] == 1


def test_route_registered():
    paths = {r.resource.canonical for r in web.build_app(None).router.routes()
             if getattr(r, "resource", None)}
    assert "/api/track" in paths
