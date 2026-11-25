import asyncio
import pytest
from crm import runtime,shared_auth,storage,assignments,primary_fence
from crm.contract import CRMError
from test_crm_core import crm_world
from test_crm_primary_fence import setup
from test_crm_assignments import apply,preview


def test_restart_finishes_job_but_live_revocation_stops_each_pack(crm_world,monkeypatch):
    w,_=setup(crm_world,monkeypatch);p=w['managers'][0]
    primary_fence.activate(w['contracts']);monkeypatch.setattr(assignments,'MAX_SLOTS',1)
    active=[True];calls=[]
    async def verify(token):
        calls.append(token)
        if not active[0]:raise shared_auth.fault('manager_required',403)
        return dict(uid=p.actor_uid,device_id=51,name='Synthetic',role='manager')
    def service():return shared_auth.Sessions(b'k'*32,verify,resolve=lambda _:p,audit=lambda *_:None,background_store=runtime.remember)
    token='synthetic-authority-only-for-test-0123456789'
    issued=asyncio.run(service().exchange(token))
    result,_=apply(w,preview(w))
    with storage.connect() as c:
        sealed=c.execute('SELECT sealed FROM crm_background_credentials').fetchone()[0]
        assert token not in sealed
    with pytest.raises(CRMError):asyncio.run(service().background_principal(issued['token'][5:]))
    with pytest.raises(CRMError):service().open('crm1.'+sealed.rstrip('='))
    restarted=service()
    active[0]=False
    assert asyncio.run(runtime.tick(restarted))['denied_accounts']==1
    assert assignments.job(p,job_id=result['job_id'])['applied_slots']==0
    active[0]=True
    asyncio.run(runtime.tick(restarted))
    assert assignments.job(p,job_id=result['job_id'])['applied_slots']==1
    asyncio.run(runtime.tick(service()))
    assert assignments.job(p,job_id=result['job_id'])['state']=='completed'
    assert len(calls)>=4
    with storage.connect() as c:assert c.execute('SELECT ok FROM crm_runtime_status').fetchone()[0]==1


def test_invalid_primary_is_visible_in_service_status(crm_world,monkeypatch):
    import sqlite3
    w,path=setup(crm_world,monkeypatch);primary_fence.activate(w['contracts'])
    with sqlite3.connect(path) as c:c.execute('DROP TRIGGER crm_primary_guard_identity')
    asyncio.run(runtime.tick(None))
    with storage.connect() as c:
        assert tuple(c.execute('SELECT ok,code FROM crm_runtime_status').fetchone())==(0,'primary_fence_unavailable')


def test_report_uses_fresh_team_at_next_slot_without_polling_idle_credentials(crm_world,monkeypatch):
    from dataclasses import replace
    from datetime import datetime,timedelta,timezone
    import json
    from crm import manager_reports,three_hour
    w,_=setup(crm_world,monkeypatch);primary_fence.activate(w['contracts']);p=w['managers'][0]
    manager_reports.read(p)
    assert runtime.credentials()==[]
    future=(datetime.now(timezone.utc)+timedelta(hours=4)).isoformat()
    monkeypatch.setattr(manager_reports,'now',lambda:future);monkeypatch.setattr(three_hour,'now',lambda:future)
    fresh=replace(p,employee_scope=p.employee_scope|{w['target']})
    assert manager_reports.tick({p.actor_uid:fresh})['completed']==1
    with storage.connect() as c:
        authority=json.loads(c.execute("SELECT authority_json FROM crm_background_authorities WHERE kind='report'").fetchone()[0])
        assert w['target'] in authority['employee_scope']
        assert manager_reports.pending(c)==[]
