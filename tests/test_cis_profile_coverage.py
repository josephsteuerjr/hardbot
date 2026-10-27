"""Stable principal, exact product portfolios and scoped manager filters."""
import json
import subprocess
from pathlib import Path

import pytest

import db
import staff_roster
import web
from test_pc_first_accounts import world,approve_pc,http,MANAGER

CANONICAL='Own Employee'
HARD='Own Employee HARD'
SOFT='Own Employee SOFT'


def setup(world):
    items=[{'employee':CANONICAL,'products':['hard','soft'],'manager_uid':MANAGER,
            'product_names':{'hard':HARD,'soft':SOFT}}]
    preview=staff_roster.preview(items);assert staff_roster.apply(items,preview['sha256'],by='synthetic')['ok']
    with db.connect() as c:
        for cid,hard,soft in [('SYN-CIS-HARD',HARD,''),('SYN-CIS-SOFT','',SOFT),
                              ('SYN-CIS-FOREIGN','Foreign Employee','Foreign Employee')]:
            c.execute('INSERT INTO clients(contract_id,client,responsible,responsible_soft) VALUES(?,?,?,?)',
                      (cid,'SYNTHETIC '+cid,hard,soft))
    data,_=approve_pc(world)
    return {'X-App-Token':data['token']}


def test_both_products_keep_principal_but_read_different_assignments(world):
    headers=setup(world)
    identity=http('GET','/api/identity',headers=headers)[1]
    hard=http('GET','/api/bootstrap',headers=headers)[1]
    soft=http('GET','/api/bootstrap?mode=soft',headers=headers)[1]
    assert identity['employee']==hard['employee']==soft['employee']==CANONICAL
    assert identity['uid']==hard['uid']==soft['uid']
    assert set(identity['owner_names'])==set(hard['owner_names'])==set(soft['owner_names'])=={CANONICAL,HARD,SOFT}
    assert hard['portfolio_employee']==HARD and soft['portfolio_employee']==SOFT
    assert {p['rep'] for p in hard['persons']}=={'SYN-CIS-HARD'}
    assert {p['rep'] for p in soft['persons']}=={'SYN-CIS-SOFT'}


def test_empty_soft_never_returns_hard_cases(world):
    headers=setup(world)
    with db.connect() as c:c.execute("UPDATE clients SET responsible_soft='' WHERE responsible_soft=?",(SOFT,))
    assert http('GET','/api/bootstrap',headers=headers)[1]['persons']
    status,soft=http('GET','/api/bootstrap?mode=soft',headers=headers)
    assert status==200 and soft['persons']==[] and not (soft.get('roles') or {}).get('soft')


def test_manager_display_filter_resolves_only_already_authorized_owner(world,monkeypatch):
    setup(world)
    manager={'telegram_id':MANAGER,'role':'manager','name':'Synthetic Leader'}
    team=[CANONICAL,HARD,SOFT]
    monkeypatch.setattr(web,'_manager_gate',lambda request:(MANAGER,manager,team))
    from urllib.parse import quote
    for profile,cid in [('hard','SYN-CIS-HARD'),('soft','SYN-CIS-SOFT')]:
        status,body=http('GET','/api/manager/portfolio?mode='+profile+'&employee='+quote(CANONICAL))
        assert status==200 and {i['rep'] for i in body['items']}=={cid}
    team[:]=[CANONICAL]
    assert http('GET','/api/manager/portfolio?mode=soft&employee='+quote(CANONICAL))[0]==404
    team[:]=[CANONICAL,HARD,SOFT]
    assert http('GET','/api/manager/portfolio?mode=soft&employee=Foreign%20Employee')[0]==404


def test_generic_notes_and_receipts_respect_same_explicit_person_in_either_mode(world):
    headers=setup(world)
    for profile in ['hard','soft']:
        current={**headers,'X-Work-Product':profile}
        for cid in ['SYN-CIS-HARD','SYN-CIS-SOFT']:
            assert http('POST','/api/note',{'text':'Synthetic note','rep':cid,'op_id':'synthetic-'+profile+'-'+cid},current)[0]==200
            # The ownership gate is before media validation; no photo is written.
            assert http('POST','/api/receipt?rep='+cid+'&op=synthetic-receipt',{},current)[0]==415
        assert http('POST','/api/note',{'text':'Foreign','rep':'SYN-CIS-FOREIGN'},current)[0]==404
        assert http('POST','/api/receipt?rep=SYN-CIS-FOREIGN&op=synthetic-receipt',{},current)[0]==404


def test_ambiguous_or_inactive_roster_cannot_supply_alias_proof(world):
    setup(world)
    with db.connect() as c:
        c.execute("UPDATE staff_roster SET active=0 WHERE employee_id=(SELECT employee_id FROM employee_aliases WHERE name=?)",(CANONICAL,))
    assert staff_roster.principal_names(CANONICAL)==[CANONICAL]


def test_actual_shell_accepts_declared_legacy_alias_only_for_same_uid_and_routes_work():
    source=Path(__file__).resolve().parents[1]/'server/miniapp/index.html'
    script="""const fs=require('fs'),assert=require('assert/strict');
const s=fs.readFileSync(process.argv[1],'utf8'),a=s.indexOf('function setPrincipal(d){'),b=s.indexOf('// Факт сначала',a);
assert(a>0&&b>a);const STATE={};eval(s.slice(a,b));
setPrincipal({uid:17,employee:'Synthetic Person',owner_names:['Synthetic Person','Synthetic Person SOFT']});
assert(queueBelongs({_owner:{uid:17,employee:'Synthetic Person SOFT'}}));
assert(!queueBelongs({_owner:{uid:18,employee:'Synthetic Person SOFT'}}));
assert(!queueBelongs({_owner:{uid:17,employee:'Foreign Person'}}));
assert(!queueBelongs({_owner:{employee:'Synthetic Person SOFT'}}));
STATE.uid=18;assert(!queueBelongs({_owner:{uid:18,employee:'Synthetic Person SOFT'}}));
setPrincipal({uid:17,employee:'Synthetic Person',owner_names:['Synthetic Person']});
assert(!queueBelongs({_owner:{uid:17,employee:'Synthetic Person SOFT'}}));
const headers={'X-App-Token':'synthetic','X-Work-Product':'soft','Content-Type':'application/json'};
for(const kind of ['photo','marker','save'])assert.equal(queuedHeaders({kind},headers)['X-Work-Product'],'hard');
for(const entry of [{kind:'save',save_kind:'call'},{kind:'payment'}])assert.equal(queuedHeaders(entry,headers)['X-Work-Product'],'soft');
assert.equal(headers['X-Work-Product'],'soft');assert.equal(queuedHeaders({kind:'photo'},headers)['X-App-Token'],'synthetic');
"""
    subprocess.run(['node','-e',script,str(source)],check=True,capture_output=True)


def test_legacy_alias_queue_retries_same_operation_and_survives_offline_reload():
    from shell_node import run_shell
    result=run_shell(r'''
const token="TOK-A-aaaaaaaaaaaaaaaaaaaa";
const person={uid:17,employee:"Synthetic Person",owner_names:["Synthetic Person","Synthetic Person SOFT"],persons:[]};
const srv=fakeServer({tokens:{[token]:person},routes:{"/api/identity":()=>({status:200,body:{ok:true,...person}})}});
const w=newWorld({ls:{app_token:token},idb:{queue:[
 {op_id:"old-call",kind:"save",save_kind:"call",rep:"R1",values:{soft_comment:"Synthetic"},failed:"server-owner",_owner:{uid:17,employee:"Synthetic Person SOFT"}},
 {op_id:"foreign-call",kind:"save",save_kind:"call",rep:"R2",values:{},_owner:{uid:18,employee:"Synthetic Person SOFT"}}
]}});
const p=await openPage(w,{url:"https://hard.test/",server:srv});await p.booted();await settle(w);
await p.run("flushQueue()");await settle(w);
out.sent=p.fetchLog.filter(r=>r.url==="/api/save").map(r=>({body:JSON.parse(r.body)}));
out.queue=await p.run('IDB.all("queue")');
srv.cfg.tokens[token]="neterr";srv.cfg.routes={};
const offline=await openPage(w,{url:"https://hard.test/",server:srv});await offline.booted();
out.offline=await offline.run('({uid:STATE.uid,employee:STATE.employee,owners:STATE.ownerNames,accept:queueBelongs({_owner:{uid:17,employee:"Synthetic Person SOFT"}})})');
''')
    assert [r['body']['op_id'] for r in result['sent']]==['old-call']
    assert [r['op_id'] for r in result['queue']]==['foreign-call']
    assert result['offline']['uid']==17 and result['offline']['accept']
