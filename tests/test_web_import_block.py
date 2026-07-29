"""Blocked registry imports must be side-effect free at the HTTP boundary."""

import asyncio
from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

import db
import scoring
import web


def test_blocked_imports_return_409_before_geocoding(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
    geocode_calls = []

    blocked_grid = {
        "blocked": True,
        "error": "duplicate_contract_conflict",
        "duplicate_conflicts": [{"contract_id": "DUP-2"}],
    }
    monkeypatch.setattr(db, "pick_grid", lambda sheets: sheets[0]["grid"])
    monkeypatch.setattr(
        db,
        "import_grid",
        lambda grid, *, source_kind, source_proof, require_source_proof: (
            blocked_grid.copy()
        ),
    )
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
                "/admin/import?source_kind=published_master",
                data=form,
                headers=headers,
            )
            assert xlsx_response.status == 409
            assert (await xlsx_response.json())["blocked"] is True

            json_response = await client.post(
                "/admin/import.json",
                json={
                    "data_exchange_contract": web.DATA_EXCHANGE_CONTRACT,
                    "source_kind": "published_master",
                    "source_proof": {"test": "proof"},
                    "sheets": [
                        {
                            "name": "Портфели",
                            "grid": [["contract_id"], ["DUP-2"]],
                        }
                    ],
                },
                headers=headers,
            )
            assert json_response.status == 409
            assert (await json_response.json()) == blocked_grid
        finally:
            await client.close()

    asyncio.run(scenario())
    assert geocode_calls == []


def test_consumed_import_replay_never_runs_post_import_mutator(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
    monkeypatch.setattr(db, "pick_grid", lambda sheets: sheets[0]["grid"])
    monkeypatch.setattr(
        db,
        "import_grid",
        lambda grid, *, source_kind, source_proof, require_source_proof: {
            "blocked": False,
            "idempotent_replay": True,
            "source_proof_digest": "a" * 64,
            "total_rows": 1,
            "inserted": 0,
            "updated": 0,
        },
    )

    def forbidden_geocode():
        raise AssertionError("consumed replay must be completely write-free")

    monkeypatch.setattr(scoring, "geocode_city_centroids", forbidden_geocode)

    async def scenario():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            response = await client.post(
                "/admin/import.json",
                json={
                    "data_exchange_contract": web.DATA_EXCHANGE_CONTRACT,
                    "source_kind": "published_master",
                    "source_proof": {"test": "proof"},
                    "sheets": [{"name": "Портфели", "grid": [["id"]]}],
                },
                headers={"X-Admin-Key": "test-admin-key"},
            )
            assert response.status == 200
            assert (await response.json())["idempotent_replay"] is True
        finally:
            await client.close()

    asyncio.run(scenario())


def test_legacy_admin_reset_cannot_bypass_clearance(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")

    def forbidden_reset():
        raise AssertionError("legacy reset must not mutate the database")

    monkeypatch.setattr(db, "wipe_registry", forbidden_reset)
    monkeypatch.setattr(db, "reset_visits", forbidden_reset)

    async def scenario():
        client = TestClient(TestServer(web.build_app(None)))
        await client.start_server()
        try:
            for scope in ("all", "visits"):
                response = await client.post(
                    "/admin/reset",
                    json={"scope": scope},
                    headers={"X-Admin-Key": "test-admin-key"},
                )
                assert response.status == 409
                payload = await response.json()
                assert payload["blocked"] is True
                assert "snapshot" in payload["error"]
                assert "clearance" in payload["error"]
        finally:
            await client.close()

    asyncio.run(scenario())
