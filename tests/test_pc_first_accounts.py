"""Synthetic people, real SQLite transactions/HTTP; no production accounts."""
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json

from aiohttp.test_utils import TestClient, TestServer
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
import pytest

import accounts
import db
import pc_registration
import staff_roster
import web

PASSWORD='Syn'+'thetic123!'
MANAGER=781001


def identity():
    key=Ed25519PrivateKey.generate()
    raw=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
    return key,base64.b64encode(raw).decode(),hashlib.sha256(raw).hexdigest()


def proof(ident,employee,username,secret=PASSWORD):
    key,pub,fp=ident
    c=pc_registration.challenge(pub,fp,'Synthetic PC','127.0.0.1')
    message='\n'.join((c['challenge_id'],c['nonce'],employee,username,hashlib.sha256(secret.encode()).hexdigest()))
    return {'challenge_id':c['challenge_id'],'signature':base64.b64encode(key.sign(message.encode())).decode()}


def pc_login(username,ident):
    return accounts.login(username,PASSWORD,device={'kind':'desktop','fp':ident[2],'label':'Synthetic PC'},
        entrance='internal',audience='employee',pc_proof=proof(ident,'',username))


def http(method,path,body=None,headers=None):
    async def run():
        async with TestClient(TestServer(web.build_app(None))) as c:
            r=await c.request(method,path,json=body,headers={'X-Client-Version':'3.2.0',**(headers or {})})
            return r.status,await r.json() if r.content_type=='application/json' else {}
    return asyncio.run(run())


@pytest.fixture
def world(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'pc-first.db')
    monkeypatch.setattr(db,'admin_bootstrap_marker',lambda:tmp_path/'bootstrap')
    monkeypatch.setattr(web,'DEV',False)
    monkeypatch.setenv('ACCOUNT_CUTOVER_AT_UTC','2000-01-01T00:00:00Z')
    db.init_db()
    db.set_manager(MANAGER,'Synthetic Leader',[],['Own Employee','Zero Portfolio'])
    db.set_manager(MANAGER+1,'Other Leader',[],['Foreign Employee'])
    data=[{'employee':x,'office':'Synthetic office','products':['hard','soft'],'manager_uid':uid}
          for x,uid in [('Own Employee',MANAGER),('Zero Portfolio',MANAGER),('Foreign Employee',MANAGER+1)]]
    p=staff_roster.preview(data)
    assert staff_roster.apply(data,p['sha256'],by='synthetic-fixture')['ok']
    web._ADMIN_FAILS.clear()
    return identity()


def register(ident,name='Own Employee',username='own-employee'):
    return http('POST','/api/account/register',{'employee':name,'username':username,'password':PASSWORD,
        'role':'full','office':'Forged scope','pc_proof':proof(ident,name,username)})


def approve_pc(ident,name='Own Employee',username='own-employee'):
    status,data=register(ident,name,username)
    assert status==201,data
    with db.connect() as c: dev=c.execute('SELECT id FROM account_devices WHERE account_id=(SELECT id FROM accounts WHERE username=?)',(username,)).fetchone()[0]
    result=accounts.manager_approve(MANAGER,device_id=dev,observed_pc=True)
    assert result.get('active'),result
    return data,dev


def test_unsigned_mobile_registration_and_forged_pc_fields_rejected(world):
    for fields in ({},{'entrance':'internal','device':{'kind':'desktop','fp':'forged'}},{'pc_proof':{'challenge_id':'unknown','signature':'bad'}}):
        assert http('POST','/api/account/register',{'employee':'Own Employee','username':'forged','password':PASSWORD,**fields})[0]==400
    with db.connect() as c: assert c.execute('SELECT count(*) FROM accounts').fetchone()[0]==0


def test_signed_registration_atomic_pending_pc_and_replay(world):
    p=proof(world,'Own Employee','own-employee')
    body={'employee':'Own Employee','username':'own-employee','password':PASSWORD,'pc_proof':p,'role':'full'}
    status,data=http('POST','/api/account/register',body)
    assert status==201,data
    with db.connect() as c:
        a=c.execute('SELECT * FROM accounts').fetchone(); d=c.execute('SELECT * FROM account_devices').fetchone()
    assert a['role']=='agent' and a['status']=='awaiting' and d['kind']=='desktop' and d['status']=='pending'
    assert accounts.device_gate_by_token('agent_devices',d['agent_dev_id'])[0]=='pending'
    assert http('POST','/api/account/register',body)[0]==400


def test_pc_first_no_phone_required_and_personal_observation_required(world):
    status,data=register(world); assert status==201,data
    with db.connect() as c: device=c.execute('SELECT id FROM account_devices').fetchone()[0]
    assert not accounts.manager_approve(MANAGER,device_id=device)['ok']
    assert not accounts.manager_approve(MANAGER+1,device_id=device,observed_pc=True)['ok']
    assert accounts.manager_approve(MANAGER,device_id=device,observed_pc=True)['active']
    with db.connect() as c:
        d=c.execute('SELECT * FROM account_devices').fetchone()
        assert c.execute("SELECT count(*) FROM account_devices WHERE kind='phone'").fetchone()[0]==0
    assert accounts.device_gate_by_token('agent_devices',d['agent_dev_id'])[0]=='ok'
    assert accounts.entered_list()[0]['ready']


def test_zero_portfolio_roster_is_selectable_and_scoped(world):
    rows=http('GET','/api/account/staff')[1]['employees']
    assert 'Zero Portfolio' in {x['employee'] for x in rows}
    data,dev=approve_pc(world,'Zero Portfolio','zero-portfolio')
    assert data['role']=='agent'
    assert 'Zero Portfolio' in db.manager_employees(MANAGER)
    assert not accounts.manager_approve(MANAGER+1,device_id=dev,observed_pc=True)['ok']


def test_two_products_same_pc_and_replacement_revoke_all_bearers(world):
    data,dev=approve_pc(world)
    second=pc_login('own-employee',world); assert second['action']=='ok',second
    with db.connect() as c:
        token_ids=[r[0] for r in c.execute('SELECT token_id FROM account_token_sessions WHERE device_id=?',(dev,))]
        assert len(token_ids)==2
    assert all(accounts.device_gate_by_token('agent_devices',i)[0]=='ok' for i in token_ids)
    replacement=identity(); new=pc_login('own-employee',replacement); assert new['action']=='ok_pending',new
    with db.connect() as c: pending=c.execute("SELECT id FROM account_devices WHERE fingerprint=?",(replacement[2],)).fetchone()[0]
    assert accounts.manager_approve(MANAGER,device_id=pending,observed_pc=True)['active']
    assert all(accounts.device_gate_by_token('agent_devices',i)[0]=='blocked' for i in token_ids)
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM account_devices WHERE kind='desktop' AND status='active'").fetchone()[0]==1
        assert all(c.execute('SELECT revoked_at FROM agent_devices WHERE id=?',(i,)).fetchone()[0] for i in token_ids)


def test_parallel_pc_approvals_serialize_to_single_slot(world):
    approve_pc(world)
    candidates=[identity(),identity()]
    for ident in candidates: assert pc_login('own-employee',ident)['action']=='ok_pending'
    with db.connect() as c: pending=[r[0] for r in c.execute("SELECT id FROM account_devices WHERE status='pending'")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda d:accounts.manager_approve(MANAGER,device_id=d,observed_pc=True),pending))
    assert all(r['ok'] for r in results)
    with db.connect() as c: assert c.execute("SELECT count(*) FROM account_devices WHERE status='active' AND kind='desktop'").fetchone()[0]==1


def test_every_staff_phone_requires_own_manager_even_with_gps(world):
    approve_pc(world)
    first=accounts.login('own-employee',PASSWORD,device={'kind':'phone','fp':'phone-1'},entrance='public',audience='mobile',gps={'lat':1,'lon':2})
    assert first['action']=='ok_pending' and not first['gps_exception_pending']
    with db.connect() as c: dev=c.execute("SELECT * FROM account_devices WHERE fingerprint='phone-1'").fetchone()
    assert not accounts.manager_approve(MANAGER+1,device_id=dev['id'])['ok']
    assert accounts.manager_approve(MANAGER,device_id=dev['id'])['active']
    assert accounts.device_gate_by_token('agent_devices',dev['agent_dev_id'])[0]=='ok'
    second=accounts.login('own-employee',PASSWORD,device={'kind':'phone','fp':'phone-2'},entrance='public',audience='mobile')
    assert second['action']=='ok_pending' and not second['gps_exception_pending']
    with db.connect() as c: new=c.execute("SELECT * FROM account_devices WHERE fingerprint='phone-2'").fetchone()
    assert accounts.manager_approve(MANAGER,device_id=new['id'])['active']
    assert accounts.device_gate_by_token('agent_devices',dev['agent_dev_id'])[0]=='blocked'
    assert accounts.device_gate_by_token('agent_devices',new['agent_dev_id'])[0]=='ok'


def test_phone_approval_survives_missing_stale_gps_and_expired_old_exception(world):
    approve_pc(world)
    phone=accounts.login('own-employee',PASSWORD,device={'kind':'phone','fp':'phone'},entrance='public',audience='mobile')
    with db.connect() as c: dev=c.execute("SELECT * FROM account_devices WHERE kind='phone'").fetchone()
    assert accounts.manager_approve(MANAGER,device_id=dev['id'])['active']
    with db.connect() as c:
        old=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
        c.execute('UPDATE account_devices SET gps_seen_at=?,gps_exception_until=? WHERE id=?',(old,old,dev['id']))
    assert accounts.device_gate_by_token('agent_devices',dev['agent_dev_id'])[0]=='ok'
    status,body=http('GET','/api/bootstrap',headers={'X-App-Token':phone['token']})
    assert status==200 and not body.get('pending')
    # A cached previous shell must not demote an approved phone via this legacy endpoint.
    result=http('POST','/api/account/gps_request',headers={'X-App-Token':phone['token']})
    assert result[0]==200 and not result[1]['pending']
    again=accounts.login('own-employee',PASSWORD,device={'kind':'phone','fp':'phone'},entrance='public',audience='mobile')
    assert again['action']=='ok'


def test_old_client_protocol_cannot_reach_api_after_cutover(world):
    status,data=http('GET','/api/bootstrap',headers={'X-Client-Version':'3.1.8'})
    assert status==426 and data['minimum_version']=='3.2.0'


@pytest.mark.parametrize('method,path', [
    ('POST','/api/photo'), ('POST','/api/call'),
    ('POST','/admin/import.json'), ('PATCH','/api/note'),
    ('DELETE','/api/photo'),
])
def test_retired_queued_writes_are_transient_denials_without_data_changes(world,method,path):
    # Older installed photo queues discard their item on any 4xx. Retirement
    # must keep the unposted evidence while still refusing the old request.
    tables=('visits','calls','notes','photo_geo','payment_evidence')
    with db.connect() as c:
        before={t:c.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in tables}
    status,data=http(method,path,{'synthetic_retired_queue':True},
                     headers={'X-Client-Version':'3.1.8'})
    assert status==503 and data['minimum_version']=='3.2.0'
    with db.connect() as c:
        assert {t:c.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in tables}==before


def test_manager_mobile_uses_common_login_and_live_scope_not_legacy_binding(world):
    assert accounts.create_account('Synthetic Leader','synthetic-leader',PASSWORD,role='manager',telegram_id=MANAGER)['ok']
    accounts.approve_account('synthetic-leader','synthetic-admin-one')
    accounts.approve_account('synthetic-leader','synthetic-admin-two')
    accounts.change_password('synthetic-leader',PASSWORD,'Changed123')
    login=accounts.login('synthetic-leader','Changed123',device={'kind':'phone','fp':'manager-phone'},
        entrance='public',audience='mobile',gps={'lat':1,'lon':2})
    assert login['action']=='ok' and login['role']=='manager'
    headers={'X-Pult-Token':login['token']}
    assert http('GET','/api/manager/entered',headers=headers)[0]==200
    db.set_bind(MANAGER,'Foreign Employee')
    db.set_manager(MANAGER,'Synthetic Leader',[],[],role='manager')
    status,body=http('GET','/api/bootstrap',headers=headers)
    assert status==200 and body['roles']['manager'] and not body['roles']['employees']
    assert not body.get('needsOnboarding') and not body.get('persons')
    assert http('GET','/api/person?rep=foreign',headers=headers)[0]==403
    db.set_manager(MANAGER,'Synthetic Leader',[],[],role='security')
    assert http('GET','/api/bootstrap',headers=headers)[0]==401


@pytest.mark.parametrize('role', ['manager', 'security', 'full'])
def test_leader_phone_never_needs_gps_but_replacement_still_needs_approval(world, role):
    db.set_manager(MANAGER, 'Synthetic Leader', [], [], role=role)
    assert accounts.create_account('Synthetic Leader','gps-free-leader',PASSWORD,role=role,telegram_id=MANAGER)['ok']
    assert accounts.approve_account('gps-free-leader','synthetic-admin')['active']
    assert accounts.change_password('gps-free-leader',PASSWORD,'Changed123')['ok']
    payload={'username':'gps-free-leader','password':'Changed123','audience':'mobile',
             'device':{'kind':'phone','fp':'leader-phone'},'defer_gps':True,'role':'agent'}
    status,first=http('POST','/api/account/login',payload)
    assert status==200 and first['action']=='ok' and first['role']==role
    assert not first['gps_exception_pending']
    with db.connect() as c:
        dev=c.execute("SELECT * FROM account_devices WHERE fingerprint='leader-phone'").fetchone()
        c.execute('UPDATE account_devices SET gps=?,gps_seen_at=? WHERE id=?',
                  ('{}',(datetime.now(timezone.utc)-timedelta(days=2)).isoformat(),dev['id']))
    assert accounts.device_gate_by_token('manager_devices',dev['agent_dev_id'])[0]=='ok'
    status,body=http('GET','/api/bootstrap',headers={'X-Pult-Token':first['token']})
    assert status==200 and not body.get('pending')
    status,again=http('POST','/api/account/login',payload)
    assert status==200 and again['action']=='ok'
    payload['device']['fp']='replacement-leader-phone'
    status,replacement=http('POST','/api/account/login',payload)
    assert status==200 and replacement['action']=='ok_pending'
    assert not replacement['gps_exception_pending']
    with db.connect() as c:
        new=c.execute("SELECT * FROM account_devices WHERE fingerprint='replacement-leader-phone'").fetchone()
    assert accounts.approve_device(new['id'],'synthetic-admin')['active']
    assert accounts.device_gate_by_token('manager_devices',new['agent_dev_id'])[0]=='ok'
    assert accounts.device_gate_by_token('manager_devices',dev['agent_dev_id'])[0]=='blocked'


def test_staff_cannot_bypass_phone_approval_with_client_role_or_coordinates(world):
    approve_pc(world)
    payload={'username':'own-employee','password':'Wrong123!','audience':'mobile',
             'device':{'kind':'phone','fp':'staff-phone'},'role':'manager','gps':{'lat':1,'lon':2}}
    with db.connect() as c: before=c.execute('SELECT count(*) FROM account_devices').fetchone()[0]
    status,wrong=http('POST','/api/account/login',payload)
    assert status==403 and wrong['action']=='bad' and 'token' not in wrong
    with db.connect() as c: assert before==c.execute('SELECT count(*) FROM account_devices').fetchone()[0]
    payload['password']=PASSWORD
    status,pending=http('POST','/api/account/login',payload)
    assert status==200 and pending['action']=='ok_pending' and pending['role']=='agent'
    with db.connect() as c: dev=c.execute("SELECT * FROM account_devices WHERE fingerprint='staff-phone'").fetchone()
    assert accounts.device_gate_by_token('agent_devices',dev['agent_dev_id'])[0]=='pending'
    assert not accounts.manager_approve(MANAGER+1,device_id=dev['id'])['ok']
    assert accounts.manager_approve(MANAGER,device_id=dev['id'])['active']
    assert accounts.device_gate_by_token('agent_devices',dev['agent_dev_id'])[0]=='ok'


def test_old_phone_approval_without_observed_pc_cannot_survive_strict_policy(world):
    assert accounts.create_account('Own Employee','legacy-agent',PASSWORD)['ok']
    old=accounts.login('legacy-agent',PASSWORD,device={'kind':'phone','fp':'old-phone'},entrance='public',audience='mobile',gps={'lat':1,'lon':2})
    with db.connect() as c:
        c.execute("UPDATE accounts SET status='active',must_change_password=0 WHERE username='legacy-agent'")
        c.execute("UPDATE account_devices SET status='active',approved_by='legacy' WHERE kind='phone'")
        dev=c.execute("SELECT * FROM account_devices WHERE fingerprint='old-phone'").fetchone()
    assert accounts.device_gate_by_token('agent_devices',dev['agent_dev_id'])[0]=='pending'
    # Existing legacy approval alone stays pending; its own manager may now
    # explicitly confirm this phone without making the employee install a PC app.
    assert accounts.manager_approve(MANAGER,device_id=dev['id'])['ok']
    assert accounts.device_gate_by_token('agent_devices',dev['agent_dev_id'])[0]=='ok'


def test_same_person_two_programs_preserves_separate_portfolio_names_and_fact_authors(world):
    items=[{'employee':'Own Employee','office':'Primary office','offices':['Primary office','Soft office'],
        'direction':'tj','products':['hard','soft'],'product_names':{'hard':'Own Employee','soft':'Own Employee SOFT'}}]
    preview=staff_roster.preview(items);assert staff_roster.apply(items,preview['sha256'],by='synthetic')['ok']
    with db.connect() as c:
        c.execute("INSERT INTO clients(contract_id,client,responsible,responsible_soft) VALUES('SYN-MULTI','Synthetic Client','Own Employee','Own Employee SOFT')")
    data,_=approve_pc(world)
    headers={'X-App-Token':data['token']}
    status,hard=http('GET','/api/bootstrap?mode=field',headers=headers)
    status2,soft=http('GET','/api/bootstrap?mode=soft',headers=headers)
    assert status==status2==200 and hard['employee']==soft['employee']=='Own Employee'
    assert soft['portfolio_employee']=='Own Employee SOFT'
    assert len(hard['persons'])==len(soft['persons'])==1
    call={'rep':'SYN-MULTI','kind':'call','finish':True,'op_id':'synthetic-multi-call',
          'form_version':web.FORM_PROTOCOL_VERSION,'input_keys':['soft_call_status'],
          'values':{'soft_call_status':'Нет контакта'}}
    status,result=http('POST','/api/save',call,headers)
    assert status==200 and result['ok'],result
    with db.connect() as c:
        assert c.execute('SELECT employee FROM calls').fetchone()[0]=='Own Employee SOFT'
        assert c.execute('SELECT employee FROM accounts').fetchone()[0]=='Own Employee'


def test_roster_rejects_shared_portfolio_at_two_persons(world):
    with pytest.raises(ValueError,match='разных сотрудников'):
        staff_roster.preview([{'employee':'A','products':['soft'],'product_names':{'soft':'Shared'}},
                              {'employee':'B','products':['soft'],'product_names':{'soft':'Shared'}}])


def test_existing_employee_with_separate_privileged_login_can_confirm_replacement_pc(world):
    _, first = approve_pc(world)
    with db.connect() as conn:
        employee = conn.execute("SELECT id,telegram_id FROM accounts WHERE username='own-employee'").fetchone()
    db.set_manager(MANAGER+2, 'Own Employee', [], ['Zero Portfolio'])
    with db.connect() as conn:
        # This row predates the prohibition on creating another account under the same name.
        conn.execute("INSERT INTO accounts(username,employee,role,password_hash,status,telegram_id,created_at,updated_at,created_by) "
                     "SELECT 'separate-manager',employee,'manager',password_hash,'active',?,created_at,updated_at,'fixture' "
                     "FROM accounts WHERE id=?", (MANAGER+2,employee['id']))
    replacement = pc_login('own-employee', identity())
    assert replacement['action'] == 'ok_pending'
    with db.connect() as conn:
        second = conn.execute('SELECT id FROM account_devices WHERE account_id=? AND status=?',
                              (employee['id'], 'pending')).fetchone()[0]
    row = next(row for row in accounts.manager_team(MANAGER)['employees'] if row['employee']=='Own Employee')
    assert row['can_approve'] and row['account']['username']=='own-employee'
    assert not accounts.manager_approve(MANAGER+1,device_id=second,observed_pc=True)['ok']
    assert not accounts.manager_approve(MANAGER+2,device_id=second,observed_pc=True)['ok']
    assert accounts.manager_approve(MANAGER,device_id=second,observed_pc=True)['active']
    with db.connect() as conn:
        assert conn.execute('SELECT status FROM account_devices WHERE id=?',(first,)).fetchone()[0]=='revoked'
        assert conn.execute("SELECT telegram_id FROM accounts WHERE username='separate-manager'").fetchone()[0]==MANAGER+2
