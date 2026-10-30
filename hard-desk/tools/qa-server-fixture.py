"""Loopback-only HTTP/SQLite fixture for desktop acceptance; all data is synthetic."""
import asyncio
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
run_id = re.sub(r'[^a-zA-Z0-9_-]', '_', os.environ.get('HARD_QA_RELEASE_ROLE', 'office'))
base = Path(os.environ.get('HARD_QA_ROOT', ROOT / '_local/desktop-qa'))
data = Path(os.environ.get('HARD_QA_FIXTURE_DIR', base / ('server-' + run_id))).resolve()
assert data.is_relative_to((ROOT / '_local').resolve()), 'Fixture must stay in _local'
data.mkdir(parents=True, exist_ok=True)
os.environ['APP_DATA'] = str(data)
os.environ['ADMIN_ID'] = '700700700'
os.environ['DEV'] = '0'
os.environ['MINIAPP_URL'] = 'https://synthetic.invalid'
sys.path.insert(0, str(ROOT / 'server'))
import db
import web
from aiohttp import web as aio

db.init_db()
metadata = data / 'identity.json'
if not metadata.exists():
    hard, soft = 'Синтетический Хард', 'Синтетический Софт'
    dual = os.environ.get('HARD_QA_DUAL_PRODUCT') == '1'
    with db.connect() as conn:
        for cid, client, responsible, responsible_soft, debt in [
            ('LIVE-001', 'Синтетический клиент А', hard, soft, '1000.25'),
            ('LIVE-002', 'Синтетический клиент А', hard, soft, '150.25'),
            ('LIVE-003', 'Синтетический клиент Б', hard, soft, '200'),
            ('FOREIGN-001', 'Чужой синтетический клиент', 'Другой сотрудник', 'Другой сотрудник', '999'),
        ]:
            conn.execute('''INSERT INTO clients(contract_id,client,birth_date,responsible,
                responsible_soft,country,debt_total,debt_today,partner,address,phone)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                (cid, client, '1990-01-01', responsible, responsible_soft, 'Таджикистан', debt, debt,
                 'Синтетический контрагент', 'Синтетический адрес', '000001'))
        conn.commit()
    tokens, phone_tokens, login_links = {}, {}, {}
    for kind, uid, employee in [('hard', 810101, hard), ('soft', 810102, soft)]:
        if dual and kind == 'hard':
            continue
        db.set_bind(uid, employee)
        code = db.create_app_link(uid)
        enrolled = db.enroll_agent_device(code, 'Synthetic QA desktop')
        assert enrolled
        name, uid, device_id, token = enrolled
        tokens[kind] = {'token': token, 'id': device_id, 'telegram_id': uid, 'employee': name}
        phone_tokens[kind] = db.redeem_app_link(code, agent='android')
    if dual:
        import staff_roster
        items = [{'employee': soft, 'office': 'Synthetic office', 'direction': 'uz',
                  'products': ['hard', 'soft'], 'product_names': {'hard': hard, 'soft': soft}}]
        preview = staff_roster.preview(items)
        assert staff_roster.apply(items, preview['sha256'], by='synthetic-fixture')['ok']
    for role, uid in [('manager', 810201), ('security', 810202)]:
        db.set_manager(uid, 'Synthetic ' + role, [], [hard, soft], role=role)
        code = db.create_app_link(uid)
        login_links[role] = code
        enrolled = db.enroll_manager_device(code, 'Synthetic QA desktop')
        assert enrolled
        uid, device_id, token = enrolled
        tokens[role] = {'token': token, 'id': device_id, 'telegram_id': uid, 'name': 'Synthetic ' + role, 'role': role}
        phone_tokens[role] = db.redeem_app_link(db.create_app_link(uid), agent='android')
        login_links[role] = db.create_app_link(uid)
    metadata.write_text(json.dumps({'tokens': tokens, 'phone_tokens': phone_tokens, 'login_links': login_links}, ensure_ascii=False), encoding='utf8')
config = json.loads(metadata.read_text(encoding='utf8'))

async def main():
    app = web.build_app(None)
    override = os.environ.get('HARD_QA_SHELL_OVERRIDE')
    if override:
        shell = Path(override).resolve()
        assert shell.is_relative_to((ROOT / '_local').resolve())
        @aio.middleware
        async def shell_override(request, handler):
            if shell.exists() and request.path in ('/', '/version.json'):
                text = shell.read_text('utf-8')
                if request.path == '/version.json':
                    return aio.json_response({'shellVersion': int(re.search(r'const SHELL_VERSION=(\d+);', text)[1])})
                return aio.Response(text=text, content_type='text/html', headers={'Cache-Control': 'no-store'})
            return await handler(request)
        app.middlewares.insert(0, shell_override)
    runner = aio.AppRunner(app)
    await runner.setup()
    site = aio.TCPSite(runner, '127.0.0.1', int(sys.argv[1]) if len(sys.argv) > 1 else 0)
    await site.start()
    config['port'] = site._server.sockets[0].getsockname()[1]
    prefix = 'OFFICE-QA ' if os.environ.get('HARD_QA_FIXTURE_DIR') else 'RELEASE-QA '
    print(prefix + json.dumps(config, ensure_ascii=False), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()

asyncio.run(main())
