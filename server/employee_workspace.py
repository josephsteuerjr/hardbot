"""Employee read-only registry and monthly cabinet. Never changes assignments."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import re

import db
import debt
import office_work
import workday
from fields import BY_KEY

MONTH_COLUMN = re.compile(r'^Оплаты за (0[1-9]|1[0-2])\.(\d{2})$')
MONEY_KEYS = (*debt.ARTICLES, 'debt_total', 'debt_today', 'issued_sum')


def init_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS registry_monthly_totals (
        contract_id TEXT NOT NULL, month TEXT NOT NULL, amount TEXT,
        currency TEXT NOT NULL, source_column TEXT NOT NULL,
        observed_at TEXT NOT NULL, PRIMARY KEY(contract_id, month))''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_registry_monthly_month ON registry_monthly_totals(month)')


def amount(value):
    """Keep cents, known zero and signed corrections; unknown stays None."""
    if value is None or isinstance(value, bool) or str(value).strip() in ('', '—', '-'):
        return None
    text = re.sub(r'[^\d.,-]', '', str(value))
    if ',' in text and '.' in text:
        separator = ',' if text.rfind(',') > text.rfind('.') else '.'
        text = text.replace('.' if separator == ',' else ',', '').replace(separator, '.')
    else:
        text = text.replace(',', '.')
    try:
        value = Decimal(text)
        if value.is_finite() and abs(value) <= Decimal(str(db.MONEY_MAX)):
            return value.quantize(Decimal('.01'))
    except InvalidOperation:
        pass
    return None


def month_columns(header):
    """The template places current totals after explicitly dated prior months.

    No clock-based rollover: an October file imported in November stays October.
    Without a dated history column the ambiguous current column is not assigned
    a month. Historical columns remain independently usable.
    """
    result = {}
    current = []
    for index, title in enumerate(header):
        title = str(title or '').strip()
        match = MONTH_COLUMN.fullmatch(title)
        if match:
            result[index] = (f'20{match[2]}-{match[1]}', title)
        elif title == 'Текущие платежи':
            current.append(index)
    if result and len(current) == 1:
        latest = max(month for month, _ in result.values())
        year, month = map(int, latest.split('-'))
        following = f'{year + (month == 12):04d}-{month % 12 + 1:02d}'
        if following <= workday.today().strftime('%Y-%m'):
            result[current[0]] = (following, 'Текущие платежи')
    return result


MONTHLY_TOTAL_UPSERT = '''INSERT INTO registry_monthly_totals VALUES(?,?,?,?,?,?)
    ON CONFLICT(contract_id,month) DO UPDATE SET amount=excluded.amount,
    currency=excluded.currency,source_column=excluded.source_column,
    observed_at=excluded.observed_at'''


def monthly_rows(cid, raw, columns, partner, observed_at):
    currency = debt.unit([partner])
    for index, (month, title) in columns.items():
        cell = raw[index] if index < len(raw) else None
        # In this registry template a present monthly payment column with an
        # empty cell means no payment, as in db.paid_months. Missing columns
        # and missing contract/month snapshots never turn into known zero.
        value = Decimal('0.00') if cell is None or str(cell).strip() == '' else amount(cell)
        yield (cid, month, str(value) if value is not None else None,
               currency, title, observed_at)


def remember_monthly(conn, cid, raw, columns, partner):
    observed_at = datetime.now(timezone.utc).isoformat(timespec='seconds')
    conn.executemany(MONTHLY_TOTAL_UPSERT, monthly_rows(cid, raw, columns, partner, observed_at))


def row_data(row):
    result = {key: debt._cell(row, key) for key in (
        'contract_id', 'client', 'partner', 'pool', 'address', 'phone', 'phone_main',
        'birth_date', 'kd_date', 'dp', 'product_type', 'assign_date',
        'last_payment_date', 'last_payment_sum', 'overall_result', 'visit_date',
        'meeting_date', 'soft_comm_date', 'soft_next_call_date')}
    for key in MONEY_KEYS:
        value = amount(debt._cell(row, key))
        result[key] = float(value) if value is not None else None
    result['total'] = result['debt_today'] if result['debt_today'] is not None else result['debt_total']
    result['unit'] = debt.unit([result['partner']])
    result['soft_status'] = db.effective_soft_status(row)
    result['debt'] = [{'key': key, 'title': BY_KEY[key].title,
                       'value': result[key]} for key in debt.ARTICLES
                      if result[key] is not None]
    return result


def _freshness(conn):
    row = conn.execute("SELECT MAX(consumed_at) FROM import_receipts WHERE status='consumed'").fetchone()
    return row[0] or ''


def _rows(employee, profile):
    if profile not in ('hard', 'soft'):
        raise ValueError('Выберите Хард или Софт')
    return office_work.portfolio_rows(employee, profile)


def portfolio(employee, profile, *, query='', partner='', sort='client', direction='asc', page=1, size=100):
    if sort not in {'client', 'contract_id', 'partner', 'pool', 'total', 'dp', 'kd_date', 'issued_sum', *debt.ARTICLES}:
        raise ValueError('Неизвестный порядок сортировки')
    if direction not in ('asc', 'desc') or not 1 <= size <= 200 or page < 1:
        raise ValueError('Неверная страница')
    rows = _rows(employee, profile)
    partners = sorted({str(r['partner']) for r in rows if r['partner']})
    words = db._name_key(query).split()
    selected = []
    for row in rows:
        item = row_data(row)
        if partner and item['partner'] != partner:
            continue
        haystack = db._name_key(' '.join(str(item.get(k) or '') for k in ('client', 'contract_id', 'phone', 'phone_main', 'address', 'partner', 'pool')))
        if words and not all(word in haystack for word in words):
            continue
        selected.append(item)
    def sort_key(item):
        value = item.get(sort)
        if sort == 'dp':
            value = amount(value)
        return (value is None or value == '', value if isinstance(value, (int, float, Decimal)) else str(value or '').casefold(), item['contract_id'])
    known = [r for r in selected if r.get(sort) not in (None, '')]
    unknown = [r for r in selected if r.get(sort) in (None, '')]
    selected = sorted(known, key=sort_key, reverse=direction == 'desc') + sorted(unknown, key=lambda r:r['contract_id'])
    total = len(selected)
    page = min(page, max(1, (total + size - 1) // size))
    with db.connect() as conn:
        fresh = _freshness(conn)
    return {'ok': True, 'profile': profile, 'employee': employee, 'page': page, 'size': size,
            'total': total, 'portfolio_total': len(rows), 'partners': partners,
            'rows': selected[(page - 1) * size:page * size], 'asof': fresh,
            'money': office_work.portfolio_totals(rows), 'today': workday.today().isoformat()}


def dashboard(employee, profile, month, *, page=1, size=100, detail='payments'):
    try:
        start = date.fromisoformat(month + '-01')
    except (ValueError, TypeError):
        raise ValueError('Выберите месяц в формате ГГГГ-ММ') from None
    if not re.fullmatch(r'\d{4}-\d{2}', month or '') or start > workday.today() or page < 1 or not 1 <= size <= 200 or detail not in ('payments', 'operations', 'promises'):
        raise ValueError('Неверный месяц или страница')
    import calendar
    end = min(date(start.year, start.month, calendar.monthrange(start.year, start.month)[1]), workday.today())
    rows = _rows(employee, profile)
    visible = {r['contract_id']: r for r in rows}
    report = office_work.report(employee, profile, start.isoformat(), end.isoformat())
    if profile == 'soft':
        # One person may have explicit canonical/Soft labels. Preserve both
        # historical authors without changing stored facts or current scope.
        import staff_roster, digest_data, soft_reporting
        authors=staff_roster.product_author_names(employee,'soft')
        calls={call['id']:call for author in authors for call in db.soft_calls_for_employee(author,start.isoformat(),end.isoformat())}
        rng={'from':start.isoformat(),'to':end.isoformat(),'days':(end-start).days+1,'kind':'custom','title':month}
        own_stats=digest_data._soft_employee_stats(employee,rng,calls=list(calls.values()),details=True)
        report['stats']={key:own_stats[key] for key in ('calls','persons','rpc','ptp','kp','rpc_rate','ptp_rate','kp_rate')}
        report['events']=[]
        for case in own_stats.get('cases',[]):
            ids=soft_reporting.contract_ids(calls[case['id']])
            rep=next((cid for cid in ids if cid in visible),'')
            report['events'].append({'id':case['id'],'rep':rep,'contracts':ids,
                                    'day':case['date'],'title':case['outcome'],'source':'Звонок Софта'})
    with db.connect() as conn:
        payments = {}
        for offset in range(0, len(rows), 400):
            ids = list(visible)[offset:offset+400]
            for row in conn.execute('SELECT * FROM registry_monthly_totals WHERE month=? AND contract_id IN (' + ','.join('?' for _ in ids) + ')', (month, *ids)):
                payments[row['contract_id']] = dict(row)
        months = [r[0] for r in conn.execute('SELECT DISTINCT month FROM registry_monthly_totals ORDER BY month DESC')]
        fresh = _freshness(conn)
    totals = defaultdict(Decimal)
    details = []
    missing = 0
    for cid, row in visible.items():
        payment = payments.get(cid)
        value = amount(payment.get('amount')) if payment else None
        if value is None:
            missing += 1
            continue
        unit = payment['currency']
        totals[unit] += value
        if value != 0:
            details.append({'rep': cid, 'contract_id': cid, 'client': row['client'], 'partner': row['partner'],
                            'amount': float(value), 'unit': unit, 'month': month, 'observed_at': payment['observed_at']})
    details.sort(key=lambda r:(r['client'] or '',r['contract_id']))
    operations=[]
    for event in report.get('events', []):
        ids=[cid for cid in event.get('contracts',[]) if cid in visible]
        if event.get('rep') in visible and event['rep'] not in ids:ids.append(event['rep'])
        operations.append({'id': event.get('id') or event.get('op_id'), 'rep': ids[0] if ids else '',
                           'contracts':ids,'client':visible[ids[0]]['client'] if ids else 'Договор вне текущего портфеля',
                           'day':event.get('day'), 'title':event.get('title'), 'source':event.get('source')})
    promises = [p for p in report.get('promises',[]) if p.get('date',month+'-01')[:7] == month or p.get('due','')[:7] == month]
    for p in promises:
        p['contracts']=[cid for cid in p.get('contracts',[p.get('contract_id')]) if cid in visible]
        p['rep']=next(iter(p['contracts']), '')
    stats = {k:v for k,v in report.get('stats',{}).items() if isinstance(v,(int,float))}
    processed = {cid for e in operations for cid in e['contracts']}
    visits=report.get('visits',[])
    for v in visits:
        if v.get('rep') in visible:processed.add(v['rep'])
    stats.update(processed_contracts=len(processed), portfolio_contracts=len(rows),
                 portfolio_persons=report['portfolio_persons'], paying_contracts=sum(r['amount']>0 for r in details),
                 promises_active=len(promises), promises_kept=sum(p.get('state')=='kept' for p in promises))
    stats['promises_states']=dict(Counter(p.get('state','incomplete') for p in promises))
    chosen={'payments':details,'operations':operations,'promises':promises}[detail]
    total=len(chosen);page=min(page,max(1,(total+size-1)//size))
    return {'ok':True,'employee':employee,'profile':profile,'month':month,'from':start.isoformat(),'to':end.isoformat(),
            'today':workday.today().isoformat(),'asof':fresh,'months':months,'stats':stats,'portfolio_money':report['money'],
            'payments':{'totals':[{'unit':u,'sum':float(v)} for u,v in sorted(totals.items())],
                        'known_contracts':len(rows)-missing,'missing_contracts':missing,'rows':len(details),
                        'basis':'registry_month_total','attribution':'current_portfolio'},
            'detail':detail,'page':page,'size':size,'total':total,'rows':chosen[(page-1)*size:page*size]}
