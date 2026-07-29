"""Загрузка фото: raw-body для iOS PWA и безопасная совместимость с v12 multipart.

Реальный инцидент: File, переживший iOS IndexedDB/restart, создавал заголовок
multipart с boundary, но тело без этой границы. Старая v12 удаляет queue-item на
4xx, поэтому повреждённый legacy multipart обязан получить 503 до обновления
оболочки. Новый raw-клиент получает точные 413/422 и не держит следующие операции.
"""

import asyncio
import uuid

import pytest
from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

import db
import web


JPEG = b"\xff\xd8\xff\xe0" + b"synthetic-jpeg"
HEIC = b"\x00\x00\x00\x18ftypheic" + b"synthetic-heic"
AVIF_COMPAT = (
    b"\x00\x00\x00\x1cftypmif1\x00\x00\x00\x00avifmif1"
)


def _identity(monkeypatch, tmp_path):
    suffix = uuid.uuid4().hex[:10]
    uid = 8_000_000 + int(suffix[:5], 16)
    employee = f"Photo Test {suffix}"
    rep = f"PHOTO-{suffix}"
    db.init_db()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO clients (contract_id, client, responsible) VALUES (?,?,?)",
            (rep, "Synthetic", employee),
        )
        conn.commit()
    db.bind_agent(uid, employee)
    monkeypatch.setattr(web, "DEV", True)
    monkeypatch.setattr(web, "PHOTO_DIR", tmp_path / "photos")
    return uid, rep


async def _client():
    client = TestClient(TestServer(web.build_app(None)))
    await client.start_server()
    return client


def _raw_headers(uid, ext=".jpg"):
    return {
        "X-Dev-Uid": str(uid),
        "X-Photo-Raw": "1",
        "X-Photo-Ext": ext,
        "Content-Type": "application/octet-stream",
    }


def test_raw_jpeg_is_atomic_and_same_op_is_idempotent(monkeypatch, tmp_path):
    uid, rep = _identity(monkeypatch, tmp_path)

    async def scenario():
        client = await _client()
        try:
            url = f"/api/photo?rep={rep}&op=same-op"
            first = await client.post(url, data=JPEG, headers=_raw_headers(uid))
            assert first.status == 200
            assert (await first.json()) == {"ok": True, "photos": 1}

            second = await client.post(url, data=JPEG, headers=_raw_headers(uid))
            assert second.status == 200
            assert (await second.json()) == {"ok": True, "photos": 1, "dup": True}
        finally:
            await client.close()

    asyncio.run(scenario())
    path = web.PHOTO_DIR / rep / "same-op.jpg"
    assert path.read_bytes() == JPEG
    assert db.photos_count(rep) == 1
    assert not list((web.PHOTO_DIR / rep).glob("*.part"))


def test_concurrent_same_op_publishes_and_counts_once(monkeypatch, tmp_path):
    uid, rep = _identity(monkeypatch, tmp_path)

    async def scenario():
        client = await _client()
        try:
            url = f"/api/photo?rep={rep}&op=concurrent-op"
            first, second = await asyncio.gather(
                client.post(url, data=JPEG, headers=_raw_headers(uid)),
                client.post(url, data=JPEG, headers=_raw_headers(uid)),
            )
            assert first.status == second.status == 200
            payloads = [await first.json(), await second.json()]
            assert sum(bool(payload.get("dup")) for payload in payloads) == 1
            assert {payload["photos"] for payload in payloads} == {1}
        finally:
            await client.close()

    asyncio.run(scenario())
    assert db.photos_count(rep) == 1
    assert len(list((web.PHOTO_DIR / rep).glob("concurrent-op.*"))) == 1
    assert not list((web.PHOTO_DIR / rep).glob("*.part"))


def test_raw_heic_keeps_compatible_extension(monkeypatch, tmp_path):
    uid, rep = _identity(monkeypatch, tmp_path)

    async def scenario():
        client = await _client()
        try:
            response = await client.post(
                f"/api/photo?rep={rep}&op=iphone-heic",
                data=HEIC,
                headers=_raw_headers(uid, ".HEIC"),
            )
            assert response.status == 200
        finally:
            await client.close()

    asyncio.run(scenario())
    assert (web.PHOTO_DIR / rep / "iphone-heic.HEIC").read_bytes() == HEIC
    assert db.photos_count(rep) == 1


def test_avif_compatible_brand_is_detected_when_major_brand_is_mif1(
        monkeypatch, tmp_path):
    uid, rep = _identity(monkeypatch, tmp_path)

    async def scenario():
        client = await _client()
        try:
            response = await client.post(
                f"/api/photo?rep={rep}&op=iphone-avif",
                data=AVIF_COMPAT,
                headers=_raw_headers(uid, ".avif"),
            )
            assert response.status == 200
        finally:
            await client.close()

    asyncio.run(scenario())
    assert (web.PHOTO_DIR / rep / "iphone-avif.avif").read_bytes() == AVIF_COMPAT
    assert db.photos_count(rep) == 1


def test_duplicate_retry_repairs_counter_after_fs_sqlite_gap(monkeypatch, tmp_path):
    uid, rep = _identity(monkeypatch, tmp_path)
    real_ensure = db.ensure_photos_count
    calls = 0

    def fail_once(contract_id, permanent_files):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("synthetic crash after publish")
        return real_ensure(contract_id, permanent_files)

    monkeypatch.setattr(db, "ensure_photos_count", fail_once)

    async def scenario():
        client = await _client()
        try:
            url = f"/api/photo?rep={rep}&op=fs-db-gap"
            first = await client.post(url, data=JPEG, headers=_raw_headers(uid))
            assert first.status == 500
            assert db.photos_count(rep) == 0

            retry = await client.post(url, data=JPEG, headers=_raw_headers(uid))
            assert retry.status == 200
            assert (await retry.json()) == {
                "ok": True, "photos": 1, "dup": True}
        finally:
            await client.close()

    asyncio.run(scenario())
    assert calls == 2
    assert db.photos_count(rep) == 1
    assert len(list((web.PHOTO_DIR / rep).glob("fs-db-gap.*"))) == 1


def test_raw_empty_invalid_and_too_large_never_publish(monkeypatch, tmp_path):
    uid, rep = _identity(monkeypatch, tmp_path)

    async def scenario():
        client = await _client()
        try:
            empty = await client.post(
                f"/api/photo?rep={rep}&op=empty",
                data=b"",
                headers=_raw_headers(uid),
            )
            assert empty.status == 422

            invalid = await client.post(
                f"/api/photo?rep={rep}&op=not-image",
                data=b"not an image",
                headers=_raw_headers(uid),
            )
            assert invalid.status == 422

            mismatch = await client.post(
                f"/api/photo?rep={rep}&op=wrong-ext",
                data=JPEG,
                headers=_raw_headers(uid, ".png"),
            )
            assert mismatch.status == 422

            monkeypatch.setattr(web, "PHOTO_MAX_BYTES", len(JPEG) - 1)
            large = await client.post(
                f"/api/photo?rep={rep}&op=large",
                data=JPEG,
                headers=_raw_headers(uid),
            )
            assert large.status == 413
        finally:
            await client.close()

    asyncio.run(scenario())
    assert db.photos_count(rep) == 0
    photo_dir = web.PHOTO_DIR / rep
    assert not photo_dir.exists() or not list(photo_dir.iterdir())


def test_legacy_multipart_accepts_photo_after_other_field(monkeypatch, tmp_path):
    uid, rep = _identity(monkeypatch, tmp_path)

    async def scenario():
        client = await _client()
        try:
            form = FormData()
            form.add_field("note", "legacy metadata")
            form.add_field(
                "photo", JPEG, filename="legacy.jpeg", content_type="image/jpeg")
            response = await client.post(
                f"/api/photo?rep={rep}&op=legacy-ok",
                data=form,
                headers={"X-Dev-Uid": str(uid)},
            )
            assert response.status == 200
        finally:
            await client.close()

    asyncio.run(scenario())
    assert (web.PHOTO_DIR / rep / "legacy-ok.jpeg").read_bytes() == JPEG
    assert db.photos_count(rep) == 1


def test_legacy_malformed_and_empty_return_503_for_v12_queue(monkeypatch, tmp_path):
    uid, rep = _identity(monkeypatch, tmp_path)

    async def scenario():
        client = await _client()
        try:
            malformed = await client.post(
                f"/api/photo?rep={rep}&op=malformed",
                data=b"",
                headers={
                    "X-Dev-Uid": str(uid),
                    "Content-Type":
                        "multipart/form-data; boundary=----WebKitFormBoundarySynthetic",
                },
            )
            assert malformed.status == 503

            empty_form = FormData()
            empty_form.add_field(
                "photo", b"", filename="empty.jpg", content_type="image/jpeg")
            empty = await client.post(
                f"/api/photo?rep={rep}&op=legacy-empty",
                data=empty_form,
                headers={"X-Dev-Uid": str(uid)},
            )
            assert empty.status == 503

            wrong_form = FormData()
            wrong_form.add_field(
                "image", JPEG, filename="wrong.jpg", content_type="image/jpeg")
            wrong = await client.post(
                f"/api/photo?rep={rep}&op=legacy-wrong",
                data=wrong_form,
                headers={"X-Dev-Uid": str(uid)},
            )
            assert wrong.status == 503
        finally:
            await client.close()

    asyncio.run(scenario())
    assert db.photos_count(rep) == 0
    photo_dir = web.PHOTO_DIR / rep
    assert not photo_dir.exists() or not list(photo_dir.iterdir())


def test_legacy_stream_error_during_file_body_returns_503(monkeypatch, tmp_path):
    uid, rep = _identity(monkeypatch, tmp_path)

    class BrokenField:
        filename = "broken.jpg"

        def __init__(self):
            self.reads = 0

        async def read_chunk(self, size=None):
            self.reads += 1
            if self.reads == 1:
                return JPEG[:6]
            raise ValueError("synthetic truncated multipart")

    async def broken_photo_field(request):
        return BrokenField()

    monkeypatch.setattr(web, "_legacy_photo_field", broken_photo_field)

    async def scenario():
        client = await _client()
        try:
            response = await client.post(
                f"/api/photo?rep={rep}&op=legacy-truncated",
                data=b"synthetic multipart body",
                headers={
                    "X-Dev-Uid": str(uid),
                    "Content-Type": "multipart/form-data; boundary=synthetic",
                },
            )
            assert response.status == 503
        finally:
            await client.close()

    asyncio.run(scenario())
    assert db.photos_count(rep) == 0
    photo_dir = web.PHOTO_DIR / rep
    assert not photo_dir.exists() or not list(photo_dir.iterdir())


def test_interrupted_stream_leaves_no_final_or_part_file(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "PHOTO_DIR", tmp_path / "photos")
    dest = web.PHOTO_DIR / "INTERRUPTED"

    async def broken_chunks():
        yield JPEG[:6]
        raise ConnectionResetError("synthetic disconnect")

    async def scenario():
        with pytest.raises(ConnectionResetError, match="synthetic disconnect"):
            await web._store_photo(broken_chunks(), dest, "broken-op", ".jpg")

    asyncio.run(scenario())
    assert not list(dest.iterdir())
