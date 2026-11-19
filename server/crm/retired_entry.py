"""Route already-installed temporary shortcuts into the ordinary CRM."""
from hashlib import sha256
from pathlib import Path

from aiohttp import web

PREFIX = '/admin/analyst/'
WORKER = (Path(__file__).resolve().parents[1] / 'miniapp' / 'crm-retired.js').read_text(encoding='utf-8')
VERSION = sha256(WORKER.encode()).hexdigest()[:16]
HEADERS = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
           'X-Content-Type-Options': 'nosniff'}


def install(app):
    async def old_entry(request):
        asset = request.match_info.get('asset', '')
        if asset == 'BUILD.json':
            return web.json_response({'version': VERSION}, headers=HEADERS)
        if asset == 'sw.js':
            return web.Response(text=WORKER.replace('__VERSION__', VERSION), content_type='text/javascript',
                                headers={**HEADERS, 'Service-Worker-Allowed': PREFIX})
        if asset == 'continue.js':
            return web.Response(text="location.replace('/mobile/');", content_type='text/javascript', headers=HEADERS)
        if asset:
            return web.json_response({'error': 'moved', 'url': '/mobile/'}, status=410, headers=HEADERS)
        response = web.Response(
            text='<!doctype html><html lang="ru"><meta charset="utf-8">'
                 '<meta name="viewport" content="width=device-width, initial-scale=1">'
                 '<title>Взыскание СНГ</title>'
                 '<a href="/mobile/">Открыть Взыскание СНГ</a>'
                 '<script src="/admin/analyst/continue.js" defer></script></html>',
            content_type='text/html',
            headers={**HEADERS, 'Content-Security-Policy': "default-src 'none'; script-src 'self'; frame-ancestors 'none'"},
        )
        response.del_cookie('__Secure-rimborso-analyst', path=PREFIX, secure=True, httponly=True, samesite='Strict')
        return response

    async def old_address(request):
        raise web.HTTPPermanentRedirect(PREFIX)

    app.router.add_get(PREFIX.rstrip('/'), old_address)
    app.router.add_get(PREFIX + '{asset:.*}', old_entry)
