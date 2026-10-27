"""A completed portfolio import must update real registration eligibility."""
import json
import sqlite3

import db
import employee_identity
import staff_roster
from fields import FIELDS
from test_pc_first_accounts import world, http, identity, proof, PASSWORD, MANAGER


def import_employee(name='New Employee', *, preview=False):
    fields=list(FIELDS)
    data={'contract_id':'SYN-NEW-OWNER','client':'Synthetic Client','responsible':name}
    grid=[[None]*(len(fields)+1),[f.title for f in fields]+['ответственный Сотрудник СОФТ'],
          [f.source for f in fields]+['софт'],[data.get(f.key) for f in fields]+[name]]
    report=db.import_grid(grid,source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,import_profile='both',preview_only=preview)
    assert not report['blocked'],report


def test_import_updates_picker_without_replacing_zero_portfolio_roster(world):
    assert 'New Employee' not in {r['employee'] for r in http('GET','/api/account/staff')[1]['employees']}
    import_employee(preview=True)
    assert 'New Employee' not in {r['employee'] for r in http('GET','/api/account/staff')[1]['employees']}
    import_employee()
    import_employee()  # Idempotent import and repeated reads must not duplicate people.
    status,body=http('GET','/api/account/staff')
    assert status==200
    names=[r['employee'] for r in body['employees']]
    assert names.count('New Employee')==1 and 'Zero Portfolio' in names
    assert next(r for r in body['employees'] if r['employee']=='New Employee')['products']==['hard','soft']
    with db.connect() as c:
        assert c.execute('SELECT count(*) FROM staff_roster').fetchone()[0]==3
        assert c.execute('SELECT count(*) FROM accounts').fetchone()[0]==0


def test_new_owner_registers_pending_then_only_own_manager_can_approve(world):
    import_employee()
    db.set_employee_city('New Employee','Known office')
    ident=identity()
    status,result=http('POST','/api/account/register',{'employee':'New Employee','username':'new-employee',
        'password':PASSWORD,'role':'full','office':'Forged office','pc_proof':proof(ident,'New Employee','new-employee')})
    assert status==201,result
    import accounts
    with db.connect() as c:
        acc=c.execute('SELECT * FROM accounts').fetchone();dev=c.execute('SELECT * FROM account_devices').fetchone()
        assert acc['role']=='agent' and acc['status']=='awaiting' and acc['office']=='Known office'
        assert dev['status']=='pending'
    assert accounts.device_gate_by_token('agent_devices',dev['agent_dev_id'])[0]=='pending'
    assert not accounts.manager_approve(MANAGER+1,device_id=dev['id'],observed_pc=True)['ok']
    db.set_manager(MANAGER,'Synthetic Leader',[],['Own Employee','Zero Portfolio','New Employee'])
    assert accounts.manager_approve(MANAGER,device_id=dev['id'],observed_pc=True)['active']
    status,bootstrap=http('GET','/api/bootstrap?mode=soft',headers={'X-App-Token':result['token']})
    assert status==200 and len(bootstrap['persons'])==1


def test_inactive_alias_and_product_label_never_create_another_candidate(world):
    items=[{'employee':'Retired Employee','products':['soft'],'active':False,
            'product_names':{'soft':'Retired Employee СОФТ'}}]
    checked=staff_roster.preview(items);assert staff_roster.apply(items,checked['sha256'],by='synthetic')['ok']
    with db.connect() as c:
        eid=c.execute("SELECT employee_id FROM employee_aliases WHERE name='Retired Employee'").fetchone()[0]
        c.execute('INSERT INTO employee_aliases(name,employee_id) VALUES(?,?)',('Retired Alias',eid))
        for i,name in enumerate(['Retired Employee','Retired Employee СОФТ','Retired Alias','Own Employee SOFT']):
            c.execute('INSERT INTO clients(contract_id,responsible_soft) VALUES(?,?)',('SYN-HIDDEN-'+str(i),name))
    status,body=http('GET','/api/account/staff');assert status==200
    names={r['employee'] for r in body['employees']}
    assert not names & {'Retired Employee','Retired Employee СОФТ','Retired Alias','Own Employee SOFT'}
    assert 'Own Employee' in names and 'Zero Portfolio' in names


def test_privileged_portfolio_owner_cannot_self_register(world):
    import_employee('Synthetic Leader')
    assert 'Synthetic Leader' not in {r['employee'] for r in http('GET','/api/account/staff')[1]['employees']}
    ident=identity()
    status,_=http('POST','/api/account/register',{'employee':'Synthetic Leader','username':'forged-leader',
        'password':PASSWORD,'pc_proof':proof(ident,'Synthetic Leader','forged-leader')})
    assert status==400
    with db.connect() as c:assert c.execute('SELECT count(*) FROM accounts').fetchone()[0]==0


def test_candidate_read_works_on_readonly_database_and_refuses_ambiguous_identity(world):
    import_employee()
    with db.connect() as c:
        for ident,name in [('syn-a','Ambiguous Employee'),('syn-b','AMBIGUOUS EMPLOYEE')]:
            c.execute('INSERT INTO employee_identities(id,display_name) VALUES(?,?)',(ident,name))
            c.execute('INSERT INTO employee_aliases(name,employee_id) VALUES(?,?)',(name,ident))
        c.execute("INSERT INTO clients(contract_id,responsible_soft) VALUES('SYN-AMBIGUOUS','Ambiguous Employee')")
        before=list(c.iterdump())
    c=sqlite3.connect('file:'+str(db.DB_PATH)+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
    try:
        rows=staff_roster.candidates(c)
        assert 'New Employee' in {r['employee'] for r in rows}
        assert 'Ambiguous Employee' not in {r['employee'] for r in rows}
        assert list(c.iterdump())==before
    finally:c.close()


def test_enrolled_person_alias_without_roster_is_not_offered_a_second_registration(world):
    import accounts
    assert accounts.create_account('Legacy Employee','legacy-enrolled',PASSWORD)['ok']
    with db.connect() as c:
        employee_identity.ensure(c,['Legacy Employee'])
        eid=c.execute("SELECT employee_id FROM employee_aliases WHERE name='Legacy Employee'").fetchone()[0]
        c.execute('INSERT INTO employee_aliases(name,employee_id) VALUES(?,?)',('Legacy Alias',eid))
        c.execute("INSERT INTO clients(contract_id,responsible_soft) VALUES('SYN-ENROLLED','Legacy Alias')")
        c.execute("INSERT INTO clients(contract_id,responsible_soft) VALUES('SYN-ENROLLED-MAIN','Legacy Employee')")
    status,body=http('GET','/api/account/staff');assert status==200
    assert 'Legacy Alias' not in {r['employee'] for r in body['employees']}
    assert [r['employee'] for r in body['employees']].count('Legacy Employee')==1
    with db.connect() as c:assert c.execute('SELECT count(*) FROM accounts').fetchone()[0]==1
