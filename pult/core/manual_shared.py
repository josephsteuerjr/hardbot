"""Primary shared edit-provenance ledger for writers on different computers.

The shared directory holds primary working metadata and operation receipts.
Local builds, Excel staging, backups and scratch SQLite files remain local.
Two sealed ledger slots make a torn network write recoverable without a DB
on SMB. An OS file lock serializes cooperating Python processes per workbook.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date,datetime
import hashlib,json,os
from pathlib import Path
import shutil,tempfile,time,uuid
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from .manual_edits import Store,BookView,block_states,digest
from .manual_journal import Journal,_read,_create,_sealed
from .manual_workbooks import read,write_xlsx_dates


class SharedBusy(RuntimeError):pass


@contextmanager
def file_mutex(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    handle=None;stream=None
    try:
        if os.name=='nt':
            import win32con,win32file,pywintypes
            try:
                handle=win32file.CreateFile(str(path),win32con.GENERIC_READ|win32con.GENERIC_WRITE,0,None,win32con.OPEN_ALWAYS,win32con.FILE_ATTRIBUTE_NORMAL,None)
            except pywintypes.error as exc:raise SharedBusy('Реестр обрабатывается на другом компьютере') from exc
        else:
            import fcntl
            stream=path.open('a+b')
            try:fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError as exc:raise SharedBusy('Реестр обрабатывается на другом компьютере') from exc
        yield
    finally:
        if handle is not None:handle.Close()
        if stream is not None:stream.close()


def _write_sealed(path,payload):
    # These are two persistent transaction slots, never network build/cache files.
    raw=json.dumps(_sealed(payload),ensure_ascii=False,separators=(',',':'))
    with Path(path).open('w',encoding='utf-8') as f:
        f.write(raw);f.flush();os.fsync(f.fileno())
    if _read(path)!=payload:raise ValueError('Не подтверждён журнал происхождения правок')


def _stat(path):
    s=Path(path).stat()
    return [s.st_size,s.st_mtime_ns,s.st_ino]


class SharedEngine:
    def __init__(self,directory,local_directory):
        self.local=Path(local_directory)
        if str(self.local.resolve()).startswith('\\\\'):raise ValueError('Рабочие копии и сборки должны оставаться на локальном диске')
        if os.name=='nt':
            import ctypes
            if ctypes.windll.kernel32.GetDriveTypeW(str(self.local.absolute().anchor))==4:
                raise ValueError('Рабочие копии и сборки должны оставаться на локальном диске')
        self.local.mkdir(parents=True,exist_ok=True)
        self.journal=Journal(directory)
        self.planner=Store(self.local/'planner.sqlite3')

    def _folder(self,path):return self.journal.book_directory(path)

    def _checkpoint(self,path):
        files=list(self._folder(path).glob('*.complete.json'))
        if not files:return None
        latest=max(files,key=lambda p:(p.stat().st_mtime_ns,p.name))
        payload=_read(latest)
        state=payload.get('ledger')
        if state and state['book_key']!=Store.key(path):raise ValueError('Квитанция относится к другой книге')
        return state

    def _load(self,path):
        folder=self._folder(path);valid=[];invalid=False
        for slot in (0,1):
            p=folder/('ledger-'+str(slot)+'.json')
            if p.exists():
                try:valid.append(_read(p))
                except (ValueError,KeyError,json.JSONDecodeError):invalid=True
        if not valid:
            if any((folder/('ledger-'+str(i)+'.json')).exists() for i in (0,1)):
                checkpoint=self._checkpoint(path)
                if checkpoint:self._save(path,checkpoint);return checkpoint
                raise ValueError('Не читается основной журнал правок: проверить квитанции')
            return None
        latest=max(valid,key=lambda x:x['version'])
        if invalid:
            checkpoint=self._checkpoint(path)
            if checkpoint and checkpoint['version']>latest['version']:
                self._save(path,checkpoint);latest=checkpoint
        if latest['book_key']!=Store.key(path):raise ValueError('Журнал относится к другой книге')
        return latest

    def _save(self,path,state):
        p=self._folder(path)/('ledger-'+str(state['version']%2)+'.json')
        _write_sealed(p,state)

    def _heartbeat(self,path,state,now):
        _write_sealed(self._folder(path)/'heartbeat.json',dict(schema=1,version=state['version'],source_sha256=state['source_sha256'],checked_at=now))

    def _last_check(self,path,state):
        slot=self._folder(path)/('ledger-'+str(state['version']%2)+'.json')
        last=slot.stat().st_mtime_ns/1e9
        try:
            heartbeat=_read(self._folder(path)/'heartbeat.json')
            if heartbeat['version']==state['version'] and heartbeat['source_sha256']==state['source_sha256']:
                last=max(last,(self._folder(path)/'heartbeat.json').stat().st_mtime_ns/1e9)
        except (OSError,ValueError,KeyError):pass
        return last

    def _plan(self,path,book,state,now):
        baseline={(row,block):value for row,block,value in state['blocks']} if state else {}
        saved_on=datetime.fromtimestamp(book.mtime_ns/1e9,ZoneInfo('Asia/Tashkent')).date()
        last_check=self._last_check(path,state) if state else None
        # A block difference since a snapshot on the same office calendar day
        # has a known day, even across a pause. Crossing midnight without an
        # observation is ambiguous and remains a review, never a guessed day.
        continuous=bool(state and saved_on==datetime.fromtimestamp(last_check,ZoneInfo('Asia/Tashkent')).date() and book.mtime_ns/1e9>=last_check-2)
        plan=self.planner.observe(path,book.sha256,book.view,saved_on=saved_on,continuously_observed=continuous,external_baseline=(state['source_sha256'] if state else None,baseline))
        pending={(row,block):day for row,block,day in state['pending']} if state else {}
        review=set(tuple(x) for x in state['review']) if state else set()
        for row,block,day in plan.date_updates:
            pending[row,block]=max(day,pending.get((row,block),day));review.discard((row,block))
        review.update(plan.review_blocks)
        return plan,pending,review

    def _state(self,path,book,old,operation,pending,review,now):
        active=set(block_states(book.view))
        pending={key:day for key,day in pending.items() if key in active}
        review={key for key in review if key in active}
        for identity,(_r,_c,current) in book.view.dates.items():
            if identity in pending and current and current>=pending[identity]:pending.pop(identity)
        return dict(schema=1,book_key=Store.key(path),version=old['version']+1 if old else 1,
                    source_sha256=book.sha256,source_stat=list(book.source_stat),checked_at=now,
                    last_operation_id=operation,
                    blocks=[[row,block,value] for (row,block),value in sorted(block_states(book.view).items())],
                    pending=[[row,block,day] for (row,block),day in sorted(pending.items())],review=[list(x) for x in sorted(review)])

    def _recover(self,path,state):
        folder=self._folder(path);active=folder/'active.json'
        if not active.exists():return state
        intent=_read(active);operation=intent['operation_id']
        complete=folder/(operation+'.complete.json')
        if complete.exists():
            result=_read(complete)
            if result.get('intent_digest')!=digest(intent) or result.get('operation_id')!=operation:raise ValueError('Не совпала квитанция операции')
            committed=result.get('ledger')
            if committed and (state is None or state['version']<committed['version']):self._save(path,committed);state=committed
            os.replace(active,folder/(operation+'.intent.json'));return state
        if state and state.get('last_operation_id')==operation:
            # A sealed new slot proves that verified publication reached the
            # metadata commit. Finalize its receipt; never repeat workbook I/O.
            saved=SimpleNamespace(sha256=state['source_sha256'],view=BookView((),{},0))
            self.journal.finish(path,saved,operation,ledger=state,include_blocks=False,outcome='recovered_verified_metadata')
            return state
        from .manual_sync import sha
        if Path(path).is_file() and sha(path)==intent['pre_sha256']:
            self.journal.cancel_unpublished(path,operation,reason='verified_source_unchanged_after_interruption')
            return state
        raise ValueError('Итог предыдущей записи неизвестен: сначала проверить квитанцию')

    def scan(self,path,*,write=True,now=None):
        from .manual_sync import _writable,sha,_write_xlsb,_vba_parts
        path=Path(path);now=time.time() if now is None else now
        folder=self._folder(path)
        with file_mutex(folder/'ledger.lock'):
            old=self._recover(path,self._load(path))
            source_stat=_stat(path)
            if old and source_stat!=old['source_stat']:
                checkpoint=self._checkpoint(path)
                if checkpoint and checkpoint['version']>old['version']:
                    self._save(path,checkpoint);old=checkpoint
            if old and source_stat==old['source_stat']:
                if not old['pending'] or not write:
                    self._heartbeat(path,old,now)
                    return dict(stage='observed',writes=0,pending=len(old['pending']),review=len(old['review']))
                try:_writable(path)
                except (ValueError,RuntimeError):
                    self._heartbeat(path,old,now)
                    return dict(stage='pending_open_workbook',writes=0,pending=len(old['pending']),review=len(old['review']))
            book=read(path)
            plan,pending,review=self._plan(path,book,old,now)
            operation='observe-'+uuid.uuid4().hex
            self.journal.begin(path,book,operation,include_blocks=False,extra={'origin':'manual_observation','ledger_before_version':old['version'] if old else 0})
            publication_started=False
            try:
                writes=[];busy=False
                if pending and write:
                    try:_writable(path)
                    except (ValueError,RuntimeError):busy=True
                    if not busy:
                        for identity,day in pending.items():
                            position=book.view.dates.get(identity)
                            if position is None:
                                review.add(identity);continue
                            row,column,current=position
                            if current is None or current<day:writes.append((row,column,date.fromisoformat(day),True))
                after=book
                if writes:
                    with tempfile.TemporaryDirectory(prefix='manual-dates-',dir=self.local) as temp:
                        candidate=Path(temp)/path.name
                        if path.suffix.casefold()=='.xlsx':write_xlsx_dates(path,candidate,writes)
                        else:_write_xlsb(path,candidate,writes)
                        checked=read(candidate)
                        if checked.view.cells!=book.view.cells or _vba_parts(path)!=_vba_parts(candidate):raise ValueError('Не пройдена проверка сохранности книги')
                        actual={(r,c):day for _key,(r,c,day) in checked.view.dates.items()}
                        if any(actual.get((r,c))!=day.isoformat() for r,c,day,_ in writes):raise ValueError('Не подтверждена дата Excel')
                        backup=self.local/'backups'/hashlib.sha256(Store.key(path).encode()).hexdigest()[:16]/book.sha256/path.name
                        backup.parent.mkdir(parents=True,exist_ok=True)
                        if not backup.exists():shutil.copy2(path,backup)
                        if sha(backup)!=book.sha256:raise ValueError('Не совпала копия до записи')
                        _writable(path)
                        if sha(path)!=book.sha256:raise ValueError('Реестр изменился до публикации')
                        publication_started=True
                        import enrich
                        enrich._replace_on_share(candidate,path)
                        if sha(path)!=checked.sha256:raise ValueError('Не подтверждён результат замены')
                        after=read(path)
                state=self._state(path,after,old,operation,pending,review,now)
                self._save(path,state)
                self.journal.finish(path,after,operation,ledger=state,include_blocks=False)
                self._heartbeat(path,state,now)
                return dict(stage='verified',writes=len(writes),pending=len(state['pending']),review=len(state['review']),operation_id=operation)
            except BaseException:
                if not publication_started:self.journal.cancel_unpublished(path,operation,reason='failed_before_publication')
                elif sha(path)==book.sha256:self.journal.cancel_unpublished(path,operation,reason='verified_source_unchanged')
                raise

    @contextmanager
    def automatic(self,path,*,operation_id=None,require_closed=True):
        from .manual_sync import _writable,sha
        path=Path(path);folder=self._folder(path)
        with file_mutex(folder/'ledger.lock'):
            old=self._recover(path,self._load(path))
            exists=path.is_file()
            book=read(path) if exists else SimpleNamespace(sha256=None,mtime_ns=0,view=BookView((),{},0))
            if exists and require_closed:_writable(path)
            now=time.time()
            if exists:
                plan,pending,review=self._plan(path,book,old,now)
            else:
                pending={(row,block):day for row,block,day in old['pending']} if old else {}
                review=set(tuple(x) for x in old['review']) if old else set()
            operation=operation_id or 'auto-'+uuid.uuid4().hex
            self.journal.begin(path,book,operation,include_blocks=False,extra={
                'origin':'automatic','ledger_before_version':old['version'] if old else 0,
                'automatic_pending':[[row,block,day] for (row,block),day in sorted(pending.items())],
                'automatic_review':[list(x) for x in sorted(review)]})
            try:
                yield book
            except BaseException:
                unchanged=(not path.exists()) if not exists else path.is_file() and sha(path)==book.sha256
                if unchanged:self.journal.cancel_unpublished(path,operation,reason='verified_failed_operation_source_unchanged')
                raise
            else:
                after=read(path)
                state=self._state(path,after,old,operation,pending,review,time.time())
                self._save(path,state)
                self.journal.finish(path,after,operation,ledger=state,include_blocks=False)
                self._heartbeat(path,state,time.time())

    def recover_verified_publication(self,path,*,operation_id,pre_sha256,post_sha256):
        """Close exactly one interrupted automatic write with durable byte proof.

        This cannot approve arbitrary changed bytes or create edit timestamps.
        Pending/review metadata is the exact state saved before publication.
        """
        from .manual_sync import sha
        path=Path(path);folder=self._folder(path)
        with file_mutex(folder/'ledger.lock'):
            old=self._load(path)
            active=self.journal.active(path)
            if not active:
                if sha(path)!=post_sha256:raise ValueError('Не совпал результат записи книги')
                return
            if (active.get('operation_id')!=operation_id or active.get('origin')!='automatic'
                    or active.get('pre_sha256')!=pre_sha256 or sha(path)!=post_sha256):
                raise ValueError('Квитанция и результат прерванной записи не совпали')
            if 'automatic_pending' not in active or 'automatic_review' not in active:
                raise ValueError('Для старой операции нужна ручная проверка происхождения правок')
            pending={(row,block):day for row,block,day in active['automatic_pending']}
            review={tuple(item) for item in active['automatic_review']}
            after=read(path)
            state=self._state(path,after,old,operation_id,pending,review,time.time())
            self._save(path,state)
            self.journal.finish(path,after,operation_id,ledger=state,include_blocks=False,
                                outcome='recovered_verified_publication')
            self._heartbeat(path,state,time.time())
