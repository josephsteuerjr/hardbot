"""Saved-workbook edit provenance in Python. No macros or Office trust access.

The observer keeps an explicit baseline. First sight is calibration, never
history. Known automatic writes refresh the baseline only after publication.
Unknown changes across an observation gap require review instead of invented
per-row timestamps from the workbook's single modification time.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable

import edit_tracking as tracking

ENDS = {
    tracking.EXCEL_KEYS[0]: 'комментарий звонка',
    tracking.EXCEL_KEYS[1]: 'Историчный комментарий Софт',
}


def comparable(value: Any):
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        # Excel serial dates are wall-clock values without a timezone. COM
        # attaches an artificial UTC tzinfo; calamine correctly returns naive.
        value=value.replace(tzinfo=None)
        return value.date().isoformat() if value.time().isoformat()=='00:00:00' else value.isoformat(timespec='seconds')
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return value
    if isinstance(value,Decimal):
        return int(value) if value==value.to_integral_value() else float(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False,
                                    sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def hashes_by_block(state):
    groups={}
    for row_key,block,title,value_hash in state:
        groups.setdefault((row_key,block),[]).append((title,value_hash))
    return {key:digest(sorted(values)) for key,values in groups.items()}


def block_states(book):
    return hashes_by_block((cell.identity[0],cell.block,cell.title,
                           digest(['formula' if cell.formula else 'value',cell.value])) for cell in book.cells)


@dataclass(frozen=True)
class Cell:
    contract_id: str
    block: str
    title: str
    row: int
    column: int
    value: Any
    formula: bool = False
    row_key: str | None = None

    @property
    def identity(self):
        return self.row_key or self.contract_id, self.block, self.title


@dataclass(frozen=True)
class BookView:
    cells: tuple[Cell, ...]
    dates: dict[tuple[str, str], tuple[int, int, str | None]]
    header_row: int


def view(grid: list[list], *, formula_cells: Iterable[tuple[int, int]] = ()) -> BookView:
    """Coordinates are zero-based, headers identify the two contextual blocks."""
    formulas = set(formula_cells)
    header_row = next((i for i, row in enumerate(grid[:10])
                       if any(tracking.normalized(c) == tracking.normalized('Номер КД / ID') for c in row)), None)
    if header_row is None:
        raise ValueError('Не найден заголовок КД')
    headers = grid[header_row]
    date_cols = tracking.columns(headers)
    id_cols = [i for i, value in enumerate(headers)
               if tracking.normalized(value) == tracking.normalized('Номер КД / ID')]
    if len(id_cols) != 1:
        raise ValueError('Неоднозначная колонка КД')
    bounds = {}
    for key, anchors in tracking.BOUNDARIES.items():
        starts = [i for i, title in enumerate(headers)
                  if tracking.normalized(title) in {tracking.normalized(a) for a in anchors}]
        ends = [i for i, title in enumerate(headers)
                if tracking.normalized(title) == tracking.normalized(ENDS[key])]
        if not starts:
            continue
        if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
            raise ValueError('Неоднозначная граница блока ручных правок')
        bounds[key] = range(starts[0], ends[0] + 1)
    cells, dates, contracts = [], {}, set()
    identity_titles={tracking.normalized(t) for t in ('Страна','Партнер','Пул','Клиент','Дата рождения','Дата оформления КД','Выданная сумма')}
    identity_columns=[(i,tracking.normalized(t)) for i,t in enumerate(headers) if tracking.normalized(t) in identity_titles]
    for row_number, row in enumerate(grid[header_row + 2:], header_row + 2):
        raw = row[id_cols[0]] if id_cols[0] < len(row) else None
        cid = str(comparable(raw) or '').strip()
        if not cid:
            continue
        identity_values=[(title,comparable(row[c] if c<len(row) else None)) for c,title in identity_columns]
        row_key=cid+'\x1f'+digest(identity_values) if identity_values else cid
        if row_key in contracts:
            raise ValueError('Повторяющийся КД: нельзя однозначно отслеживать строку')
        contracts.add(row_key)
        for key, columns in bounds.items():
            titles = set()
            for column in columns:
                title = tracking.normalized(headers[column])
                if not title or title in titles:
                    raise ValueError('Неоднозначное поле блока ручных правок')
                titles.add(title)
                value = row[column] if column < len(row) else None
                cells.append(Cell(cid, key, title, row_number, column,
                                  comparable(value), (row_number, column) in formulas,row_key))
        for column, key in date_cols.items():
            value = row[column] if column < len(row) else None
            dates[row_key, key] = row_number, column, tracking.canonical_day(value)
    return BookView(tuple(cells), dates, header_row)


@dataclass(frozen=True)
class Observation:
    book_key: str
    source_sha256: str
    baseline_sha256: str | None
    calibrated: bool
    writes: tuple[tuple[int, int, date, bool], ...]
    changed_blocks: tuple[tuple[str, str], ...]
    review_blocks: tuple[tuple[str, str], ...]
    state: tuple[tuple[str, str, str, str], ...]
    date_updates: tuple[tuple[str,str,str], ...] = ()
    seen_shared: tuple[str,...] = ()


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as conn, conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS manual_books(
                    book_key TEXT PRIMARY KEY, sha256 TEXT NOT NULL,
                    observed_at TEXT NOT NULL, origin TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS manual_cells(
                    book_key TEXT NOT NULL, contract_id TEXT NOT NULL,
                    block TEXT NOT NULL, title TEXT NOT NULL, value_hash TEXT NOT NULL,
                    PRIMARY KEY(book_key,contract_id,block,title));
                CREATE TABLE IF NOT EXISTS manual_operations(
                    operation_id TEXT PRIMARY KEY, book_key TEXT NOT NULL,
                    pre_sha256 TEXT, post_sha256 TEXT NOT NULL, origin TEXT NOT NULL,
                    observed_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS manual_pending_dates(
                    book_key TEXT NOT NULL,row_key TEXT NOT NULL,block TEXT NOT NULL,
                    edited_on TEXT NOT NULL,PRIMARY KEY(book_key,row_key,block));
                CREATE TABLE IF NOT EXISTS manual_review(
                    book_key TEXT NOT NULL,row_key TEXT NOT NULL,block TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,observed_at TEXT NOT NULL,
                    PRIMARY KEY(book_key,row_key,block));
                CREATE TABLE IF NOT EXISTS manual_auto_leases(
                    book_key TEXT PRIMARY KEY,operation_id TEXT NOT NULL UNIQUE,
                    pre_sha256 TEXT NOT NULL,started_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS manual_checks(
                    book_key TEXT PRIMARY KEY,checked_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS manual_seen_shared(
                    book_key TEXT NOT NULL,operation_id TEXT NOT NULL,
                    PRIMARY KEY(book_key,operation_id));
            ''')

    def connect(self):
        return sqlite3.connect(self.path, timeout=15)

    @staticmethod
    def key(path: str | Path) -> str:
        return str(Path(path).resolve()).casefold()

    def observe(self, path: str | Path, source_sha256: str, book: BookView,
                *, saved_on: date | None, continuously_observed: bool,
                known_automatic: Iterable[tuple[str, str, str, Any]] = (),
                automatic_blocks: Iterable[dict] = (),
                external_baseline: tuple[str|None,dict]|None=None) -> Observation:
        """Plan only. Caller proves stable read, formula locations and save day.

        continuous observation is mandatory for assigning a historical save
        day. A file modification time alone cannot prove each changed row day
        after an offline gap. Review cases are not silently acknowledged.
        """
        key = self.key(path)
        with closing(self.connect()) as conn:
            prior = conn.execute('SELECT sha256 FROM manual_books WHERE book_key=?', (key,)).fetchone()
            baseline = {tuple(row[:3]): row[3] for row in conn.execute(
                'SELECT contract_id,block,title,value_hash FROM manual_cells WHERE book_key=?', (key,))}
        if external_baseline is not None:
            prior=(external_baseline[0],) if external_baseline[0] is not None else None
        automatic = {(cid, block, tracking.normalized(title)): digest(comparable(value))
                     for cid, block, title, value in known_automatic}
        state = tuple((cell.identity[0], cell.block, cell.title,
                       digest(['formula' if cell.formula else 'value', cell.value]))
                      for cell in book.cells)
        transitions=list(automatic_blocks)
        events={}
        if prior is not None:
            for cell in book.cells:
                if automatic.get(cell.identity,automatic.get((cell.contract_id,cell.block,cell.title))) == digest(cell.value):
                    baseline[cell.identity]=digest(['formula' if cell.formula else 'value',cell.value])
            expected=external_baseline[1] if external_baseline is not None else hashes_by_block((*identity,value_hash) for identity,value_hash in baseline.items())
            for operation in transitions:
                before={(row,block):value for row,block,value in operation['before_blocks']}
                after={(row,block):value for row,block,value in operation['after_blocks']}
                for identity,value in before.items():
                    if identity in expected and expected[identity]!=value:
                        events[identity]=operation.get('saved_on') if operation.get('continuous') else None
                expected=after
            current=hashes_by_block(state)
            for identity,value in current.items():
                if identity in expected and expected[identity]!=value:
                    events[identity]=saved_on if continuously_observed else None
        changed=set(events)
        writes, review,updates = [], [],[]
        for identity in sorted(changed):
            position = book.dates.get(identity)
            day=events[identity]
            if position is None or day is None:
                review.append(identity)
                continue
            row, column, current = position
            if current is None or day.isoformat() > current:
                writes.append((row, column, day, True))
                updates.append((*identity,day.isoformat()))
        return Observation(key, source_sha256, prior[0] if prior else None,
                           prior is None, tuple(sorted(writes)), tuple(sorted(changed)),
                           tuple(review), state,tuple(updates),tuple(t['operation_id'] for t in transitions))

    def commit(self, plan: Observation, *, post_sha256: str, operation_id: str,
               origin: str, observed_at: str | None = None,
               retain_unknown_day_for_review: bool = False,
               acknowledged_dates: Iterable[tuple[str,str,str]] = (),
               lease_operation_id: str | None = None):
        """Call only after verified publication (or explicit calibration).

        CAS and idempotency prevent stale observers and uncertain retries from
        moving the baseline. A review is retained for an explicit resolution.
        """
        if plan.review_blocks and not retain_unknown_day_for_review:
            raise ValueError('Неизвестный день ручной правки: требуется проверка')
        if origin not in ('calibration', 'manual_observation', 'automatic'):
            raise ValueError('Неизвестное происхождение снимка')
        observed_at = observed_at or datetime.now(timezone.utc).isoformat()
        with closing(self.connect()) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            old_operation = conn.execute('SELECT book_key,post_sha256,origin FROM manual_operations WHERE operation_id=?', (operation_id,)).fetchone()
            if old_operation:
                if old_operation != (plan.book_key, post_sha256, origin):
                    raise ValueError('operation_id уже связан с другой записью')
                return
            lease=conn.execute('SELECT operation_id FROM manual_auto_leases WHERE book_key=?',(plan.book_key,)).fetchone()
            if lease and (origin!='automatic' or lease_operation_id!=lease[0]):
                raise ValueError('В книге выполняется автоматическая запись')
            current = conn.execute('SELECT sha256 FROM manual_books WHERE book_key=?', (plan.book_key,)).fetchone()
            if (current[0] if current else None) != plan.baseline_sha256:
                raise ValueError('Базовый снимок уже изменился')
            conn.execute('DELETE FROM manual_cells WHERE book_key=?', (plan.book_key,))
            conn.executemany('INSERT INTO manual_cells VALUES(?,?,?,?,?)',
                             ((plan.book_key, *cell) for cell in plan.state))
            conn.execute('INSERT INTO manual_books VALUES(?,?,?,?) ON CONFLICT(book_key) DO UPDATE SET sha256=excluded.sha256, observed_at=excluded.observed_at, origin=excluded.origin',
                         (plan.book_key, post_sha256, observed_at, origin))
            conn.execute('INSERT INTO manual_operations VALUES(?,?,?,?,?,?)',
                         (operation_id, plan.book_key, plan.source_sha256,
                          post_sha256, origin, observed_at))
            for row_key,block,day in plan.date_updates:
                conn.execute('INSERT INTO manual_pending_dates VALUES(?,?,?,?) ON CONFLICT(book_key,row_key,block) DO UPDATE SET edited_on=max(edited_on,excluded.edited_on)',(plan.book_key,row_key,block,day))
                conn.execute('DELETE FROM manual_review WHERE book_key=? AND row_key=? AND block=?',(plan.book_key,row_key,block))
            for row_key,block in plan.review_blocks:
                conn.execute('INSERT INTO manual_review VALUES(?,?,?,?,?) ON CONFLICT(book_key,row_key,block) DO UPDATE SET source_sha256=excluded.source_sha256,observed_at=excluded.observed_at',(plan.book_key,row_key,block,plan.source_sha256,observed_at))
            for row_key,block,day in acknowledged_dates:
                conn.execute('DELETE FROM manual_pending_dates WHERE book_key=? AND row_key=? AND block=? AND edited_on<=?',(plan.book_key,row_key,block,day))
            conn.executemany('INSERT OR IGNORE INTO manual_seen_shared VALUES(?,?)',((plan.book_key,op) for op in plan.seen_shared))
            if lease_operation_id:
                conn.execute('DELETE FROM manual_auto_leases WHERE book_key=? AND operation_id=?',(plan.book_key,lease_operation_id))

    def pending(self,path: str|Path):
        with closing(self.connect()) as conn:
            return tuple(conn.execute('SELECT row_key,block,edited_on FROM manual_pending_dates WHERE book_key=? ORDER BY row_key,block',(self.key(path),)))

    def seen_shared(self,path: str|Path):
        key=self.key(path)
        with closing(self.connect()) as conn:
            return {r[0] for r in conn.execute('SELECT operation_id FROM manual_seen_shared WHERE book_key=? UNION SELECT operation_id FROM manual_operations WHERE book_key=? AND origin=?',(key,key,'automatic'))}

    def reviews(self,path: str|Path):
        with closing(self.connect()) as conn:
            return tuple(conn.execute('SELECT row_key,block,source_sha256,observed_at FROM manual_review WHERE book_key=? ORDER BY row_key,block',(self.key(path),)))

    def baseline(self,path: str|Path):
        with closing(self.connect()) as conn:
            row=conn.execute('SELECT sha256,observed_at,origin FROM manual_books WHERE book_key=?',(self.key(path),)).fetchone()
            return row

    def heartbeat(self,path: str|Path,checked_at: float):
        with closing(self.connect()) as conn,conn:
            conn.execute('INSERT INTO manual_checks VALUES(?,?) ON CONFLICT(book_key) DO UPDATE SET checked_at=excluded.checked_at',(self.key(path),checked_at))

    def recently_observed(self,path: str|Path,now: float,max_gap_seconds: float=90):
        with closing(self.connect()) as conn:
            row=conn.execute('SELECT checked_at FROM manual_checks WHERE book_key=?',(self.key(path),)).fetchone()
            return bool(row and 0<=now-row[0]<=max_gap_seconds)

    def lease(self,path: str|Path):
        with closing(self.connect()) as conn:
            return conn.execute('SELECT operation_id,pre_sha256,started_at FROM manual_auto_leases WHERE book_key=?',(self.key(path),)).fetchone()

    def begin_automatic(self,path: str|Path,*,operation_id: str,pre_sha256: str):
        key=self.key(path)
        with closing(self.connect()) as conn,conn:
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute('SELECT 1 FROM manual_auto_leases WHERE book_key=?',(key,)).fetchone():
                raise ValueError('Не завершена предыдущая автоматическая запись')
            baseline=conn.execute('SELECT sha256 FROM manual_books WHERE book_key=?',(key,)).fetchone()
            if not baseline or baseline[0]!=pre_sha256:
                raise ValueError('Перед автоматической записью не учтены изменения файла')
            conn.execute('INSERT INTO manual_auto_leases VALUES(?,?,?,?)',(key,operation_id,pre_sha256,datetime.now(timezone.utc).isoformat()))

    def abort_unchanged(self,path: str|Path,*,operation_id: str,actual_sha256: str):
        key=self.key(path)
        with closing(self.connect()) as conn,conn:
            row=conn.execute('SELECT operation_id,pre_sha256 FROM manual_auto_leases WHERE book_key=?',(key,)).fetchone()
            if row!=(operation_id,actual_sha256):
                raise ValueError('Итог записи неизвестен: сначала проверить квитанцию')
            conn.execute('DELETE FROM manual_auto_leases WHERE book_key=?',(key,))

    def cancel_unpublished(self,path: str|Path,*,operation_id: str):
        """Caller proves publication never started; concurrent user edits stay."""
        with closing(self.connect()) as conn,conn:
            conn.execute('DELETE FROM manual_auto_leases WHERE book_key=? AND operation_id=?',(self.key(path),operation_id))

    def automatic(self, path: str | Path, sha256: str, book: BookView,
                  *, pre_sha256: str, operation_id: str,leased: bool=False):
        """A writer supplies its actual verified post-write state explicitly."""
        plan = self.observe(path, sha256, book, saved_on=None,
                            continuously_observed=False)
        if plan.baseline_sha256 != pre_sha256:
            raise ValueError('Перед автоматической записью не учтены изменения файла')
        if leased:
            lease=self.lease(path)
            if not lease or lease[:2]!=(operation_id,pre_sha256):raise ValueError('Не совпала операция автоматической записи')
        plan = Observation(plan.book_key, plan.source_sha256,
                           plan.baseline_sha256, plan.calibrated, (), (), (), plan.state)
        self.commit(plan, post_sha256=sha256, operation_id=operation_id,
                    origin='automatic',acknowledged_dates=[(row_key,block,current) for (row_key,block),(_row,_column,current) in book.dates.items() if current],lease_operation_id=operation_id if leased else None)
