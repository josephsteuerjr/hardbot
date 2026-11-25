"""Editable Python integration for analyst scripts and the saved-file observer."""
from __future__ import annotations
from contextlib import contextmanager,nullcontext
import argparse,json,os,shutil,subprocess,sys,tempfile,time,uuid
from pathlib import Path
from .manual_config import load_config,state_directory,book_paths,personal_paths,preferred_path,is_configured
from .manual_sync import sha,_writable,maybe_automatic
from .manual_shared import SharedEngine,file_mutex,SharedBusy
from .manual_workbooks import read_rows,read,preserve_calculation_metadata

def read_frame(path,*,nrows=None):
    import pandas as pd
    return pd.DataFrame(read_rows(path,nrows=nrows))

def engine(config):return SharedEngine(config['journal'],state_directory()/'work')

def prepare(paths):
    config=load_config()
    if not config.get('enabled'):return
    observer=engine(config)
    for path in paths:
        if not is_configured(path,config):continue
        result=observer.scan(path)
        if result['pending']:
            raise RuntimeError(f'Сборка остановлена: в {Path(path).name} ожидают записи даты правок ({result["pending"]}). Закройте книгу и повторите проверку.')
        if result['review']:
            print(f'{Path(path).name}: день правки неизвестен для {result["review"]} блоков; новые даты не подставлены. Сведения сохранены в журнале.')

def fingerprint(path):return sha(path) if Path(path).is_file() else None

def case_key(values,headers):
    """Contract IDs can repeat between partners; never merge their results."""
    import re
    import edit_tracking
    positions={edit_tracking.normalized(title):i for i,title in enumerate(headers)}
    def value(title):
        index=positions.get(edit_tracking.normalized(title))
        if index is None or index>=len(values):return ''
        item=values[index]
        return '' if item is None else re.sub(r'\.0+$','',str(item).strip())
    cid=value('Номер КД / ID')
    return (cid,value('Партнер').casefold()) if cid else None

def save_local(book,path):
    """SaveCopyAs preserves legacy shared custom views; only a local candidate."""
    path=Path(path);copy=path.with_name('saved-'+uuid.uuid4().hex+path.suffix)
    book.api.SaveCopyAs(str(copy));book.close()
    os.replace(copy,path)

def publish(path,candidate,expected_sha256):
    path,candidate=Path(path),Path(candidate)
    # Cooperating processes serialize through the shared primary ledger.
    # The source SHA is checked again inside that lock before replacement.
    with maybe_automatic(path):
        if fingerprint(path)!=expected_sha256:raise RuntimeError('Книга изменена после подготовки: '+str(path))
        if path.exists():_writable(path)
        checked=read(candidate)
        directory=state_directory()/'publications'/uuid.uuid4().hex
        directory.mkdir(parents=True)
        receipt=dict(stage='intent',path=str(path),pre_sha256=expected_sha256,candidate_sha256=checked.sha256)
        def save():
            (directory/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
        if path.exists():
            backup=directory/path.name;shutil.copy2(path,backup)
            if sha(backup)!=expected_sha256:raise RuntimeError('Не совпала копия до публикации')
            receipt['backup']=str(backup)
        save()
        if fingerprint(path)!=expected_sha256:raise RuntimeError('Книга изменена перед заменой: '+str(path))
        import enrich
        enrich._replace_on_share(candidate,path)
        if sha(path)!=checked.sha256:raise RuntimeError('Не подтверждён результат публикации')
        receipt.update(stage='verified',post_sha256=checked.sha256);save()

@contextmanager
def staged_master(path,*,publish_result=True):
    path=Path(path)
    if publish_result:prepare([path])
    before=sha(path)
    work=state_directory()/'staging';work.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='master-',dir=work) as folder:
        local=Path(folder)/path.name;shutil.copy2(path,local)
        if sha(local)!=before or sha(path)!=before:raise RuntimeError('Реестр изменён во время копирования')
        yield local
        if publish_result and sha(local)!=before:
            preserve_calculation_metadata(path,local)
            publish(path,local,before)

def _status(summary):
    directory=state_directory();directory.mkdir(parents=True,exist_ok=True)
    path=directory/'watch-status.json';temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(temp,path)

def scan(config,*,calibrate=False):
    observer=engine(config);summary=dict(checked=0,writes=0,pending=0,review=0,errors=[],checked_at=time.time())
    for path in book_paths(config):
        try:
            result=observer.scan(path,write=not calibrate)
            summary['checked']+=1
            for key in ('writes','pending','review'):summary[key]+=result[key]
        except Exception as exc:summary['errors'].append(dict(path=str(path),error=str(exc)))
    _status(summary);return summary

def start_worker(config_path):
    # A profile lock prevents repeated launches by consecutive merge/split jobs.
    directory=state_directory();directory.mkdir(parents=True,exist_ok=True)
    try:
        with file_mutex(directory/'worker.lock'):
            (directory/'stop-worker').unlink(missing_ok=True)
    except SharedBusy:return
    executable=Path(sys.executable).with_name('pythonw.exe') if os.name=='nt' else Path(sys.executable)
    if not executable.is_file():executable=Path(sys.executable)
    log=(directory/'worker.log').open('a',encoding='utf-8')
    try:
        subprocess.Popen([str(executable),'-m','pult.core.manual_runtime','--worker','--config',str(config_path)],
                         stdin=subprocess.DEVNULL,stdout=log,stderr=log,close_fds=True,
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,env=os.environ.copy(),
                         cwd=Path(__file__).resolve().parents[2])
    finally:log.close()

def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',required=True,type=Path)
    parser.add_argument('--worker',action='store_true');parser.add_argument('--calibrate',action='store_true')
    args=parser.parse_args(argv)
    os.environ['HARD_MANUAL_DATES_CONFIG']=str(args.config)
    if not args.worker:
        config=load_config(args.config)
        if not config.get('enabled'):raise RuntimeError('Учёт дат ещё не включён')
        result=scan(config,calibrate=args.calibrate)
        print(json.dumps(result,ensure_ascii=False));return bool(result['errors'])
    directory=state_directory();directory.mkdir(parents=True,exist_ok=True)
    try:
        with file_mutex(directory/'worker.lock'):
            stop=directory/'stop-worker'
            while not stop.exists():
                config={}
                try:
                    config=load_config(args.config)
                    if config.get('enabled'):
                        # Only one workstation observes at a time. Every known
                        # writer also uses the per-book lock on its own PC.
                        with file_mutex(Path(config['journal'])/'observer.lock'):scan(config)
                except SharedBusy:pass
                except Exception as exc:_status(dict(errors=[dict(error=str(exc))],checked_at=time.time()))
                time.sleep(max(2,min(float(config.get('poll_seconds',10)),60)))
            stop.unlink(missing_ok=True)
    except SharedBusy:return 0
    return 0

if __name__=='__main__':raise SystemExit(main())
