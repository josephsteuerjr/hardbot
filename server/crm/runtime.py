"""Persistent service loop: revalidate the original account, then do bounded work."""
import asyncio
import contextlib
import json
from . import manager_reports, primary_fence, report_presets, storage
from .contract import CRMError, canonical, now


def remember(actor_uid,sealed,grant_id=None):
    with storage.connect() as conn:
        storage.begin(conn, [f'background-credential:{actor_uid}'])
        row=conn.execute('SELECT sealed FROM crm_background_credentials WHERE actor_uid=?',(actor_uid,)).fetchone()
        slots={}
        if row:
            try:
                decoded=json.loads(row['sealed'])
                if isinstance(decoded,dict):slots=decoded
            except (ValueError,TypeError):
                slots['']=row['sealed']
        slots[grant_id or '']=sealed
        # Keep the pre-grant format readable by the previous report worker.
        payload=slots[''] if slots.keys()=={''} else json.dumps(slots,separators=(',',':'))
        conn.execute('INSERT INTO crm_background_credentials VALUES(?,?,?) ON CONFLICT(actor_uid) '
            'DO UPDATE SET sealed=excluded.sealed,updated_at=excluded.updated_at',
            (actor_uid,payload,now()))


def credentials():
    with storage.connect(readonly=True) as conn:
        actors=sorted({r['actor_uid'] for r in manager_reports.pending(conn)})
        result=[]
        for start in range(0,len(actors),200):
            batch=actors[start:start+200]
            rows=conn.execute('SELECT actor_uid,sealed FROM crm_background_credentials WHERE actor_uid IN ('+','.join('?' for _ in batch)+')',batch)
            for row in rows:
                try:
                    slots=json.loads(row['sealed'])
                    if not isinstance(slots,dict):raise ValueError()
                except (ValueError,TypeError):
                    slots={'':row['sealed']}
                for grant_id,sealed in slots.items():
                    result.append(dict(actor_uid=row['actor_uid'],grant_id=grant_id or None,sealed=sealed))
        return result


def status(ok,code,detail,worker='scheduler'):
    with storage.connect() as conn:
        conn.execute('INSERT INTO crm_runtime_status VALUES(?,?,?,?,?) ON CONFLICT(worker) DO UPDATE SET '
            'heartbeat_at=excluded.heartbeat_at,ok=excluded.ok,code=excluded.code,detail_json=excluded.detail_json',
            (worker,now(),int(ok),code,canonical(detail)))


async def tick(sessions):
    verified={};denied=[];slots=asyncio.Semaphore(4)
    async def verify(item):
        async with slots:
            try:
                principal=await sessions.background_principal(item['sealed'])
                verified[(item['actor_uid'],principal.grant_id)]=principal
            except CRMError:denied.append((item['actor_uid'],item.get('grant_id')))
    await asyncio.gather(*(verify(item) for item in await asyncio.to_thread(credentials)))
    work=await asyncio.to_thread(manager_reports.tick,verified)
    projection=await asyncio.to_thread(primary_fence.project_pending)
    def mode():
        with storage.connect(readonly=True) as conn:
            row=conn.execute("SELECT value FROM crm_meta WHERE key='distribution_mode'").fetchone()
            return row[0] if row else None
    cutover=await asyncio.to_thread(mode)=='cutover'
    conflicts=await asyncio.to_thread(primary_fence.sync_source_conflicts) if cutover and projection['ready'] else 0
    result=dict(work=work or {},projection=projection,denied_accounts=len(denied),source_conflicts=conflicts)
    ok=not cutover or projection['ready']
    await asyncio.to_thread(status,ok,'' if ok else 'primary_fence_unavailable',result)
    return result


async def serve_operations(sessions,interval):
    while True:
        busy=False
        try:
            result=await tick(sessions)
            busy=bool(result['work'].get('completed') or result['projection'].get('projected') or result['source_conflicts'])
        except asyncio.CancelledError:raise
        except Exception as exc:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(status,False,getattr(exc,'code',type(exc).__name__),{})
        await asyncio.sleep(0.05 if busy else interval)


async def serve_reports():
    while True:
        try:
            result=await asyncio.to_thread(report_presets.schedule)
            await asyncio.to_thread(status,True,'',result,'report-scheduler')
        except asyncio.CancelledError:raise
        except Exception as exc:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(status,False,getattr(exc,'code',type(exc).__name__),{},'report-scheduler')
        await asyncio.sleep(60)


async def serve(sessions,interval=2):
    reports=asyncio.create_task(serve_reports())
    try:
        await serve_operations(sessions,interval)
    finally:
        reports.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reports
