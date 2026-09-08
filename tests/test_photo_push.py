"""Фото руководителям: чанкование альбомов (notify.send_album) и реалтайм-пуш
по закрытию визита (web._notify_managers). Телеграм заменён фейковым ботом.

Проверяем, что:
  • альбомы режутся по 10, подпись — только на первом элементе первого альбома;
  • одиночный файл уходит send_photo (медиагруппа требует ≥2 элементов);
  • пуш уходит только руководителям, у кого сотрудник в зоне ответственности;
  • без фото уходит текст «без фото», рубильник settings.visit_push=0 глушит всё.
"""
import asyncio
from types import SimpleNamespace

import db
import notify
import photostore
import web


class FakeBot:
    def __init__(self):
        self.calls = []   # (вид, chat_id, сколько медиа, подпись)

    async def send_media_group(self, chat_id, media):
        self.calls.append(("album", chat_id, len(media), media[0].caption))

    async def send_photo(self, chat_id, photo, caption=None):
        self.calls.append(("photo", chat_id, 1, caption))

    async def send_document(self, chat_id, doc, caption=None, reply_markup=None):
        self.calls.append(("doc", chat_id, 1, caption))

    async def send_message(self, chat_id, text):
        self.calls.append(("msg", chat_id, 0, text))


def test_send_album_chunks_and_caption():
    bot = FakeBot()
    asyncio.run(notify.send_album(bot, 42, [f"p{i}.jpg" for i in range(23)], "подпись"))
    assert [(c[0], c[2]) for c in bot.calls] == [("album", 10), ("album", 10), ("album", 3)]
    assert bot.calls[0][3] == "подпись"                      # подпись — на первом
    assert all(c[3] is None for c in bot.calls[1:])


def test_send_album_single_file():
    bot = FakeBot()
    asyncio.run(notify.send_album(bot, 42, ["one.jpg"], "п"))
    assert bot.calls == [("photo", 42, 1, "п")]


# --- реалтайм-пуш по визиту ---------------------------------------------------
EMP = "Пушкин Сотрудник"
MGR_OK, MGR_ALIEN = 9001, 9002


def _seed(with_photos: bool):
    db.init_db()
    with db.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO clients (contract_id, client, responsible, "
            "call_status, agreed_payment) VALUES (?,?,?,?,?)",
            ("D-P1", "Тестов Т.", EMP, "В работе", "Обещает оплатить клиент"))
        conn.commit()
    d = photostore.PHOTO_DIR / "D-P1"
    if d.is_dir():
        for f in d.iterdir():
            f.unlink()
    if with_photos:
        d.mkdir(parents=True, exist_ok=True)
        (d / "1.jpg").write_bytes(b"x")
        (d / "2.jpg").write_bytes(b"x")
    db.set_manager(MGR_OK, "Василий", [], [EMP])
    db.set_manager(MGR_ALIEN, "Чужой", [], ["Другой Сотрудник"])
    db.set_setting("visit_push", "1")


def _push():
    bot = FakeBot()
    req = SimpleNamespace(app={"bot": bot})
    asyncio.run(web._notify_managers(req, db.get_client("D-P1")))
    return bot.calls


def _cleanup():
    db.delete_manager(MGR_OK)
    db.delete_manager(MGR_ALIEN)


def test_push_album_to_matching_manager_only():
    _seed(with_photos=True)
    calls = _push()
    assert [(c[0], c[1], c[2]) for c in calls] == [("album", MGR_OK, 2)]
    assert "Тестов Т." in calls[0][3] and "📸 2 фото" in calls[0][3]
    assert all(c[1] != MGR_ALIEN for c in calls)             # чужому не ушло
    _cleanup()


def test_push_text_when_no_photos():
    _seed(with_photos=False)
    calls = _push()
    assert [(c[0], c[1]) for c in calls] == [("msg", MGR_OK)]
    assert "без фото" in calls[0][3]
    _cleanup()


def test_push_kill_switch():
    _seed(with_photos=True)
    db.set_setting("visit_push", "0")
    assert _push() == []
    db.set_setting("visit_push", "1")
    _cleanup()
