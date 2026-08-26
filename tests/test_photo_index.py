"""Индекс фото (photo_geo как индекс каждого снимка), сигналы photoaudit,
поиск по КД для руководителя и бэкфилл.

Главные ловушки, которые здесь пинятся: часовые пояса (+5 ч UZ/TJ не должны
давать плашку на каждом честном снимке), километры против метров в гео-
правилах, строка индекса без гео не отбирает точку съёмки у отчёта в беседу,
откреплённый договор не прячет альбом, отказы в доступе оставляют след.
"""
import asyncio
import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import make_mocked_request

import db
import photoaudit
import photostore
import web
from config import ADMIN_ID

TODAY = date.today().isoformat()
NOW = datetime.now().replace(microsecond=0)
EMP = "Фотов Сотрудник"
MGR = 557001


def _seed():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'PH-%'")
        conn.execute("DELETE FROM photo_geo WHERE file LIKE 'PH-%'")
        conn.execute("DELETE FROM managers WHERE telegram_id=?", (MGR,))
        conn.commit()
    row = {"contract_id": "PH-1", "client": "Фотогенов Ф.", "responsible": EMP,
           "address": "адрес", "assign_date": "01.08.2026",
           "geo_lat": 41.31, "geo_lon": 69.28, "geo_precision": "house"}
    with db.connect() as conn:
        keys = ", ".join(f'"{k}"' for k in row)
        conn.execute(f"INSERT OR REPLACE INTO clients ({keys}) VALUES "
                     f"({', '.join('?' * len(row))})", tuple(row.values()))
        conn.commit()
    db.set_manager(MGR, "Фоторук", [], [EMP])


# --- запись индекса -----------------------------------------------------------
def test_record_photo_writes_row_without_geo():
    _seed()
    db.record_photo("PH-1", "PH-1/a.jpg", 5, sha256="s1", size_bytes=10,
                    source="gallery", meta_state="ok")
    r = db.photo_geo_for_files(["PH-1/a.jpg"])["PH-1/a.jpg"]
    assert r["lat"] is None and r["sha256"] == "s1" and r["source"] == "gallery"


def test_upload_amends_only_backfill_rows():
    _seed()
    db.record_photo("PH-1", "PH-1/b.jpg", 0, sha256="old",
                    meta_state="backfill", meta_origin="backfill")
    # настоящая загрузка перекрывает синтетическую строку
    db.record_photo("PH-1", "PH-1/b.jpg", 7, sha256="new", meta_state="ok")
    r = db.photo_geo_for_files(["PH-1/b.jpg"])["PH-1/b.jpg"]
    assert r["sha256"] == "new" and r["meta_origin"] == "upload"
    # а upload-строку повторная доставка НЕ переписывает (подделанный EXIF)
    db.record_photo("PH-1", "PH-1/b.jpg", 8, sha256="fake", meta_state="ok")
    r2 = db.photo_geo_for_files(["PH-1/b.jpg"])["PH-1/b.jpg"]
    assert r2["sha256"] == "new" and r2["telegram_id"] == 7


def test_add_photo_geo_backcompat_fallback():
    _seed()
    db.add_photo_geo("PH-1", "PH-1/c.jpg", 5, 41.0, 69.0, 30)
    r = db.photo_geo_for_files(["PH-1/c.jpg"])["PH-1/c.jpg"]
    assert r["captured_at"]                      # фолбэк or now сохранён


# --- ключи и пути -------------------------------------------------------------
def test_photo_path_rejects_traversal(tmp_path):
    (tmp_path / "PH-1").mkdir()
    f = tmp_path / "PH-1" / "x.jpg"
    f.write_bytes(b"x")
    assert photostore.photo_path("PH-1/x.jpg", root=tmp_path) == f.resolve()
    for bad in ("../x.jpg", "PH-1/../x.jpg", "PH-1/..", "a/" * 5 + "x.jpg",
                "PH-1/~t.jpg", "PH-1/x.part", "", "x.jpg"):
        assert photostore.photo_path(bad, root=tmp_path) is None


def test_photo_key_matches_index_key(tmp_path):
    (tmp_path / "A/B").mkdir(parents=True)
    f = tmp_path / "A" / "B" / "x.jpg"
    f.write_bytes(b"x")
    assert photostore.photo_key(f, root=tmp_path) == "A/B/x.jpg"


# --- photoaudit: даты и сигналы -----------------------------------------------
def _row(**kw):
    base = {"file": "PH-1/f.jpg", "contract_id": "PH-1", "telegram_id": 5,
            "lat": None, "lon": None, "acc_m": None, "captured_at": None,
            "created_at": NOW.isoformat(), "sha256": None, "size_bytes": 1,
            "source": None, "shot_at_client": None, "exif_dt": None,
            "exif_tz": None, "exif_dt_norm": None, "exif_lat": None,
            "exif_lon": None, "exif_gps_at": None, "exif_make": None,
            "exif_model": None, "exif_software": None, "meta_state": None,
            "shell_version": None, "meta_origin": "upload", "shrunk": None,
            "addr_lat": None, "addr_lon": None, "addr_tol_m": None,
            "assign_date_at": None}
    base.update(kw)
    return base


def _build(rows, visits=(), files=None):
    ctx = {"cid": "PH-1", "person": "Ф.", "contracts": [], "dirs": ["PH-1"]}
    return photoaudit.build(
        ctx, rows, files or {}, visits=list(visits),
        thresholds={"stale_h": 24, "skew_s": 300, "gap_m": 500},
        date_from="", date_to="", dup_rows=rows)


def test_uzbek_wall_clock_gives_no_clock_ahead():
    """+5 ч разницы стенных часов и UTC-сервера — НЕ «часы вперёд»."""
    r = _row(exif_dt=(NOW + timedelta(hours=5)).isoformat(), meta_state="ok")
    out = _build([r])
    codes = [s["code"] for s in out["items"][0]["signals"]]
    assert "clock_ahead" not in codes
    it = out["items"][0]
    assert it["shot_at_kind"] == "upload"        # сырой exif_dt не в арифметике
    assert it["shot_at_device"] == r["exif_dt"]  # но показан отдельно


def test_normalized_exif_ahead_is_flagged():
    r = _row(exif_dt_norm=(NOW + timedelta(hours=2)).isoformat())
    out = _build([r])
    assert any(s["code"] == "clock_ahead"
               for s in out["items"][0]["signals"])


def test_exif_gps_far_uses_meters_and_tolerance():
    # ~1.1 км от адреса; допуск 300 + запас 1000 → не сигналим на 1.1 км...
    r = _row(exif_lat=41.32, exif_lon=69.28, addr_lat=41.31, addr_lon=69.28,
             addr_tol_m=300)
    out = _build([r])
    assert not any(s["code"] == "exif_gps_far"
                   for s in out["items"][0]["signals"])
    # ...а на 11 км — сигналим (метры, не километры!)
    r2 = _row(file="PH-1/g.jpg", exif_lat=41.41, exif_lon=69.28,
              addr_lat=41.31, addr_lon=69.28, addr_tol_m=300)
    out2 = _build([r2])
    assert any(s["code"] == "exif_gps_far" and s["level"] == "info"
               for s in out2["items"][0]["signals"])


def test_no_tolerance_no_geo_signal():
    r = _row(exif_lat=45.0, exif_lon=60.0, addr_lat=41.31, addr_lon=69.28,
             addr_tol_m=None)
    out = _build([r])
    assert not any(s["code"] == "exif_gps_far"
                   for s in out["items"][0]["signals"])


def test_captured_at_equal_created_at_is_upload_kind():
    ts = NOW.isoformat()
    r = _row(captured_at=ts, created_at=ts)
    out = _build([r])
    assert out["items"][0]["shot_at_kind"] == "upload"   # фолбэк or now — не съёмка


def test_null_meta_state_gets_its_own_info_signal():
    out = _build([_row()])
    sigs = out["items"][0]["signals"]
    ms = next(s for s in sigs if s["code"] == "meta_unavailable")
    assert ms["level"] == "info" and "стар" in ms["text"]


def test_file_without_row_is_no_row(tmp_path):
    d = tmp_path / "PH-1"
    d.mkdir()
    f = d / "old.jpg"
    f.write_bytes(b"x")
    out = _build([], files={"PH-1/old.jpg": f})
    it = out["items"][0]
    assert it["meta_state"] == "no_row" and it["uploaded_at"]


def test_same_sha_other_contract_review():
    r1 = _row(sha256="dup")
    r2 = _row(file="PH-9/z.jpg", contract_id="PH-9", sha256="dup")
    out = _build([r1, r2])
    it = next(i for i in out["items"] if i["file"] == "PH-1/f.jpg")
    s = next(x for x in it["signals"] if x["code"] == "same_file_other_contract")
    assert s["level"] == "review" and s["other_contract"] == "PH-9"


def test_shot_before_assignment_uses_snapshot_date():
    r = _row(exif_dt_norm=(NOW - timedelta(days=40)).isoformat(),
             assign_date_at="2026-08-01", meta_state="ok")
    out = _build([r])
    assert any(s["code"] == "shot_before_assignment"
               for s in out["items"][0]["signals"])
    # дата закрепления не распознана → правило не применяется
    r2 = _row(file="PH-1/h.jpg",
              exif_dt_norm=(NOW - timedelta(days=40)).isoformat(),
              assign_date_at=None)
    out2 = _build([r2])
    assert not any(s["code"] == "shot_before_assignment"
                   for s in out2["items"][0]["signals"])


# --- API поиска ---------------------------------------------------------------
def _call(handler, uid, qs, monkeypatch):
    monkeypatch.setattr(web, "_uid", lambda _r: uid)
    req = make_mocked_request("GET", f"/api/manager/photos{qs}")
    resp = asyncio.run(handler(req))
    return json.loads(resp.body.decode()), resp


def test_search_finds_unassigned_contract(monkeypatch):
    _seed()
    db.record_photo("PH-1", "PH-1/a.jpg", 5, sha256="s1")
    with db.connect() as conn:      # договор открепили — альбом не исчезает
        conn.execute("UPDATE clients SET responsible='', unassigned_from=?, "
                     "unassigned_reason='manual' WHERE contract_id='PH-1'", (EMP,))
        conn.commit()
    j, _ = _call(web._manager_photos, MGR, "?contract_id=PH-1", monkeypatch)
    assert j["ok"] and any(i["file"] == "PH-1/a.jpg" for i in j["items"])


def test_search_denied_leaves_audit_row(monkeypatch):
    _seed()
    db.set_manager(557002, "Чужой рук", [], ["Другой Ч."])
    db.record_photo("PH-1", "PH-1/a.jpg", 5, sha256="s1")
    with pytest.raises(web.web.HTTPNotFound):
        _call(web._manager_photos, 557002, "?contract_id=PH-1", monkeypatch)
    tail = [dict(r) for r in db.audit_tail(10)]
    assert any(r["action"] == "photo_search_denied" for r in tail)
    db.delete_manager(557002)


def test_owner_searches_even_on_empty_scope(monkeypatch):
    _seed()
    if not ADMIN_ID:
        pytest.skip("ADMIN_ID не задан")
    db.record_photo("PH-1", "PH-1/a.jpg", 5, sha256="s1")
    j, _ = _call(web._manager_photos, ADMIN_ID, "?contract_id=PH-1", monkeypatch)
    assert j["ok"]


def test_dup_of_foreign_contract_is_masked(monkeypatch):
    _seed()
    with db.connect() as conn:
        conn.execute("INSERT OR REPLACE INTO clients (contract_id, client, "
                     "responsible, address) VALUES ('PH-9','Чужой Ч.',"
                     "'Другой Сотрудник','другой адрес')")
        conn.commit()
    db.record_photo("PH-1", "PH-1/a.jpg", 5, sha256="dupX")
    db.record_photo("PH-9", "PH-9/z.jpg", 6, sha256="dupX")
    j, _ = _call(web._manager_photos, MGR, "?contract_id=PH-1", monkeypatch)
    it = next(i for i in j["items"] if i["file"] == "PH-1/a.jpg")
    s = next(x for x in it["signals"] if x["code"] == "same_file_other_contract")
    assert s["other_contract"] is None and s.get("foreign") is True


# --- бэкфилл ------------------------------------------------------------------
def test_backfill_skips_fresh_and_is_idempotent(tmp_path):
    _seed()
    import photo_backfill
    d = tmp_path / "PH-1"
    d.mkdir()
    old = d / "old.jpg"
    old.write_bytes(b"oldbytes")
    import os
    past = (NOW - timedelta(hours=2)).timestamp()
    os.utime(old, (past, past))
    fresh = d / "fresh.jpg"
    fresh.write_bytes(b"freshbytes")           # mtime = сейчас → пропуск
    s1 = photo_backfill.backfill(tmp_path, apply=True)
    assert s1["inserted"] == 1 and s1["skipped_fresh"] == 1
    r = db.photo_geo_for_files(["PH-1/old.jpg"])["PH-1/old.jpg"]
    assert r["meta_state"] == "backfill" and r["sha256"]
    s2 = photo_backfill.backfill(tmp_path, apply=True)
    assert s2["inserted"] == 0 and s2["already"] == 1
