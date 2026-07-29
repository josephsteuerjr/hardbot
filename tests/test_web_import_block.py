"""Blocked registry imports must be side-effect free at the HTTP boundary."""

import asyncio
from pathlib import Path

from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

import db
import scoring
import web


def test_blocked_imports_return_409_before_geocoding(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
    geocode_calls = []

    def blocked_xlsx(path):
        assert Path(path).is_file()
        return {
            "blocked": True,
            "error": "duplicate_contract_conflict",
            "duplicate_conflicts": [{"contract_id": "DUP-1"}],
        }

    blocked_grid = {
        "blocked": True,
        "error": "duplicate_contract_conflict",
        "duplicate_conflicts": [{"contract_id": "DUP-2"}],
    }
    monkeypatch.setattr(db, "import_xlsx", blocked_xlsx)
    monkeypatch.setattr(db, "pick_grid", lambda sheets: sheets[0])
    monkeypatch.setattr(db, "import_grid", lambda grid: blocked_grid.copy())
    monkeypatch.setattr(
        scoring,
        "geocode_city_centroids",
        lambda: geocode_calls.append("called"),
    )

    async def scenario():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            headers = {"X-Admin-Key": "test-admin-key"}

            form = FormData()
            form.add_field(
                "file",
                b"synthetic workbook",
                filename="blocked.xlsx",
                content_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
            )
            xlsx_response = await client.post(
                "/admin/import", data=form, headers=headers)
            assert xlsx_response.status == 409
            assert (await xlsx_response.json())["blocked"] is True

            json_response = await client.post(
                "/admin/import.json",
                json={"sheets": [[["contract_id"], ["DUP-2"]]]},
                headers=headers,
            )
            assert json_response.status == 409
            assert (await json_response.json()) == blocked_grid
        finally:
            await client.close()

    asyncio.run(scenario())
    assert geocode_calls == []
