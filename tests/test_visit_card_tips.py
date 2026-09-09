"""Доп. адрес / доп. номер обязан быть во всех трёх карточках визита.

08.09.2026, первый день массового теста: выездник записал в «Доп. адрес /
доп. номер» телефон клиента и телефон жены, а в карточке, которую руководитель
получил в личку и переслал в свою беседу, этого не оказалось — жалоба пришла
тем же вечером. Поле bot-only (в реестр не уходит), поэтому потерять его можно
было только здесь: в трёх независимых сборщиках текста.
"""
import asyncio
import json

import notify
import web


ROW = {
    "contract_id": "TIP-1", "client": "ISMOILOVA S.", "responsible": "Темурхочаев А.",
    "call_status": "Обещает оплатить", "agreed_payment": "Обещает оплатить клиент",
    "visit_comment": "приехал по адресу, встретила мама", "call_comment": "",
    "tips": "Номер клиента 993336488\nНомер жены 988091962",
    "city": "Душанбе", "address": "ул. Халимова", "promise_date": "", "forecast_sum": "",
}


def test_report_chat_card_has_tips():
    text = notify.visit_report_text(ROW, photos_n=1)
    assert "📇" in text and "993336488" in text and "988091962" in text
    assert text.index("💬") < text.index("📇"), "доп. адрес идёт после комментария"


def test_report_chat_card_escapes_tips():
    """Бот шлёт HTML: «<» из доп. адреса не должен ронять доставку."""
    text = notify.visit_report_text({**ROW, "tips": "дом <5> & кв"}, photos_n=0)
    assert "&lt;5&gt;" in text and "&amp;" in text


def test_report_chat_card_without_tips_has_no_empty_line():
    text = notify.visit_report_text({**ROW, "tips": ""}, photos_n=0)
    assert "📇" not in text


def test_manager_dm_and_group_cards_have_tips(monkeypatch):
    """Обе ручки web.py собирают текст сами — проверяем каждую отправку."""
    sent: list[str] = []

    class _Bot:
        async def send_message(self, chat_id, text, **kw):
            sent.append(text)

    async def _album(_bot, _chat, _files, text):
        sent.append(text)
        return {"ok": True}

    monkeypatch.setattr(notify, "send_album", _album, raising=False)
    monkeypatch.setattr(web, "day_photos", lambda *_a, **_k: [])
    monkeypatch.setattr(web.db, "person_contracts", lambda *_a, **_k: [])
    monkeypatch.setattr(web.db, "managers_for_employee",
                        lambda _e: [{"telegram_id": 7001, "matched_as": None}])
    # Подпись с умолчанием: у части рубильников оно своё (owner_visits — "0").
    monkeypatch.setattr(web.db, "push_enabled", lambda _k, _default="1": True)
    monkeypatch.setattr(web.db, "log_push", lambda *_a, **_k: None)
    monkeypatch.setattr(web.db, "same_visit_logged_today", lambda *_a, **_k: False)
    monkeypatch.setattr(web.notify_module if hasattr(web, "notify_module") else notify,
                        "classify_send_error", lambda e: ("failed", str(e)),
                        raising=False)

    class _Req:
        app = {"bot": _Bot()}

    asyncio.run(web._notify_managers(_Req(), dict(ROW), None, geo_far=None, actor=None))
    assert sent, "карточка руководителю не собралась"
    assert any("📇" in t and "993336488" in t for t in sent)


def test_group_card_has_tips(monkeypatch):
    posted: list[str] = []

    async def _post(_bot, text=None, **_kw):
        posted.append(text or "")
        return True

    monkeypatch.setattr(notify, "post_to_group", _post, raising=False)
    monkeypatch.setattr(notify, "group_chat_id", lambda: -100500, raising=False)
    monkeypatch.setattr(web.db, "log_push", lambda *_a, **_k: None)

    class _Req:
        app = {"bot": object()}

    asyncio.run(web._notify_group(_Req(), dict(ROW), uid=1, actor=None))
    assert posted and "📇" in posted[0] and "993336488" in posted[0]


def test_tips_is_a_bot_only_snapshot_key():
    """Если поле выпадет из снимка, в карточке его тоже не будет — держим связь."""
    import db
    assert "tips" in db.BOT_SNAPSHOT_KEYS
    assert "tips" not in db.BOT_VISIT_KEYS, "tips в реестр не выгружается"
    json.dumps(ROW, ensure_ascii=False)
