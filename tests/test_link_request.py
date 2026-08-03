"""PWA-ссылка создаётся только по отдельному слову «ссылка»."""

import asyncio
from types import SimpleNamespace

import handlers_main
import handlers_manager


class FakeMessage:
    def __init__(self, text="", uid=501):
        self.text = text
        self.from_user = SimpleNamespace(
            id=uid, username="worker", first_name="Иван", last_name="Иванов")
        self.answers = []

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))


def test_link_word_match_is_exact_case_insensitive():
    for text in ("ссылка", " ССЫЛКА ", "СсЫлКа", "\nссылка\t"):
        assert handlers_main._is_applink_request(text)
    for text in (None, "", "дай ссылку", "ссылка!", "/ссылка", "link"):
        assert not handlers_main._is_applink_request(text)


def test_legacy_link_button_handler_is_removed():
    # Старые сообщения с callback_data="applink" тоже не должны обходить новый
    # контракт: выпуск кода возможен только после текстового слова «ссылка».
    assert not hasattr(handlers_main, "applink_cb")


def test_start_never_issues_link(monkeypatch):
    msg = FakeMessage("/start")
    marker = object()
    monkeypatch.setattr(handlers_main, "_remember", lambda _user: None)
    monkeypatch.setattr(handlers_main, "lang_of", lambda _uid: "ru")
    monkeypatch.setattr(handlers_main, "_open_app_kb", lambda _lang, _uid: marker)
    monkeypatch.setattr(handlers_main, "t", lambda key, _lang: key)
    monkeypatch.setattr(
        handlers_main, "_applink_text",
        lambda *_args: (_ for _ in ()).throw(AssertionError("/start создал ссылку")),
    )

    asyncio.run(handlers_main.cmd_start(msg))
    assert msg.answers == [("start_app", marker)]


def test_only_link_word_gets_app_link(monkeypatch):
    marker = object()
    issued = []
    monkeypatch.setattr(handlers_main, "_remember", lambda _user: None)
    monkeypatch.setattr(handlers_main, "lang_of", lambda _uid: "ru")
    monkeypatch.setattr(handlers_main, "_open_app_kb", lambda _lang, uid=None: marker)
    monkeypatch.setattr(handlers_main, "t", lambda key, _lang: key)
    monkeypatch.setattr(
        handlers_main, "_applink_text",
        lambda uid, _lang: issued.append(uid) or "APP-LINK",
    )

    requested = FakeMessage("  ССЫЛКА ")
    asyncio.run(handlers_main.any_message(requested))
    assert issued == [501]
    assert requested.answers == [("APP-LINK", marker)]

    issued.clear()
    ordinary = FakeMessage("привет")
    asyncio.run(handlers_main.any_message(ordinary))
    assert issued == []
    assert ordinary.answers == [("start_app", marker)]


def test_manager_router_honors_same_explicit_trigger(monkeypatch):
    calls = []

    async def send_link(_message):
        calls.append("link")
        return True

    async def send_digest(_message):
        calls.append("digest")

    monkeypatch.setattr(handlers_manager, "_send_requested_applink", send_link)
    monkeypatch.setattr(handlers_manager, "_send_digest", send_digest)

    requested = FakeMessage("ссылка")
    asyncio.run(handlers_manager.fallback(requested))
    assert calls == ["link"]
    assert requested.answers == []

    calls.clear()
    ordinary = FakeMessage("привет")
    asyncio.run(handlers_manager.fallback(ordinary))
    assert calls == ["digest"]
    assert ordinary.answers[0][0].startswith("Меню")
