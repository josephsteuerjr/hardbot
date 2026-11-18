"""Retire old installed desktop protocols at the CRM cutover."""
import os
import re

from aiohttp import web


def version(value):
    match = re.fullmatch(r'([0-9]{1,4})\.([0-9]{1,4})\.([0-9]{1,4})', value)
    return tuple(map(int, match.groups())) if match else None


def install(app):
    minimum = os.getenv('HARD_CRM_MIN_DESKTOP_VERSION', '').strip()
    if not minimum:
        return
    limit = version(minimum)
    if limit is None:
        raise ValueError('HARD_CRM_MIN_DESKTOP_VERSION requires a stable version')

    @web.middleware
    async def require_crm(request, handler):
        headers = request.headers
        protected = request.path.startswith(('/api/', '/admin/'))
        legacy_tauri = (
            headers.get('X-Client-Runtime') == 'tauri'
            and headers.get('X-Data-Protocol') == '10'
        )
        desktop = 'Electron/' in headers.get('User-Agent', '') or (
            headers.get('X-Client-Runtime') == 'desktop'
            and headers.get('X-Work-Product') == 'analyst'
        )
        if protected and (desktop or legacy_tauri) and request.method != 'OPTIONS':
            current = version(headers.get('X-Client-Version', ''))
            if legacy_tauri or current is None or current < limit:
                write = request.method not in ('GET', 'HEAD')
                return web.json_response({
                    'ok': False,
                    'error': 'Установите обновление программы. Откройте проверку обновлений в приложении.',
                    'minimum_version': minimum,
                }, status=503 if write else 426, headers={'Retry-After': '300'} if write else None)
        return await handler(request)

    app.middlewares.insert(0, require_crm)
