"""Fail-closed collection planner for pre-versioned employee workbooks.

Pure policy: no Excel, network, or publication. An unresolved difference
blocks the entire plan. This adapter is a bridge, not a revision protocol.
"""
from dataclasses import dataclass, field
from datetime import date
from .collection_guard import comparable, day, CollectionConflict, choose

KEY = 'Номер КД / ID'
PARTNER = 'Партнер'
OWNERS = {'soft': 'ответственный Сотрудник СОФТ', 'hard': 'ответственный Сотрудник ХАРД'}
EDIT_DAYS = {'soft': 'Дата ручной правки звонка', 'hard': 'Дата ручной правки выезда'}
EVENT_DAYS = {'soft': 'Дата коммуникации', 'hard': 'Дата выезда'}
PAYMENT_FIELDS = {'Сумма последнего платежа', 'Дата последнего платежа'}
FORECAST = ('Сумма прогноза', 'Прогноз', 'дата обещания', 'Общий результат')
EXPLICIT_ROLES = {
    'статус звонка софт': 'soft', 'Комментарий текущего месяца Софт': 'soft',
    'Дата коммуникации': 'soft', 'Дата ручной правки звонка': 'soft',
    'Дата выезда': 'hard', 'Комментарий текущего месяца выезда': 'hard',
    'Cтатус звонка': 'hard', 'комментарий звонка': 'hard', 'Дата ручной правки выезда': 'hard',
}


def normalized(value):
    if comparable(value)[0] == 'empty':
        return ''
    return ' '.join(str(value).strip().casefold().split())


def identity(row):
    cid = row.get(KEY)
    if comparable(cid)[0] == 'empty':
        return None
    # Numeric cells and textual integral IDs match without changing leading zeros.
    if isinstance(cid, (float, int)) and float(cid).is_integer():
        cid = str(int(cid))
    return str(cid).strip(), normalized(row.get(PARTNER))


def clock(row, role):
    return max((v for title in (EDIT_DAYS[role], EVENT_DAYS[role])
                if (v := day(row.get(title)))), default=None)


def canonical(title):
    return 'Дата встречи/Дата перезвона' if title == 'Дата встречи' else str(title).strip()


@dataclass
class Book:
    name: str
    role: str
    owner: str
    rows: list[dict]


@dataclass
class Plan:
    changes: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    stale_assignments: int = 0
    unmatched: list[dict] = field(default_factory=list)

    @property
    def blocked(self):
        return bool(self.conflicts)


def plan(master_rows, books, field_roles, target_fields, *, today=None):
    today = today or date.today()
    result = Plan()
    index = {}
    for pos, row in enumerate(master_rows):
        key = identity(row)
        if key is None:
            continue
        if not key[1] or key in index:
            result.conflicts.append(dict(kind='master_identity', key=key, row=pos))
        index[key] = (pos, row)
    sources = {}
    for book in books:
        if book.role not in OWNERS:
            raise ValueError('Не определён профиль книги: ' + book.name)
        seen = set()
        for row in book.rows:
            key = identity(row)
            if key is None:
                continue
            if not key[1] or key in seen:
                result.conflicts.append(dict(kind='source_identity', key=key, book=book.name))
                continue
            seen.add(key)
            if key not in index:
                result.unmatched.append(dict(key=key, book=book.name))
                # A full scatter would discard these employee-only records.
                result.conflicts.append(dict(kind='unmatched_contract', key=key, book=book.name))
                continue
            master = index[key][1]
            title = OWNERS[book.role]
            owner = normalized(master.get(title))
            if not owner or owner != normalized(row.get(title)) or owner != normalized(book.owner):
                result.stale_assignments += 1
                # Do not silently erase old-owner work during a subsequent scatter.
                fields = [f for f in target_fields if f not in PAYMENT_FIELDS | set(OWNERS.values())
                          and EXPLICIT_ROLES.get(f, field_roles.get(f, 'shared')) in (book.role, 'shared')]
                if any(comparable(row.get(f))[0] != 'empty' and comparable(row.get(f)) != comparable(master.get(f)) for f in fields):
                    result.conflicts.append(dict(kind='stale_assignment_changes', key=key, book=book.name))
                continue
            if clock(row, book.role) and clock(row, book.role) > today:
                result.conflicts.append(dict(kind='future_edit', key=key, book=book.name))
                continue
            sources.setdefault(key, []).append((book, row))
    for key, entries in sources.items():
        pos, current = index[key]
        fields = set(target_fields) - PAYMENT_FIELDS - set(OWNERS.values()) - set(FORECAST)
        proposals = {}
        for title in sorted(fields):
            role = EXPLICIT_ROLES.get(title, field_roles.get(title, 'shared'))
            allowed = [(b, r) for b, r in entries if role in ('shared', b.role) and title in r]
            baseline_clock = clock(current, role) if role in OWNERS else max((d for k in OWNERS if (d := clock(current, k))), default=None)
            selected, selected_day = current.get(title), baseline_clock
            for book, row in allowed:
                incoming = row.get(title)
                # A prior-month projection must not repopulate an explicitly
                # emptied current-month comment during the next collection.
                if title.startswith('Комментарий текущего месяца') and comparable(current.get(title))[0] == 'empty' and comparable(incoming)[0] != 'empty':
                    source_day = day(row.get(EVENT_DAYS[book.role]))
                    if not source_day:
                        result.conflicts.append(dict(kind='ambiguous_value', key=key, field=title,
                                                     book=book.name, master=current.get(title), incoming=incoming))
                        continue
                    if (source_day.year, source_day.month) < (today.year, today.month):
                        continue
                try:
                    if choose(selected, incoming, selected_day, clock(row, book.role)):
                        selected, selected_day = incoming, clock(row, book.role)
                except CollectionConflict:
                    result.conflicts.append(dict(kind='ambiguous_value', key=key, field=title,
                                                 book=book.name, master=current.get(title), incoming=incoming))
            if comparable(selected) != comparable(current.get(title)):
                proposals[title] = selected
        # Forecast and overall result are one projection, never a mix of two events.
        group = tuple(f for f in FORECAST if f in target_fields)
        base = tuple(current.get(f) for f in group)
        chosen = base
        chosen_day = max((d for k in OWNERS if (d := clock(current, k))), default=None)
        for book, row in entries:
            if not group or not all(f in row for f in group):
                continue
            candidate = tuple(row.get(f) for f in group)
            if all(comparable(v)[0] == 'empty' for v in candidate):
                continue  # legacy blanks are not explicit deletion instructions
            if tuple(map(comparable, chosen)) == tuple(map(comparable, candidate)):
                chosen_day = max((d for d in (chosen_day, clock(row, book.role)) if d), default=None)
                continue
            incoming_day = clock(row, book.role)
            if (not chosen_day and all(comparable(v)[0] == 'empty' for v in chosen)) or (chosen_day and incoming_day and incoming_day > chosen_day):
                chosen, chosen_day = candidate, incoming_day
            elif not (chosen_day and incoming_day and incoming_day < chosen_day):
                result.conflicts.append(dict(kind='ambiguous_forecast_group', key=key, book=book.name,
                                             master=base, incoming=candidate))
        for title, before, after in zip(group, base, chosen):
            if comparable(before) != comparable(after):
                proposals[title] = after
        for title, value in proposals.items():
            result.changes.append(dict(row=pos, key=key, field=title, before=current.get(title), after=value))
    return result
