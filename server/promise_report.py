"""Historical Soft promises, with an independent payment evaluation date.

The existing operations workbook keeps its period semantics. This view selects
immutable call snapshots, then reuses the same registry-payment attribution.
"""
from collections import defaultdict
from datetime import date
from decimal import Decimal
import json

import db
import digest_data
import soft_reporting
import staff_roster


STATUSES = {
    'kept': 'Выполнено (KP)',
    'partial': 'Частичная оплата',
    'waiting': 'Ожидаем срока',
    'unpaid': 'Срок прошёл, оплаты нет',
    'incomplete': 'Недостаточно данных',
}


def parameters(frm, to, asof, *, due='', country=''):
    def iso(value, label):
        try:
            parsed = date.fromisoformat(str(value or ''))
        except (ValueError, TypeError):
            raise ValueError('Укажите ' + label + '.') from None
        if parsed.isoformat() != value:
            raise ValueError('Проверьте ' + label + '.')
        return parsed
    lo, hi = iso(frm, 'начало периода обещаний'), iso(to, 'конец периода обещаний')
    cutoff = iso(asof, 'дату проверки оплат')
    today = db.workday.today()
    if lo > hi:
        raise ValueError('Начало периода позже его конца.')
    if cutoff < hi:
        raise ValueError('Дата проверки оплат раньше конца периода обещаний.')
    if cutoff > today:
        raise ValueError('Дата проверки оплат ещё не наступила.')
    if due:
        iso(due, 'срок оплаты')
    if country not in ('', 'tj', 'uz'):
        raise ValueError('Проверьте страну.')
    return dict(from_date=lo.isoformat(), to_date=hi.isoformat(), asof=cutoff.isoformat(),
                due=due, country=country, today=today.isoformat())


def _authors(employees, countries):
    """Expand only an explicit roster mapping already in the caller's scope."""
    cities = db.employee_cities()
    groups = {name: dict(employee=name, office=cities.get(name,''), country=countries.get(name, ''))
              for name in employees}
    with db.connect() as conn:
        roster = staff_roster.rows(conn, active_only=False)
    allowed = set(employees)
    for row in roster:
        names = {row['employee'], *json.loads(row['product_names']).values()}
        if not names & allowed:
            continue
        aliases = json.loads(row['product_names'])
        office = cities.get(aliases.get('soft',row['employee'])) or cities.get(row['employee']) or row['office']
        entry = dict(employee=row['employee'], office=office,
                     country=row['direction'] or countries.get(row['employee'], ''))
        # Only the canonical name and this person's explicit Soft name are call
        # authors. A similarly-spelled name is never inferred or added.
        for name in {row['employee'], aliases.get('soft', row['employee'])}:
            groups[name] = entry
    return groups


def _money(rows, key):
    amounts = defaultdict(Decimal)
    for row in rows:
        if row['currency'] and row.get(key) is not None:
            amounts[row['currency']] += Decimal(str(row[key]))
    return [dict(unit=unit, sum=float(amount)) for unit, amount in sorted(amounts.items())]


def _totals(items):
    result = dict(ptp=len(items))
    for key in STATUSES:
        result[key] = sum(row['state'] == key for row in items)
    result['kp_rate'] = result['kept'] / result['ptp'] if result['ptp'] else None
    result['promised_money'] = _money(items, 'amount')
    result['paid_money'] = _money(items, 'received')
    return result


def data(mgr, params, employees):
    countries = digest_data._employee_country_index(params['from_date'], params['to_date'])
    authors = _authors(employees, countries)
    if params['country']:
        authors = {name: row for name, row in authors.items() if row['country'] == params['country']}
    selected = []
    names = sorted(authors)
    with db.connect() as conn:
        for start in range(0, len(names), 400):
            chunk = names[start:start+400]
            marks = ','.join('?' for _ in chunk)
            selected.extend(conn.execute(
                'SELECT * FROM calls WHERE created_at>=? AND created_at<=? '
                f'AND employee IN ({marks}) ORDER BY created_at,id',
                (params['from_date'], params['to_date'], *chunk)).fetchall())
    promises = [row for row in selected if soft_reporting.status(row) == 'Обещает оплатить']
    ids = {cid for row in promises for cid in soft_reporting.contract_ids(row)}
    history = db.calls_for_contracts(ids, params['asof']) if ids else []
    history_ids = {cid for row in history for cid in soft_reporting.contract_ids(row)}
    contracts = {row['contract_id']: row for row in db.clients_by_ids(history_ids)} if history_ids else {}
    operation = soft_reporting.build(promises, contracts, history,
        db.registry_payments_for(history_ids), asof=params['asof'])
    by_id = {row['id']: row for row in promises}
    items = []
    for promise in operation['promises']:
        if params['due'] and promise['due'] != params['due']:
            continue
        author = by_id[promise['call_id']]['employee']
        identity = authors[author]
        closed = bool(promise['due'] and promise['due'] <= params['asof']
                      and promise['due'] < params['today'])
        state = promise['state']
        if state == 'pending':
            state = 'unpaid' if closed else 'waiting'
        amount = promise['amount'] if promise['amount'] > 0 else None
        items.append(dict(employee=identity['employee'], author=author,
                          office=identity['office'], country=identity['country'],
                          call_id=promise['call_id'], rep=promise['rep'],
                          date=promise['date'], due=promise['due'],
                          currency=promise['unit'], amount=amount,
                          received=promise['received'],
                          remaining=max(0, round(amount-promise['received'], 2)) if amount is not None else None,
                          state=state, status=STATUSES[state], deadline_closed=closed))
    items.sort(key=lambda row: (row['employee'].casefold(), row['date'], row['call_id']))
    groups = defaultdict(list)
    for row in items:
        groups[(row['employee'], row['office'], row['currency'])].append(row)
    rows = [dict(employee=employee, office=office, currency=currency, **_totals(values))
            for (employee, office, currency), values in sorted(groups.items())]
    freshness = db.payment_data_freshness(params['today'])
    return dict(screen='promise_report', params=params, manager_name=mgr.get('name') or '',
                scope=digest_data.scope_label(mgr), calls=len(selected), items=items,
                employees=rows, totals=_totals(items), payment_data=freshness,
                open_deadlines=any(not row['deadline_closed'] and row['state'] != 'incomplete' for row in items),
                assessment='По сохранённым реестровым оплатам')


def xlsx(mgr, params, employees):
    import promise_report_book
    return promise_report_book.write(data(mgr, params, employees))
