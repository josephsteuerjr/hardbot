from datetime import date
from decimal import Decimal
import json
import pytest
import db, employee_workspace as ew, workday
from test_pc_first_accounts import world, approve_pc, http


@pytest.fixture
def employee(world,monkeypatch):
    monkeypatch.setattr(workday,'today',lambda:date(2026,10,31))
    data,_=approve_pc(world)
    with db.connect() as c:
        for cid,name,owner,soft in [('OWN-A','SAME CLIENT','Own Employee','Own Employee'),('OWN-B','SAME CLIENT','Own Employee','Own Employee'),('FOREIGN','OTHER CLIENT','Foreign Employee','Foreign Employee')]:
            c.execute('INSERT INTO clients(contract_id,client,responsible,responsible_soft,partner,birth_date,debt_today,issued_sum,kd_date,dp,debt_overdue,interest_overdue) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (cid,name,owner,soft,'ЗК','1990-01-01','0' if cid=='OWN-B' else '100.25','1234.56','2026-01-15','82','70.01','30.24'))
        cols=ew.month_columns(['Оплаты за 09.26','Текущие платежи'])
        ew.remember_monthly(c,'OWN-A',[20,'100.25'],cols,'ЗК')
        ew.remember_monthly(c,'OWN-B',[0,'-0.10'],cols,'ЗК')
        ew.remember_monthly(c,'FOREIGN',[0,999999],cols,'ЗК')
    return {'X-App-Token':data['token']}


def test_portfolio_is_server_paged_contract_rows_with_cents_and_known_zero(employee):
    status,data=http('GET','/api/workspace/portfolio?profile=soft&size=1&sort=contract_id',headers=employee)
    assert status==200 and data['total']==2 and len(data['rows'])==1
    row=data['rows'][0]
    assert row['contract_id']=='OWN-A' and row['issued_sum']==1234.56 and row['interest_overdue']==30.24 and row['dp']=='82'
    second=http('GET','/api/workspace/portfolio?profile=soft&size=1&page=2&sort=contract_id',headers=employee)[1]
    assert second['rows'][0]['total']==0
    selected=http('GET','/api/workspace/portfolio?profile=soft&q=OWN-B',headers=employee)[1]
    assert selected['total']==1 and selected['rows'][0]['contract_id']=='OWN-B'
    assert http('GET','/api/workspace/portfolio?profile=soft&q=FOREIGN',headers=employee)[1]['total']==0


def test_debt_and_loan_card_same_as_table(employee):
    status,data=http('GET','/api/person?rep=OWN-A&mode=soft',headers=employee)
    assert status==200
    assert {r['contract_id'] for r in data['contracts']}=={'OWN-A','OWN-B'}
    row=next(r for r in data['contracts'] if r['contract_id']=='OWN-A')
    assert row['kd_date']=='2026-01-15' and row['issued_sum']==1234.56 and row['dp']=='82'
    assert next(d['value'] for d in row['debt'] if d['key']=='interest_overdue')==30.24


def test_dashboard_monthly_totals_are_not_last_payment_or_promise(employee):
    with db.connect() as c:
        c.execute("UPDATE clients SET paid_current=777777,last_payment_sum='888888',last_payment_date='2026-10-25',forecast_sum='999999' WHERE contract_id='OWN-A'")
    status,data=http('GET','/api/workspace/dashboard?profile=soft&month=2026-10',headers=employee)
    assert status==200
    assert data['payments']['totals']==[{'unit':'₽','sum':100.15}]
    assert data['stats']['portfolio_contracts']==2 and data['stats']['paying_contracts']==1
    assert data['payments']['known_contracts']==2 and data['total']==2
    assert 'FOREIGN' not in json.dumps(data)
    september=http('GET','/api/workspace/dashboard?profile=soft&month=2026-09',headers=employee)[1]
    assert september['payments']['totals'][0]['sum']==20


def test_partial_missing_month_not_invented_zero_and_reimport_replaces(employee):
    with db.connect() as c:
        ew.remember_monthly(c,'OWN-A',[0,'12.34'],ew.month_columns(['Оплаты за 09.26','Текущие платежи']),'ЗК')
        ew.remember_monthly(c,'OWN-A',[0,'12.34'],ew.month_columns(['Оплаты за 09.26','Текущие платежи']),'ЗК')
        c.execute("DELETE FROM registry_monthly_totals WHERE contract_id='OWN-B' AND month='2026-10'")
    data=ew.dashboard('Own Employee','soft','2026-10')
    assert data['payments']['totals'][0]['sum']==12.34 and data['payments']['missing_contracts']==1


def test_reassignment_uses_current_scope_never_sends_old_contract(employee):
    with db.connect() as c:c.execute("UPDATE clients SET responsible_soft='Foreign Employee' WHERE contract_id='OWN-A'")
    data=http('GET','/api/workspace/dashboard?profile=soft&month=2026-10',headers=employee)[1]
    assert data['payments']['totals'][0]['sum']==-0.1
    assert 'OWN-A' not in json.dumps(data)


def test_explicit_hard_profile_uses_own_alias_despite_soft_header(employee):
    import staff_roster
    items=[{'employee':'Own Employee','products':['hard','soft'],'product_names':{'hard':'Own Employee HARD','soft':'Own Employee'}}]
    preview=staff_roster.preview(items);staff_roster.apply(items,preview['sha256'],by='test')
    with db.connect() as c:c.execute("UPDATE clients SET responsible='Own Employee HARD' WHERE responsible='Own Employee'")
    status,data=http('GET','/api/workspace/portfolio?profile=hard',headers={**employee,'X-Work-Product':'soft'})
    assert status==200 and data['total']==2 and data['employee']=='Own Employee'


@pytest.mark.parametrize('query',['profile=wrong','profile=soft&month=2026-13','profile=soft&month=2027-01','profile=soft&page=0','profile=soft&size=10000','profile=soft&detail=secret'])
def test_invalid_parameters_do_not_broaden_scope(employee,query):
    assert http('GET','/api/workspace/dashboard?'+query,headers=employee)[0]==400


def test_denied_without_auth_and_read_only(employee):
    assert http('GET','/api/workspace/dashboard?profile=soft')[0]==401
    assert http('POST','/api/workspace/portfolio',{},employee)[0]==405


def test_month_inference_survives_late_import_and_unknown_current_not_guessed(monkeypatch):
    monkeypatch.setattr(workday,'today',lambda:date(2027,1,5))
    assert ew.month_columns(['Оплаты за 09.26','Текущие платежи'])[1][0]=='2026-10'
    assert ew.month_columns(['Оплаты за 12.26','Текущие платежи'])[1][0]=='2027-01'
    assert ew.month_columns(['Текущие платежи'])=={}
    assert ew.amount('1 234,56')==Decimal('1234.56') and ew.amount('NaN') is None


def test_monthly_import_hook_preview_does_not_persist_and_apply_idempotent(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'monthly-import.db');monkeypatch.setattr(workday,'today',lambda:date(2026,10,31));db.init_db()
    from fields import FIELDS
    row={'contract_id':'TEST-ONLY','client':'Synthetic','responsible':'Synthetic Worker','partner':'ЗК'}
    grid=[[f.title for f in FIELDS]+['Оплаты за 09.26','Текущие платежи'],[f.source for f in FIELDS]+['сх','сх'],[row.get(f.key) for f in FIELDS]+[5,'10.25']]
    preview=db.import_grid(grid,source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,preview_only=True)
    assert not preview['blocked'],preview.get('error') or preview
    with db.connect() as c:assert c.execute('SELECT count(*) FROM registry_monthly_totals').fetchone()[0]==0
    result=db.import_grid(grid,source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    assert not result['blocked'],result
    with db.connect() as c:
        assert c.execute("SELECT amount FROM registry_monthly_totals WHERE month='2026-10'").fetchone()[0]=='10.25'
