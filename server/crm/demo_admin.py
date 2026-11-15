"""Real primary administrator API, restricted to the marked synthetic DB."""
import hashlib
import secrets
import threading
from pathlib import Path
import accounts
import db
from .contract import CRMError, now
_lock=threading.Lock()
_endpoint=None

def endpoint(p):
    global _endpoint
    if p.role!='analyst' or p.actor_uid!=91005:raise CRMError('Требуется проверочный вход Аналитика.','forbidden',403)
    with _lock:
        if _endpoint:return dict(_endpoint)
        with db.connect() as conn:
            if not conn.execute("SELECT 1 FROM crm_meta WHERE key='synthetic_candidate'").fetchone():
                raise CRMError('Проверочный административный контур недоступен.','forbidden',403)
        first=secrets.token_urlsafe(24)+'1aA';second=secrets.token_urlsafe(24)+'2bB';username='crm-demo-analyst'
        with db.connect() as conn:exists=conn.execute('SELECT 1 FROM accounts WHERE username=?',(username,)).fetchone()
        if exists:accounts.set_password(username,first,by='synthetic-fixture')
        else:
            result=accounts.create_account('Тестовый аналитик',username,first,role='analyst',telegram_id=91005,by='synthetic-fixture')
            if not result['ok']:raise RuntimeError('Demo administrator initialization failed')
        accounts.approve_account(username,'synthetic-fixture')
        accounts.change_password(username,first,second)
        db.set_manager(91005,'Тестовый аналитик',[],[],role='manager')
        with db.connect() as conn:
            conn.execute("UPDATE managers SET role='analyst' WHERE telegram_id=91005")
            aid=conn.execute('SELECT id FROM accounts WHERE username=?',(username,)).fetchone()[0]
            conn.execute("INSERT OR IGNORE INTO account_admin_grants(account_id,principal,granted_by,operation_id,at_utc,next_pc_allowed) VALUES(?,'synthetic:analyst','synthetic-fixture','crm-demo-analyst',?,1)",(aid,now()))
            conn.execute('UPDATE account_admin_grants SET next_pc_allowed=1 WHERE account_id=?',(aid,));conn.commit()
        import staff_roster
        roster=staff_roster.preview([dict(employee='Тестовый сотрудник',office='Душанбе',direction='tj',products=['hard','soft'],manager_uid=91002),
            dict(employee='Сотрудник без аккаунта',office='Ташкент',direction='uz',products=['hard'],manager_uid=91002)])
        staff_roster.apply(roster['items'],roster['sha256'],by='synthetic-fixture')
        import asyncio
        import web as primary
        from aiohttp import web
        primary.DEV=False
        ready=threading.Event();result={}
        def serve():
            loop=asyncio.new_event_loop();asyncio.set_event_loop(loop)
            async def start():
                runner=web.AppRunner(primary.build_app(None),access_log=None);await runner.setup()
                site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
                result['url']='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])
            try:loop.run_until_complete(start())
            except Exception as exc:result['error']=type(exc).__name__
            finally:ready.set()
            if 'url' in result:loop.run_forever()
        threading.Thread(target=serve,name='crm-demo-primary',daemon=True).start()
        if not ready.wait(20) or 'url' not in result:raise RuntimeError('Demo primary did not start')
        from pult.core.account_session import login
        try:session=login(result['url'],username,second,Path(db.DB_PATH).with_name('demo-admin-settings.json'))
        except RuntimeError as exc:
            import json
            try:details=json.loads(exc.__cause__.read())
            except Exception:details={}
            raise RuntimeError('Demo administrator login: '+str(details.get('error') or details.get('action') or exc)) from exc
        if session.get('action')!='ok':raise RuntimeError('Demo administrator was not approved')
        token='account:'+session['token']
        _endpoint=dict(url=result['url'],token=token,fingerprint=hashlib.sha256(token.encode()).hexdigest())
        return dict(_endpoint)
