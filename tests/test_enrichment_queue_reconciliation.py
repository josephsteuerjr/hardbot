import copy
import pytest
from pult.core.enrichment_queue import Service,file_sha

def prepared(tmp_path):
    path=tmp_path/'personal.xlsx';path.write_bytes(b'untouched personal')
    service=Service(tmp_path/'queue',lambda:'owner',lambda *a,**k:{})
    job=dict(path=str(path),employee='Old owner',profile='soft',rows=[dict(contract_id='MISSING',_profile='soft')])
    run=service.store.create('reconcile','owner',dict(jobs=[job],fields=[],since='31.10.2026',until='01.11.2026'),[])
    item=service.store.books(run['id'])[0];item.update(state='attention',missing=['MISSING'],message='not found');service.store.save_book(run['id'],item)
    return service,run,path

def master(tmp_path,rows):
    path=tmp_path/'master.xlsx';path.write_bytes(b'current approved master')
    return dict(path=str(path),file_sha256=file_sha(path),grid=[['Номер КД / ID','ответственный Сотрудник','ответственный Сотрудник СОФТ'],['сх','хард','софт'],*rows])

def test_changed_owner_is_an_explicit_omission_without_fabricated_delivery(tmp_path):
    service,run,path=prepared(tmp_path);snapshot=file_sha(service.store.directory/'reconcile/source.bin');before=file_sha(path)
    plan=service.preview_reconciliation(master(tmp_path,[['MISSING','','New owner']]))
    assert len(plan['proposals'])==1 and not plan['blocked']
    state=service.apply_reconciliation(plan)
    assert state['remaining']==1 and state['excluded']==1 and state['busy']==0 and state['missing']==0
    item=service.store.books(run['id'])[0]
    assert item['omissions'][0]['current_owner']=='New owner' and not item.get('result') and not item.get('ledger_ack')
    assert service.store.inputs(run)['jobs'][0]['rows'][0]['contract_id']=='MISSING'
    assert not service.effective_job(service.store.inputs(run)['jobs'][0],item)['rows']
    assert file_sha(path)==before and file_sha(service.store.directory/'reconcile/source.bin')==snapshot

def test_same_owner_and_ambiguous_ids_cannot_be_excluded(tmp_path):
    service,run,path=prepared(tmp_path)
    for rows in ([['MISSING','','Old owner']],[['MISSING','','New owner'],['MISSING','','Another owner']]):
        plan=service.preview_reconciliation(master(tmp_path,rows))
        assert not plan['proposals'] and len(plan['blocked'])==1

def test_master_change_rejects_decisions_before_queue_mutation(tmp_path):
    service,run,path=prepared(tmp_path);book=master(tmp_path,[]);plan=service.preview_reconciliation(book)
    (tmp_path/'master.xlsx').write_bytes(b'new changed source')
    with pytest.raises(ValueError,match='изменился'):service.apply_reconciliation(plan)
    assert not service.store.books(run['id'])[0].get('omissions')

def test_old_attention_rows_are_missing_not_busy(tmp_path):
    service,run,path=prepared(tmp_path);state=service.view(run)
    assert state['busy']==0 and state['attention']==1 and state['missing']==1
    assert state['books'][0]['reason_code']=='missing'
