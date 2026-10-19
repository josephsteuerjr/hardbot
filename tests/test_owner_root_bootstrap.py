"""Root bootstrap policy on synthetic identities and a disposable SQLite DB."""
import base64
import hashlib
import json
import sqlite3

import pytest

import accounts
import config
import db
import root_bootstrap
import web
from test_pc_first_accounts import identity,proof,PASSWORD,http

OWNER=9781001
OP='synthetic-owner-operation-001'


@pytest.fixture
def owner(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'owner.db')
    monkeypatch.setattr(db,'admin_bootstrap_marker',lambda:tmp_path/'bootstrap')
    monkeypatch.setattr(web,'ADMIN_ID',OWNER)
    monkeypatch.setattr(config,'ADMIN_ID',OWNER)
    monkeypatch.setattr(root_bootstrap.os,'geteuid',lambda:0,raising=False)
    monkeypatch.delenv('ACCOUNT_CUTOVER_AT_UTC',raising=False)
    db.init_db()
    legacy=db.enroll_admin_device('legacy-test-pc','Synthetic Legacy')
    monkeypatch.setenv('ACCOUNT_CUTOVER_AT_UTC','2000-01-01T00:00:00Z')
    db.set_manager(OWNER,'Synthetic Owner',['Preserved City'],['Preserved Employee'],role='full')
    with db.connect() as c:
        c.execute("INSERT INTO clients(contract_id,client,responsible) VALUES('SYNTH-001','Synthetic Client','Preserved Employee')")
    web._ADMIN_FAILS.clear()
    return {'path':tmp_path,'legacy':legacy}


def test_owner_bootstrap_requires_real_root_context(owner,monkeypatch):
    monkeypatch.setattr(root_bootstrap.os,'geteuid',lambda:1000)
    with pytest.raises(PermissionError): root_bootstrap.provision('synthetic-owner',PASSWORD,OP,'no-nonce')
    with db.connect() as c: assert c.execute('SELECT count(*) FROM accounts').fetchone()[0]==0


def test_wrong_server_nonce_never_creates_owner(owner):
    root_bootstrap.challenge(OP)
    with pytest.raises(RuntimeError,match='SSH сервер'):root_bootstrap.provision('synthetic-owner',PASSWORD,OP,'wrong')
    with db.connect() as c:
        assert c.execute('SELECT count(*) FROM accounts').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM account_admin_grants').fetchone()[0]==0


def test_root_owner_has_explicit_single_root_audit_and_preserved_scope(owner):
    with db.connect() as c: before=dict(c.execute('SELECT * FROM managers WHERE telegram_id=?',(OWNER,)).fetchone())
    nonce=root_bootstrap.challenge(OP)['nonce']
    result=root_bootstrap.provision('synthetic-owner',PASSWORD,OP,nonce)
    assert result['ok'] and result['status']=='active'
    with sqlite3.connect(result['backup']) as backup:
        assert backup.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert backup.execute('SELECT count(*) FROM accounts').fetchone()[0]==0
        assert backup.execute('SELECT count(*) FROM clients').fetchone()[0]==1
    with db.connect() as c:
        account=c.execute('SELECT * FROM accounts').fetchone()
        events=[dict(r) for r in c.execute('SELECT * FROM account_approval_events')]
        assert dict(c.execute('SELECT * FROM managers WHERE telegram_id=?',(OWNER,)).fetchone())==before
    assert account['activated_by']=='ssh-root' and json.loads(account['approvals'])==[]
    assert account['role']=='analyst' and account['telegram_id']!=OWNER
    assert len(events)==1 and events[0]['action']=='owner_bootstrap' and events[0]['actor']=='ssh-root:root-owner'
    again=root_bootstrap.provision('synthetic-owner','Different123',OP,'consumed')
    assert again['already_applied']
    assert accounts.verify_password(PASSWORD,account['password_hash'])
    with pytest.raises(RuntimeError,match='уже существует'):
        root_bootstrap.provision('another-owner',PASSWORD,OP+'-different','missing')


def test_owner_analyst_password_session_works_and_unlinked_admin_is_closed(owner):
    root_bootstrap.provision('synthetic-owner',PASSWORD,OP,root_bootstrap.challenge(OP)['nonce'])
    pc=identity()
    login=accounts.login('synthetic-owner',PASSWORD,device={'kind':'desktop','fp':pc[2]},entrance='internal',
        audience='analyst',pc_proof=proof(pc,'','synthetic-owner'))
    assert login['action']=='ok',login
    status,data=http('GET','/admin/accounts.json',headers={'X-Pult-Token':login['token']})
    assert status==200 and data['approver']=='root-owner'
    legacy=owner['legacy']
    assert http('GET','/admin/accounts.json',headers={'X-Device-Token':legacy['token'],'X-Device-Id':'legacy-test-pc'})[0]==401


def test_manager_role_and_forged_audience_do_not_grant_admin(owner):
    manager=OWNER+1
    db.set_manager(manager,'Synthetic Manager',[],['Preserved Employee'],role='manager')
    assert accounts.create_account('Synthetic Manager','synthetic-manager',PASSWORD,role='manager',telegram_id=manager)['ok']
    accounts.approve_account('synthetic-manager','synthetic-admin-one')
    accounts.approve_account('synthetic-manager','synthetic-admin-two')
    accounts.change_password('synthetic-manager',PASSWORD,'Changed123')
    pc=identity()
    result=accounts.login('synthetic-manager','Changed123',device={'kind':'desktop','fp':pc[2]},entrance='internal',
        audience='analyst',pc_proof=proof(pc,'','synthetic-manager','Changed123'))
    assert result['action']=='blocked' and 'token' not in result


def test_existing_real_operator_keeps_same_principal_and_cannot_be_a_second_human(owner):
    legacy=owner['legacy']
    with db.connect() as c:
        c.execute("UPDATE admin_devices SET account_approver='synthetic-real-person',revoked_at='2026-01-01' WHERE id=?",(legacy['id'],))
    nonce=root_bootstrap.challenge(OP)['nonce']
    with pytest.raises(RuntimeError,match='личность администратора'):
        root_bootstrap.provision('synthetic-owner',PASSWORD,OP,nonce)
    result=root_bootstrap.provision('synthetic-owner',PASSWORD,OP,nonce,
        legacy_token=legacy['token'],legacy_device_id='legacy-test-pc')
    assert result['principal']=='synthetic-real-person'
    other=OP+'-other'
    with pytest.raises(RuntimeError,match='уже существует'):
        root_bootstrap.provision('second-name',PASSWORD,other,root_bootstrap.challenge(other)['nonce'],
            legacy_token=legacy['token'],legacy_device_id='legacy-test-pc')
    pc=identity()
    result=accounts.login('synthetic-owner',PASSWORD,device={'kind':'desktop','fp':pc[2]},entrance='internal',
        audience='analyst',pc_proof=proof(pc,'','synthetic-owner'))
    status,data=http('GET','/admin/accounts.json',headers={'X-Pult-Token':result['token']})
    assert status==200 and data['approver']=='synthetic-real-person'


def test_explicit_root_recovery_revokes_old_pc_and_permits_one_new_pc(owner):
    root_bootstrap.provision('synthetic-owner',PASSWORD,OP,root_bootstrap.challenge(OP)['nonce'])
    pc=identity()
    def login(ident,secret):
        return accounts.login('synthetic-owner',secret,device={'kind':'desktop','fp':ident[2]},entrance='internal',
            audience='analyst',pc_proof=proof(ident,'','synthetic-owner',secret))
    old=login(pc,PASSWORD)
    assert old['action']=='ok'
    op=OP+'-recover';changed='Changed'+'456!'
    result=root_bootstrap.provision('synthetic-owner',changed,op,root_bootstrap.challenge(op)['nonce'],recovery=True)
    assert result['recovery'] and result['principal']=='root-owner'
    assert http('GET','/admin/accounts.json',headers={'X-Pult-Token':old['token']})[0] in (401,403)
    new=login(identity(),changed)
    assert new['action']=='ok',new
    other=login(identity(),changed)
    assert other['action']=='ok_pending',other
    assert http('GET','/admin/accounts.json',headers={'X-Pult-Token':other['token']})[0] in (401,403)
    assert http('GET','/admin/accounts.json',headers={'X-Pult-Token':new['token']})[0]==200


def test_strict_blocks_legacy_operator_issuance_but_allows_separate_engineering(owner):
    for action in (lambda:db.enroll_admin_device('forbidden-pc'),lambda:db.issue_enroll_code('forbidden')):
        with pytest.raises(ValueError,match='закрыт'): action()
    engineer=db.enroll_admin_device('synthetic-engineer','Synthetic engineer',role='engineer')
    assert engineer['token']


def test_primary_owner_cannot_be_locked_by_generic_ui_actions_and_can_change_own_password(owner):
    root_bootstrap.provision('synthetic-owner',PASSWORD,OP,root_bootstrap.challenge(OP)['nonce'])
    pc=identity()
    login=accounts.login('synthetic-owner',PASSWORD,device={'kind':'desktop','fp':pc[2]},entrance='internal',
        audience='analyst',pc_proof=proof(pc,'','synthetic-owner'))
    headers={'X-Pult-Token':login['token']}
    status,listing=http('GET','/admin/accounts.json',headers=headers)
    assert status==200 and listing['accounts'][0]['owner_root'] is True
    device_id=listing['accounts'][0]['devices'][0]['id']
    with db.connect() as c: before=dict(c.execute('SELECT * FROM accounts').fetchone())
    for body in [*({'action':action,'username':'synthetic-owner'} for action in ('reset','disable','enable')),
                 {'action':'revoke','device_id':device_id}]:
        status,result=http('POST','/admin/account_action.json',body,headers)
        assert status==403 and 'root' in result['error'],result
        assert http('GET','/admin/accounts.json',headers=headers)[0]==200
    with db.connect() as c: assert dict(c.execute('SELECT * FROM accounts').fetchone())==before
    status,result=http('POST','/api/account/password',{'username':'synthetic-owner','current':PASSWORD,'new':'Changed123!'})
    assert status==200,result
    with db.connect() as c: assert accounts.verify_password('Changed123!',c.execute('SELECT password_hash FROM accounts').fetchone()[0])


def test_canonical_manager_name_creation_is_atomic_preserves_scopes_and_blocks_employee_duplicate(owner):
    import staff_roster
    uid=OWNER+1; canonical='Synthetic Canonical Leader'
    db.set_manager(uid,'Short Name',['Preserved City'],['Preserved Employee'],direction='tj',role='manager')
    with db.connect() as c: before=dict(c.execute('SELECT * FROM managers WHERE telegram_id=?',(uid,)).fetchone())
    result=accounts.create_manager_account(uid,'canonical-leader',PASSWORD,display_name=canonical,by='synthetic-admin')
    assert result['ok'] and result['employee']==canonical
    with db.connect() as c:
        after=dict(c.execute('SELECT * FROM managers WHERE telegram_id=?',(uid,)).fetchone())
        assert {k:v for k,v in after.items() if k!='name'}=={k:v for k,v in before.items() if k!='name'}
    rows=[{'employee':canonical,'office':'Preserved City','products':['hard'],'manager_uid':OWNER}]
    preview=staff_roster.preview(rows);staff_roster.apply(rows,preview['sha256'],by='synthetic-admin')
    pc=identity()
    status,result=http('POST','/api/account/register',{'employee':canonical,'username':'duplicate-worker','password':PASSWORD,
        'pc_proof':proof(pc,canonical,'duplicate-worker')})
    assert status==400 and 'администратор' in result['error'],result
    other=OWNER+2
    db.set_manager(other,'Unchanged Name',[],[],role='manager')
    failed=accounts.create_manager_account(other,'canonical-leader',PASSWORD,display_name='Failed Rename',by='synthetic-admin')
    assert not failed['ok']
    with db.connect() as c: assert c.execute('SELECT name FROM managers WHERE telegram_id=?',(other,)).fetchone()[0]=='Unchanged Name'
