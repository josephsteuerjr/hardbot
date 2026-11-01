import hashlib
import threading
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
