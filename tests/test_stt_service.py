"""Служба распознавания речи (stt/server.py): контракт на подменном распознавателе.

Модель здесь не грузится — проверяется HTTP-часть: токен, статусы, лимиты,
временные файлы и тишина в журнале. Тест с настоящей моделью в конце
пропускается, если её нет.
"""

import asyncio
import importlib.util
import logging
import os
import stat
import sys
from pathlib import Path

import pytest
from aiohttp import ClientSession, ClientTimeout, FormData
from aiohttp.test_utils import TestClient, TestServer

ROOT = Path(__file__).resolve().parent.parent


def _load_server():
    spec = importlib.util.spec_from_file_location("hard_stt_server", ROOT / "stt" / "server.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


stt = _load_server()
TOKEN = "t" * 48
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeBackend:
    def __init__(self, text="готова полевая проверка", ready=True):
        self.ready_flag = ready
        self.busy_flag = False
        self.text = text
        self.fail = None
        self.calls = []

    @property
    def ready(self):
        return self.ready_flag

    @property
    def busy(self):
        return self.busy_flag

    def transcribe(self, path, language):
        path = Path(path)
        self.calls.append({"bytes": path.read_bytes(), "language": language,
                           "path": path, "mode": stat.S_IMODE(path.stat().st_mode)})
        if self.fail is not None:
            raise self.fail
        return self.text


def _settings(tmp_path, **overrides):
    temp = tmp_path / "tmp"
    temp.mkdir(exist_ok=True)
    values = {"temp_dir": temp, "max_bytes": 1024 * 1024, "rate_per_minute": 60, "burst": 20}
    values.update(overrides)
    return stt.Settings(**values)


def _run(settings, backend, scenario, clock=None):
    async def go():
        extra = {"clock": clock} if clock is not None else {}
        service = stt.Service(settings, backend, TOKEN, **extra)
        client = TestClient(TestServer(service.app))
        await client.start_server()
        try:
            return await scenario(client)
        finally:
            await client.close()

    return asyncio.run(go())


def _form(audio=b"audio-bytes", language="ru", filename="private-name.webm", extra=None):
    form = FormData()
    if audio is not None:
        form.add_field("audio", audio, filename=filename, content_type="application/octet-stream")
    if language is not None:
        # content_type удерживает multipart даже без файла (иначе aiohttp шлёт urlencoded)
        form.add_field("language", language, content_type="text/plain")
    for name, value in (extra or {}).items():
        form.add_field(name, value)
    return form


async def _post(client, form, headers=AUTH):
    response = await client.post("/v1/transcriptions", data=form, headers=headers)
    return response, await response.json(content_type=None)


# --- здоровье -------------------------------------------------------------------

def test_health_requires_token_and_reports_readiness(tmp_path):
    backend = FakeBackend(ready=False)

    async def scenario(client):
        anonymous = await client.get("/healthz")
        assert anonymous.status == 401
        assert anonymous.headers["WWW-Authenticate"] == "Bearer"
        body = await anonymous.json()
        assert body["schema"] == stt.ERROR_SCHEMA
        assert body["error"]["code"] == "unauthorized"

        wrong = await client.get("/healthz", headers={"Authorization": "Bearer " + "x" * 48})
        assert wrong.status == 401

        warming = await client.get("/healthz", headers=AUTH)
        assert warming.status == 503
        assert await warming.json() == {"schema": stt.HEALTH_SCHEMA, "status": "warming",
                                        "ready": False, "busy": False}

        backend.ready_flag = True
        ready = await client.get("/healthz", headers=AUTH)
        assert ready.status == 200
        assert await ready.json() == {"schema": stt.HEALTH_SCHEMA, "status": "ready",
                                      "ready": True, "busy": False}

    _run(_settings(tmp_path), backend, scenario)


# --- распознавание ----------------------------------------------------------------

def test_transcribe_round_trip_spools_to_private_temp_and_removes_it(tmp_path):
    backend = FakeBackend(text="  готова полевая проверка  ")
    settings = _settings(tmp_path)

    async def scenario(client):
        response, body = await _post(client, _form(audio=b"synthetic-audio", language="ru"))
        assert response.status == 200
        assert body["schema"] == stt.SCHEMA
        assert body["text"] == "  готова полевая проверка  "
        assert body["language"] == "ru"
        assert len(body["request_id"]) == 32

    _run(settings, backend, scenario)
    call = backend.calls[0]
    assert call["bytes"] == b"synthetic-audio"
    assert call["language"] == "ru"
    assert call["path"].parent == settings.temp_dir
    assert call["path"].name.startswith(stt.TEMP_PREFIX)
    assert "private-name" not in call["path"].name
    if os.name == "posix":
        assert call["mode"] == 0o600
    assert not call["path"].exists()
    assert list(settings.temp_dir.iterdir()) == []


@pytest.mark.parametrize("language, expected", [("ru", "ru"), ("uz", "uz"), ("auto", None), ("RU", "ru")])
def test_language_is_mapped_for_the_model(tmp_path, language, expected):
    backend = FakeBackend()

    async def scenario(client):
        response, body = await _post(client, _form(language=language))
        assert response.status == 200
        assert body["language"] == language.lower()

    _run(_settings(tmp_path), backend, scenario)
    assert backend.calls[0]["language"] == expected


def test_rejections_follow_the_wire_contract(tmp_path):
    backend = FakeBackend()
    settings = _settings(tmp_path, max_bytes=1000)

    async def scenario(client):
        response, body = await _post(client, _form(), headers={})
        assert (response.status, body["error"]["code"]) == (401, "unauthorized")

        backend.ready_flag = False
        response, body = await _post(client, _form())
        assert (response.status, body["error"]["code"]) == (503, "warming")
        assert response.headers["Retry-After"] == "2"
        backend.ready_flag = True

        backend.busy_flag = True
        response, body = await _post(client, _form())
        assert (response.status, body["error"]["code"]) == (429, "busy")
        assert response.headers["Retry-After"] == "2"
        backend.busy_flag = False

        response = await client.post("/v1/transcriptions", data=b"raw", headers={
            **AUTH, "Content-Type": "application/octet-stream"})
        body = await response.json(content_type=None)
        assert (response.status, body["error"]["code"]) == (415, "unsupported_media_type")

        response, body = await _post(client, _form(audio=b"x" * 5000))
        assert (response.status, body["error"]["code"]) == (413, "audio_too_large")

        response, body = await _post(client, _form(audio=b"x" * 400_000))
        assert (response.status, body["error"]["code"]) == (413, "audio_too_large")

        response, body = await _post(client, _form(language=None))
        assert (response.status, body["error"]["code"]) == (400, "missing_language")

        response, body = await _post(client, _form(audio=None))
        assert (response.status, body["error"]["code"]) == (400, "missing_audio")

        response, body = await _post(client, _form(language="tg"))
        assert (response.status, body["error"]["code"]) == (400, "invalid_language")

        response, body = await _post(client, _form(extra={"telegram_id": "1"}))
        assert (response.status, body["error"]["code"]) == (400, "unexpected_field")

        response, body = await _post(client, _form(audio=b""))
        assert (response.status, body["error"]["code"]) == (400, "empty_audio")

        backend.fail = stt.BackendFailed("decode")
        response, body = await _post(client, _form())
        assert (response.status, body["error"]["code"]) == (422, "transcription_failed")
        backend.fail = stt.BackendBusy("late")
        response, body = await _post(client, _form())
        assert (response.status, body["error"]["code"]) == (429, "busy")
        backend.fail = RuntimeError("secret detail")
        response, body = await _post(client, _form())
        assert (response.status, body["error"]["code"]) == (500, "internal_error")
        assert "secret detail" not in await response.text()
        backend.fail = None

        for body in (body,):
            assert body["schema"] == stt.ERROR_SCHEMA
            assert set(body) == {"schema", "request_id", "error"}
            assert set(body["error"]) == {"code", "message"}

    _run(settings, backend, scenario)
    assert list(settings.temp_dir.iterdir()) == []


def test_rate_limit_after_burst_with_retry_after(tmp_path):
    backend = FakeBackend()
    now = [1000.0]
    settings = _settings(tmp_path, rate_per_minute=6, burst=2)

    async def scenario(client):
        for _ in range(2):
            response, _body = await _post(client, _form())
            assert response.status == 200
        response, body = await _post(client, _form())
        assert (response.status, body["error"]["code"]) == (429, "rate_limited")
        assert int(response.headers["Retry-After"]) >= 1
        now[0] += 10.0                      # 6/мин = один слот за 10 с
        response, _body = await _post(client, _form())
        assert response.status == 200

    _run(settings, backend, scenario, clock=lambda: now[0])
    assert len(backend.calls) == 3


def test_journal_never_contains_audio_name_or_text(tmp_path, caplog):
    backend = FakeBackend(text="секретный распознанный текст")
    caplog.set_level(logging.INFO, logger="stt")

    async def scenario(client):
        response, body = await _post(client, _form(filename="ivanov-petrov-debt.webm"))
        assert response.status == 200
        backend.fail = RuntimeError("stack detail")
        response, _ = await _post(client, _form())
        assert response.status == 500

    _run(_settings(tmp_path), backend, scenario)
    journal = caplog.text
    assert "status=200" in journal and "status=500" in journal
    for forbidden in ("ivanov", "секретный", "audio-bytes", "stack detail"):
        assert forbidden not in journal


# --- токен и настройки -------------------------------------------------------------

def test_read_token_validation(tmp_path):
    good = tmp_path / "good.token"
    good.write_text(TOKEN + "\n", encoding="ascii")
    if os.name == "posix":
        good.chmod(0o640)
    assert stt.read_token(good) == TOKEN

    short = tmp_path / "short.token"
    short.write_text("short", encoding="ascii")
    with pytest.raises(stt.ConfigError):
        stt.read_token(short)

    spaced = tmp_path / "spaced.token"
    spaced.write_text("a" * 20 + " " + "b" * 20, encoding="ascii")
    if os.name == "posix":
        spaced.chmod(0o640)
    with pytest.raises(stt.ConfigError):
        stt.read_token(spaced)

    with pytest.raises(stt.ConfigError):
        stt.read_token(tmp_path / "absent.token")

    if os.name == "posix":
        public = tmp_path / "public.token"
        public.write_text(TOKEN, encoding="ascii")
        public.chmod(0o644)
        with pytest.raises(stt.ConfigError, match="640"):
            stt.read_token(public)


def test_settings_from_env_defaults_and_bounds():
    defaults = stt.Settings.from_env({})
    assert (defaults.model, defaults.compute_type, defaults.device) == ("small", "int8", "cpu")
    assert defaults.socket == Path("/run/stt/stt.sock")
    assert defaults.token_file == Path("/run/secrets/stt.token")
    assert (defaults.max_bytes, defaults.rate_per_minute, defaults.burst, defaults.upload_timeout) \
        == (stt.HARD_MAX_BYTES, 6, 2, 30)
    assert 1 <= defaults.cpu_threads() <= 4

    custom = stt.Settings.from_env({"STT_MODEL": "medium", "STT_THREADS": "3",
                                    "STT_RATE_PER_MINUTE": "12", "STT_BURST": "4",
                                    "STT_MAX_BYTES": "1024", "STT_SOCKET": "/x/y.sock"})
    assert (custom.model, custom.cpu_threads(), custom.rate_per_minute, custom.burst) \
        == ("medium", 3, 12, 4)
    assert custom.max_bytes == 1024 and custom.socket == Path("/x/y.sock")

    with pytest.raises(stt.ConfigError):
        stt.Settings.from_env({"STT_MAX_BYTES": str(stt.HARD_MAX_BYTES + 1)})
    with pytest.raises(stt.ConfigError):
        stt.Settings.from_env({"STT_BURST": "abc"})


def test_token_bucket_refills_at_rate():
    now = [0.0]
    bucket = stt.TokenBucket(6, 2, clock=lambda: now[0])
    assert bucket.take() == 0 and bucket.take() == 0
    assert bucket.take() == 10
    now[0] += 5
    assert bucket.take() == 5
    now[0] += 5
    assert bucket.take() == 0


def test_prepare_temp_dir_sweeps_only_own_leftovers(tmp_path):
    temp = tmp_path / "stt-tmp"
    temp.mkdir()
    (temp / f"{stt.TEMP_PREFIX}old{stt.TEMP_SUFFIX}").write_bytes(b"x")
    (temp / "someone-else.bin").write_bytes(b"y")
    assert stt.prepare_temp_dir(temp) == 1
    assert sorted(p.name for p in temp.iterdir()) == ["someone-else.bin"]


# --- сокет (только POSIX) --------------------------------------------------------------

@pytest.mark.skipif(os.name != "posix", reason="Unix-сокеты есть только на POSIX")
def test_serve_listens_on_unix_socket_with_0660(tmp_path):
    sock = tmp_path / "run" / "stt.sock"
    settings = _settings(tmp_path, socket=sock)
    backend = FakeBackend(text="через сокет")

    async def scenario():
        server = asyncio.create_task(stt.serve(settings, backend=backend, token=TOKEN))
        for _ in range(100):
            if sock.exists():
                break
            await asyncio.sleep(0.05)
        assert stat.S_IMODE(sock.stat().st_mode) == 0o660
        async with ClientSession(connector=stt.UnixConnector(path=str(sock)),
                                 timeout=ClientTimeout(total=5), headers=AUTH) as session:
            async with session.get("http://stt/healthz") as response:
                assert response.status == 200
            async with session.post("http://stt/v1/transcriptions", data=_form()) as response:
                assert (await response.json())["text"] == "через сокет"
        assert await stt.check(stt.Settings(socket=sock, token_file=_token_file(tmp_path))) == 0
        server.cancel()
        with pytest.raises(asyncio.CancelledError):
            await server
        assert not sock.exists()

    asyncio.run(scenario())


def _token_file(tmp_path):
    path = tmp_path / "check.token"
    path.write_text(TOKEN, encoding="ascii")
    if os.name == "posix":
        path.chmod(0o600)
    return path


# --- настоящая модель (пропускается без неё) ---------------------------------------------

CANARY = ROOT / "_local" / "praxis-stt-canary-ru.mp3"
CANARY_WORDS = ("готова", "полевая", "проверка", "распознавания", "речи",
                "служба", "тесту", "один", "два", "три")


def test_real_model_transcribes_synthetic_canary(tmp_path):
    pytest.importorskip("faster_whisper")
    models_dir = os.getenv("STT_MODELS_DIR", "").strip()
    if not models_dir or not Path(models_dir).is_dir():
        pytest.skip("STT_MODELS_DIR с кэшем модели не задан")
    if not CANARY.is_file():
        pytest.skip("нет синтетической записи _local/praxis-stt-canary-ru.mp3")
    settings = _settings(tmp_path, models_dir=Path(models_dir),
                         model=os.getenv("STT_MODEL", "small"))
    backend = stt.WhisperBackend(settings)
    backend.load()
    text = backend.transcribe(CANARY, "ru").casefold()
    found = [w for w in CANARY_WORDS if w in text]
    assert len(found) >= 6, text
