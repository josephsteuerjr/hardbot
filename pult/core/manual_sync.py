"""Observe saved workbooks and safely publish queued dates, entirely in Python."""
from __future__ import annotations

from .bootstrap import ensure_paths
ensure_paths()

import argparse
from contextlib import contextmanager,ExitStack
from datetime import date,datetime,timezone
import hashlib
from functools import wraps
import inspect
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import uuid
import zipfile
from zoneinfo import ZoneInfo

from .manual_edits import Store
from .manual_workbooks import read,write_xlsx_dates,preserve_calculation_metadata


def default_state_dir():
    return Path(os.environ.get('LOCALAPPDATA',Path.home()))/'Hard/manual-edits'


def sha(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):result.update(chunk)
    return result.hexdigest()


def default_store():
    return Store(default_state_dir()/'state.sqlite3')


def active_config(path: str|Path):
    """Feature activation is explicit; previews and arbitrary files stay read-only."""
    from .manual_config import load_config,is_configured
    config=load_config()
    if not config.get('enabled'):return None
    if not is_configured(path,config):return None
    return config


@contextmanager
def maybe_automatic(path: str|Path,*,require_closed=True):
    config=active_config(path)
    if not config:
        yield None;return
    if config.get('journal'):
        from .manual_shared import SharedEngine
        engine=SharedEngine(config['journal'],default_state_dir()/'work')
        with engine.automatic(path,require_closed=require_closed) as book:yield book
        return
    store=Store(Path(config.get('state',default_state_dir()/'state.sqlite3')))
    if not Path(path).exists():
        yield None
        if Path(path).is_file():
            after=read(path)
            prior=store.baseline(path)
            store.automatic(path,after.sha256,after.view,pre_sha256=prior[0] if prior else None,operation_id='creation-'+uuid.uuid4().hex)
        return
    with automatic_write(path,store=store) as book:yield book


def track_automatic_file(parameter='path'):
    def decorate(function):
        signature=inspect.signature(function)
        @wraps(function)
        def wrapped(*args,**kwargs):
            path=signature.bind(*args,**kwargs).arguments[parameter]
            with maybe_automatic(path):return function(*args,**kwargs)
        return wrapped
    return decorate


def publication_identity(path):
    """Identify the already-held provenance lease; do not acquire it twice."""
    config=active_config(path)
    if not config or not config.get('journal'):return None
    from .manual_journal import Journal
    intent=Journal(config['journal']).active(path)
    if not intent or intent.get('origin')!='automatic':
        raise ValueError('Не подтверждена операция автоматической записи')
    return intent['operation_id']


def recover_verified_publication(path,evidence):
    config=active_config(path)
    if not config:return
    if not config.get('journal'):
        raise ValueError('Прерванная запись старого локального журнала требует проверки')
    operation=evidence.get('provenance_operation_id')
    if not operation:raise ValueError('Не найден номер операции происхождения записи')
    from .manual_shared import SharedEngine
    engine=SharedEngine(config['journal'],default_state_dir()/'work')
    engine.recover_verified_publication(path,operation_id=operation,
        pre_sha256=evidence['pre_sha256'],post_sha256=evidence['post_sha256'])


def track_publication(function):
    @wraps(function)
    def wrapped(stage,*args,**kwargs):
        files=kwargs.get('selected_files')
        if files is None:files=stage.files
        with ExitStack() as stack:
            for item in files:
                stack.enter_context(maybe_automatic(item.original_path,require_closed=False))
            return function(stage,*args,**kwargs)
    return wrapped


def observe_saved(store: Store,path: Path,*,now: float|None=None,continuous: bool|None=None):
    now=time.time() if now is None else now
    if store.lease(path):raise ValueError('В книге выполняется автоматическая запись')
    book=read(path)
    baseline=store.baseline(path)
    if baseline and baseline[0]==book.sha256:
        store.heartbeat(path,now)
        return book,None
    if continuous is None:continuous=store.recently_observed(path,now)
    # UZ/TJ office calendar, same day boundary as the application's workday.
    saved_on=datetime.fromtimestamp(book.mtime_ns/1e9,ZoneInfo('Asia/Tashkent')).date()
    plan=store.observe(path,book.sha256,book.view,saved_on=saved_on,continuously_observed=continuous)
    store.commit(plan,post_sha256=book.sha256,operation_id='observe-'+uuid.uuid4().hex,
                 origin='calibration' if plan.calibrated else 'manual_observation',
                 observed_at=datetime.fromtimestamp(now,timezone.utc).isoformat(),
                 retain_unknown_day_for_review=True)
    store.heartbeat(path,now)
    return book,plan


@contextmanager
def automatic_write(path: str|Path,*,store: Store|None=None,operation_id: str|None=None):
    """Known Python writer: observe pre-state, lease, verify post-state, ACK.

    Exits after a code exception only if the original bytes are unchanged.
    Changed/unknown results keep their durable lease for receipt-based recovery.
    """
    store=store or default_store();path=Path(path)
    book,_=observe_saved(store,path)
    operation_id=operation_id or 'auto-'+uuid.uuid4().hex
    store.begin_automatic(path,operation_id=operation_id,pre_sha256=book.sha256)
    try:
        yield book
    except BaseException:
        actual=sha(path)
        if actual==book.sha256:store.abort_unchanged(path,operation_id=operation_id,actual_sha256=actual)
        raise
    else:
        after=read(path)
        store.automatic(path,after.sha256,after.view,pre_sha256=book.sha256,operation_id=operation_id,leased=True)
        store.heartbeat(path,time.time())


def _vba_parts(path):
    with zipfile.ZipFile(path) as archive:
        return {n:hashlib.sha256(archive.read(n)).hexdigest() for n in archive.namelist() if 'vbaproject' in n.casefold()}


def _write_xlsb(source: Path,target: Path,writes):
    # Existing preservation path for binary workbooks. There is no VBA code,
    # no project access and no Office registry or trust setting change.
    import pythoncom
    import xlwings as xw
    import enrich
    pythoncom.CoInitialize()
    app=None;book=None
    try:
        shutil.copy2(source,target)
        app=xw.App(visible=False,add_book=False)
        app.display_alerts=False;app.screen_updating=False
        app.api.EnableEvents=False;app.api.AutomationSecurity=3
        book=app.books.open(str(target),update_links=False,read_only=False,add_to_mru=False)
        app.calculation='manual';app.api.CalculateBeforeSave=False
        if book.api.ReadOnly:raise ValueError('Локальная книга открыта только для чтения')
        ws=book.sheets['Портфели']
        enrich._write_cells(ws,writes)
        # A normal Save normalizes legacy shared custom-view filter names.
        # SaveCopyAs preserves their saved ranges; keep the workbook basename
        # and replace only the closed local candidate.
        with tempfile.TemporaryDirectory(prefix='manual-save-',dir=target.parent) as saved:
            copy=Path(saved)/target.name
            book.api.SaveCopyAs(str(copy));book.close();book=None
            os.replace(copy,target)
        preserve_calculation_metadata(source,target)
    finally:
        if book is not None:
            try:book.close()
            except Exception:pass
        if app is not None:
            try:app.quit()
            except Exception:pass
        pythoncom.CoUninitialize()


def _writable(path):
    # Use the project's established marker + exclusive-access checks.
    from registry_cycle.file_access import inspect_busy_file
    busy=inspect_busy_file(path)
    if busy:raise ValueError('Книга занята: '+busy.reason)


def flush_dates(store: Store,path: str|Path,*,work_dir: Path|None=None):
    path=Path(path)
    book,_=observe_saved(store,path)
    pending=store.pending(path)
    if not pending:return {'stage':'no_pending_dates','writes':0}
    _writable(path)
    writes=[]
    for row_key,block,day in pending:
        position=book.view.dates.get((row_key,block))
        if position is None:raise ValueError('Изменилась строка или отсутствует колонка даты; нужна проверка')
        row,column,current=position
        if current is None or current<day:writes.append((row,column,date.fromisoformat(day),True))
    operation='manual-dates-'+uuid.uuid4().hex
    state_dir=store.path.parent
    receipts=state_dir/'receipts';receipts.mkdir(exist_ok=True)
    receipt=receipts/(operation+'.json')
    state=dict(operation_id=operation,path=str(path),stage='intent',pre_sha256=book.sha256,writes=len(writes),origin='manual_observation')
    def save():
        tmp=receipt.with_suffix('.tmp');tmp.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(tmp,receipt)
    save()
    store.begin_automatic(path,operation_id=operation,pre_sha256=book.sha256)
    publication_started=False
    try:
        with tempfile.TemporaryDirectory(prefix='hard-manual-dates-',dir=work_dir) as folder:
            candidate=Path(folder)/path.name
            if writes:
                if path.suffix.casefold()=='.xlsx':write_xlsx_dates(path,candidate,writes)
                elif path.suffix.casefold()=='.xlsb':_write_xlsb(path,candidate,writes)
                else:raise ValueError('Неподдерживаемый формат Excel')
                checked=read(candidate)
                if checked.view.cells!=book.view.cells:raise ValueError('Изменились ячейки результатов или формулы')
                if _vba_parts(path)!=_vba_parts(candidate):raise ValueError('Изменился существующий проект книги')
                expected={(row,column):value.isoformat() for row,column,value,_ in writes}
                actual={(r,c):day for _key,(r,c,day) in checked.view.dates.items()}
                if any(actual.get(pos)!=day for pos,day in expected.items()):raise ValueError('Дата записана не настоящей датой Excel')
                backup=state_dir/'backups'/hashlib.sha256(str(path).casefold().encode()).hexdigest()[:16]/book.sha256/path.name
                backup.parent.mkdir(parents=True,exist_ok=True)
                if not backup.exists():shutil.copy2(path,backup)
                if sha(backup)!=book.sha256:raise ValueError('Не совпала копия до записи')
                state.update(stage='candidate_verified',candidate_sha256=checked.sha256,backup=str(backup));save()
                _writable(path)
                if sha(path)!=book.sha256:raise ValueError('Файл изменён до публикации')
                publication_started=True
                import enrich
                enrich._replace_on_share(candidate,path)
                if sha(path)!=checked.sha256:raise ValueError('Не подтверждён результат замены')
                after=read(path)
            else:after=book
            store.automatic(path,after.sha256,after.view,pre_sha256=book.sha256,operation_id=operation,leased=True)
            state.update(stage='verified',post_sha256=after.sha256);save()
            store.heartbeat(path,time.time())
            return {'stage':'verified','writes':len(writes),'receipt':str(receipt)}
    except BaseException as exc:
        if not publication_started:
            store.cancel_unpublished(path,operation_id=operation)
        elif sha(path)==book.sha256:
            store.abort_unchanged(path,operation_id=operation,actual_sha256=book.sha256)
        state.update(stage='requires_review' if publication_started else 'not_published',error=str(exc));save()
        raise


def main(argv=None):
    parser=argparse.ArgumentParser(description='Даты правок сохранённых реестров без макросов')
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--calibrate',action='store_true')
    args=parser.parse_args(argv)
    config=json.loads(args.config.read_text('utf-8'))
    store=Store(Path(config.get('state',default_state_dir()/'state.sqlite3')))
    paths=[Path(p) for p in config['books']]
    shared=None
    if config.get('journal'):
        from .manual_shared import SharedEngine
        shared=SharedEngine(config['journal'],default_state_dir()/'work')
    poll=max(2,float(config.get('poll_seconds',10)))
    while True:
        summary={'checked':0,'dates_written':0,'pending':0,'review':0,'errors':[]}
        for path in paths:
            try:
                if shared is not None:
                    result=shared.scan(path,write=not args.calibrate)
                    summary['checked']+=1;summary['dates_written']+=result['writes']
                    summary['pending']+=result['pending'];summary['review']+=result['review']
                    continue
                if store.lease(path):
                    summary['pending']+=1;continue
                observe_saved(store,path,continuous=False if args.calibrate else None)
                summary['checked']+=1
                if not args.calibrate:
                    result=flush_dates(store,path)
                    summary['dates_written']+=result['writes']
                summary['review']+=len(store.reviews(path))
            except Exception as exc:
                summary['errors'].append({'path':str(path),'error':str(exc)})
        log=store.path.parent/'watch-status.json';tmp=log.with_suffix('.tmp')
        tmp.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(tmp,log)
        if args.once or args.calibrate:
            print(json.dumps({k:v for k,v in summary.items() if k!='errors'},ensure_ascii=False));return 0 if not summary['errors'] else 1
        time.sleep(poll)


if __name__=='__main__':raise SystemExit(main())
