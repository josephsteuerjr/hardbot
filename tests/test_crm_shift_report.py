from dataclasses import replace
import uuid
import pytest
from crm import shifts,storage
from crm.contract import CRMError
from test_crm_core import crm_world


def test_period_clips_cross_midnight_work_and_breaks_to_selected_dates(crm_world):
    w=crm_world;p=replace(w['member'],timezone='UTC')
    employee=p.employee_uuid;shift=str(uuid.uuid4())
    with storage.connect() as conn:
        conn.execute('INSERT INTO crm_shifts VALUES(?,?,?,?,?,?,?)',(shift,employee,'2026-10-01T23:00:00Z','2026-10-02T03:00:00Z','UTC','finished',4))
        for kind,at in [('start','2026-10-01T23:00:00Z'),('break','2026-10-01T23:30:00Z'),('resume','2026-10-02T00:30:00Z'),('finish','2026-10-02T03:00:00Z')]:
            conn.execute('INSERT INTO crm_shift_events VALUES(?,?,?,?,?,?,?)',(str(uuid.uuid4()),shift,p.actor_uid,kind,at,'{}',None))
        conn.commit()
    report=shifts.period_report(p,'2026-10-02','2026-10-02')
    row=report['employees'][0]
    assert row['elapsed_seconds']==10800 and row['break_seconds']==1800 and row['declared_seconds']==9000
    assert report['shifts'][0]['workday']=='2026-10-01'
    assert shifts.period_report(p,'2026-10-03','2026-10-03')['shifts']==[]


def test_team_report_uses_employee_scope_and_does_not_have_the_90_shift_limit(crm_world):
    w=crm_world;p=replace(w['member'],role='manager',employee_scope=frozenset({w['member'].employee_uuid,w['stranger'].employee_uuid}),timezone='UTC')
    with storage.connect() as conn:
        for index in range(95):
            # Non-overlapping declarations spanning a 95-day period.
            from datetime import date,timedelta
            day=(date(2026,1,1)+timedelta(days=index)).isoformat()
            conn.execute('INSERT INTO crm_shifts VALUES(?,?,?,?,?,?,?)',(str(uuid.uuid4()),p.employee_uuid,day+'T08:00:00Z',day+'T09:00:00Z','UTC','finished',2))
        conn.commit()
    report=shifts.period_report(p,'2026-01-01','2026-05-01')
    assert len(report['employees'])==2 and len(report['shifts'])==95
    assert sum(row['declared_seconds'] for row in report['employees'])==95*3600
    with pytest.raises(CRMError):shifts.period_report(w['stranger'],'2026-01-01','2026-01-01',p.employee_uuid)
