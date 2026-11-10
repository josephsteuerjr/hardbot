"""Separate PostgreSQL pilot service; no bot, legacy writers or production DB."""
import asyncio
import os
from pathlib import Path

import aiohttp
from aiohttp import web
from . import http, storage
from .contract import CRMError
from .shared_auth import Authority, Sessions, NAMESPACE


def create_app(*, sessions=None):
    if not storage.postgres_enabled() and sessions is None:
        raise RuntimeError('Shared CRM requires explicit isolated PostgreSQL configuration')
    app = web.Application(client_max_size=128 * 1024 * 1024)

    @web.middleware
    async def headers(request, handler):
        try:
            response = await handler(request)
        except CRMError as exc:
            response = web.json_response(dict(error=exc.code, text=str(exc)), status=exc.status)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-CRM-Namespace'] = NAMESPACE
        return response
    app.middlewares.append(headers)

    async def lifecycle(app):
        nonlocal sessions
        if sessions is not None:
            yield
            return
        key = Path(os.environ['HARD_CRM_SESSION_KEY_FILE']).read_bytes()
        async with aiohttp.ClientSession(trust_env=False) as client:
            sessions = Sessions(key, Authority(client))
            yield
    app.cleanup_ctx.append(lifecycle)

    async def authenticate(request):
        return await sessions.authenticate(request)

    async def exchange(request):
        # Working credentials are admitted only here. No payload-provided
        # role, subject, server URL or local settings can grant access.
        if request.query_string or request.content_length:
            raise CRMError('Подключение не принимает параметры личности.')
        result = await sessions.exchange(request.headers.get('X-Pult-Token', ''))
        return web.json_response({'ok': True, **result})

    async def health(request):
        def probe():
            with storage.connect(readonly=True) as conn:
                return conn.execute("SELECT value FROM crm_meta WHERE key='deployment_namespace'").fetchone()
        try:
            row = await asyncio.to_thread(probe)
            if not row or row[0] != NAMESPACE:
                raise ValueError()
            return web.json_response({'ok': True, 'namespace': NAMESPACE, 'storage': 'postgresql'})
        except Exception:
            return web.json_response({'ok': False}, status=503)

    app.router.add_post('/auth/exchange', exchange)
    app.router.add_get('/health', health)
    http.install(app, authenticate=authenticate)
    return app


if __name__ == '__main__':
    # Access logs can expose request metadata. Business audit is deliberately
    # scoped and stored in PostgreSQL; credentials are never logged.
    web.run_app(create_app(), host='0.0.0.0', port=8080, access_log=None,
                print=None, shutdown_timeout=20)
