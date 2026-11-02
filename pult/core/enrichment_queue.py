"""Durable, profile-bound delivery; an unfinished book is never a success.

Only the exact approved export is retried. Every publication has a durable
intent before the atomic replacement; recovery checks bytes before any write.
All snapshots, work files, original backups and receipts stay on local disk.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from contextlib import ExitStack

from .durable_jobs import Journal

TERMINAL = {'done'}
RETRY_SECONDS = 10


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), default=str).encode()).hexdigest()


def file_sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def authority(settings):
    from .settings import active_profile
    profile = active_profile(settings)
    return digest([settings.get('active'), str(profile.get('url', '')).rstrip('/'),
                   hashlib.sha256(str(profile.get('device_token', '')).encode()).hexdigest()])


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        if str(self.directory.resolve()).startswith('\\\\'):
            raise ValueError('Очередь и резервные копии должны храниться на этом компьютере')
        if os.name == 'nt':
            import ctypes
            if ctypes.windll.kernel32.GetDriveTypeW(str(self.directory.absolute().anchor)) == 4:
                raise ValueError('Очередь и резервные копии должны храниться на этом компьютере')
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'enrichment.sqlite3'
        self.codec = object.__new__(Journal)
        with self.connect() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS runs(
                    id TEXT PRIMARY KEY, authority TEXT NOT NULL,
                    state TEXT NOT NULL, created REAL NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS books(
                    run_id TEXT NOT NULL, ordinal INTEGER NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(run_id,ordinal));
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_run ON runs(authority) WHERE state<>'done';
            ''')

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.execute('PRAGMA synchronous=FULL')
        return conn

    def active(self, owner):
        with self.connect() as conn:
            row = conn.execute("SELECT payload FROM runs WHERE authority=? AND state<>'done' ORDER BY created LIMIT 1", (owner,)).fetchone()
        return self.codec.open(row[0]) if row else None

    def get(self, run_id, owner):
        with self.connect() as conn:
            row = conn.execute('SELECT authority,payload FROM runs WHERE id=?', (run_id,)).fetchone()
        if not row or row[0] != owner:
            raise ValueError('Операция относится к другому входу или серверу')
        return self.codec.open(row[1])

    def latest(self, owner):
        with self.connect() as conn:
            row = conn.execute('SELECT payload FROM runs WHERE authority=? ORDER BY created DESC LIMIT 1', (owner,)).fetchone()
        return self.codec.open(row[0]) if row else None

    def books(self, run_id):
        with self.connect() as conn:
            rows = conn.execute('SELECT payload FROM books WHERE run_id=? ORDER BY ordinal', (run_id,)).fetchall()
        return [self.codec.open(row[0]) for row in rows]

    def save_book(self, run_id, book):
        with self.connect() as conn:
            conn.execute('UPDATE books SET payload=? WHERE run_id=? AND ordinal=?',
                         (self.codec.seal(book), run_id, book['ordinal']))

    def save(self, run):
        with self.connect() as conn:
            conn.execute('UPDATE runs SET state=?,payload=? WHERE id=? AND authority=?',
                         (run['state'], self.codec.seal(run), run['id'], run['authority']))

    def create(self, run_id, owner, inputs, approved, fingerprint='enrichment'):
        if not run_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in run_id):
            raise ValueError('Некорректный номер операции')
        folder = self.directory / run_id
        folder.mkdir()
        snapshot = folder / 'source.bin'
        sealed = self.codec.seal(inputs)
        with snapshot.open('x', encoding='utf-8') as stream:
            stream.write(sealed); stream.flush(); os.fsync(stream.fileno())
        run = dict(id=run_id, authority=owner, state='running', created=time.time(),
                   since=inputs['since'], until=inputs['until'], source_sha256=file_sha(snapshot),
                   folder=str(folder), paused=False,fingerprint=fingerprint)
        with self.connect() as conn:
            if conn.execute("SELECT 1 FROM runs WHERE authority=? AND state<>'done'", (owner,)).fetchone():
                raise ValueError('Сначала завершите предыдущий перенос книг')
            conn.execute('INSERT INTO runs VALUES(?,?,?,?,?)', (run_id, owner, run['state'], run['created'], self.codec.seal(run)))
            for ordinal, job in enumerate(inputs['jobs']):
                item = dict(ordinal=ordinal, file=str(job['path']), employee=job['employee'],
                            state='pending', message='', next_retry=0,
                            approved=[row for row in approved if row['book_path'] == str(job['path'])])
                conn.execute('INSERT INTO books VALUES(?,?,?)', (run_id, ordinal, self.codec.seal(item)))
        return run

    def inputs(self, run):
        path = Path(run['folder']) / 'source.bin'
        if file_sha(path) != run['source_sha256']:
            raise ValueError('Не подтверждён сохранённый источник переноса')
        return self.codec.open(path.read_text('utf-8'))


class Service:
    def __init__(self, directory, owner, admin):
        self.store = Store(directory)
        self.owner = owner
        self.admin = admin
        self.mutex = threading.Lock()

    def current(self):
        return self.store.active(self.owner())

    def require_complete(self):
        run = self.current()
        if run:
            status = self.view(run)
            if not status['remaining']:
                self.process(run)
                return
            raise RuntimeError(f'Обогащение не завершено: осталось книг {status["remaining"]}. '
                               'Закройте занятые книги или исправьте указанные ошибки. '
                               'После завершения сделайте новый предпросмотр импорта.')

    def view(self, run=None):
        run = run or self.current() or self.store.latest(self.owner())
        if not run:
            return None
        books = self.store.books(run['id'])
        done = [book for book in books if book['state'] == 'done']
        pending = [book for book in books if book['state'] != 'done']
        state = 'done' if not pending else 'paused' if run.get('paused') else 'attention' if any(b['state'] == 'attention' for b in pending) else 'waiting'
        issues=[{**b,'reason_code':self.reason(b)} for b in books]
        exclusions=sum(len(b.get('omissions') or []) for b in books)
        return dict(id=run['id'], state=state, since=run['since'], until=run['until'],processing=self.mutex.locked(),
                    total=len(books), completed=len(done), remaining=len(pending),
                    busy=sum(b['reason_code']=='busy' and b['state']!='done' for b in issues),
                    attention=sum(b['state']=='attention' for b in books),
                    missing=sum(len(b.get('missing') or []) for b in pending),excluded=exclusions,
                    cells=sum(int((b.get('result') or {}).get('cells_written') or 0) for b in done),
                    books=[{k:b.get(k) for k in ['ordinal','file','employee','state','message','review_token','conflicts','reason_code','missing','omissions']} for b in issues],
                    toast='Обогащение завершено' if not pending else f'Перенос не завершён: осталось книг {len(pending)}',
                    toast_err=bool(pending), run_state=state)

    @staticmethod
    def reason(book):
        if book.get('reason_code'):return book['reason_code']
        if book.get('missing'):return 'missing'
        if book.get('conflicts'):return 'conflict'
        if book.get('state')=='waiting' and str(book.get('message','')).startswith('Книга открыта в Excel'):return 'busy'
        return 'error' if book.get('state')=='attention' else 'retry'

    @staticmethod
    def effective_job(job,item):
        from enrich import norm_cid
        omitted={row['contract_id'] for row in item.get('omissions') or []}
        return {**job,'rows':[row for row in job['rows'] if norm_cid(row.get('contract_id')) not in omitted]}

    def prepare(self, run_id, jobs, fields, since, until, approved, fingerprint='enrichment'):
        self.require_complete()
        # Paths and key spelling are frozen, including UNC versus mapped drives.
        normalized = [{**job, 'path': str(Path(job['path']).resolve())} for job in jobs]
        import enrich
        choices=[]
        for row in approved:
            choices.append({**row, 'book_path':str(Path(row['book_path']).resolve())})
        run = self.store.create(run_id, self.owner(),
                                dict(jobs=normalized, fields=fields, since=since, until=until), choices,fingerprint)
        return run

    def submit(self,run_id,jobs,fields,since,until,approved,say=lambda line:None):
        return self.process(self.prepare(run_id,jobs,fields,since,until,approved),say)

    def ready(self,run):
        return bool(run and not run.get('paused') and (all(item['state']=='done' for item in self.store.books(run['id'])) or any(
            item['state'] not in ('done','attention') and item.get('next_retry',0)<=time.time()
            for item in self.store.books(run['id']))))

    def failure(self,message):
        run=self.current()
        if not run:return None
        for item in self.store.books(run['id']):
            if item['state']!='done':self._attention(run,item,message)
        run['state']='attention';self.store.save(run)
        return self.view(run)

    def _ledger(self, jobs):
        from . import enrichment as en
        answer = self.admin('GET', en.ledger_query([j['employee'] for j in jobs])+'&books=1', timeout=120) or {}
        return {**dict(answer.get('ledger') or {}), '_books':dict(answer.get('books') or {})}

    def _attention(self, run, item, message,reason='error'):
        item.update(state='attention', message=str(message),reason_code=reason, next_retry=time.time()+RETRY_SECONDS)
        self.store.save_book(run['id'], item)

    def _wait(self, run, item, message,reason='retry'):
        item.update(state='waiting', message=str(message),reason_code=reason, next_retry=time.time()+RETRY_SECONDS)
        self.store.save_book(run['id'], item)

    def process(self, run=None, say=lambda line: None):
        if not self.mutex.acquire(blocking=False):
            return self.view()
        try:
            run = run or self.current()
            if not run or run.get('paused'):
                return self.view(run)
            if run['authority'] != self.owner():
                raise ValueError('Вход изменился; автоматический перенос остановлен')
            inputs = self.store.inputs(run)
            stack=ExitStack();native=[None]
            def get_excel():
                import enrich,xlwings as xw
                if native[0] is not None:
                    try:native[0].api.Workbooks.Count;return native[0]
                    except Exception:stack.close();native[0]=None
                enrich._co_init();native[0]=stack.enter_context(enrich.open_excel(xw,say));return native[0]
            for item in self.store.books(run['id']):
                if item['state'] in ('done','attention') or item.get('next_retry',0)>time.time():
                    continue
                if self.owner() != run['authority']:
                    break
                say(f'{item["employee"]} — {Path(item["file"]).name}')
                try:
                    self._book(run, item, inputs, say,get_excel)
                except Exception as exc:
                    if item.get('intent') or item.get('result'):
                        self._wait(run, item, 'Проверяется результат записи: '+str(exc))
                    else:
                        self._attention(run, item, str(exc))
            books = self.store.books(run['id'])
            run['state'] = 'done' if all(b['state']=='done' for b in books) else 'attention' if any(b['state']=='attention' for b in books) else 'waiting'
            self.store.save(run)
            return self.view(run)
        finally:
            try:
                if 'stack' in locals():stack.close()
            finally:self.mutex.release()

    def _book(self, run, item, inputs, say,get_excel=None):
        import enrich
        from . import enrichment as en
        from registry_cycle.file_access import inspect_busy_file
        job=self.effective_job(inputs['jobs'][item['ordinal']],item)
        # Reconcile a known publication before calculating any new writes.
        if item.get('intent') and not item.get('result'):
            intent=item['intent'];current=file_sha(item['file'])
            if current==intent['post_sha256']:
                from .manual_sync import recover_verified_publication
                recover_verified_publication(item['file'],intent)
                result={**intent,'employee':item['employee'],'path':item['file'],'file':item['file']}
                result['receipt']=str(enrich.write_batch_receipt([result],Path(run['folder'])/'receipts',since=inputs['since'],until=inputs['until']))
                item.update(result=result,state='ledger');self.store.save_book(run['id'],item)
            elif current==intent['pre_sha256']:
                item.pop('intent',None);item['state']='pending';self.store.save_book(run['id'],item)
            else:
                self._attention(run,item,'Книга изменилась после прерывания записи. Результат требует проверки; повторная запись не выполняется.');return
        if item.get('result'):
            return self._finish(run,item,inputs)
        busy=inspect_busy_file(item['file'])
        if busy:
            if getattr(busy,'kind','locked')!='locked':
                self._attention(run,item,'Не удалось получить доступ к книге: '+busy.reason,reason='access');return
            self._wait(run,item,'Книга открыта в Excel. Перенос продолжится после закрытия.',reason='busy');return
        try:
            ledger=self._ledger([job])
        except Exception as exc:
            self._wait(run,item,'Нет подтверждённой истории доставки: '+str(exc));return
        preview=enrich.preview_many([job],inputs['fields'],since=inputs['since'],until=inputs['until'],ledger=ledger)[0]
        if preview.get('error'):
            self._attention(run,item,preview['error']);return
        if preview.get('missing'):
            item['missing']=preview['missing']
            self._attention(run,item,'В книге нет договоров из выбранного переноса: '+', '.join(str(x) for x in preview['missing'][:12])+'. Сверьте с текущим общим реестром: назначения могли измениться.',reason='missing');return
        item.pop('missing',None)
        approved={en.conflict_key(row):row for row in item.get('approved',[])}
        decisions={};unreviewed=[]
        for conflict in en.collect_conflicts([preview])[0]:
            chosen=approved.get(en.conflict_key(conflict))
            if chosen and chosen['decision']=='file':decisions[en.conflict_key(conflict)]='file'
            elif chosen and chosen['decision']=='server' and all(str(chosen.get(k,''))==str(conflict.get(k,'')) for k in ('file','server','delivered','visit_date')):
                decisions[en.conflict_key(conflict)]='server'
            else:unreviewed.append(conflict)
        if unreviewed:
            item.update(conflicts=unreviewed,review_sha256=preview['file_sha256'],review_token=digest([preview['file_sha256'],unreviewed]))
            self._attention(run,item,'В книге появились новые изменения. Выберите, какие значения оставить.',reason='conflict');return
        for key,row in approved.items():
            if row['decision']=='file':decisions.setdefault(key,'file')
        expected=preview['file_sha256']
        folder=Path(run['folder'])
        for name in ('backups','receipts','work'):(folder/name).mkdir(exist_ok=True)
        item.update(state='preparing',message='Запись и проверка книги');self.store.save_book(run['id'],item)
        def before_commit(path):
            if file_sha(path)!=expected:
                raise enrich.BookChangedDuringRun('Книгу изменили после проверки; будет построен новый план')
        def publication(path,evidence):
            if self.owner()!=run['authority']:
                raise RuntimeError('Вход изменился до записи')
            item['intent']={**evidence,'employee':item['employee']};item['state']='publishing'
            self.store.save_book(run['id'],item)
        def delivered(payload):
            receipt=json.loads(Path(payload['receipt']).read_text('utf-8'))
            saved={k:v for k,v in receipt['files'][0].items() if not (k in ('error','written_unverified','not_attempted') and not v)}
            item['result']={**saved, 'ledger_entries':payload['entries'],
                            'employee':item['employee'],'file':item['file'],
                            'path':item['file'],'receipt':payload['receipt']}
            item.update(ledger_payload=payload,state='ledger');self.store.save_book(run['id'],item)
            answer=self.admin('POST','/admin/enrichment/ledger.json',payload,timeout=120)
            if not isinstance(answer,dict) or answer.get('recorded')!=len(payload['entries']):
                raise ValueError('История доставки подтверждена не полностью')
            item['ledger_ack']=answer;self.store.save_book(run['id'],item);return answer
        results=enrich.enrich_many([job],inputs['fields'],say,since=inputs['since'],until=inputs['until'],
                    ledger=ledger,decisions=decisions,server_authoritative=False,
                    backup_root=folder/'backups',receipt_dir=folder/'receipts',work_dir=folder/'work',
                    before_commit=before_commit,on_publication_intent=publication,on_delivered=delivered,
                    **({'excel_app':get_excel()} if get_excel else {}))
        result=results[0]
        if result.get('written_unverified'):
            item['result']=result;self.store.save_book(run['id'],item)
            self._attention(run,item,'Книга записана, проверка не завершена. Автоматическая повторная запись запрещена.');return
        if result.get('error'):
            # Engine guarantees errors without a publication intent precede writes.
            message=result['error']
            if item.get('intent'):
                self._wait(run,item,'Проверяется результат записи: '+message)
            elif any(code in message.lower() for code in ('rpc','-2146959355','-2147023174','-2147417848','сервер приложения','сессия excel')):
                self._wait(run,item,'Excel временно не ответил. Запись не начиналась; проверка будет повторена автоматически.')
            elif any(word in message.lower() for word in ('занят','откры','изменили','изменена','другом компьютере','sharing violation','zero-write')):
                self._wait(run,item,message)
            else:self._attention(run,item,message)
            return
        item.update(result=result,state='ledger');self.store.save_book(run['id'],item)
        self._finish(run,item,inputs)

    def _finish(self,run,item,inputs):
        import enrich
        result=item['result'];entries=enrich.ledger_entries_of([result])
        if result.get('written_unverified'):
            self._attention(run,item,'Не завершена проверка записанного файла; повторная запись запрещена.');return
        if entries and not item.get('ledger_ack'):
            ledger=self._ledger([inputs['jobs'][item['ordinal']]])
            values=enrich.ledger_for_book(ledger,item['file'])
            if all((values.get(e['contract_id'],{}).get(e['key']) or {}).get('value')==e['value'] for e in entries):
                item['ledger_ack']={'recorded':len(entries),'reconciled':True}
            else:
                # Never overwrite a later delivery from another operator.
                from datetime import datetime
                for entry in entries:
                    actual=values.get(entry['contract_id'],{}).get(entry['key']) or {}
                    try:newer=datetime.fromisoformat(str(actual.get('at') or '').replace('Z','+00:00')).timestamp()>run['created']
                    except ValueError:newer=False
                    if newer and actual.get('value')!=entry['value']:
                        self._attention(run,item,'Историю доставки уже изменил другой перенос. Старая квитанция автоматически не отправляется.');return
                if file_sha(item['file'])!=result.get('post_sha256'):
                    self._attention(run,item,'Книга изменилась, а подтверждение истории доставки отсутствует. Требуется проверка квитанции.');return
                ack=enrich.replay_ledger_receipt(result['receipt'],lambda payload:self.admin('POST','/admin/enrichment/ledger.json',payload,timeout=120))
                if ack.get('recorded')!=len(entries):raise ValueError('История доставки подтверждена не полностью')
                item['ledger_ack']=ack
        self.store.save_book(run['id'],item)
        # Check the same immutable source with the current ledger; no native
        # write is repeated merely because a previous HTTP response was lost.
        ledger=self._ledger([inputs['jobs'][item['ordinal']]])
        from . import enrichment as en
        choices={en.conflict_key(row):row['decision'] for row in item.get('approved',[])}
        post=enrich.preview_many([self.effective_job(inputs['jobs'][item['ordinal']],item)],inputs['fields'],since=inputs['since'],until=inputs['until'],ledger=ledger,decisions=choices)[0]
        if post.get('error') or post.get('cells_written') or post.get('conflicts') or post.get('missing'):
            self._attention(run,item,post.get('error') or 'После записи остались различия. Проверьте книгу; повторная запись не выполняется.');return
        item.update(state='done',message='Книга и история доставки проверены',verified_sha256=post['file_sha256'])
        item.pop('reason_code',None)
        self.store.save_book(run['id'],item)

    def preview_reconciliation(self,book):
        from . import registry_import as ri
        from enrich import norm_cid,cell_text
        run=self.current()
        if not run:raise ValueError('Незавершённого переноса нет')
        if file_sha(book['path'])!=book['file_sha256']:raise ValueError('Общий реестр изменился; выберите его заново')
        normalized=dict(book);ri.select_profile(normalized,'both')
        head,first,cid,hard=normalized['head'],normalized['first'],normalized['c_id'],normalized['owner_columns'][0]
        soft=normalized['owner_columns'][1]
        grouped={}
        for row in book['grid'][first:]:
            if len(row)>cid and norm_cid(row[cid]):grouped.setdefault(norm_cid(row[cid]),[]).append(row)
        proposals=[];blocked=[];inputs=self.store.inputs(run)
        for item in self.store.books(run['id']):
            if item['state']=='done' or not item.get('missing'):continue
            if item.get('intent') or item.get('result'):raise ValueError('Сначала проверьте исход выполненной записи')
            job=inputs['jobs'][item['ordinal']];profile=job.get('profile') or (job['rows'][0].get('_profile') if job['rows'] else None)
            if profile not in ('hard','soft'):raise ValueError('Не определён тип ответственности книги')
            for identifier in item['missing']:
                rows=grouped.get(identifier,[])
                owner=cell_text(rows[0][hard if profile=='hard' else soft]) if len(rows)==1 and len(rows[0])>(hard if profile=='hard' else soft) else ''
                detail=dict(ordinal=item['ordinal'],employee=item['employee'],contract_id=identifier,profile=profile,current_owner=owner)
                if len(rows)>1:blocked.append({**detail,'reason':'Номер договора неоднозначен в общем реестре'})
                elif rows and ri.emp_key(owner)==ri.emp_key(item['employee']):blocked.append({**detail,'reason':'Договор по-прежнему закреплён за этим сотрудником; исправьте состав личной книги'})
                else:proposals.append({**detail,'reason':'assignment_changed' if rows else 'absent_from_master'})
        plan=dict(run_id=run['id'],source_path=book['path'],source_sha256=book['file_sha256'],proposals=proposals,blocked=blocked)
        plan['token']=digest(plan)
        return plan

    def apply_reconciliation(self,plan):
        if file_sha(plan['source_path'])!=plan['source_sha256']:raise ValueError('Общий реестр изменился после сверки; выполните её заново')
        expected={k:v for k,v in plan.items() if k!='token'}
        if plan.get('token')!=digest(expected):raise ValueError('Не подтверждён план сверки')
        if not self.mutex.acquire(blocking=False):raise ValueError('Дождитесь завершения текущей проверки книги')
        try:
            run=self.store.get(plan['run_id'],self.owner());books=self.store.books(run['id'])
            for row in plan['proposals']:
                item=books[row['ordinal']]
                if item['state']=='done' or item.get('intent') or item.get('result') or row['contract_id'] not in item.get('missing',[]):raise ValueError('Очередь изменилась после сверки; выполните её заново')
            run.setdefault('reconciliations',[]).append({**plan,'at':time.time()});self.store.save(run)
            for item in books:
                changes=[r for r in plan['proposals'] if r['ordinal']==item['ordinal']]
                if not changes:continue
                item.setdefault('omissions',[]).extend([{**r,'source_path':plan['source_path'],'source_sha256':plan['source_sha256'],'reconciliation':plan['token']} for r in changes])
                removed={r['contract_id'] for r in changes};item['missing']=[c for c in item['missing'] if c not in removed]
                if not item['missing']:item.update(state='waiting',reason_code='retry',next_retry=0,message='Назначения сверены с общим реестром; ожидает переноса оставшихся строк')
                self.store.save_book(run['id'],item)
            run['state']='waiting';self.store.save(run)
            return self.view(run)
        finally:self.mutex.release()

    def retry(self,run_id,decisions=(),review_token=None):
        run=self.store.get(run_id,self.owner())
        if run['state']=='done':return self.view(run)
        for item in self.store.books(run_id):
            if item['state']=='done':continue
            if item.get('conflicts'):
                if review_token!=item.get('review_token'):continue
                if file_sha(item['file'])!=item.get('review_sha256'):raise ValueError('Книгу изменили после проверки; обновите список ошибок')
                from . import enrichment as en
                actual={en.conflict_key(row):row for row in item['conflicts']}
                selected=[]
                for choice in decisions:
                    row=actual.get(en.conflict_key(choice))
                    if row is None or choice.get('decision') not in ('file','server'):raise ValueError('Решение не относится к текущей проверке')
                    selected.append({**row,'decision':choice['decision']})
                if len(selected)!=len(actual):raise ValueError('Выберите действие для каждого нового конфликта')
                item['approved']=[r for r in item.get('approved',[]) if en.conflict_key(r) not in actual]+selected
                item.pop('conflicts',None)
            item.update(state='waiting',next_retry=0,message='Ожидает повторной проверки')
            self.store.save_book(run_id,item)
        run.update(state='waiting',paused=False);self.store.save(run)
        return self.view(run)
