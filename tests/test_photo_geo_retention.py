"""Срок хранения координат фото и ручка стирания.

Точка съёмки — доказательство выезда и одновременно данные о САМОМ сотруднике:
у галерейного снимка EXIF-точка чаще всего его домашний адрес. До 27.08.2026
стереть её можно было только полным сбросом теста — вместе со всей работой.
"""
import asyncio
import json
from datetime import date, timedelta

import pytest
from aiohttp.test_utils import make_mocked_request

import db
import photoaudit
import web

TODAY = date.today().isoformat()
EMP = "Хранилов Срок"
CID = "GEO-1"


@pytest.fixture()
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "hardbot.db")
    db.init_db()
    with db.connect() as conn:
        conn.execute("INSERT OR REPLACE INTO clients (contract_id, client,"
                     " responsible) VALUES (?,?,?)", (CID, "Клиент Г.", EMP))
        conn.execute("INSERT OR REPLACE INTO agent_bind (telegram_id, employee)"
                     " VALUES (?,?)", (777, EMP))
        conn.commit()
    return tmp_path


def _photo(name, *, days_ago=0, tid=777, cid=CID, exif=True):
    when = (date.today() - timedelta(days=days_ago)).isoformat()
    with db.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO photo_geo (contract_id, file, telegram_id,"
            " lat, lon, acc_m, exif_lat, exif_lon, exif_gps_at, created_at,"
            " source) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (cid, f"{cid}/{name}", tid, 41.3, 69.2, 12,
             41.31 if exif else None, 69.21 if exif else None,
             f"{when}T10:00:00" if exif else None, f"{when}T10:05:00",
             "gallery"))
        conn.commit()


def _row(name, cid=CID):
    with db.connect() as conn:
        return conn.execute("SELECT * FROM photo_geo WHERE file = ?",
                            (f"{cid}/{name}",)).fetchone()


def test_no_automatic_wipe_until_asked(base):
    """По умолчанию координаты не стираются: доказательство выезда не должно
    исчезать само. Срок — осознанное решение оператора."""
    assert db.PHOTO_GEO_RETENTION_DEFAULT == 0
    assert db.photo_geo_retention_days() == 0
    _photo("ancient.jpg", days_ago=900)
    assert db.photo_geo_sweep() == 0
    assert _row("ancient.jpg")["lat"] == 41.3
    db.set_setting("photo_geo_retention_days", "30")
    assert db.photo_geo_retention_days() == 30
    # мусор в настройке трактуем в сторону БЕЗДЕЙСТВИЯ, а не стирания
    db.set_setting("photo_geo_retention_days", "мусор")
    assert db.photo_geo_retention_days() == 0


def test_sweep_clears_old_and_keeps_fresh(base):
    db.set_setting("photo_geo_retention_days", "90")
    _photo("old.jpg", days_ago=200)
    _photo("fresh.jpg", days_ago=3)
    assert db.photo_geo_sweep() == 1
    old, fresh = _row("old.jpg"), _row("fresh.jpg")
    assert old["lat"] is None and old["exif_lat"] is None
    assert old["exif_gps_at"] is None and old["geo_forgotten_at"]
    assert fresh["lat"] == 41.3 and fresh["exif_lat"] == 41.31


def test_sweep_keeps_everything_that_is_not_a_coordinate(base):
    """Стираем МЕСТО, а не факт съёмки: хэш, размер и время нужны разбору
    «то же фото по другому договору» и после уборки."""
    with db.connect() as conn:
        conn.execute("INSERT INTO photo_geo (contract_id, file, telegram_id,"
                     " lat, lon, exif_lat, exif_lon, sha256, size_bytes,"
                     " created_at, shot_at_client, source, addr_lat, addr_lon)"
                     " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (CID, f"{CID}/keep.jpg", 777, 41.3, 69.2, 41.31, 69.21,
                      "a" * 64, 12345, "2026-01-01T10:00:00",
                      "2026-01-01T09:59:00", "camera", 41.0, 69.0))
        conn.commit()
    db.set_setting("photo_geo_retention_days", "90")
    db.photo_geo_sweep()
    r = _row("keep.jpg")
    assert r["lat"] is None and r["exif_lat"] is None
    assert r["sha256"] == "a" * 64 and r["size_bytes"] == 12345
    assert r["shot_at_client"] == "2026-01-01T09:59:00"
    assert r["source"] == "camera"
    # адресная точка должника — данные реестра, а не о сотруднике
    assert r["addr_lat"] == 41.0 and r["addr_lon"] == 69.0


def test_zero_days_turns_the_sweep_off(base):
    _photo("ancient.jpg", days_ago=900)
    db.set_setting("photo_geo_retention_days", "0")
    assert db.photo_geo_sweep() == 0
    assert _row("ancient.jpg")["lat"] == 41.3


def test_forget_by_employee_touches_only_his_photos(base):
    _photo("mine.jpg", tid=777)
    _photo("foreign.jpg", tid=999)
    assert db.photo_geo_forget(employee=EMP) == 1
    assert _row("mine.jpg")["lat"] is None
    assert _row("foreign.jpg")["lat"] == 41.3


def test_forget_by_unknown_employee_clears_nothing(base):
    """Опечатка в ФИО не должна означать «все»: пустой список привязок — это
    ноль строк, а не отсутствие фильтра."""
    _photo("mine.jpg")
    assert db.photo_geo_forget(employee="Нетаков Некто") == 0
    assert _row("mine.jpg")["lat"] == 41.3


def test_forget_by_contract(base):
    _photo("a.jpg", cid=CID)
    _photo("b.jpg", cid="GEO-2")
    assert db.photo_geo_forget(contract_id=CID) == 1
    assert _row("a.jpg")["lat"] is None
    assert _row("b.jpg", "GEO-2")["lat"] == 41.3


def test_bad_date_is_an_error_not_a_full_wipe(base):
    _photo("a.jpg")
    with pytest.raises(ValueError):
        db.photo_geo_forget(before="позавчера")
    assert _row("a.jpg")["lat"] == 41.3


def test_stats_show_what_is_stored(base):
    db.set_setting("photo_geo_retention_days", "90")
    _photo("a.jpg", days_ago=200)
    _photo("b.jpg", days_ago=1)
    st = db.photo_geo_stats()
    assert st["photos"] == 2 and st["with_geo"] == 2 and st["forgotten"] == 0
    db.photo_geo_sweep()
    st2 = db.photo_geo_stats()
    assert st2["with_geo"] == 1 and st2["forgotten"] == 1


# --- ручка оператора ----------------------------------------------------------
def _api(monkeypatch, method, body=None):
    monkeypatch.setattr(web, "_admin", lambda _r: None)
    req = make_mocked_request(method, "/admin/photo_geo")

    async def _json():
        return body or {}
    req.json = _json
    resp = asyncio.run(web._admin_photo_geo(req))
    return json.loads(resp.body.decode())


def test_handle_clears_and_leaves_a_trace(base, monkeypatch):
    _photo("a.jpg")
    out = _api(monkeypatch, "POST", {"forget": {"employee": EMP}})
    assert out["cleared"] == 1 and out["with_geo"] == 0
    actions = [r["action"] for r in db.audit_tail(20)]
    assert "photo_geo_forget" in actions


def test_handle_sets_the_term(base, monkeypatch):
    out = _api(monkeypatch, "POST", {"retention_days": 30})
    assert out["retention_days"] == 30
    assert db.photo_geo_retention_days() == 30


def test_handle_rejects_nonsense_term(base, monkeypatch):
    from aiohttp import web as aioweb
    with pytest.raises(aioweb.HTTPBadRequest):
        _api(monkeypatch, "POST", {"retention_days": -5})
    with pytest.raises(aioweb.HTTPBadRequest):
        _api(monkeypatch, "POST", {"retention_days": "позже"})


def test_audit_shows_forgotten_point_as_a_rule_not_a_violation(base):
    """Руководитель обязан видеть разницу: «гео не дали» и «координаты стёрты
    по сроку» — разные факты, второй не вина выездника."""
    _photo("a.jpg", days_ago=400)
    db.set_setting("photo_geo_retention_days", "90")
    db.photo_geo_sweep()
    rows = db.photo_index_for_dirs([CID])
    out = photoaudit.build(
        {"cid": CID, "dirs": [CID]}, rows, {f"{CID}/a.jpg": None},
        visits=[], thresholds=photoaudit.thresholds(db.get_setting),
        date_from=None, date_to=None, dup_rows=[])
    codes = {s["code"] for item in out["items"] for s in item["signals"]}
    assert "geo_forgotten" in codes
    assert "camera_without_point" not in codes
