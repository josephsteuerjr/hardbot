"""Real mobile BFF and compiled PWA: understandable connection states."""
import asyncio
import json
import os
from pathlib import Path
import sys

from aiohttp.test_utils import TestServer
from playwright.sync_api import sync_playwright

from crm.mobile_backend import SyntheticBackend
from crm import mobile_server


def browser_checks(url, qa):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 390, 'height': 844})
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(url)
        page.locator('[data-mobile-role="employee"]').click()
        page.locator('[data-person]').first.wait_for()
        page.evaluate("document.querySelector('[data-section=connection]').click()")
        page.wait_for_timeout(1000)
        page.screenshot(path=str(qa / 'connection-debug.png'), full_page=True)
        page.locator('.mobile-connection-status strong').filter(has_text='Связь есть').wait_for(timeout=5000)
        assert page.locator('.mobile-session-expiry').count() == 0
        assert 'Неподтверждённых отправок нет' in page.locator('.mobile-queue-summary').inner_text()
        page.wait_for_function('navigator.serviceWorker && navigator.serviceWorker.controller')
        page.screenshot(path=str(qa / 'connection-online.png'), full_page=True)

        context.set_offline(True)
        page.reload()
        page.wait_for_timeout(1000)
        page.screenshot(path=str(qa / 'connection-offline-debug.png'), full_page=True)
        page.locator('.mobile-connection-status strong').filter(has_text='Нет связи').wait_for(timeout=5000)
        assert 'сохранить на телефоне' in page.locator('.mobile-connection-status').inner_text()
        page.screenshot(path=str(qa / 'connection-offline.png'), full_page=True)
        context.set_offline(False)

        page.route('**/mobile/boot', lambda route: route.fulfill(status=503,
            content_type='application/json', body=json.dumps({'error': 'upstream_unavailable', 'text': 'temporary'})))
        page.reload()
        page.locator('.mobile-connection-status strong').filter(has_text='Сервер временно недоступен').wait_for()
        assert 'Не повторяйте отправку вслепую' in page.locator('.mobile-connection-status').inner_text()
        page.unroute('**/mobile/boot')

        page.route('**/mobile/boot', lambda route: route.fulfill(status=401,
            content_type='application/json', body=json.dumps({'error': 'session', 'text': 'sign in'})))
        page.reload()
        page.locator('.mobile-connection-status strong').filter(has_text='Нужно войти снова').wait_for()
        assert page.locator('#mobile-login').is_visible()
        assert 'Черновики и очередь сохранены' in page.locator('.mobile-connection-status').inner_text()
        page.unroute('**/mobile/boot')
        page.reload()
        page.locator('.mobile-connection-status strong').filter(has_text='Связь есть').wait_for()
        assert not errors, errors
        browser.close()
        return {'passed': True, 'errors': errors,
                'checks': ['online sign-in status and queue summary', 'cold offline status',
                           'server 503 preserves work guidance', '401 requires same-user sign-in',
                           'recovery after network restoration']}


async def main(qa):
    backend = SyntheticBackend(qa / 'synthetic')
    mobile_server.ASSETS = qa / 'web'
    server = TestServer(mobile_server.create_app(backend, qa / 'data', key=os.urandom(32)))
    await server.start_server()
    try:
        return await asyncio.to_thread(browser_checks, str(server.make_url('/mobile/')), qa)
    finally:
        await server.close()
        assert backend.client.closed, 'Synthetic HTTP client was not closed'


if __name__ == '__main__':
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    result = asyncio.run(main(output))
    (output / 'checks.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(result, ensure_ascii=False))
