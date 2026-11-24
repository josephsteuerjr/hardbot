from datetime import date
import json

from crm import storage,work_portfolio
from crm.contract import canonical
from test_crm_core import crm_world


def test_call_queue_uses_due_date_before_pagination(crm_world,monkeypatch):
    monkeypatch.setattr(work_portfolio,'now',lambda:'2026-11-21T19:59:00Z')
    member=crm_world['member']
    overdue,future=crm_world['contracts']
    with storage.connect() as conn:
        for identifier,planned in [(overdue,'2026-11-19'),(future,'2026-11-23')]:
            row=conn.execute('SELECT fields_json FROM crm_contracts WHERE contract_uuid=?',(identifier,)).fetchone()
            fields=json.loads(row[0])
            fields.update(soft_call_status='В работе',soft_next_call_date=planned)
            conn.execute('UPDATE crm_contracts SET fields_json=? WHERE contract_uuid=?',(canonical(fields),identifier))
    due=work_portfolio.read(member,work='call',call_queue='today',limit=1)
    assert due['total_contracts']==1 and not due['has_more']
    row=due['contracts'][0]
    assert row['contract_uuid']==overdue
    assert row['call_schedule']==dict(date='2026-11-19',overdue_days=2,reason='overdue')
    every=work_portfolio.read(member,work='call')
    assert [r['contract_uuid'] for r in every['contracts']]==[overdue,future]
    assert every['contracts'][1]['call_schedule']['reason']=='scheduled'


def test_call_queue_legacy_date_explicit_clear_and_missing_status():
    day=date(2026,11,21)
    values={'meeting_date':'21.11.2026','soft_call_status':'В работе'}
    assert work_portfolio.call_schedule(values,day)['reason']=='today'
    assert work_portfolio.call_schedule({**values,'soft_next_call_date':None},day)['reason']=='no_date'
    assert work_portfolio.call_schedule({'soft_next_call_date':'2026-11-22'},day)['reason']=='no_status'
