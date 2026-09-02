import asyncio
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from stt_client import MAX_AUDIO_BYTES, STTClient, STTError


TOKEN = "a" * 48


def _token_file(tmp_path: Path) -> Path:
    path = tmp_path / "client.token"
    path.write_text(TOKEN + "\n", encoding="ascii")
    return path


def test_transcribe_sends_only_generic_audio_and_language(tmp_path):
    async def scenario():
        observed = {}

        async def handler(request):
            observed["authorization"] = request.headers.get("Authorization")
            reader = await request.multipart()
            fields = []
            while field := await reader.next():
                if field.name == "audio":
                    fields.append(
                        (
                            field.name,
                            field.filename,
                            await field.read(),
                        )
                    )
                else:
                    fields.append((field.name, None, await field.text()))
            observed["fields"] = fields
            return web.json_response(
                {
                    "schema": "praxis.stt.v1",
                    "request_id": "opaque",
                    "text": "  распознанный текст  ",
                    "language": "ru",
                }
            )

        app = web.Application()
        app.router.add_post("/v1/transcriptions", handler)
        server = TestServer(app)
        await server.start_server()
        try:
            audio = tmp_path / "secret-client-name.webm"
            audio.write_bytes(b"audio-bytes")
            client = STTClient(
                token_file=_token_file(tmp_path),
                unix_socket=None,
                base_url=str(server.make_url("")).rstrip("/"),
            )
            assert await client.transcribe(audio, "ru") == "распознанный текст"
        finally:
            await server.close()
        assert observed == {
            "authorization": f"Bearer {TOKEN}",
            "fields": [
                ("audio", "audio.bin", b"audio-bytes"),
                ("language", None, "ru"),
            ],
        }

    asyncio.run(scenario())


def test_health_requires_ready_schema(tmp_path):
    async def scenario():
        async def handler(request):
            assert request.headers["Authorization"] == f"Bearer {TOKEN}"
            return web.json_response(
                {
                    "schema": "praxis.stt.health.v1",
                    "status": "ready",
                    "ready": True,
                    "busy": False,
                }
            )

        app = web.Application()
        app.router.add_get("/healthz", handler)
        server = TestServer(app)
        await server.start_server()
        try:
            client = STTClient(
                token_file=_token_file(tmp_path),
                unix_socket=None,
                base_url=str(server.make_url("")).rstrip("/"),
            )
            assert (await client.health())["ready"] is True
        finally:
            await server.close()

    asyncio.run(scenario())


def test_busy_and_warming_are_retryable_without_leaking_body(tmp_path):
    async def scenario(status):
        async def handler(_request):
            return web.json_response(
                {
                    "schema": "praxis.stt.error.v1",
                    "error": "internal detail that must not escape",
                },
                status=status,
                headers={"Retry-After": "3"},
            )

        app = web.Application()
        app.router.add_post("/v1/transcriptions", handler)
        server = TestServer(app)
        await server.start_server()
        try:
            audio = tmp_path / f"voice-{status}.webm"
            audio.write_bytes(b"audio")
            client = STTClient(
                token_file=_token_file(tmp_path),
                unix_socket=None,
                base_url=str(server.make_url("")).rstrip("/"),
            )
            with pytest.raises(STTError) as caught:
                await client.transcribe(audio, "auto")
            assert caught.value.status == status
            assert caught.value.retryable is True
            assert caught.value.retry_after == 3
            assert "internal detail" not in str(caught.value)
        finally:
            await server.close()

    asyncio.run(scenario(429))
    asyncio.run(scenario(503))


def test_local_validation_rejects_bad_token_language_and_size(tmp_path):
    bad_token = tmp_path / "bad.token"
    bad_token.write_text("short", encoding="ascii")
    audio = tmp_path / "audio"
    audio.write_bytes(b"x")
    client = STTClient(
        token_file=bad_token,
        unix_socket=None,
        base_url="http://127.0.0.1:1",
    )
    with pytest.raises(STTError, match="язык"):
        asyncio.run(client.transcribe(audio, "tg"))
    with pytest.raises(STTError, match="[Тт]окен"):
        asyncio.run(client.transcribe(audio, "ru"))

    large = tmp_path / "large"
    with large.open("wb") as stream:
        stream.truncate(MAX_AUDIO_BYTES + 1)
    valid = STTClient(
        token_file=_token_file(tmp_path),
        unix_socket=None,
        base_url="http://127.0.0.1:1",
    )
    with pytest.raises(STTError) as caught:
        asyncio.run(valid.transcribe(large, "ru"))
    assert caught.value.status == 413
