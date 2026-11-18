import asyncio

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from deploy.crm.release_gate import install


TAURI_MANAGER = {
    "X-Client-Runtime": "tauri",
    "X-Work-Product": "manager",
    "X-Client-Version": "3.5.0",
    "X-Data-Protocol": "10",
}
ANALYST = {
    "X-Client-Runtime": "desktop",
    "X-Work-Product": "analyst",
    "X-Client-Version": "3.2.29",
}
ELECTRON = {"User-Agent": "Rimborso Electron/39.0.0", "X-Client-Version": "3.3.0"}
PWA = {"X-Client-Version": "3.2.0", "X-Shell-Version": "112"}


@pytest.mark.parametrize(
    "method,path,headers,expected",
    [
        ("GET", "/api/identity", TAURI_MANAGER, 426),
        ("POST", "/api/manager/entry", TAURI_MANAGER, 503),
        ("GET", "/updates/cis-collection-tauri-stable/manager.json", TAURI_MANAGER, 200),
        ("OPTIONS", "/api/identity", TAURI_MANAGER, 200),
        ("POST", "/admin/import.json", ANALYST, 503),
        ("POST", "/admin/import.json", {**ANALYST, "X-Client-Version": "3.3.0"}, 200),
        ("GET", "/api/identity", {**ELECTRON, "X-Client-Version": "3.2.1"}, 426),
        ("GET", "/api/identity", ELECTRON, 200),
        ("GET", "/api/identity", PWA, 200),
    ],
)
def test_cutover_gate(monkeypatch, method, path, headers, expected):
    monkeypatch.setenv("HARD_CRM_MIN_DESKTOP_VERSION", "3.3.0")
    assert asyncio.run(request(method, path, headers)) == expected


def test_legacy_tauri_can_work_before_cutover(monkeypatch):
    monkeypatch.delenv("HARD_CRM_MIN_DESKTOP_VERSION", raising=False)
    assert asyncio.run(request("GET", "/api/identity", TAURI_MANAGER)) == 200


async def request(method, path, headers):
    app = web.Application()
    install(app)

    async def work(_):
        return web.Response()

    app.router.add_route("*", "/{path:.*}", work)
    async with TestClient(TestServer(app)) as client:
        response = await client.request(method, path, headers=headers)
        return response.status
