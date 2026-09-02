"""Route-only HTTP process for no-restart production activation.

This module deliberately does not import ``app.py`` and therefore cannot start
Telegram polling.  It reuses Hardbot's normal authentication, database access,
country-safe OSRM selection and VROOM planning for just two GET endpoints.
"""

from __future__ import annotations

import os

from aiohttp import web as aiohttp_web

import web as hardbot_web


async def _health(_request: aiohttp_web.Request) -> aiohttp_web.Response:
    return aiohttp_web.json_response({"ok": True, "service": "hardbot2-routing"})


def build_app() -> aiohttp_web.Application:
    app = aiohttp_web.Application(middlewares=[hardbot_web._cors])
    app.router.add_get("/healthz", _health)
    app.router.add_get("/api/route", hardbot_web._route)
    app.router.add_get("/api/route_day", hardbot_web._route_day)
    return app


if __name__ == "__main__":
    aiohttp_web.run_app(
        build_app(),
        host="0.0.0.0",
        port=int(os.getenv("ROUTING_SIDECAR_PORT", "8082")),
    )
