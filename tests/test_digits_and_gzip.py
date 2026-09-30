"""Две правки из опыта быстрого обмена (30.09.2026), взятые в main.

1. Строка из цифр, которую Excel при вводе испортил бы («0012345» → 12345,
   больше 15 знаков — потеря точности), пишется текстом: иначе в книге
   остаётся искажённое значение, а повторная сверка плана падает уже после
   записи.
2. Крупное тело запроса пульта едет в gzip: реестр на 50 тыс. строк — 45–57 МБ
   JSON, по туннелю дважды (предпросмотр и применение); сжатый — 3–6 МБ.
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pult"))

import enrich  # noqa: E402
from pult.core import api  # noqa: E402


@pytest.mark.parametrize("v,lossy", [
    ("0012345", True), ("0", False), ("12345", False), ("8600123412341234", True),
    ("123456789012345", False), (" 007 ", True), ("12 345", False), ("٠١٢", False), (12, False)])
def test_digits_that_excel_would_mangle_are_written_as_text(v, lossy):
    assert enrich.excel_digits_lossy(v) is lossy
    if isinstance(v, str):
        assert enrich._excel_safe(v).startswith("'") is (lossy or enrich.excel_formula_like(v))


class _Resp:
    def __init__(self, b):
        self.b, self.headers = b, {}

    def read(self):
        return self.b

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_large_bodies_go_gzipped_small_ones_plain():
    seen = []

    def opener(req, **kw):
        seen.append(req)
        return _Resp(b'{"ok": true}')
    big = {"sheets": [{"name": "Портфели", "grid": [["Иванов"] * 40] * 400}]}
    api.request("https://h", "POST", "/admin/import/preview.json", big, opener=opener)
    api.request("https://h", "POST", "/admin/period", {"from": ""}, opener=opener)
    assert seen[0].get_header("Content-encoding") == "gzip"
    assert json.loads(gzip.decompress(seen[0].data)) == big
    assert seen[1].get_header("Content-encoding") is None
    assert json.loads(seen[1].data) == {"from": ""}


def test_server_unpacks_gzip_body_and_limits_the_unpacked_size():
    """aiohttp распаковывает тело сам и меряет client_max_size по распакованному."""
    import asyncio
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    async def h(req):
        return web.json_response({"n": len((await req.json())["x"])})

    async def scenario():
        app = web.Application(client_max_size=1024 * 1024)
        app.router.add_post("/p", h)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            out = []
            for n in (1000, 400_000):
                body = gzip.compress(json.dumps({"x": ["абв"] * n}).encode())
                r = await client.post("/p", data=body, headers={
                    "Content-Type": "application/json", "Content-Encoding": "gzip"})
                out.append(r.status)
            return out
        finally:
            await client.close()
    assert asyncio.run(scenario()) == [200, 413]
