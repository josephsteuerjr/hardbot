"""Голосовой комментарий: фича живёт за VOICE_MODEL (путь к модели faster-whisper).

Без модели: /api/voice отвечает 503, миниапп прячет микрофон (meta.voice=false).
Само распознавание здесь не гоняем — модель есть только на сервере.
"""
import asyncio
from types import SimpleNamespace

import pytest
from aiohttp import web as aioweb

import db
import web


def test_meta_voice_flag(monkeypatch):
    db.init_db()
    monkeypatch.setattr(web, "VOICE_MODEL", "")
    assert web._meta("ru")["voice"] is False
    monkeypatch.setattr(web, "VOICE_MODEL", "/models/whisper/x")
    assert web._meta("ru")["voice"] is True


def test_voice_503_when_disabled(monkeypatch):
    db.init_db()
    monkeypatch.setattr(web, "VOICE_MODEL", "")
    monkeypatch.setattr(web, "_auth", lambda r: (1, "Кто-то"))
    req = SimpleNamespace(query={})
    with pytest.raises(aioweb.HTTPServiceUnavailable):
        asyncio.run(web._voice(req))
