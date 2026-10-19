"""Phone-first employee signup, real SQLite and HTTP, synthetic identities only."""
import pytest
import accounts
import db
import web
from test_pc_first_accounts import world, http, PASSWORD, MANAGER, pc_login, identity


def signup(name='Own Employee',username='mobile-own',**extra):
    return http('POST','/api/account/register',{
        'employee':name,'username':username,'password':PASSWORD,'audience':'mobile',
        'device':{'kind':'phone','fp':'synthetic-phone-001','label':'Synthetic iPhone'},**extra})


def slot(username='mobile-own'):
    with db.connect() as c:
        return dict(c.execute('SELECT d.* FROM account_devices d JOIN accounts a ON a.id=d.account_id WHERE a.username=? ORDER BY d.id DESC LIMIT 1',(username,)).fetchone())


def test_phone_signup_pending_then_own_manager_unlocks_without_pc(world):
    status,result=signup(role='full',office='Forged office',telegram_id=MANAGER)
    assert status==201 and result['token'] and result['role']=='agent'
    d=slot();headers={'X-App-Token':result['token']}
    assert d['kind']=='phone' and d['status']=='pending'
    assert http('GET','/api/bootstrap',headers=headers)[1]['pending']
    assert not accounts.manager_approve(MANAGER+1,device_id=d['id'])['ok']
    assert accounts.manager_approve(MANAGER,device_id=d['id'])['active']
    status,data=http('GET','/api/bootstrap',headers=headers)
    assert status==200 and not data.get('pending') and data['employee']=='Own Employee'
    row=accounts.entered_list()[0]
    assert row['ready'] and not row['desktop_ready'] and row['desktop'] is None
    with db.connect() as c:
        assert c.execute("SELECT count(*) FROM account_devices WHERE kind='desktop'").fetchone()[0]==0
        assert c.execute('SELECT office FROM accounts').fetchone()[0]=='Synthetic office'
    assert not accounts.approve_device(d['id'],'forged-admin')['ok']


@pytest.mark.parametrize('extra',[
    {'audience':'employee'}, {'device':{'kind':'desktop','fp':'synthetic-desktop'}},
    {'device':{'kind':'phone','fp':''}}, {'device':'forged'},
    {'pc_proof':{'challenge_id':'forged','signature':'forged'}},
])
def test_mobile_cannot_create_unsigned_pc_or_ignore_bad_signature(world,extra):
    assert signup(**extra)[0]==400
    with db.connect() as c:assert c.execute('SELECT count(*) FROM accounts').fetchone()[0]==0


def test_phone_signup_duplicate_and_privileged_identity_remain_rejected(world):
    assert signup()[0]==201
    assert signup(username='another-login')[0]==400
    assert signup('Unknown Employee','unknown-user')[0]==400
    db.set_manager(MANAGER+2,'Zero Portfolio',[],[],role='manager')
    assert signup('Zero Portfolio','privileged-user')[0]==400
    with db.connect() as c:assert c.execute('SELECT count(*) FROM accounts').fetchone()[0]==1


def test_phone_replacement_revokes_old_token_and_pc_stays_pending(world):
    result=signup()[1];first=slot()
    assert accounts.manager_approve(MANAGER,device_id=first['id'])['active']
    second=accounts.login('mobile-own',PASSWORD,device={'kind':'phone','fp':'synthetic-phone-002'},entrance='public',audience='mobile')
    latest=slot();assert latest['status']=='pending'
    assert accounts.device_gate_by_token('agent_devices',first['agent_dev_id'])[0]=='ok'
    assert accounts.manager_approve(MANAGER,device_id=latest['id'])['active']
    assert accounts.device_gate_by_token('agent_devices',first['agent_dev_id'])[0]=='blocked'
    assert not http('GET','/api/bootstrap',headers={'X-App-Token':second['token']})[1].get('pending')
    pc=pc_login('mobile-own',identity());assert pc['action']=='ok_pending'
    d=slot();assert d['kind']=='desktop' and not accounts.manager_approve(MANAGER,device_id=d['id'])['ok']
    assert accounts.manager_approve(MANAGER,device_id=d['id'],observed_pc=True)['active']


def test_soft_phone_records_call_and_hard_has_separate_portfolio(world):
    with db.connect() as c:
        c.execute("INSERT INTO clients(contract_id,client,responsible,responsible_soft) VALUES('SYN-MOBILE','Synthetic Client','Own Employee','Own Employee')")
    result=signup()[1];assert accounts.manager_approve(MANAGER,device_id=slot()['id'])['active']
    headers={'X-App-Token':result['token'],'X-Work-Product':'soft'}
    status,soft=http('GET','/api/bootstrap?mode=soft',headers=headers)
    assert status==200 and len(soft['persons'])==1 and soft['roles']['soft']
    status,saved=http('POST','/api/save',{
        'rep':'SYN-MOBILE','kind':'call','finish':True,'op_id':'synthetic-mobile-call',
        'form_version':web.FORM_PROTOCOL_VERSION,'input_keys':['soft_call_status','soft_comment'],
        'values':{'soft_call_status':'Нет контакта','soft_comment':'Synthetic phone note'},'append_comment':True},headers)
    assert status==200 and saved['ok'],saved
    with db.connect() as c:
        assert c.execute('SELECT count(*) FROM calls').fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM visits').fetchone()[0]==0
    status,hard=http('GET','/api/bootstrap?mode=field',headers={'X-App-Token':result['token'],'X-Work-Product':'hard'})
    assert status==200 and len(hard['persons'])==1 and hard['roles']['field']
