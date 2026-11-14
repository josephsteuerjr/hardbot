"""Distribution changes effective authority, never original work authorship."""
from dataclasses import replace
import json
import uuid
import pytest
from crm import access, assignments, storage, store, registry
from crm.contract import CRMError, canonical, identifier, now
from test_crm_core import crm_world, call_body
from test_crm_employee_registry import edit


def allocation_world(w):
    at='2026-01-01T00:00:00+00:00'
    caps=sorted(set(w['member'].capabilities)|{'assign','analytics'})
    managers=[]
    with storage.connect() as c:
        c.execute("INSERT INTO crm_meta VALUES('distribution_mode','isolated')")
        for i in (1,2):
            eid=identifier('staff','distribution-manager-'+str(i));uid=400+i
            c.execute('INSERT INTO crm_employees VALUES(?,?,?,?,?)',(eid,'staff-manager-'+str(i),'Manager '+str(i),'fixture',1))
            c.execute('INSERT INTO crm_actor_links VALUES(?,?,?,?)',(uid,eid,'fixture',1))
            c.execute('INSERT INTO crm_memberships VALUES(?,?,?,?,?,?,?,?)',
                      (str(uuid.uuid4()),eid,'team-A','manager',canonical(caps),'Europe/Samara',at,None))
            for work in ('call','visit'):
                c.execute('INSERT INTO crm_distribution_managers VALUES(?,?,?)',(eid,'team-A',work))
            managers.append(access.Principal(uid,eid,'team-A','manager',frozenset(caps),frozenset({eid,w['employee']})))
        for cid in w['contracts']:
            for work in ('call','visit'):c.execute('INSERT INTO crm_managed_contracts VALUES(?,?,?)',('team-A',cid,work))
        eid=identifier('staff','allocation-target')
        c.execute('INSERT INTO crm_employees VALUES(?,?,?,?,?)',(eid,'staff-target','New employee','fixture',1))
        c.execute('INSERT INTO crm_memberships VALUES(?,?,?,?,?,?,?,?)',
                  (str(uuid.uuid4()),eid,'team-A','employee',canonical(sorted(w['member'].capabilities)),'Europe/Samara',at,None))
    return dict(w,managers=managers,target=eid,new_member=replace(w['member'],actor_uid=500,employee_uuid=eid,employee_scope=frozenset({eid})))


def preview(w,action='replace',works=None,target='default',selection=None,manager=0):
    return assignments.preview(w['managers'][manager],dict(schema_version=1,action=action,
        employee_uuid=w['target'] if target=='default' else target,work_kinds=works or ['call'],
        selection=selection or dict(kind='people',ids=[w['person']],part='whole',filters={}),comment=''))


def apply(w,p,manager=0,op=None):
    body=dict(schema_version=1,operation_id=op or str(uuid.uuid4()),preview_id=p['preview_id'])
    return assignments.commit(w['managers'][manager],body),body


def test_transfer_changes_only_call_and_preserves_debt_and_authorship(crm_world):
    w=allocation_world(crm_world);m=w['managers'][0]
    accepted=store.command(w['member'],call_body(w))
    p=preview(w);assert p['contracts']==2 and p['people']==1
    result,body=apply(w,p)
    assert len(result['changes'])==2 and assignments.commit(m,body)==result
    with storage.connect() as c:
        assert access.writable_actions(c,w['member'],w['contracts'][0]) and 'call' not in access.writable_actions(c,w['member'],w['contracts'][0])
        assert 'visit' in access.writable_actions(c,w['member'],w['contracts'][0])
        assert 'call' in access.writable_actions(c,w['new_member'],w['contracts'][0])
        assert not next(r for r in assignments.targets(c,m) if r['employee_uuid']==w['target'])['has_account']
        assert c.execute('SELECT COUNT(*) FROM crm_events').fetchone()[0]==1
    assert store.command(w['member'],call_body(w,op=accepted['operation_id']))==accepted
    with pytest.raises(CRMError):store.command(w['member'],call_body(w))
    with storage.connect() as c:c.execute('INSERT INTO crm_actor_links VALUES(?,?,?,?)',(500,w['target'],'fixture',1))
    body=call_body(w);body['base_revisions']={'call':1}
    store.command(w['new_member'],body)
    stats=assignments.statistics(m,month=now()[:7])
    assert stats['debt']=={'RUB':'246.90'} and stats['contracts']==2
    assert next(r for r in stats['staff'] if r['employee_uuid']==w['employee'])['facts']['call']==1
    assert next(r for r in stats['staff'] if r['employee_uuid']==w['target'])['facts']['call']==1


def test_remove_last_assignment_keeps_management_and_can_reassign(crm_world):
    w=allocation_world(crm_world);m=w['managers'][0]
    apply(w,preview(w,'remove',['call','visit'],None))
    assert len(assignments.portfolio(m,unassigned='1')['people'])==1
    assert assignments.statistics(m)['unassigned_slots']==4
    with storage.connect() as c:assert not access.scoped_contracts(c,w['member'])
    apply(w,preview(w,'assign',['call','visit']))
    assert not assignments.portfolio(m,unassigned='1')['people']
    assert assignments.statistics(m)['unassigned_slots']==0


def test_conflict_replay_noop_occupied_and_payload_reuse(crm_world):
    w=allocation_world(crm_world)
    with pytest.raises(CRMError) as e:preview(w,'assign')
    assert e.value.code=='assignment_occupied'
    p1=preview(w);p2=preview(w,manager=1)
    result,body=apply(w,p1)
    with pytest.raises(CRMError) as e:apply(w,p2,manager=1)
    assert e.value.code=='assignment_conflict'
    assert assignments.receipt(w['managers'][0],operation_id=body['operation_id'])==result
    with pytest.raises(CRMError):assignments.receipt(w['managers'][1],operation_id=body['operation_id'])
    p=preview(w);noop,_=apply(w,p)
    assert noop['changes']==[] and noop['no_op']==2
    with pytest.raises(CRMError) as e:assignments.commit(w['managers'][0],dict(body,preview_id=p['preview_id']))
    assert e.value.code=='operation_reused'
    with storage.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM crm_assignment_changes').fetchone()[0]==2


def test_forgery_common_read_target_membership_and_atomicity(crm_world):
    w=allocation_world(crm_world)
    for actor in (replace(w['member'],read_all_contracts=True),replace(w['stranger'],all_contracts=True)):
        with pytest.raises(CRMError):assignments.portfolio(actor)
    with pytest.raises(CRMError):preview(w,target=w['stranger'].employee_uuid)
    p=preview(w,works=['call','visit'])
    with storage.connect() as c:c.execute('DELETE FROM crm_managed_contracts WHERE contract_uuid=? AND capability=?', (w['contracts'][1],'visit'))
    with pytest.raises(CRMError):apply(w,p)
    with storage.connect() as c:assert c.execute('SELECT COUNT(*) FROM crm_assignment_changes').fetchone()[0]==0
    p=preview(w)
    with storage.connect() as c:c.execute('UPDATE crm_memberships SET ends_at=? WHERE employee_uuid=?',(now(),w['target']))
    with pytest.raises(CRMError) as e:apply(w,p)
    assert e.value.code=='target_unavailable'


def test_old_task_grant_ends_with_changed_work_but_history_survives(crm_world):
    w=allocation_world(crm_world);cid=w['contracts'][0]
    body=dict(schema_version=1,operation_id=str(uuid.uuid4()),kind='task',contract_ids=[cid],
              payload=dict(title='Call task',due_at=now(),assignee_uuid=w['employee'],work_kind='call'),base_revisions={})
    task=store.command(w['managers'][0],body)
    apply(w,preview(w))
    with storage.connect() as c:
        from crm import tasks
        assert 'call' not in tasks.grants(c,w['member']).get(cid,set())
        assert c.execute("SELECT status FROM crm_entities WHERE event_id=?",(task['event_id'],)).fetchone()[0]=='open'
        assert 'visit' in access.writable_actions(c,w['member'],cid)


def test_filtered_part_all_selection_frozen_and_authorized_changes(crm_world):
    w=allocation_world(crm_world);m=w['managers'][0]
    snapshot=assignments.portfolio(m)
    assert snapshot['people'][0]['contracts'][0]['external_id']=='DUPLICATE'
    p=preview(w,selection=dict(kind='filter',filters=dict(search='creditor-0'),part='filtered'))
    assert p['contracts']==1
    apply(w,p)
    cursor=snapshot['watermark']
    assert assignments.changes(m,**cursor)['dirty']
    assert assignments.changes(w['member'],**cursor)['dirty']
    assert not assignments.changes(w['stranger'],**cursor)['dirty']
    latest=assignments.changes(m,**cursor)['cursor']
    assert not assignments.changes(m,**latest)['dirty']
    assert set(assignments.changes(w['member'],**cursor))=={'cursor','resync','dirty','as_of'}


def test_legacy_overlap_is_fail_closed_and_readiness_is_server_owned(crm_world):
    w=allocation_world(crm_world);m=w['managers'][0]
    with storage.connect() as c:
        c.execute('INSERT INTO crm_assignments VALUES(?,?,?,?,?,?,?)',
                  (str(uuid.uuid4()),w['target'],w['contracts'][0],'call','2026-01-01',None,'ambiguous'))
        assert 'call' not in access.writable_actions(c,w['member'],w['contracts'][0])
    with pytest.raises(CRMError) as e:preview(w)
    assert e.value.code=='assignment_ambiguous'
    with storage.connect() as c:c.execute("DELETE FROM crm_meta WHERE key='distribution_mode'")
    with pytest.raises(CRMError) as e:assignments.portfolio(m)
    assert e.value.code=='distribution_unavailable'


def test_accepted_registry_replay_survives_loss_of_assignment(crm_world):
    w=allocation_world(crm_world);body=edit(w)
    accepted=registry.edit(w['member'],body)
    apply(w,preview(w,'remove',['call','visit'],None))
    assert registry.edit(w['member'],body)==accepted
    with pytest.raises(CRMError):registry.edit(w['member'],edit(w))


def test_source_baseline_does_not_restore_native_transfer_or_removal(crm_world):
    import sqlite3
    from crm import legacy_exchange
    from test_crm_legacy_exchange import world
    w=allocation_world(crm_world);path=world(w)
    apply(w,preview(w))
    snapshot=legacy_exchange.capture(path)
    legacy_exchange.apply(snapshot,legacy_exchange.preview(snapshot))
    with storage.connect() as c:
        assert assignments.states(c,w['contracts'])[w['contracts'][0],'call']['employee_uuid']==w['target']
    # Source diverges independently; native decision wins, issue is durable.
    with sqlite3.connect(path) as c:c.execute("UPDATE clients SET responsible_soft='Outsider' WHERE contract_id='R0'")
    fresh=legacy_exchange.capture(path);review=legacy_exchange.preview(fresh)
    assert review['issues'].get('legacy_assignment_conflict')==1
    legacy_exchange.apply(fresh,review)
    apply(w,preview(w,'remove',['call'],None))
    fresh=legacy_exchange.capture(path);legacy_exchange.apply(fresh,legacy_exchange.preview(fresh))
    with storage.connect() as c:
        assert all(assignments.states(c,w['contracts'])[cid,'call']['employee_uuid'] is None for cid in w['contracts'])
        assert all('call' not in access.writable_actions(c,w['member'],cid) for cid in w['contracts'])
    current=registry.read(replace(w['member'],read_all_contracts=True),view='all')['rows']
    assert all(r['fields']['responsible_soft']=='Не назначен' for r in current)
