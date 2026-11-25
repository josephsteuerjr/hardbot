"""Owned synthetic PostgreSQL HTTP fixture. Loopback only, explicit QA DSNs.

No working sessions or database can be selected by request. Run in a disposable
container and forward its loopback port over the already pinned SSH connection.
"""
import asyncio
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import uuid
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'server'),str(ROOT/'tests')]
from aiohttp import web
import psycopg
import pytest
import db
from crm import storage,schema,http,access
from crm.contract import CRMError
from test_crm_core import crm_world
from test_crm_assignments import allocation_world


def scoped(dsn,namespace):
    parts=urlsplit(dsn)
    query=[(k,v) for k,v in parse_qsl(parts.query) if k!='options']+[('options','-csearch_path='+namespace)]
    return urlunsplit((parts.scheme,parts.netloc,'/crm_qa',urlencode(query),''))


async def main():
    from psycopg.conninfo import conninfo_to_dict
    base=Path(os.environ['HARD_CRM_TEST_DSN_FILE']).read_text().strip()
    app_dsn=Path(os.environ['HARD_CRM_TEST_APP_DSN_FILE']).read_text().strip()
    info=conninfo_to_dict(base);assert info['dbname']=='crm_qa' and info['user']=='crm_qa'
    assert conninfo_to_dict(app_dsn)['user']=='crm_app'
    secret=Path(os.environ['HARD_CRM_QA_SECRET_FILE']).read_text().strip()
    assert re.fullmatch('[a-f0-9]{64}',secret)
    namespace='crm_test_'+uuid.uuid4().hex
    os.environ['HARD_CRM_SHARED']='0'
    monkey=pytest.MonkeyPatch();temp=Path(tempfile.mkdtemp(prefix='crm-manager-qa-'))
    fixture=crm_world.__wrapped__(temp,monkey)
    with db.connect() as local:
        snapshot=[]
        for row in local.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'crm_%' ORDER BY rowid"):
            name=row[0]
            if name=='crm_meta':continue
            columns=[r[1] for r in local.execute('PRAGMA table_info('+name+')')]
            values=[tuple(r) for r in local.execute('SELECT * FROM '+name)]
            if values:snapshot.append((name,columns,values))
    with psycopg.connect(base,autocommit=True) as c:c.execute('CREATE SCHEMA '+namespace)
    os.environ['HARD_CRM_SHARED']='1';os.environ.pop('HARD_CRM_DATABASE_URL_FILE',None)
    os.environ['HARD_CRM_DATABASE_URL']=scoped(base,namespace)
    os.environ['HARD_CRM_DOCUMENTS']=str(temp/'documents')
    try:
        with storage.connect() as c:
            schema.initialize(c)
            for name,columns,values in snapshot:
                c.executemany('INSERT INTO '+name+'('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',values)
        w=allocation_world(fixture)
        with storage.connect() as c:
            if os.environ.get('HARD_CRM_QA_SCALE')=='1':
                c.execute("INSERT INTO crm_people SELECT md5('windows-person-' || i::text)::uuid::text,'Scale ' || i::text,'synthetic-windows','2026-01-01' FROM generate_series(0,49999) i")
                c.execute("INSERT INTO crm_contracts SELECT md5('windows-contract-' || i::text)::uuid::text,md5('windows-person-' || i::text)::uuid::text,'scale-creditor','SCALE-' || i::text,NULL,'RUB','{\"debt_today\":\"1.25\"}','{\"debt_today\":\"1.25\"}','{}','{}','2026-01-01' FROM generate_series(0,49999) i")
                c.execute("INSERT INTO crm_managed_contracts SELECT 'team-A',contract_uuid,work FROM crm_contracts CROSS JOIN (VALUES('call'),('visit')) k(work) WHERE creditor_id='scale-creditor'")
                c.execute('ANALYZE crm_contracts');c.execute('ANALYZE crm_managed_contracts')
            c.execute('INSERT INTO crm_actor_links VALUES(?,?,?,?)',(500,w['target'],'fixture',1))
            c.execute('GRANT USAGE ON SCHEMA '+namespace+' TO crm_app')
            c.execute('GRANT SELECT ON ALL TABLES IN SCHEMA '+namespace+' TO crm_app')
            c.execute('GRANT UPDATE (name) ON crm_people TO crm_app')
            for table in ('crm_assignment_heads','crm_assignment_operations','crm_assignment_previews','crm_assignment_changes',
                'crm_task_grant_revocations','crm_assignment_jobs','crm_assignment_packs','crm_assignments','crm_events','crm_event_contracts',
                'crm_commands','crm_outbox','crm_audit','crm_entities','crm_delivery_targets','crm_clock_anchors','crm_active_intervals','crm_telemetry','crm_background_authorities','crm_report_snapshots'):
                c.execute('GRANT INSERT ON '+table+' TO crm_app')
            for table in ('crm_assignment_heads','crm_assignment_jobs','crm_assignment_packs','crm_assignments','crm_contracts','crm_entities','crm_outbox','crm_background_authorities'):
                c.execute('GRANT UPDATE ON '+table+' TO crm_app')
            c.execute('GRANT USAGE ON ALL SEQUENCES IN SCHEMA '+namespace+' TO crm_app')
        os.environ['HARD_CRM_DATABASE_URL']=scoped(app_dsn,namespace)
        actors={'m1':401,'m2':402,'a':101,'c':500}
        async def authenticate(request):
            if not secrets.compare_digest(request.headers.get('X-QA-Secret',''),secret):
                raise CRMError('Нет доступа к синтетическому стенду.','forbidden',403)
            key=request.headers.get('X-QA-Actor');uid=actors.get(key)
            if uid is None:raise CRMError('Неизвестный синтетический актор.','forbidden',403)
            def read():
                with storage.connect() as c:
                    names=[r[0] for r in c.execute('SELECT name FROM crm_employees')] if key.startswith('m') else None
                    return access.from_uid(c,uid,manager_names=names,manager_write=bool(names),common_read=True)
            return await asyncio.to_thread(read)
        app=web.Application();http.install(app,authenticate=authenticate)
        runner=web.AppRunner(app,access_log=None);await runner.setup()
        port=int(os.environ.get('HARD_CRM_QA_PORT','18087'))
        await web.TCPSite(runner,'127.0.0.1',port).start()
        result=Path(os.environ['HARD_CRM_QA_RESULT']);result.mkdir(exist_ok=True)
        (result/'server.json').write_text(json.dumps(dict(namespace=namespace,port=port,actors=actors,
            storage='postgresql',role='crm_app',synthetic=True,person=fixture['person'],contracts=fixture['contracts'],target=w['target'])),encoding='utf-8')
        stop=asyncio.Event()
        import signal
        for sig in (signal.SIGTERM,signal.SIGINT):asyncio.get_running_loop().add_signal_handler(sig,stop.set)
        await stop.wait();await runner.cleanup()
    finally:
        with psycopg.connect(base,autocommit=True) as c:
            assert re.fullmatch('crm_test_[a-f0-9]{32}',namespace)
            c.execute('DROP SCHEMA '+namespace+' CASCADE')


if __name__=='__main__':asyncio.run(main())
