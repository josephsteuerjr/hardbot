"""Measured server paging/selection on 50,000 synthetic contracts, never production."""
import json
import os
import time
import pytest
from crm import assignments,storage
from test_crm_postgres import pg_world
from test_crm_assignments import allocation_world,preview,apply

pytestmark=pytest.mark.skipif(not os.environ.get('HARD_CRM_TEST_DSN_FILE'),reason='Requires isolated PostgreSQL QA')


def test_fifty_thousand_contracts_paging_frozen_selection_and_latency(pg_world,record_property):
    w=allocation_world(pg_world);m=w['managers'][0]
    with storage.connect() as c:
        c.execute("INSERT INTO crm_people SELECT md5('scale-person-' || (i/2)::text)::uuid::text,'Scale ' || (i/2)::text,'synthetic-scale','2026-01-01' FROM generate_series(0,49999) i GROUP BY i/2")
        c.execute("INSERT INTO crm_contracts SELECT md5('scale-contract-' || i::text)::uuid::text,md5('scale-person-' || (i/2)::text)::uuid::text,'scale-creditor-' || (i%2)::text,'SCALE-' || (i/2)::text,NULL,'RUB',"
            "'{\"debt_today\":\"1.25\"}','{\"debt_today\":\"1.25\"}','{}','{}','2026-01-01' FROM generate_series(0,49999) i")
        c.execute("INSERT INTO crm_managed_contracts SELECT 'team-A',contract_uuid,work FROM crm_contracts CROSS JOIN (VALUES('call'),('visit')) k(work) WHERE creditor_id LIKE 'scale-%'")
        c.execute("INSERT INTO crm_assignments SELECT md5(c.contract_uuid || k.work)::uuid::text,?,c.contract_uuid,k.work,'2026-01-01',NULL,'synthetic-scale' FROM crm_contracts c CROSS JOIN (VALUES('call'),('visit')) k(work) WHERE creditor_id LIKE 'scale-%'",(w['employee'],))
        c.execute('ANALYZE crm_contracts');c.execute('ANALYZE crm_assignments');c.execute('ANALYZE crm_managed_contracts')
    measures={}
    for name,fn in [('page',lambda:assignments.portfolio(m,search='Scale',offset=10000,limit=50)),
                    ('statistics',lambda:assignments.statistics(m)),('idle_changes',lambda:assignments.changes(m))]:
        times=[]
        for _ in range(3):
            start=time.perf_counter();result=fn();times.append(round((time.perf_counter()-start)*1000,2))
        measures[name]=dict(samples_ms=times,max_ms=max(times))
        if name=='page':assert result['total']==25000 and len(result['people'])==50
        if name=='statistics':assert result['contracts']==50002 and result['debt']=={'RUB':'62746.90'}
    start=time.perf_counter()
    p=preview(w,works=['call','visit'],selection=dict(kind='filter',filters=dict(search='Scale'),part='whole'))
    measures['all_filter_preview_ms']=round((time.perf_counter()-start)*1000,2)
    assert p['slots_count']==100000 and len(p['slots'])==200 and p['contracts']==50000
    started=time.perf_counter();result,_=apply(w,p);measures['start_job_ms']=round((time.perf_counter()-started)*1000,2)
    assert result['status']=='job_started'
    started=time.perf_counter();first=assignments.job_step(m,dict(schema_version=1,job_id=result['job_id'],pack_index=0));measures['atomic_pack_ms']=round((time.perf_counter()-started)*1000,2)
    assert first['applied_slots']==200
    assignments.job_cancel(m,dict(schema_version=1,job_id=result['job_id']))
    record_property('manager_scale_measurements',json.dumps(measures))
    print(json.dumps(measures))
    assert measures['page']['max_ms']<5000
    assert measures['atomic_pack_ms']<2000
