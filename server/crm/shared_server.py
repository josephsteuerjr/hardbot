"""Canonical PostgreSQL API and scheduler, with explicitly configured primary projection."""
import asyncio
import contextlib
import os
from pathlib import Path

import aiohttp
from aiohttp import web
from . import http, storage, runtime
from .contract import CRMError, instant, now
from .shared_auth import Authority, Sessions, NAMESPACE


def create_app(*, sessions=None):
    if not storage.postgres_enabled() and sessions is None:
        raise RuntimeError('Shared CRM requires explicit PostgreSQL configuration')
    app = web.Application(client_max_size=128 * 1024 * 1024)
    managed_runtime=sessions is None

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
            sessions = Sessions(key, Authority(client),background_store=runtime.remember)
            worker=asyncio.create_task(runtime.serve(sessions),name='crm-background')
            try:yield
            finally:
                worker.cancel()
                with contextlib.suppress(asyncio.CancelledError):await worker
    app.cleanup_ctx.append(lifecycle)

    async def authenticate(request):
        return await sessions.authenticate(request)

    async def exchange(request):
        # Working credentials are admitted only here. No payload-provided
        # role, subject, server URL or local settings can grant access.
        if request.query_string or request.content_length:
            raise CRMError('Подключение не принимает параметры личности.')
        entries = [(key, request.headers.get(key, '').strip()) for key in
                   ('X-Pult-Token', 'X-App-Token', 'X-Device-Token')]
        entries = [(key, token) for key, token in entries if token]
        if len(entries) != 1:
            raise CRMError('Требуется один действующий вход.', 'pilot_session_invalid', 401)
        header, credential = entries[0]
        kind = ('employee' if header == 'X-App-Token' else 'analyst-device' if header == 'X-Device-Token'
                else 'analyst' if request.headers.get('X-CRM-Entry') == 'analyst' else 'manager')
        result = await sessions.exchange(credential, kind=kind,
                                        device_fingerprint=request.headers.get('X-Device-Id', ''))
        return web.json_response({'ok': True, **result})

    async def health(request):
        from .schema import VERSION
        def probe():
            with storage.connect(readonly=True) as conn:
                meta={r[0]:r[1] for r in conn.execute("SELECT key,value FROM crm_meta WHERE key IN ('deployment_namespace','schema_version')")}
                worker=conn.execute("SELECT * FROM crm_runtime_status WHERE worker='scheduler'").fetchone()
                return meta,dict(worker) if worker else None
        try:
            meta,worker = await asyncio.to_thread(probe)
            if meta.get('deployment_namespace') != NAMESPACE or meta.get('schema_version') != str(VERSION):
                raise ValueError()
            if managed_runtime and (not worker or not worker['ok'] or (instant(now())-instant(worker['heartbeat_at'])).total_seconds()>90):
                raise ValueError()
            return web.json_response({'ok': True, 'namespace': NAMESPACE, 'storage': 'postgresql','schema_version':VERSION,
                'scheduler': 'running' if worker and worker['ok'] else 'injected',
                'authority_cache': sessions.authority_metrics()})
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
