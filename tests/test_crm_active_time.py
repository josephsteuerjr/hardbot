from datetime import timedelta
import uuid
import pytest
import db
from crm import active_time, store
from crm.contract import CRMError, instant, now
from test_crm_core import crm_world


def interval(w, *, start=0, end=30000, kind='active', anchor=None, installation='activity-qa-installation'):
    if anchor is None:
        anchor=store.anchor(w['member'],installation)['anchor_id']
        with db.connect() as conn:
            conn.execute('UPDATE crm_clock_anchors SET server_at=? WHERE anchor_id=?',
                         ((instant(now())-timedelta(seconds=120)).isoformat(),anchor));conn.commit()
    return dict(schema_version=1,operation_id=str(uuid.uuid4()),anchor_id=anchor,
                installation_id=installation,start_ms=start,end_ms=end,kind=kind)


def test_activity_replay_union_idle_and_separate_work(crm_world):
    w=crm_world;first=interval(w)
    result=active_time.ingest(w['member'],first)
    assert active_time.ingest(w['member'],first)==result
    second=interval(w,start=15000,end=45000,anchor=first['anchor_id'])
    active_time.ingest(w['member'],second)
    active_time.ingest(w['member'],interval(w,start=30000,end=60000,kind='idle',anchor=first['anchor_id']))
    with db.connect() as conn:
        saved = conn.execute("SELECT server_at FROM crm_clock_anchors WHERE anchor_id=?", (first["anchor_id"],)).fetchone()[0]
    zone = __import__('zoneinfo').ZoneInfo(w['member'].timezone)
    since = instant(saved).astimezone(zone).date().isoformat()
    until = (instant(saved) + timedelta(seconds=60)).astimezone(zone).date().isoformat()
    result=active_time.summary(w['member'],since,until)
    assert result['active_ms']==45000 and result['idle_ms']==15000
    assert active_time.summary(w['stranger'],since,until)['active_ms']==0
    with db.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM crm_events').fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM crm_shifts').fetchone()[0]==0


def test_activity_rejects_wrong_identity_time_clock_and_schema(crm_world):
    w=crm_world;body=interval(w)
    with pytest.raises(CRMError):active_time.ingest(w['stranger'],body)
    for patch in ({'end_ms':30001},{'start_ms':-1},{'end_ms':True},{'installation_id':'other-device'},
                  {'kind':'call'},{'employee_uuid':w['employee']},{'start_ms':1000000,'end_ms':1030000}):
        with pytest.raises(CRMError):active_time.ingest(w['member'],{**body,**patch})
    active_time.ingest(w['member'],body)
    with pytest.raises(CRMError):active_time.ingest(w['member'],{**body,'kind':'idle'})
    with pytest.raises(CRMError):active_time.summary(w['member'],'2026-11-31')


def test_activity_schema_upgrade_keeps_existing_work(crm_world):
    from crm import schema
    w=crm_world
    with db.connect() as conn:
        conn.execute('DROP TABLE crm_active_intervals')
        conn.execute("UPDATE crm_meta SET value='3' WHERE key='schema_version'")
        schema.initialize(conn);conn.commit()
        assert conn.execute('SELECT COUNT(*) FROM crm_active_intervals').fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM crm_contracts').fetchone()[0]==2


def test_activity_uses_employee_midnight_instead_of_utc_midnight(crm_world):
    w=crm_world
    with db.connect() as conn:
        conn.execute('INSERT INTO crm_active_intervals VALUES(?,?,?,?,?,?,?,?)',
            (str(uuid.uuid4()),w['employee'],101,'fixture-installation','active',
             '2026-11-10T19:59:50.000+00:00','2026-11-10T20:00:10.000+00:00',now()));conn.commit()
    result=active_time.summary(w['member'],'2026-11-10','2026-11-11')
    assert [day['active_ms'] for day in result['days']]==[10000,10000]
