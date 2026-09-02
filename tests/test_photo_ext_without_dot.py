"""Расширение сжатого снимка без ведущей точки — 422 и фото не доезжает НИКОГДА.

04.08.2026, живая авария. Утром выкатили сжатие фото (снимок 3-8 МБ рвал
мобильную сеть). В пути сжатия оболочка ставила расширение строкой "jpg" — без
точки, — а сервер требует ровно `\\.[A-Za-z0-9]{1,7}` и отвечает 422
«bad extension». Клиент считает 4xx неисправимым: помечает снимок «нужно
переснять» и больше не пробует. Пересъёмка даёт тот же 422.

Итог в проде: с 08:43 до 13:30 UTC на диск не легло НИ ОДНОГО фото, в журнале
ровно один `POST /api/photo 422` — единственный человек, успевший обновиться и
попробовать снять. Фикс двусторонний, и вторая половина важнее первой:

* оболочка шлёт ".jpg" — правильно;
* сервер дописывает точку сам — потому что оболочка у людей в поле обновится не
  в ту же секунду, а фото им нужны сегодня.

Значение всё равно сверяется с magic-байтами, так что послабление ничего нового
не пропускает.
"""

import asyncio
import pathlib
import uuid

import db
import web
from aiohttp.test_utils import TestClient, TestServer

JPEG = b"\xff\xd8\xff\xe0" + b"synthetic-jpeg"
INDEX = (pathlib.Path(__file__).resolve().parent.parent
         / "server" / "miniapp" / "index.html").read_text(encoding="utf-8")


def _identity(monkeypatch, tmp_path):
    suffix = uuid.uuid4().hex[:10]
    uid = 8_100_000 + int(suffix[:5], 16)
    employee = f"Ext Test {suffix}"
    rep = f"EXT-{suffix}"
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


def _post(uid, rep, ext, body=JPEG):
    """Статус ответа на одну загрузку с указанным X-Photo-Ext."""

    async def scenario():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            resp = await client.post(
                f"/api/photo?rep={rep}&op={uuid.uuid4().hex[:16]}",
                data=body,
                headers={"X-Dev-Uid": str(uid), "X-Photo-Raw": "1",
                         "X-Photo-Ext": ext,
                         "Content-Type": "application/octet-stream"},
            )
            return resp.status
        finally:
            await client.close()

    return asyncio.run(scenario())


def test_extension_without_a_dot_is_accepted(monkeypatch, tmp_path):
    """Оболочка v26 у человека в поле — фото обязано доехать без обновления."""
    uid, rep = _identity(monkeypatch, tmp_path)
    assert _post(uid, rep, "jpg") == 200, "сжатое фото со старой оболочки снова отвергается"
    assert db.photos_count(rep) == 1


def test_extension_with_a_dot_still_works(monkeypatch, tmp_path):
    uid, rep = _identity(monkeypatch, tmp_path)
    assert _post(uid, rep, ".jpg") == 200


def test_stored_file_has_a_sane_name_either_way(monkeypatch, tmp_path):
    """Точка дописывается, а не приклеивается к имени как есть."""
    uid, rep = _identity(monkeypatch, tmp_path)
    assert _post(uid, rep, "jpg") == 200
    names = [p.name for p in (web.PHOTO_DIR / rep).iterdir()]
    assert names and all(n.endswith(".jpg") and "..jpg" not in n for n in names)


def test_extension_that_contradicts_the_bytes_is_still_refused(monkeypatch, tmp_path):
    """Послабление не должно превратиться в дыру: jpeg-байтам не дать png-имя."""
    uid, rep = _identity(monkeypatch, tmp_path)
    assert _post(uid, rep, "png") == 422


def test_garbage_extension_is_still_refused(monkeypatch, tmp_path):
    uid, rep = _identity(monkeypatch, tmp_path)
    assert _post(uid, rep, "jpg/../../etc") == 422


def test_shell_sends_the_extension_with_a_dot():
    """Вторая половина фикса: сама оболочка больше не шлёт бесточечное."""
    assert 'ext:small?".jpg":photoExt(f.name,f.type)' in INDEX
    assert 'ext:small?"jpg"' not in INDEX
