"""New CRM account entry screens through the real primary HTTP routes."""
import datetime as dt

import accounts
import crm_registration
import db
import pytest
import root_bootstrap
from test_owner_root_bootstrap import owner, OP
from test_pc_first_accounts import world, http, identity, proof, PASSWORD


REGISTRATION='/api/account/crm-registration'


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setenv('HARD_CRM_DESKTOP','1')
    monkeypatch.setenv('HARD_CRM_MOBILE','1')


def assignments():
    with db.connect() as c:
        c.execute("INSERT INTO clients(contract_id,client,responsible,responsible_soft) VALUES('CRM-REG-001','Synthetic','Own Employee','Foreign Employee')")


def pause(hours):
    db.set_setting('account_device_confirmation_paused_until_utc',
                   (dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=hours)).isoformat())


def phone(label):
    return dict(kind='phone',fp='android-fixture-'+label,label='Synthetic phone')


def test_public_options_hide_staff_and_submission_uses_current_assignments(world):
    assignments()
    status,options=http('GET',REGISTRATION)
    assert status==200
    assert 'employees' not in options
    status,result=http('POST',REGISTRATION,dict(role='employee',employee='Own Employee',username='new-owner',password=PASSWORD,
                    pc_proof=proof(world,'Own Employee','new-owner'),audience='crm-desktop'))
    assert status==201 and result['pending'] is True
    assert 'token' not in result
    assert http('POST',REGISTRATION,dict(role='employee',employee='Own Employee',username='duplicate-owner',password=PASSWORD,
                device=phone('duplicate'),audience='crm-mobile'))[0]==400
    assert http('POST',REGISTRATION,dict(role='employee',employee='Zero Portfolio',username='zero-claim',password=PASSWORD,
                device=phone('invalid'),audience='crm-mobile'))[0]==400


def test_employee_confirmation_pause_checks_code_device_and_expiration(world):
    assignments();pause(2)
    code=crm_registration.issue_employee_code('Own Employee')
    body=dict(role='employee',username='new-owner',password=PASSWORD,audience='crm-desktop',code=code)
    status,_=http('POST',REGISTRATION,{**body,'employee':'Own Employee','code':''})
    assert status==400
    status,_=http('POST',REGISTRATION,{**body,'employee':'Foreign Employee','pc_proof':proof(world,'Foreign Employee','new-owner')})
    assert status==400
    status,result=http('POST',REGISTRATION,{**body,'employee':'Own Employee','pc_proof':proof(world,'Own Employee','new-owner')})
    assert status==201 and result['pending'] is False
    status,entered=http('POST','/api/account/login',dict(username='new-owner',password=PASSWORD,
        audience='crm-desktop',device={'kind':'desktop','fp':world[2]},pc_proof=proof(world,'','new-owner')))
    assert status==200 and entered['action']=='ok'
    status,subject=http('GET','/api/crm-identity',headers={'X-App-Token':entered['token']})
    assert status==200 and subject['subject']['type']=='employee'
    with db.connect() as c:
        assert c.execute('SELECT used_at FROM crm_registration_invites').fetchone()[0]
    after=crm_registration.issue_employee_code('Foreign Employee')
    pause(-1)
    status,result=http('POST',REGISTRATION,dict(role='employee',employee='Foreign Employee',username='other-owner',password=PASSWORD,
                     code=after,device=phone('expired-window'),audience='crm-mobile'))
    assert status==201 and result['pending'] is True
    entered=accounts.login('other-owner',PASSWORD,device=phone('expired-window'),entrance='public',audience='crm-mobile')
    assert entered['action']=='ok_pending'


@pytest.mark.parametrize('role,region,city',[('manager','tj','Душанбе'),('manager','uz',''),('analyst','',None)])
def test_privileged_registration_requires_admin_and_opens_only_requested_role(owner,role,region,city):
    db.set_employee_city('Synthetic Employee','Душанбе')
    pause(2)
    profile='requested-profile'
    registration=dict(role=role,employee='Synthetic Applicant',country=region,city=city,username=profile,password=PASSWORD,
                      audience='crm-mobile',device=phone(role),code='irrelevant-for-privileged')
    status,result=http('POST',REGISTRATION,registration)
    assert status==201 and result['pending'] is True
    assert accounts.login(profile,PASSWORD,device=phone(role),entrance='public',audience='crm-mobile')['action'] in ('ok_pending','blocked')
    assert http('POST','/admin/account_approve.json',{'username':profile})[0] in (401,403)

    nonce=root_bootstrap.challenge(OP)['nonce']
    root_bootstrap.provision('synthetic-owner',PASSWORD,OP,nonce)
    pc=identity()
    superuser=accounts.login('synthetic-owner',PASSWORD,device={'kind':'desktop','fp':pc[2]},entrance='internal',
        audience='analyst',pc_proof=proof(pc,'','synthetic-owner'))
    assert superuser['action']=='ok'
    admin={'X-Pult-Token':superuser['token']}
    if role=='manager':
        status,listing=http('GET','/admin/accounts.json',headers=admin)
        assert status==200
        assert next(row for row in listing['accounts'] if row['username']==profile)['registration_scope']=={'country':region,'city':city}
    status,granted=http('POST','/admin/account_approve.json',{'username':profile},headers=admin)
    assert status==200 and granted['active']
    new=accounts.login(profile,PASSWORD,device=phone(role),entrance='public',audience='crm-mobile')
    assert new['action']=='ok' and new['role']==role and not new['must_change_password']
    second = identity()
    other = accounts.login(profile,PASSWORD,device={'kind':'desktop','fp':second[2]},entrance='internal',
                           audience='crm-desktop',pc_proof=proof(second,'',profile))
    assert other['action']=='ok_pending'
    headers={'X-Pult-Token':new['token'],**({'X-CRM-Entry':'analyst'} if role=='analyst' else {})}
    assert http('GET','/api/crm-identity',headers=headers)[1]['subject']['type']==role
    assert http('POST','/admin/account_approve.json',{'username':profile},headers=headers)[0] in (401,403)
