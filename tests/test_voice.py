"""Голос hardbot: только общий Praxis STT по UDS, без local Whisper fallback."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
import uuid

from aiohttp import FormData
from aiohttp import web as aioweb
from aiohttp.test_utils import TestClient, TestServer
import pytest

import db
import stt_client
import web


def _enable(monkeypatch):
    monkeypatch.setattr(web, "VOICE_BACKEND", "praxis")
    monkeypatch.setattr(web, "PRAXIS_STT_SOCKET", "/run/praxis-stt/stt.sock")
    monkeypatch.setattr(
        web,
        "PRAXIS_STT_TOKEN_FILE",
        "/run/secrets/praxis-stt-client.token",
    )


def _identity(monkeypatch):
    suffix = uuid.uuid4().hex[:10]
    uid = 7_000_000 + int(suffix[:5], 16)
    employee = f"Voice Test {suffix}"
    rep = f"VOICE-{suffix}"
    db.init_db()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO clients (contract_id, client, responsible) VALUES (?,?,?)",
            (rep, "Synthetic", employee),
        )
        conn.commit()
    db.bind_agent(uid, employee)
    db.set_lang(uid, "uz")
    monkeypatch.setattr(web, "DEV", True)
    return uid, rep


def test_meta_voice_flag_requires_complete_praxis_config(monkeypatch):
    db.init_db()
    monkeypatch.setattr(web, "VOICE_BACKEND", "off")
    assert web._meta("ru")["voice"] is False

    _enable(monkeypatch)
    assert web._meta("ru")["voice"] is True
    monkeypatch.setattr(web, "PRAXIS_STT_SOCKET", "")
    assert web._meta("ru")["voice"] is False


def test_voice_503_when_disabled(monkeypatch):
    db.init_db()
    monkeypatch.setattr(web, "VOICE_BACKEND", "off")
    monkeypatch.setattr(web, "_auth", lambda _request: (1, "Кто-то"))
    request = SimpleNamespace(query={})
    with pytest.raises(aioweb.HTTPServiceUnavailable):
        asyncio.run(web._voice(request))


def test_voice_uses_praxis_client_and_deletes_temp(monkeypatch):
    _enable(monkeypatch)
    uid, rep = _identity(monkeypatch)
    seen = {}

    class FakeSTT:
        async def transcribe(self, path, language):
            source = Path(path)
            seen["exists_during"] = source.is_file()
            seen["path"] = source
            seen["bytes"] = source.read_bytes()
            seen["language"] = language
            return "  распознано Praxis  "

    monkeypatch.setattr(web, "_STT", FakeSTT())

    async def scenario():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            form = FormData()
            form.add_field(
                "voice",
                b"synthetic-audio",
                filename="private-name.webm",
                content_type="audio/webm",
            )
            response = await client.post(
                f"/api/voice?rep={rep}",
                data=form,
                headers={"X-Dev-Uid": str(uid)},
            )
            assert response.status == 200
            assert await response.json() == {
                "ok": True,
                "text": "распознано Praxis",
            }
        finally:
            await client.close()

    asyncio.run(scenario())
    assert seen["exists_during"] is True
    assert seen["bytes"] == b"synthetic-audio"
    assert seen["language"] == "uz"
    assert not seen["path"].exists()


def test_voice_maps_praxis_busy_to_retryable_503(monkeypatch):
    _enable(monkeypatch)
    uid, rep = _identity(monkeypatch)

    class BusySTT:
        async def transcribe(self, _path, _language):
            raise stt_client.STTError(
                "internal detail",
                status=429,
                retryable=True,
                retry_after=4,
            )

    monkeypatch.setattr(web, "_STT", BusySTT())

    async def scenario():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            form = FormData()
            form.add_field("voice", b"audio", filename="voice.webm")
            response = await client.post(
                f"/api/voice?rep={rep}",
                data=form,
                headers={"X-Dev-Uid": str(uid)},
            )
            assert response.status == 503
            assert response.headers["Retry-After"] == "4"
            assert "internal detail" not in await response.text()
        finally:
            await client.close()

    asyncio.run(scenario())


def test_empty_voice_is_422_and_never_calls_praxis(monkeypatch):
    _enable(monkeypatch)
    uid, rep = _identity(monkeypatch)

    class NeverSTT:
        async def transcribe(self, _path, _language):
            pytest.fail("empty upload must not cross process boundary")

    monkeypatch.setattr(web, "_STT", NeverSTT())

    async def scenario():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            form = FormData()
            form.add_field("voice", b"", filename="voice.webm")
            response = await client.post(
                f"/api/voice?rep={rep}",
                data=form,
                headers={"X-Dev-Uid": str(uid)},
            )
            assert response.status == 422
        finally:
            await client.close()

    asyncio.run(scenario())
