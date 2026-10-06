"""Чек об оплате: приём выездником, идемпотентность очереди, права чтения.

Чек — отдельный тип доказательства (не контрольное фото визита): свой каталог
на диске, своя таблица, ошибки ПОЛЯ не роняют запись (оффлайн-очередь хоронит
её на любом не-2xx), а просмотр руководителем/безопасником идёт тем же правом
«каталог договора целиком», что и у снимков.
"""
import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

import db
import web

EMP_OWN, EMP_OTHER = "Чеков Иннокентий", "Чужаков Петр"
MGR_OWN, MGR_OTHER = 559001, 559002
UID = 559100
REP = "RCP-1"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


@pytest.fixture()
def world(tmp_path, monkeypatch):
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM payment_evidence WHERE contract_id LIKE 'RCP-%'")
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'RCP-%'")
        conn.execute("DELETE FROM managers WHERE telegram_id IN (?,?)",
                     (MGR_OWN, MGR_OTHER))
        conn.execute(
            "INSERT INTO clients (contract_id, client, responsible, birth_date, "
            "address) VALUES (?,?,?,?,?)",
            (REP, "Должников Д.", EMP_OWN, "1990-01-01", "адрес"))
        conn.commit()
    db.set_manager(MGR_OWN, "Свой рук", [], [EMP_OWN])
    db.set_manager(MGR_OTHER, "Чужой рук", [], [EMP_OTHER])
    db.reset_role_caches()
    monkeypatch.setattr(web, "PHOTO_DIR", tmp_path / "photos")
    yield tmp_path
    with db.connect() as conn:
        conn.execute("DELETE FROM payment_evidence WHERE contract_id LIKE 'RCP-%'")
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'RCP-%'")
        conn.commit()
    db.delete_manager(MGR_OWN)
    db.delete_manager(MGR_OTHER)
    db.reset_role_caches()


def _serve(coro_fn):
    """Прогнать сценарий в своём лупе: TestClient требует живой event loop."""

    async def scenario():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            return await coro_fn(client)
        finally:
            await client.close()

    return asyncio.run(scenario())


def _rows():
    with db.connect() as conn:
        return conn.execute(
            "SELECT * FROM payment_evidence WHERE contract_id=?", (REP,)).fetchall()


def test_agent_uploads_receipt(world, monkeypatch):
    monkeypatch.setattr(web, "_auth", lambda _r: (UID, EMP_OWN))

    async def call(client):
        r = await client.post(
            "/api/receipt",
            params={"rep": REP, "op": "rc1", "amount": "1500,50",
                    "paid_at": "2026-09-28", "payer": "client"},
            data=JPEG,
            headers={"Content-Type": "application/octet-stream"})
        assert r.status == 200, await r.text()
        body = await r.json()
        assert body["ok"] is True and "rejected" not in body

    _serve(call)
    rows = _rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["amount"] == 1500.5
    assert row["paid_at"] == "2026-09-28"
    assert row["payer_is_client"] == 1
    assert row["employee"] == EMP_OWN
    assert row["sha256"] and row["size_bytes"] == len(JPEG)
    f = web.receipt_dir() / REP / (row["file"].rsplit("/", 1)[1])
    assert f.is_file() and f.read_bytes() == JPEG
    # чек не попал ни в счётчик контрольных фото, ни в их каталог
    assert not (web.PHOTO_DIR / REP).exists()


def test_soft_owner_can_attach_receipt_but_other_soft_cannot(world, monkeypatch):
    with db.connect() as conn:
        conn.execute("UPDATE clients SET responsible_soft=? WHERE contract_id=?", (EMP_OTHER, REP))
    monkeypatch.setattr(web, "_auth", lambda _r: (UID, EMP_OTHER))

    async def call(client):
        r = await client.post("/api/receipt", params={"rep": REP, "op": "soft-rc"},
                              data=JPEG, headers={"Content-Type": "application/octet-stream"})
        assert r.status == 200, await r.text()
        monkeypatch.setattr(web, "_auth", lambda _r: (UID, "Вне портфеля"))
        r = await client.post("/api/receipt", params={"rep": REP, "op": "foreign-rc"},
                              data=JPEG, headers={"Content-Type": "application/octet-stream"})
        assert r.status == 404

    _serve(call)
    assert len(_rows()) == 1 and _rows()[0]["employee"] == EMP_OTHER


def test_security_cannot_write_employee_facts(world, monkeypatch):
    db.set_manager(MGR_OWN, "СБ", [], [EMP_OWN], role="security")
    monkeypatch.setattr(web, "_auth", lambda _r: (MGR_OWN, EMP_OWN))

    async def call(client):
        for endpoint in ("/api/receipt", "/api/photo", "/api/save", "/api/marker"):
            r = await client.post(endpoint, params={"rep": REP, "op": "security-rc"},
                                  data=JPEG, headers={"Content-Type": "application/octet-stream"})
            assert r.status == 403, (endpoint, await r.text())
            assert (await r.json())["error"] == "read_only"

    _serve(call)
    assert not _rows()
    assert not web.receipt_dir().exists()


def test_same_op_twice_is_one_row(world, monkeypatch):
    monkeypatch.setattr(web, "_auth", lambda _r: (UID, EMP_OWN))

    async def call(client):
        for _ in range(2):
            r = await client.post(
                "/api/receipt", params={"rep": REP, "op": "rc2", "amount": "100"},
                data=JPEG,
                headers={"Content-Type": "application/octet-stream"})
            assert r.status == 200

    _serve(call)
    assert len(_rows()) == 1


def test_bad_fields_rejected_receipt_kept(world, monkeypatch):
    monkeypatch.setattr(web, "_auth", lambda _r: (UID, EMP_OWN))

    async def call(client):
        r = await client.post(
            "/api/receipt",
            params={"rep": REP, "op": "rc3", "amount": "много",
                    "paid_at": "2099-01-01", "payer": "other"},
            data=JPEG,
            headers={"Content-Type": "application/octet-stream"})
        assert r.status == 200
        body = await r.json()
        assert body["rejected"] == {"amount": "format", "paid_at": "future",
                                    "payer_note": "required"}

    _serve(call)
    row = _rows()[0]
    assert row["amount"] is None
    assert row["paid_at"] is None
    assert row["payer_is_client"] == 0
    assert row["payer_note"] is None


def test_foreign_contract_and_body_rules(world, monkeypatch):
    monkeypatch.setattr(web, "_auth", lambda _r: (UID, EMP_OTHER))

    async def foreign(client):
        # чужой договор — 404, как у фото и сохранения (проверка тела ниже по коду)
        r = await client.post("/api/receipt", params={"rep": REP, "op": "rc4"},
                              data=JPEG,
                              headers={"Content-Type": "application/octet-stream"})
        assert r.status == 404

    _serve(foreign)
    assert _rows() == []

    monkeypatch.setattr(web, "_auth", lambda _r: (UID, EMP_OWN))

    async def body_rules(client):
        # не сырые байты — 415
        r = await client.post("/api/receipt", params={"rep": REP, "op": "rc4"},
                              data=JPEG,
                              headers={"Content-Type": "multipart/form-data"})
        assert r.status == 415
        # битая картинка — 422 (клиент пометит переснятием)
        r = await client.post("/api/receipt", params={"rep": REP, "op": "rc4"},
                              data=b"not an image",
                              headers={"Content-Type": "application/octet-stream"})
        assert r.status == 422

    _serve(body_rules)
    assert _rows() == []


def test_person_card_carries_receipts(world, monkeypatch):
    db.record_receipt("rc5", REP, EMP_OWN, UID, amount=700.0,
                      paid_at="2026-09-30", payer_is_client=False,
                      payer_note="платил брат", file=f"{REP}/rc5.jpg",
                      sha256="x" * 64, size_bytes=len(JPEG), source="app")
    monkeypatch.setattr(web, "_auth", lambda _r: (UID, EMP_OWN))

    async def call(client):
        r = await client.get("/api/person", params={"rep": REP})
        assert r.status == 200
        body = await r.json()
        assert body["receipts"] and body["receipts"][0]["amount"] == 700.0
        assert body["receipts"][0]["payer_is_client"] is False
        assert body["receipts"][0]["payer_note"] == "платил брат"

    _serve(call)


def test_manager_receipt_file_scope(world, monkeypatch):
    db.record_receipt("rc6", REP, EMP_OWN, UID, amount=1.0,
                      file=f"{REP}/rc6.jpg", sha256="y" * 64,
                      size_bytes=len(JPEG), source="app")
    d = web.receipt_dir() / REP
    d.mkdir(parents=True)
    (d / "rc6.jpg").write_bytes(JPEG)

    def fetch(uid):
        monkeypatch.setattr(
            web, "_manager_gate",
            lambda _r: (uid, "Рук", [EMP_OWN if uid == MGR_OWN else EMP_OTHER]))

        async def call(client):
            r = await client.get("/api/manager/receipt_photo",
                                 params={"file": f"{REP}/rc6.jpg"})
            return r.status

        return _serve(call)

    assert fetch(MGR_OWN) == 200
    assert fetch(MGR_OTHER) == 404
    with db.connect() as conn:
        denied = conn.execute(
            "SELECT COUNT(*) FROM audit WHERE action='receipt_view_denied'"
        ).fetchone()[0]
    assert denied >= 1


def test_soft_receipt_view_follows_scoped_owner_and_historical_author(world, monkeypatch):
    with db.connect() as conn:
        conn.execute("UPDATE clients SET responsible_soft=? WHERE contract_id=?", (EMP_OTHER, REP))
    db.record_receipt("soft-view", REP, EMP_OTHER, UID, file=f"{REP}/soft-view.jpg", source="app")
    folder = web.receipt_dir() / REP
    folder.mkdir(parents=True)
    (folder / "soft-view.jpg").write_bytes(JPEG)

    async def call(client):
        monkeypatch.setattr(web, "_manager_gate", lambda _r: (MGR_OTHER, {}, [EMP_OTHER]))
        response = await client.get("/api/manager/receipt_photo", params={"file": f"{REP}/soft-view.jpg"})
        assert response.status == 200
        with db.connect() as conn:
            conn.execute("UPDATE clients SET responsible_soft='Новый сотрудник' WHERE contract_id=?", (REP,))
        response = await client.get("/api/manager/receipt_photo", params={"file": f"{REP}/soft-view.jpg"})
        assert response.status == 200
        monkeypatch.setattr(web, "_manager_gate", lambda _r: (MGR_OTHER, {}, ["Совсем другая команда"]))
        response = await client.get("/api/manager/receipt_photo", params={"file": f"{REP}/soft-view.jpg"})
        assert response.status == 404

    _serve(call)


def test_reset_and_policy_know_the_table():
    from limited_test_reset import RESET_TABLES
    assert "payment_evidence" in RESET_TABLES

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "receipts_policy_check", root / "tools" / "export_for_handover.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    assert "payment_evidence" in module.CARRY
