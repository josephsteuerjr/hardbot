"""Заметки выездника «для себя»: в форму визита на ходу влезает не всё."""
import asyncio
import json

import pytest
from aiohttp.test_utils import make_mocked_request

import db
import web


@pytest.fixture(autouse=True)
def _clean():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM notes")
        conn.commit()


def _call(handler, method, path, body=None, auth=(777, "Тестов")):
    req = make_mocked_request(method, path)

    async def _json():
        return body or {}

    req.json = _json
    orig = web._auth
    web._auth = lambda _r: auth
    try:
        return json.loads(asyncio.run(handler(req)).body.decode())
    finally:
        web._auth = orig


def test_note_is_saved_and_returned():
    r = _call(web._note_add, "POST", "/api/note",
              {"text": "у дома собака, заходить со двора", "op_id": "n1"})
    assert r["ok"] and r["id"]
    got = _call(web._notes, "GET", "/api/notes")
    assert [n["text"] for n in got["notes"]] == ["у дома собака, заходить со двора"]


def test_repeat_from_offline_queue_does_not_duplicate():
    """Очередь досылает одно и то же по нескольку раз — дубль выглядел бы как
    «приложение размножает мои записи»."""
    for _ in range(3):
        _call(web._note_add, "POST", "/api/note", {"text": "повтор", "op_id": "same"})
    assert len(_call(web._notes, "GET", "/api/notes")["notes"]) == 1


def test_empty_note_is_rejected():
    with pytest.raises(web.web.HTTPBadRequest):
        _call(web._note_add, "POST", "/api/note", {"text": "   ", "op_id": "n2"})


def test_notes_are_private_to_their_author():
    """Это личные записи: чужие не отдаём даже внутри одной компании."""
    _call(web._note_add, "POST", "/api/note", {"text": "моя", "op_id": "a"},
          auth=(777, "Тестов"))
    other = _call(web._notes, "GET", "/api/notes", auth=(888, "Другой"))
    assert other["notes"] == []


def test_cannot_delete_someone_elses_note():
    r = _call(web._note_add, "POST", "/api/note", {"text": "моя", "op_id": "b"},
              auth=(777, "Тестов"))
    res = _call(web._note_delete, "POST", "/api/note_delete", {"id": r["id"]},
                auth=(888, "Другой"))
    assert res["ok"] is False
    assert len(_call(web._notes, "GET", "/api/notes")["notes"]) == 1


def test_note_cannot_be_pinned_to_a_foreign_case():
    """Иначе заметка стала бы способом узнать, что такой договор существует."""
    with pytest.raises(web.web.HTTPNotFound):
        _call(web._note_add, "POST", "/api/note",
              {"text": "чужой", "op_id": "c", "rep": "НЕТ-ТАКОГО"})


def test_very_long_note_is_cut_not_lost():
    r = _call(web._note_add, "POST", "/api/note",
              {"text": "я" * (db.NOTE_MAX_LEN + 500), "op_id": "long"})
    assert r["ok"]
    got = _call(web._notes, "GET", "/api/notes")["notes"][0]
    assert len(got["text"]) == db.NOTE_MAX_LEN
