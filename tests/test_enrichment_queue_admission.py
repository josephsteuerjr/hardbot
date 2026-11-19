import hashlib
import threading
from pathlib import Path
import pytest
from pult.core.settings import Settings
from pult.core.durable_jobs import Journal


@pytest.fixture
def backend(tmp_path,monkeypatch):
 from pult import desk_backend as backend,desk_registry as registry
 settings=Settings({'active':'Synthetic','profiles':{'Synthetic':{'url':'http://127.0.0.1:1','device_token':'synthetic'}}},path=tmp_path/'backend-settings.json')
 monkeypatch.setattr(backend,'_settings',settings);monkeypatch.setattr(backend,'_journal',Journal(tmp_path/'jobs.sqlite3'));monkeypatch.setattr(backend,'_jobs',{})
 monkeypatch.setattr(backend,'_access_busy',False);monkeypatch.setattr(backend,'_background_busy',False);monkeypatch.setattr(registry,'_delivery_service',None);monkeypatch.setattr(registry,'S',registry._empty_state())
 return backend,registry


def test_accepted_job_preparation_is_durable_before_worker_or_http_reply(backend,tmp_path):
 engine,registry=backend;entered=threading.Event();release=threading.Event();evidence=tmp_path/'accepted'
 def prepare(jid,profile,fingerprint):evidence.write_text(jid+':'+fingerprint)
 def work(say):entered.set();release.wait(5);return {'run_state':'waiting','remaining':1}
 try:
  identifier=engine.start_job('enr_apply',work,request_id='durable-before-reply',fingerprint='exact-body',before_start=prepare)
  assert evidence.read_text()=='durable-before-reply:exact-body';assert entered.wait(2)
  assert engine.job_state(identifier)['state']=='running'
 finally:release.set()
 for _ in range(100):
  if engine.job_state(identifier)['state']!='running':break
  threading.Event().wait(.01)
 assert engine.job_state(identifier)['state']=='waiting'


def test_busy_admission_does_not_leave_an_approved_orphan(backend,tmp_path):
 engine,_=backend;engine._background_busy=True
 with pytest.raises(engine.JobBusyError):engine.start_job('enr_apply',lambda say:None,request_id='not-admitted',before_start=lambda *args:(tmp_path/'orphan').write_text('bad'))
 assert not (tmp_path/'orphan').exists() and not engine.job_journal().list(engine.job_profile())


def test_prepare_failure_never_becomes_done_or_starts_writes(backend):
 engine,_=backend;writes=[]
 def failed(*args):raise OSError('Synthetic full disk before durable snapshot')
 with pytest.raises(OSError):engine.start_job('enr_apply',lambda say:writes.append(1),request_id='disk-failure',before_start=failed)
 assert not writes and not engine.job_journal().list(engine.job_profile())


def test_unknown_book_state_keeps_import_blocked_without_remote_request(backend,tmp_path):
 engine,registry=backend;service=registry.delivery_service(engine._settings,lambda *a,**k:pytest.fail('No remote call is allowed'))
 path=tmp_path/'book.xlsx';path.write_bytes(b'synthetic')
 service.prepare('durable-remaining',[{'employee':'Synthetic','path':str(path),'rows':[]}],[],'01.11.2026','01.11.2026',[])
 for route in ['/reg/preview','/reg/apply']:
  with pytest.raises(RuntimeError,match='не завершено'):registry.route(route,{},admin=service.admin,start_job=lambda *a:pytest.fail('Import must not start'),settings=engine._settings)


def test_source_change_after_preview_prevents_registration_and_import(backend,tmp_path):
 engine,registry=backend;path=tmp_path/'master.xlsx';path.write_bytes(b'old')
 book={'path':str(path),'file_sha256':hashlib.sha256(b'old').hexdigest(),'import_profile':'hard'}
 body={'selected':['synthetic']};registry.S['book']=book;registry.S['preview']={'sig':registry._signature(book,['synthetic'],True,True,False,True),'meta':{},'body':{}}
 path.write_bytes(b'new');posts=[]
 def run(kind,fn):return fn(lambda s:None)
 with pytest.raises(RuntimeError,match='изменился после предпросмотра'):
  registry.imp_apply(body,admin=lambda *a,**k:posts.append(a),start_job=run,settings=engine._settings)
 assert not posts and registry.S['preview'] is None


def test_completed_books_do_not_finish_job_while_excel_is_still_closing(backend,tmp_path):
 engine,registry=backend;service=registry.delivery_service(engine._settings,lambda *a,**k:{})
 path=tmp_path/'book.xlsx';path.write_bytes(b'synthetic')
 run=service.prepare('finishing-excel',[{'employee':'Synthetic','path':str(path),'rows':[]}],[],'01.11.2026','01.11.2026',[])
 item=service.store.books(run['id'])[0];item.update(state='done');service.store.save_book(run['id'],item)
 engine.job_journal().save(dict(id=run['id'],kind='enr_apply',state='attention',result=None,error=None,log=[]),engine.job_profile(),'test')
 engine._background_busy=True
 assert engine.job_state(run['id'])['state']=='attention'
 engine._background_busy=False
 assert engine.job_state(run['id'])['state']=='done'



def test_enrichment_progress_counts_all_approved_books_after_resume(backend, tmp_path, monkeypatch):
    engine, registry = backend
    service = registry.delivery_service(engine._settings, lambda *a, **k: {})
    jobs = [{'employee': 'Synthetic', 'path': str(tmp_path / f'book-{i}.xlsb'), 'rows': []}
            for i in range(3)]
    for job in jobs:
        Path(job['path']).write_bytes(b'synthetic')
    run = service.prepare('batch-progress', jobs, [], '01.11.2026', '01.11.2026', [])
    first = service.store.books(run['id'])[0]
    first['state'] = 'done'
    service.store.save_book(run['id'], first)

    def check_book(run, item, inputs, report, get_excel):
        report('Проверка книги')
        item['state'] = 'done'
        service.store.save_book(run['id'], item)

    monkeypatch.setattr(service, '_book', check_book)
    lines = []
    result = service.process(run, lines.append)
    assert result['completed'] == result['total'] == 3
    assert [line.split()[0] for line in lines if line.endswith('Проверка книги')] == ['[2/3]', '[3/3]']
    assert not any(line.startswith('[1/1]') for line in lines)



def test_excel_start_disconnect_without_write_intent_leaves_book_for_retry(backend, tmp_path, monkeypatch):
    engine, registry = backend
    service = registry.delivery_service(engine._settings, lambda *a, **k: {})
    path = tmp_path / 'book.xlsb'
    path.write_bytes(b'synthetic')
    run = service.prepare('excel-start-error', [{'employee': 'Synthetic', 'path': str(path), 'rows': []}],
                          [], '01.11.2026', '01.11.2026', [])

    def disconnected(*args):
        raise RuntimeError("(-2147023174, 'Сервер RPC недоступен.', None, None)")

    monkeypatch.setattr(service, '_book', disconnected)
    status = service.process(run)
    assert status['state'] == 'waiting'
    assert 'Запись не начиналась' in status['books'][0]['message']
    assert path.read_bytes() == b'synthetic'



def test_new_pass_keeps_completed_books_and_replaces_pending_batch_atomically(backend, tmp_path):
    engine, registry = backend
    service = registry.delivery_service(engine._settings, lambda *a, **k: {})
    jobs = [{'employee': 'Synthetic', 'path': str(tmp_path / f'book-{i}.xlsb'), 'rows': []}
            for i in range(2)]
    for job in jobs:
        Path(job['path']).write_bytes(b'synthetic')
    day = '01.11.2026'
    old = service.prepare('old-partial-pass', jobs, [], day, day, [])
    books = service.store.books(old['id'])
    books[0].update(state='done', result={'cells_written': 1})
    books[1].update(state='attention', message='Ошибка до записи')
    for book in books:
        service.store.save_book(old['id'], book)
    old['state'] = 'attention'
    service.store.save(old)
    assert service.view()['restart_allowed']

    books[1]['intent'] = {'post_sha256': 'unknown'}
    service.store.save_book(old['id'], books[1])
    assert not service.view()['restart_allowed']
    with pytest.raises(ValueError, match='требуется сверка'):
        service.prepare('cannot-replace-written', jobs, [], day, day, [], replaces=old['id'])
    del books[1]['intent']
    service.store.save_book(old['id'], books[1])

    assert service.check_replacement(old['id'])['id'] == old['id']
    with pytest.raises(RuntimeError, match='не завершено'):
        service.require_complete()
    fresh = service.prepare('fresh-after-partial', jobs, [], day, day, [], replaces=old['id'])
    history = service.store.get(old['id'], service.owner())
    assert history['replaced_by'] == fresh['id']
    assert service.view(history)['state'] == 'replaced'
    assert service.store.books(old['id']) == books
    assert service.current()['id'] == fresh['id']
    assert service.view()['total'] == 2 and service.view()['completed'] == 0
