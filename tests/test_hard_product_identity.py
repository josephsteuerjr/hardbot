"""Explicit Hard labels must preserve the account and durable work owner."""
import json
from pathlib import Path
import shutil
import subprocess
import time

import pytest

import accounts
import db
import staff_roster
from test_pc_first_accounts import world, approve_pc, http, MANAGER, PASSWORD

NAME='Own Employee'
HARD='Own Employee HARD'


def configure_hard():
    items=[{'employee':NAME,'office':'Synthetic office','products':['hard','soft'],
            'product_names':{'hard':HARD,'soft':NAME},'manager_uid':MANAGER}]
    preview=staff_roster.preview(items)
    assert staff_roster.apply(items,preview['sha256'],by='synthetic')['ok']


def ready(world, kind='desktop', *, mapped=True):
    data,_=approve_pc(world)
    with db.connect() as c:
        for cid,client,hard,soft in [('SYN-HARD-A','SYNTHETIC ALPHA',HARD if mapped else NAME,''),
                                    ('SYN-HARD-B','SYNTHETIC BETA',HARD if mapped else NAME,''),
                                    ('SYN-SOFT','SYNTHETIC SOFT','',NAME),
                                    ('SYN-FOREIGN','SYNTHETIC FOREIGN','Foreign Employee','Foreign Employee')]:
            c.execute('INSERT INTO clients(contract_id,client,responsible,responsible_soft) VALUES(?,?,?,?)',
                      (cid,client,hard,soft))
    if mapped:configure_hard()
    if kind=='phone':
        status,data=http('POST','/api/account/login',{'username':'own-employee','password':PASSWORD,
            'audience':'mobile','device':{'kind':'phone','fp':'synthetic-alias-phone'}})
        assert status==200,data
        with db.connect() as c:device=c.execute("SELECT id FROM account_devices WHERE kind='phone'").fetchone()[0]
        assert accounts.manager_approve(MANAGER,device_id=device)['active']
    return {'X-App-Token':data['token']}


@pytest.mark.parametrize('kind',['desktop','phone'])
def test_hard_bootstrap_cards_and_identity_share_stable_principal(world,kind):
    headers=ready(world,kind)
    status,identity=http('GET','/api/identity',headers=headers);assert status==200
    status,hard=http('GET','/api/bootstrap',headers=headers);assert status==200
    status,soft=http('GET','/api/bootstrap?mode=soft',headers=headers);assert status==200
    assert hard['uid']==soft['uid']==identity['uid']
    assert hard['employee']==soft['employee']==identity['employee']==NAME
    assert {p['rep'] for p in hard['persons']}=={'SYN-HARD-A','SYN-HARD-B'}
    assert {p['rep'] for p in soft['persons']}=={'SYN-SOFT'}
    assert hard['roles']['field'] and soft['roles']['field'] and hard['roles']['soft']
    status,cards=http('GET','/api/cards',headers=headers);assert status==200
    assert identity['scope_version']==hard['scope_version']==soft['scope_version']
    assert {'SYN-HARD-A','SYN-HARD-B'}<=set(cards['cards'])
    assert 'SYN-FOREIGN' not in cards['cards']
    assert http('GET','/api/person?rep=SYN-HARD-A',headers=headers)[0]==200
    assert http('GET','/api/person?rep=SYN-FOREIGN',headers=headers)[0]==404
    assert http('GET','/api/person?rep=SYN-HARD-A&mode=soft',headers=headers)[0]==404
    assert http('GET','/api/person?rep=SYN-SOFT&mode=soft',headers=headers)[0]==200


def test_phone_durable_owner_still_matches_actual_shell_queue_guard(world):
    headers=ready(world,'phone')
    identity=http('GET','/api/identity',headers=headers)[1]
    bootstrap=http('GET','/api/bootstrap',headers=headers)[1]
    node=shutil.which('node')
    if not node:pytest.skip('node unavailable')
    source=Path(__file__).resolve().parents[1]/'server/miniapp/index.html'
    script="""const fs=require('fs'),assert=require('assert/strict');
const s=fs.readFileSync(process.argv[1],'utf8'),start=s.indexOf('function queueBelongs(it){'),end=s.indexOf('// Факт сначала',start);
assert(start>0&&end>start);const d=JSON.parse(process.argv[2]),STATE=d.bootstrap;
eval(s.slice(start,end));assert(queueBelongs({_owner:{uid:d.identity.uid,employee:d.identity.employee}}));
assert(!queueBelongs({_owner:{uid:d.identity.uid+1,employee:d.identity.employee}}));"""
    subprocess.run([node,'-e',script,str(source),json.dumps({'identity':identity,'bootstrap':bootstrap})],check=True,capture_output=True)


def test_prior_office_acknowledgement_survives_label_configuration(world):
    headers=ready(world,mapped=False)
    status,card=http('GET','/api/person?rep=SYN-HARD-A',headers=headers);assert status==200
    operation={'rep':'SYN-HARD-A','kind':'office','finish':True,'action':'note','op_id':'synthetic-before-hard-label',
        'occurred_at':int(time.time()*1000),'revision':card['office']['revision'],'values':{'note':'Synthetic queued work'}}
    assert http('POST','/api/office/action',operation,headers)[0]==200
    with db.connect() as c:
        before=dict(c.execute('SELECT * FROM office_actions').fetchone())
        c.execute('UPDATE clients SET responsible=? WHERE responsible=?',(HARD,NAME))
        account_before=list(c.execute('SELECT id,employee,telegram_id,status,password_hash FROM accounts'))
    configure_hard()
    status,result=http('POST','/api/office/action',operation,headers)
    assert status==200 and result['duplicate'],result
    assert http('POST','/api/office/action',{**operation,'values':{'note':'Changed content'}},headers)[0]==409
    with db.connect() as c:
        assert dict(c.execute('SELECT * FROM office_actions').fetchone())==before
        assert c.execute('SELECT count(*) FROM office_actions').fetchone()[0]==1
        assert list(c.execute('SELECT id,employee,telegram_id,status,password_hash FROM accounts'))==account_before
    assert http('GET','/api/identity',headers=headers)[1]['employee']==NAME
    import office_work,workday
    report=office_work.report(HARD,'hard',workday.today().isoformat(),workday.today().isoformat())
    assert report['stats']['office']==1 and report['events'][0]['employee']==NAME


def test_access_version_tracks_hard_alias_assignment_without_widening_portfolio(world):
    import access_policy
    headers=ready(world)
    before=access_policy.version([NAME])
    assert before==access_policy.version([HARD])
    with db.connect() as c:
        c.execute("INSERT INTO clients(contract_id,client,responsible) VALUES('SYN-NEW-HARD','SYNTHETIC NEW',?)",(HARD,))
    assert access_policy.version([NAME])!=before
    assert http('GET','/api/person?rep=SYN-FOREIGN',headers=headers)[0]==404
