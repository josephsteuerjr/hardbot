import asyncio
import threading
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

import web


def prepare(monkeypatch, build):
    monkeypatch.setattr(web, "_auth", lambda request: (77, "Тестовый сотрудник"))
    monkeypatch.setattr(web, "lang_of", lambda uid: "ru")
    monkeypatch.setattr(web, "_CARDS_BUILD_LIMIT", asyncio.Semaphore(1))
    monkeypatch.setattr(web, "_CARDS_BUILDS", set())
    monkeypatch.setattr(web, "_cards_payload", build)


def payload():
    return {"ok": True, "cards": {}, "diagnostics": {"server_ms": 1}}


def test_slow_prefetch_keeps_public_version_available(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def build(*args):
        started.set()
        assert release.wait(5), "Public request did not run during prefetch"
        return payload()

    prepare(monkeypatch, build)

    async def scenario():
        async with TestClient(TestServer(web.build_app())) as client:
            slow = asyncio.create_task(client.get("/api/cards"))
            try:
                assert await asyncio.to_thread(started.wait, 2)
                version = await asyncio.wait_for(client.get("/version.json"), 1)
                assert version.status == 200
                assert not slow.done()
            finally:
                release.set()
            response = await slow
            assert response.status == 200
            assert (await response.json())["cards"] == {}

    asyncio.run(scenario())


def test_cancelled_request_holds_build_slot_until_worker_finishes(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    calls = []

    def build(*args):
        calls.append(1)
        started.set()
        assert release.wait(5)
        return payload()

    prepare(monkeypatch, build)

    async def scenario():
        first = asyncio.create_task(web._cards(SimpleNamespace(query={})))
        second = None
        try:
            assert await asyncio.to_thread(started.wait, 2)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            second = asyncio.create_task(web._cards(SimpleNamespace(query={})))
            await asyncio.sleep(0)
            assert len(calls) == 1 and not second.done()
            release.set()
            assert (await asyncio.wait_for(second, 2)).status == 200
            assert len(calls) == 2
            assert not web._CARDS_BUILDS
        finally:
            release.set()
            await asyncio.gather(first, *([second] if second else []), return_exceptions=True)

    asyncio.run(scenario())
