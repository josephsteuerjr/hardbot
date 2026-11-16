"""Persistent service loop: revalidate the original account, then do bounded work."""
import asyncio
import contextlib
from . import manager_reports, primary_fence, storage
from .contract import CRMError, canonical, now


def remember(actor_uid,sealed):
    with storage.connect() as conn:
        conn.execute('INSERT INTO crm_background_credentials VALUES(?,?,?) ON CONFLICT(actor_uid) '
            'DO UPDATE SET sealed=excluded.sealed,updated_at=excluded.updated_at',(actor_uid,sealed,now()))


def credentials():
    with storage.connect(readonly=True) as conn:
        actors=sorted({r['actor_uid'] for r in manager_reports.pending(conn)})
        result=[]
        for start in range(0,len(actors),200):
            batch=actors[start:start+200]
            result.extend(dict(r) for r in conn.execute('SELECT actor_uid,sealed FROM crm_background_credentials WHERE actor_uid IN ('+','.join('?' for _ in batch)+')',batch))
        return result


def status(ok,code,detail):
    with storage.connect() as conn:
        conn.execute('INSERT INTO crm_runtime_status VALUES(?,?,?,?,?) ON CONFLICT(worker) DO UPDATE SET '
            'heartbeat_at=excluded.heartbeat_at,ok=excluded.ok,code=excluded.code,detail_json=excluded.detail_json',
            ('scheduler',now(),int(ok),code,canonical(detail)))


async def tick(sessions):
    verified={};denied=[];slots=asyncio.Semaphore(4)
    async def verify(item):
        async with slots:
            try:verified[item['actor_uid']]=await sessions.background_principal(item['sealed'])
            except CRMError:denied.append(item['actor_uid'])
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


async def serve(sessions,interval=2):
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
