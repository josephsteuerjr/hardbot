"""Permanent operation receipts shared by Python writers on different PCs.

This is an operation journal, not a workbook cache: it contains only file
digests and hashes of result blocks. The directory is supplied explicitly by
the deployment configuration; the module never chooses a network destination.
"""
from __future__ import annotations
from datetime import datetime,timezone
import hashlib,json,os
from pathlib import Path
import time

from .manual_edits import Store,block_states,digest


def _sealed(payload):
    return dict(payload,payload_digest=digest(payload))


def _read(path):
    payload=json.loads(Path(path).read_text('utf-8'))
    checksum=payload.pop('payload_digest')
    if checksum!=digest(payload) or payload.get('schema')!=1:
        raise ValueError('Не подтверждена квитанция автоматической записи')
    return payload


def _create(path,payload):
    raw=json.dumps(_sealed(payload),ensure_ascii=False,separators=(',',':'))
    with Path(path).open('x',encoding='utf-8') as f:
        f.write(raw);f.flush();os.fsync(f.fileno())


class Journal:
    def __init__(self,directory):
        self.directory=Path(directory)

    def book_directory(self,path):
        key=hashlib.sha256(Store.key(path).encode()).hexdigest()
        return self.directory/key[:2]/key

    def active(self,path):
        folder=self.book_directory(path);active=folder/'active.json'
        if not active.exists():return None
        intent=_read(active)
        if intent['book_key']!=Store.key(path):raise ValueError('Квитанция относится к другой книге')
        op=intent['operation_id']
        if (folder/(op+'.complete.json')).is_file() or (folder/(op+'.aborted.json')).is_file():
            # The result must be a valid sealed record before the intent ends.
            terminal=_read(folder/(op+('.complete.json' if (folder/(op+'.complete.json')).exists() else '.aborted.json')))
            if terminal['operation_id']!=op or terminal['book_key']!=intent['book_key']:
                raise ValueError('Не совпала завершающая квитанция')
            return None
        return intent

    def begin(self,path,book,operation_id,*,include_blocks=True,extra=None):
        if not operation_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in operation_id):
            raise ValueError('Некорректный operation_id')
        folder=self.book_directory(path);folder.mkdir(parents=True,exist_ok=True)
        active=folder/'active.json'
        if active.exists():
            raise ValueError('Не освобождена предыдущая операция; сначала проверить квитанцию')
        payload=dict(schema=1,kind='intent',operation_id=operation_id,
                     book_key=Store.key(path),started_at=time.time(),
                     at_utc=datetime.now(timezone.utc).isoformat(),
                     pre_sha256=book.sha256,mtime_ns=book.mtime_ns,
                     blocks=[[row,block,value] for (row,block),value in sorted(block_states(book.view).items())] if include_blocks else [])
        if extra:payload.update(extra)
        _create(active,payload)
        return payload

    def finish(self,path,book,operation_id,*,ledger=None,include_blocks=True,outcome='verified'):
        folder=self.book_directory(path);active=folder/'active.json';intent=_read(active)
        if intent['operation_id']!=operation_id:raise ValueError('Не совпала операция журнала')
        payload=dict(schema=1,kind='complete',operation_id=operation_id,
                     book_key=Store.key(path),post_sha256=book.sha256,
                     finished_at=time.time(),intent_digest=digest(intent),
                     blocks=[[row,block,value] for (row,block),value in sorted(block_states(book.view).items())] if include_blocks else [],outcome=outcome)
        if ledger is not None:payload['ledger']=ledger
        _create(folder/(operation_id+'.complete.json'),payload)
        os.replace(active,folder/(operation_id+'.intent.json'))

    def cancel_unpublished(self,path,operation_id,*,reason):
        folder=self.book_directory(path);active=folder/'active.json';intent=_read(active)
        if intent['operation_id']!=operation_id:raise ValueError('Не совпала операция журнала')
        _create(folder/(operation_id+'.aborted.json'),dict(schema=1,kind='aborted',operation_id=operation_id,book_key=Store.key(path),intent_digest=digest(intent),reason=reason,finished_at=time.time()))
        os.replace(active,folder/(operation_id+'.intent.json'))

    def completed(self,path,seen=()):
        if self.active(path):raise ValueError('Автоматическая операция на другом компьютере ещё не завершена')
        folder=self.book_directory(path)
        if not folder.is_dir():return []
        seen=set(seen);result=[]
        for completion in folder.glob('*.complete.json'):
            op=completion.name[:-len('.complete.json')]
            if op in seen:continue
            end=_read(completion)
            intent_path=folder/(op+'.intent.json')
            if not intent_path.exists():intent_path=folder/'active.json'
            start=_read(intent_path)
            if end['operation_id']!=op or start['operation_id']!=op or end['book_key']!=Store.key(path) or start['book_key']!=Store.key(path) or end['intent_digest']!=digest(start):
                raise ValueError('Цепочка квитанций не совпала')
            result.append(dict(operation_id=op,started_at=start['started_at'],
                               mtime_ns=start['mtime_ns'],pre_sha256=start['pre_sha256'],
                               post_sha256=end['post_sha256'],before_blocks=start['blocks'],
                               after_blocks=end['blocks']))
        return sorted(result,key=lambda r:(r['started_at'],r['operation_id']))
