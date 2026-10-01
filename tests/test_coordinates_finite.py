"""Координаты из запроса — только конечные и на Земле (ревью 25.09.2026, A11).

float() принимает «inf», «nan» и 1e999, а json.loads — литералы Infinity и NaN.
Раньше такая «точка» в /api/save роняла гео-контроль 500-й уже ПОСЛЕ записи
полей, и очередь телефона повторяла визит без конца; в /api/marker она
переписывала точку должника на Infinity, и JSON экранов руководителя переставал
разбираться. Правило теперь такое: выезд и фото принимаются всегда, мусорная
точка ложится как NULL; метка — это сама точка, поэтому ей отказ 400 по-русски
(оболочка на 4xx метку не повторяет, а оставляет видимой).
"""
import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest
from aiohttp import web as aiohttp_web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

import db
import fields
import web

EMP = "Координатов Сотрудник"
UID = 7402
ADDR = (40.28, 69.62)
JPEG = b"\xff\xd8\xff\xe0" + b"synthetic-jpeg"


def _seed(cid):
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id=?", (cid,))
        conn.execute("DELETE FROM visits WHERE contract_id=?", (cid,))
        conn.execute(
            "INSERT INTO clients (contract_id, client, birth_date, responsible, "
            "geo_lat, geo_lon, geo_src, geo_precision) VALUES (?,?,?,?,?,?,?,?)",
            (cid, "Точкин Т.", "1980-02-02", EMP, ADDR[0], ADDR[1], "osm", "street"))
        conn.commit()


def _call(handler, path, body, monkeypatch):
    monkeypatch.setattr(web, "_auth", lambda _r: (UID, EMP))
    request = make_mocked_request("POST", path, app=aiohttp_web.Application())

    async def _json():
        return body
    request.json = _json

    async def run():
        resp = await handler(request)
        await web.wait_visit_notify()
        return resp
    return asyncio.run(run())


def _finish(cid, op_id, **extra):
    body = {"rep": cid, "finish": True, "op_id": op_id,
            "form_version": web.FORM_PROTOCOL_VERSION,
            "input_keys": ["call_status"],
            "values": {"call_status": fields.DROPDOWNS[web.OFFICE_RESULT_SNAPSHOT_KEY][0]},
            "photos_pending": 0, "session_id": f"sess-{op_id}"}
    body.update(extra)
    return body


def _visit(op_id):
    with db.connect() as conn:
        return conn.execute("SELECT * FROM visits WHERE op_id=?", (op_id,)).fetchone()


def _no_infinity(resp):
    text = resp.body.decode()
    assert "Infinity" not in text and "NaN" not in text, text[:200]
    json.loads(text)


# --- /api/save: выезд принимается, точка — NULL --------------------------------------
@pytest.mark.parametrize("geo_json", [
    '{"lat": Infinity, "lon": 69.62, "acc": 10}',
    '{"lat": NaN, "lon": NaN}',
    '{"lat": 1e999, "lon": 69.62}',
    '{"lat": 40.28, "lon": -Infinity}',
    '{"lat": 91.0, "lon": 69.62}',
    '{"lat": 40.28, "lon": 181}',
    '{"lat": "inf", "lon": "69.62"}',
])
def test_visit_with_a_broken_point_is_saved_with_null(geo_json, monkeypatch):
    cid = "CF-SAVE"
    _seed(cid)
    op_id = f"cf-{uuid.uuid4().hex[:8]}"
    # json.loads — ровно то, чем aiohttp разбирает тело запроса.
    body = _finish(cid, op_id, geo=json.loads(geo_json))
    resp = _call(web._save, "/api/save", body, monkeypatch)
    assert resp.status == 200
    _no_infinity(resp)
    row = _visit(op_id)
    assert row is not None, "выезд потерян"
    assert row["visit_lat"] is None and row["visit_lon"] is None
    assert row["dist_m"] is None


def test_visit_with_broken_numbers_everywhere_is_still_saved(monkeypatch):
    """Точность, следы, точка открытия и время карточки — всё неконечное.
    Раньше каждое из них по отдельности давало 500 после записи полей."""
    cid = "CF-ALL"
    _seed(cid)
    db.set_setting("photo_grace_s", "0")
    now = web._now_ms()
    raw = json.dumps({
        "geo": {"lat": ADDR[0], "lon": ADDR[1], "acc": 1e308},
        "presence": [{"ts": now - 60_000, "lat": ADDR[0], "lon": ADDR[1], "acc": 1e308}],
        "open_geo": {"lat": ADDR[0], "lon": ADDR[1], "acc": 1e308, "ts": now - 120_000},
    }).replace("1e+308", "1e999")          # json.dumps сам бесконечность не пишет
    extra = json.loads(raw)
    assert extra["geo"]["acc"] == float("inf")
    body = _finish(cid, "cf-all-1", opened_at=float("-inf"), occurred_at=now,
                   photos_pending=float("inf"), **extra)
    try:
        resp = _call(web._save, "/api/save", body, monkeypatch)
    finally:
        db.set_setting("photo_grace_s", "")
    assert resp.status == 200
    _no_infinity(resp)
    row = _visit("cf-all-1")
    assert row is not None and row["fill_seconds"] is None
    assert row["visit_lat"] == pytest.approx(ADDR[0])   # сама точка годная — осталась


# --- /api/marker: отказ по-русски, точка должника не портится ---------------------------
@pytest.mark.parametrize("body_json", [
    '{"lat": Infinity, "lon": 69.2}',
    '{"lat": NaN, "lon": 69.2}',
    '{"lat": 1e999, "lon": 69.2}',
    '{"lat": 91, "lon": 69.2}',
    '{"lat": 41.3, "lon": -181}',
    '{"lat": "inf", "lon": "1"}',
    '{"lat": 1' + "0" * 400 + ', "lon": 1}',
    '{"lon": 69.2}',
    '[41.3, 69.2]',
])
def test_marker_refuses_a_point_off_the_earth(body_json, monkeypatch):
    cid = "CF-MARK"
    _seed(cid)
    body = json.loads(body_json)
    if isinstance(body, dict):
        body = {**body, "rep": cid, "op_id": f"cf-mk-{uuid.uuid4().hex[:8]}"}
    with pytest.raises(aiohttp_web.HTTPBadRequest) as err:
        _call(web._marker, "/api/marker", body, monkeypatch)
    assert "координаты метки" in err.value.text
    row = db.get_client(cid)
    assert (row["geo_lat"], row["geo_lon"]) == ADDR, "мусор переписал точку должника"


def test_valid_marker_still_moves_the_debtor_point(monkeypatch):
    cid = "CF-MARK-OK"
    _seed(cid)
    resp = _call(web._marker, "/api/marker",
                 {"lat": 40.3, "lon": 69.7, "rep": cid, "op_id": "cf-mk-ok"}, monkeypatch)
    assert resp.status == 200
    _no_infinity(resp)
    row = db.get_client(cid)
    assert (row["geo_lat"], row["geo_lon"]) == (40.3, 69.7)


# --- /api/photo: снимок принимается всегда ---------------------------------------------
def test_photo_geo_parser_never_raises():
    q = web._photo_geo_query
    assert q({"lat": "nan", "lon": "69.2"}) is None
    assert q({"lat": "Infinity", "lon": "69.2", "acc": "5"}) is None
    assert q({"lat": "1e999", "lon": "69.2"}) is None
    assert q({"lat": "95", "lon": "69.2"}) is None
    assert q({"lon": "69.2"}) is None
    # «Infinity» в точности: int(inf) давал OverflowError → 500 → вечный повтор.
    assert q({"lat": "41.3", "lon": "69.2", "acc": "Infinity"})[:3] == (41.3, 69.2, 0)
    assert q({"lat": "41.3", "lon": "69.2", "acc": "NaN"})[:3] == (41.3, 69.2, 0)
    assert q({"lat": "41.3", "lon": "69.2", "acc": "12", "gat": "Infinity"}) == \
        (41.3, 69.2, 12, None)


def test_photo_with_a_broken_point_is_accepted_without_it(monkeypatch, tmp_path):
    suffix = uuid.uuid4().hex[:10]
    uid = 8_200_000 + int(suffix[:5], 16)
    rep = f"CF-PHOTO-{suffix}"
    db.init_db()
    with db.connect() as conn:
        conn.execute("INSERT INTO clients (contract_id, client, responsible) VALUES (?,?,?)",
                     (rep, "Synthetic", EMP + suffix))
        conn.commit()
    db.bind_agent(uid, EMP + suffix)
    monkeypatch.setattr(web, "DEV", True)
    monkeypatch.setattr(web, "PHOTO_DIR", tmp_path / "photos")

    cases = {"op-acc-inf": "41.3,69.2,Infinity,0",
             "op-lat-inf": "Infinity,69.2,5,0",
             "op-all-nan": "NaN,NaN,NaN,NaN"}

    async def scenario():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            for op, header in cases.items():
                resp = await client.post(
                    f"/api/photo?rep={rep}&op={op}", data=JPEG,
                    headers={"X-Dev-Uid": str(uid), "X-Photo-Raw": "1",
                             "X-Photo-Ext": ".jpg",
                             "Content-Type": "application/octet-stream",
                             "X-Photo-Geo": header})
                assert resp.status == 200, (op, resp.status)
        finally:
            await client.close()

    asyncio.run(scenario())
    with db.connect() as conn:
        rows = {r["file"].split("/")[-1].split(".")[0]: r for r in conn.execute(
            "SELECT file, lat, lon, acc_m FROM photo_geo WHERE contract_id=?", (rep,))}
    assert set(rows) == set(cases), "снимок потерян"
    assert (rows["op-acc-inf"]["lat"], rows["op-acc-inf"]["acc_m"]) == (41.3, 0)
    assert rows["op-lat-inf"]["lat"] is None and rows["op-all-nan"]["lat"] is None


# --- маршруты: мусор — это «нет точки», а не Infinity в ответе ----------------------------
def test_route_to_refuses_points_off_the_earth(monkeypatch):
    monkeypatch.setattr(web, "_auth", lambda _r: (UID, EMP))
    monkeypatch.setattr(web.routing_client, "best_route", lambda *_a, **_k: None)
    for query in ("lat=inf&lon=69.6&dlat=40.3&dlon=69.7",
                  "lat=40.2&lon=69.6&dlat=nan&dlon=69.7",
                  "lat=40.2&lon=69.6&dlat=40.3&dlon=1e999"):
        with pytest.raises(aiohttp_web.HTTPBadRequest):
            asyncio.run(web._route_to(make_mocked_request("GET", f"/api/route_to?{query}")))


def test_route_day_treats_a_broken_start_as_no_start(monkeypatch):
    seen = {}

    def fake_plan(cand, start, backends, vroom):
        seen["start"] = start
        return {"order": [], "legs_km": [], "km": 0, "matrix_src": "none",
                "solver": "none"}

    monkeypatch.setattr(web, "_uid", lambda _r: UID)
    monkeypatch.setattr(web, "_employee", lambda _uid, request=None: EMP)
    monkeypatch.setattr(web.routing_client, "plan_day", fake_plan)
    db.init_db()
    resp = asyncio.run(web._route_day(SimpleNamespace(query={"lat": "inf", "lon": "69.6"})))
    assert seen["start"] is None
    _no_infinity(resp)
