"""Комментарий выездника живёт в visit_comment («Комментарий текущего месяца выезда»).

До 18.07.2026 форма миниаппа писала его в call_comment («комментарий звонка») — в
сырой выгрузке текст сидел не в той колонке. Сервер обязан: писать новый ключ,
ремапить легаси-сохранения (старая закэшированная оболочка и оффлайн-очереди на
телефонах ещё шлют call_comment) и показывать комментарий из любого из двух полей.
"""
import asyncio

from aiohttp.test_utils import make_mocked_request

import db
import web


EMP = "Тестов Сотр"


def _seed(cid: str):
    grid = [[None] * len(db.FIELDS),
            [f.title for f in db.FIELDS],
            [f.source for f in db.FIELDS],
            [{"contract_id": cid, "client": "Клиент К.", "responsible": EMP}.get(f.key)
             for f in db.FIELDS]]
    db.import_grid(
        grid,
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
    )


def _save(body, monkeypatch):
    monkeypatch.setattr(web, "_auth", lambda _r: (111, EMP))
    req = make_mocked_request("POST", "/api/save")

    async def _json():
        return body
    req.json = _json
    return asyncio.run(web._save(req))


def test_comment_saved_to_visit_comment(monkeypatch):
    db.init_db()
    _seed("VC-1")
    _save({"rep": "VC-1", "values": {"visit_comment": "видел клиента"}}, monkeypatch)
    c = db.get_client("VC-1")
    assert c["visit_comment"] == "видел клиента"
    assert not (c["call_comment"] or "")


def test_legacy_call_comment_remapped(monkeypatch):
    """Старая оболочка / оффлайн-очередь шлёт call_comment → сервер кладёт в visit_comment."""
    db.init_db()
    _seed("VC-2")
    _save({"rep": "VC-2", "values": {"call_comment": "легаси-оболочка"}}, monkeypatch)
    c = db.get_client("VC-2")
    assert c["visit_comment"] == "легаси-оболочка"
    assert not (c["call_comment"] or "")


def test_unfinished_server_draft_does_not_prefill_visit_form(monkeypatch):
    """Черновик хранится локально, а raw clients не становится form-prefill."""
    db.init_db()
    _seed("VC-3")
    _save({"rep": "VC-3", "values": {"visit_comment": "тест"}}, monkeypatch)
    data = web._person_full("VC-3", "ru")
    assert db.get_client("VC-3")["visit_comment"] == "тест"
    assert data["visit_comment"] == ""
    assert "call_comment" in data
