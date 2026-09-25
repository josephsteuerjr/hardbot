"""Снимок отдаётся руководителю по праву на ВЕСЬ каталог договора (ревью 25.09, F-3).

Ключ фото — «КД/имя», а КД бывает со слэшем: у договора «A/B» каталог
вложенный, «A/B/имя». Право раньше считалось по первому сегменту ключа, то есть
по договору «A», а файл отдавался по полному пути — руководитель с доступом к
«A» получал снимки чужого договора «A/B», зная имя файла.
"""
import asyncio
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

import db
import web

EMP_OWN, EMP_OTHER = "Фотокаталогов А.", "Фоточужаков Б."
MGR_OWN, MGR_OTHER = 558001, 558002
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


@pytest.fixture()
def photos(tmp_path, monkeypatch):
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'PHS-%'")
        conn.execute("DELETE FROM managers WHERE telegram_id IN (?,?)", (MGR_OWN, MGR_OTHER))
        for cid, client, emp in (("PHS-1", "Альбомов А.", EMP_OWN),
                                 ("PHS-1/2", "Чужеальбомов Б.", EMP_OTHER)):
            conn.execute(
                "INSERT INTO clients (contract_id, client, responsible, birth_date, "
                "address) VALUES (?,?,?,?,?)",
                (cid, client, emp, "1990-01-01", f"адрес {cid}"))
        conn.commit()
    db.set_manager(MGR_OWN, "Свой рук", [], [EMP_OWN])
    db.set_manager(MGR_OTHER, "Рук договора со слэшем", [], [EMP_OTHER])
    db.reset_role_caches()
    (tmp_path / "PHS-1" / "2").mkdir(parents=True)
    (tmp_path / "PHS-1" / "own.jpg").write_bytes(JPEG + b"own")
    (tmp_path / "PHS-1" / "2" / "foreign.jpg").write_bytes(JPEG + b"foreign")
    monkeypatch.setattr(web, "PHOTO_DIR", tmp_path)
    yield tmp_path
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'PHS-%'")
        conn.commit()
    db.delete_manager(MGR_OWN)
    db.delete_manager(MGR_OTHER)
    db.reset_role_caches()


def _as(uid, monkeypatch, *paths):
    monkeypatch.setattr(web, "_uid", lambda _r: uid)

    async def scenario():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            out = []
            for path in paths:
                resp = await client.get(path)
                out.append((resp.status, await resp.read()))
            return out
        finally:
            await client.close()

    return asyncio.run(scenario())


def test_access_to_a_does_not_open_the_nested_contract(photos, monkeypatch):
    own, foreign = _as(MGR_OWN, monkeypatch,
                       "/api/manager/photo?file=PHS-1/own.jpg",
                       "/api/manager/photo?file=PHS-1/2/foreign.jpg")
    assert own[0] == 200 and own[1].endswith(b"own"), "свой снимок перестал открываться"
    assert foreign[0] == 404, "снимок договора «A/B» отдан по праву на «A»"
    denied = [r for r in db.audit_tail(10) if r["action"] == "photo_view_denied"]
    assert denied and denied[0]["target"] == "PHS-1/2/foreign.jpg"


def test_the_nested_contract_opens_for_its_own_manager(photos, monkeypatch):
    foreign, own = _as(MGR_OTHER, monkeypatch,
                       "/api/manager/photo?file=PHS-1/2/foreign.jpg",
                       "/api/manager/photo?file=PHS-1/own.jpg")
    assert foreign[0] == 200 and foreign[1].endswith(b"foreign")
    assert own[0] == 404, "право на «A/B» открыло родительский «A»"


def test_every_key_from_the_list_opens(photos, monkeypatch):
    """Список отдаёт ключи того же вида «каталог договора/имя» — каждый из них
    открывается, и чужого вложенного каталога в списке нет."""
    listing, = _as(MGR_OWN, monkeypatch, "/api/manager/photos?contract_id=PHS-1")
    assert listing[0] == 200
    files = [i["file"] for i in json.loads(listing[1])["items"]]
    assert files == ["PHS-1/own.jpg"]
    got = _as(MGR_OWN, monkeypatch, *[f"/api/manager/photo?file={f}" for f in files])
    assert [s for s, _ in got] == [200]
