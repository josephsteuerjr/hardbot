"""Current team leaders complete staff resets after the personal password change."""
import accounts
import db
import pytest
from test_manager_account_approval import world, register, login, call, MGR
from test_pc_first_accounts import identity, proof


def activated(world):
    assert register()[0] == 201
    token = login()['token']
    row = call('GET','/api/manager/entered',world)[1]['entered'][0]
    device_id = row['devices'][0]['id']
    assert call('POST','/api/manager/account_approve',world,
                {'device_id':device_id,'observed_pc':True})[1]['active']
    return token, device_id


def owner():
    assert accounts.create_account('Synthetic Owner','synthetic-owner','Owner12345',role='analyst')['ok']
    assert accounts.approve_account('synthetic-owner','test')['ok']
    with db.connect() as c:
        aid=c.execute("SELECT id FROM accounts WHERE username='synthetic-owner'").fetchone()[0]
        c.execute("INSERT INTO account_admin_grants VALUES(?,?,?,?,?,?)",(aid,'owner-test','ssh-root','owner-test-operation','2026-01-01',0))
    return 'principal:owner-test'


def reset(world, **extra):
    return call('POST','/api/manager/account_reset',world,
                dict(username='self-own',password='Temporary123',operation_id='reset-operation-001',**extra))


def test_reset_revokes_credentials_and_leader_approves_only_after_personal_password(world):
    old, device = activated(world)
    principal = owner()
    status, result = reset(world)
    assert status == 200 and result['password_review_pending']
    assert db.agent_device_check(old)[0] != 'ok'
    row = call('GET','/api/manager/entered',world)[1]['entered'][0]
    assert row['username']=='self-own' and row['password_review_pending']
    assert not row['can_approve'] and not row['can_reset_password']
    assert not accounts.manager_approve(MGR,device_id=device,observed_pc=True)['ok']
    assert not accounts.approve_account('self-own','principal:someone-else')['ok']
    assert not accounts.approve_device(device,principal)['ok']
    assert not accounts.approve_account('self-own',principal)['ok']
    entered=accounts.login('self-own','Temporary123',device={'kind':'phone','fp':'reset-phone','label':'Synthetic Phone'},entrance='public',audience='employee')
    header={'X-App-Token':entered['token']}
    status,bootstrap=call('GET','/api/bootstrap',header)
    assert status==200 and bootstrap['pending_kind']=='password' and bootstrap['must_change_password']
    assert accounts.change_password('self-own','Temporary123','Personal123')['ok']
    status,bootstrap=call('GET','/api/bootstrap',header)
    assert status==200 and bootstrap['pending_kind']=='password_review' and not bootstrap.get('persons')
    assert call('GET','/api/identity',header)[1]['pending']
    assert not accounts.approve_account('self-own','principal:someone-else')['ok']
    assert call('GET','/api/manager/entered',world)[1]['entered'][0]['can_approve']
    with db.connect() as c:
        phone=c.execute("SELECT id FROM account_devices WHERE fingerprint='reset-phone'").fetchone()[0]
    assert accounts.manager_approve(MGR,device_id=phone)['active']
    assert not call('GET','/api/bootstrap',header)[1].get('pending')
    with db.connect() as c:
        acc=c.execute("SELECT * FROM accounts WHERE username='self-own'").fetchone()
        assert acc['status']=='active' and not acc['password_review_pending']
        decisions=[r[0] for r in c.execute("SELECT decision FROM account_approval_events WHERE action='password_reset' ORDER BY id")]
    assert decisions==['pending','approved']


def test_repeat_cannot_reset_again_or_invalidate_new_password(world):
    activated(world);principal=owner()
    assert reset(world)[0]==200
    assert reset(world)[1]['replayed']
    assert accounts.change_password('self-own','Temporary123','Personal123')['ok']
    assert accounts.approve_account('self-own',principal)['active']
    assert reset(world)[1]['replayed']
    with db.connect() as c:
        acc=c.execute("SELECT * FROM accounts WHERE username='self-own'").fetchone()
    assert accounts.verify_password('Personal123',acc['password_hash'])
    assert acc['status']=='active' and not acc['password_review_pending']


def test_reset_scope_is_rechecked_and_cannot_target_other_team_or_privileged(world):
    activated(world)
    assert register('Foreign Employee','foreign-reset')[0]==201
    for username in ('foreign-reset','missing','synthetic-owner'):
        status, _=call('POST','/api/manager/account_reset',world,
                      {'username':username,'password':'Temporary123','operation_id':'scope-operation-001'})
        assert status==400
    db.set_manager(MGR,'Synthetic Leader',[],[])
    assert reset(world)[0]==400
    with db.connect() as c:
        acc=c.execute("SELECT * FROM accounts WHERE username='self-own'").fetchone()
    assert acc['status']=='active' and accounts.verify_password('chosen123',acc['password_hash'])


def test_password_review_survives_enable_and_admin_reset(world):
    activated(world);assert reset(world)[0]==200
    assert accounts.set_status('self-own','awaiting','operator')['ok']
    assert accounts.set_password('self-own','Another123',by='principal:not-owner')['ok']
    assert not accounts.approve_account('self-own','principal:not-owner')['ok']
    assert not accounts.manager_approve(MGR,username='self-own')['ok']


def analyst_headers(*, root=True):
    owner()
    assert accounts.change_password('synthetic-owner','Owner12345','OwnerPersonal123')['ok']
    if not root:
        with db.connect() as c:
            c.execute("UPDATE account_admin_grants SET granted_by='operator' WHERE principal='owner-test'")
    pc=identity()
    entered=accounts.login('synthetic-owner','OwnerPersonal123',
        device={'kind':'desktop','fp':pc[2]},entrance='internal',audience='analyst',
        pc_proof=proof(pc,'','synthetic-owner','OwnerPersonal123'))
    assert entered['action']=='ok',entered
    return {'X-Pult-Token':entered['token']}


def test_owner_can_confirm_self_registered_password_reset_through_analyst_http(world,monkeypatch):
    activated(world)
    headers=analyst_headers()
    assert reset(world)[0]==200
    entered=accounts.login('self-own','Temporary123',
        device={'kind':'phone','fp':'new-phone'},entrance='public',audience='employee')
    assert accounts.change_password('self-own','Temporary123','Personal123')['ok']
    monkeypatch.setenv('ACCOUNT_CUTOVER_AT_UTC','2000-01-01T00:00:00Z')
    with db.connect() as c:
        before=dict(c.execute("SELECT * FROM accounts WHERE username='self-own'").fetchone())
        devices=[dict(r) for r in c.execute('SELECT * FROM account_devices WHERE account_id=? ORDER BY id',(before['id'],))]
    phone=next(d for d in devices if d['kind']=='phone')
    assert call('POST','/admin/account_approve.json',headers,{'device_id':phone['id']})[0]==403
    status,result=call('POST','/admin/account_approve.json',headers,{'username':'self-own'})
    assert status==200 and result['active'],result
    with db.connect() as c:
        after=c.execute("SELECT * FROM accounts WHERE username='self-own'").fetchone()
        assert after['status']=='active' and not after['password_review_pending']
        assert after['password_hash']==before['password_hash']
        assert [dict(r) for r in c.execute('SELECT * FROM account_devices WHERE account_id=? ORDER BY id',(before['id'],))]==devices
        audit=c.execute("SELECT actor,decision FROM account_approval_events WHERE account_id=? AND action='password_reset' ORDER BY id DESC LIMIT 1",(before['id'],)).fetchone()
        assert tuple(audit)==('principal:owner-test','approved')
    assert call('GET','/api/bootstrap',{'X-App-Token':entered['token']})[1]['pending']


@pytest.mark.parametrize('root,personal',[(False,True),(True,False)])
def test_analyst_http_cannot_skip_root_owner_or_personal_password(world,monkeypatch,root,personal):
    activated(world)
    headers=analyst_headers(root=root)
    assert reset(world)[0]==200
    if personal:
        assert accounts.change_password('self-own','Temporary123','Personal123')['ok']
    monkeypatch.setenv('ACCOUNT_CUTOVER_AT_UTC','2000-01-01T00:00:00Z')
    status,result=call('POST','/admin/account_approve.json',headers,{'username':'self-own'})
    assert status>=400 and not result.get('ok')
    with db.connect() as c:
        acc=c.execute("SELECT * FROM accounts WHERE username='self-own'").fetchone()
        assert acc['status']=='awaiting' and acc['password_review_pending']


def test_analyst_http_still_cannot_approve_initial_staff_registration(world,monkeypatch):
    assert register()[0]==201
    headers=analyst_headers()
    monkeypatch.setenv('ACCOUNT_CUTOVER_AT_UTC','2000-01-01T00:00:00Z')
    status,result=call('POST','/admin/account_approve.json',headers,{'username':'self-own'})
    assert status==403 and result['error']=='registration_requires_attested_request'


def test_personal_password_review_rechecks_current_team(world):
    activated(world)
    assert reset(world)[0]==200
    assert accounts.change_password('self-own','Temporary123','Personal123')['ok']
    entered=accounts.login('self-own','Personal123',device={'kind':'phone','fp':'new-team-phone'},entrance='public',audience='employee')
    with db.connect() as c:
        device=c.execute("SELECT id FROM account_devices WHERE fingerprint='new-team-phone'").fetchone()[0]
    db.set_manager(MGR,'Synthetic Leader',[],[])
    assert not accounts.manager_approve(MGR,device_id=device)['ok']
    with db.connect() as c:
        row=c.execute("SELECT * FROM accounts WHERE username='self-own'").fetchone()
        assert row['password_review_pending'] and row['status']=='awaiting'
        assert c.execute('SELECT status FROM account_devices WHERE id=?',(device,)).fetchone()[0]=='pending'
