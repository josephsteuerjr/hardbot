"""One personal work queue; a contract may have several independent work kinds."""
from collections import Counter,defaultdict
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
import json
import re
from zoneinfo import ZoneInfo
from . import access, logistics, storage, tasks, withdrawals
from .contract import CRMError,currency,instant,money,now
from .roles import ROLE_CAPABILITIES

WORK={'call','visit','delivery'}
FILTER_FIELDS={'partner':'partner','pool':'pool','overall_result':'overall_result',
               'call_status':'soft_call_status','visit_status':'call_status'}


def read_query(principal, query):
    return read(principal,query.get('search',''),query.get('work','all'),query.get('employee_uuid'),
        query.get('offset',0),query.get('limit',100),
        call_queue=query.get('call_queue','all'),sort=query.get('sort','priority'),
        **{key:query.get(key) for key in (*FILTER_FIELDS,'debt_from','debt_to','debt_currency')})


def call_schedule(values, today):
    from db import _as_date

    value = values.get('soft_next_call_date', values.get('meeting_date'))
    planned = _as_date(value)
    if isinstance(planned, datetime):
        planned = planned.date()
    overdue = max(0, (today-planned).days) if planned else 0
    if overdue:
        reason = 'overdue'
    elif not values.get('soft_call_status'):
        reason = 'no_status'
    elif planned == today:
        reason = 'today'
    elif not planned:
        reason = 'no_date'
    else:
        reason = 'scheduled'
    return dict(date=planned.isoformat() if planned else None, overdue_days=overdue, reason=reason)


def debt_value(values):
    value=values.get('debt_today')
    if value in (None,''):value=values.get('debt_total')
    try:return Decimal(money(value))
    except CRMError:return None


def read(principal, search='', work='all', employee=None, offset=0, limit=100, *,
         partner=None, pool=None, overall_result=None, call_status=None, visit_status=None,
         debt_from=None, debt_to=None, debt_currency=None, call_queue='all', sort='priority'):
    if sort not in {'priority','debt_desc','call_date','client'}:
        raise CRMError('Неизвестный приоритет отработки.')
    if work not in {'all','call','visit','both','delivery','logistics'}:
        raise CRMError('Неизвестный фильтр работы.')
    if call_queue not in {'all','today'} or call_queue=='today' and work!='call':
        raise CRMError('Неизвестный отбор звонков.')
    if not isinstance(search,str) or len(search)>200:
        raise CRMError('Поиск: не более 200 символов.')
    try:offset=int(offset);limit=int(limit)
    except (TypeError,ValueError):raise CRMError('Некорректная страница портфеля.')
    if offset<0 or offset>100000 or not 1<=limit<=200:
        raise CRMError('Некорректная страница портфеля.')
    terms=search.casefold().replace('ё','е').split()
    digits=re.sub(r'\D','',search) if re.fullmatch(r'[+\d\s()-]+',search) else ''
    phone_search=digits if len(digits)>=5 else ''
    choices=dict(partner=partner,pool=pool,overall_result=overall_result,call_status=call_status,visit_status=visit_status)
    if any(value is not None and (not isinstance(value,str) or len(value)>255) for value in choices.values()):
        raise CRMError('Некорректный фильтр портфеля.')
    lower=Decimal(money(debt_from)) if debt_from not in (None,'') else None
    upper=Decimal(money(debt_to)) if debt_to not in (None,'') else None
    if lower is not None and upper is not None and lower>upper:
        raise CRMError('Сумма «от» должна быть меньше или равна сумме «до».')
    unit=currency(debt_currency) if debt_currency else None
    employee=employee or principal.employee_uuid;at=now()
    with storage.connect(readonly=True) as conn:
        person=access.employee(conn,principal,employee)
        if not person['active']:raise CRMError('Доступ сотрудника приостановлен.','forbidden',403)
        member=conn.execute('SELECT * FROM crm_memberships WHERE employee_uuid=? AND starts_at<=? '
            'AND (ends_at IS NULL OR ends_at>?) ORDER BY starts_at DESC',(employee,at,at)).fetchone()
        if not member:raise CRMError('Нет действующего доступа сотрудника.','identity_unmapped',403)
        today=instant(at).astimezone(ZoneInfo(member['timezone'])).date()
        capabilities=set(json.loads(member['capabilities_json'])) & set(ROLE_CAPABILITIES.get(member['role'],()))
        if not principal.owner and (employee==principal.employee_uuid or principal.access_model==2):
            capabilities &= principal.capabilities
        kinds=defaultdict(set)
        for row in conn.execute('SELECT contract_uuid,capability FROM crm_effective_assignments WHERE employee_uuid=? '
            'AND starts_at<=? AND (ends_at IS NULL OR ends_at>?)',(employee,at,at)):
            if row['capability'] in WORK & capabilities:kinds[row['contract_uuid']].add(row['capability'])
        target=replace(principal,employee_uuid=employee,employee_scope=frozenset({employee}),all_contracts=False)
        for cid,granted in tasks.grants(conn,target).items():
            kinds[cid].update(granted & WORK & capabilities)
        courier_calls = logistics.for_employee(conn, employee, 'call') if 'call' in capabilities else {}
        courier_deliveries = logistics.for_employee(conn, employee, 'delivery') if 'delivery' in capabilities else {}
        for cid in courier_calls:
            kinds[cid].add('logistics')
        for cid in courier_deliveries:
            kinds[cid].add('delivery')
        blocked = withdrawals.blocked(conn, kinds)
        ids=sorted(cid for cid,work_kinds in kinds.items() if work_kinds and cid not in blocked)
        rows=[]
        for start in range(0,len(ids),400):
            batch=ids[start:start+400]
            for source in conn.execute('SELECT c.contract_uuid,c.person_uuid,c.external_id,c.legacy_id,c.creditor_id,c.currency,c.fields_json,p.name AS client_name '
                'FROM crm_contracts c JOIN crm_people p USING(person_uuid) WHERE c.contract_uuid IN ('+','.join('?' for _ in batch)+')',batch):
                row=dict(source);row['fields']=json.loads(row.pop('fields_json'))
                row['logistics_call']=courier_calls.get(row['contract_uuid'])
                row['work_kinds']=sorted(kinds[row['contract_uuid']]);rows.append(row)
    counts=Counter(all=len(rows),call=0,visit=0,both=0,delivery=0,only_call=0,only_visit=0)
    options={key:set() for key in (*FILTER_FIELDS,'debt_currency')}
    selected=[]
    for row in rows:
        assigned=set(row['work_kinds']);both={'call','visit'}<=assigned
        for kind in assigned:counts[kind]+=1
        counts['both']+=both;counts['only_call']+=assigned=={'call'};counts['only_visit']+=assigned=={'visit'}
        if work == 'logistics':
            if not row['logistics_call']:
                continue
        elif work=='both' and not both or work not in {'all','both'} and work not in assigned:continue
        values=row['fields']
        partner_name=str(values.get('partner') or '').strip() or row['creditor_id']
        if terms:
            phones=(values.get('phone'),values.get('phone_main'),values.get('soft_extra_phone'),
                    (row['logistics_call'] or {}).get('phone'))
            searchable=' '.join(str(v or '') for v in (row['client_name'],row['external_id'],row['creditor_id'],
                                partner_name,values.get('address'),values.get('address_customer'),*phones))
            searchable=searchable.casefold().replace('ё','е')
            if not all(term in searchable for term in terms) and not (
                    phone_search and any(phone_search in re.sub(r'\D','',str(phone or '')) for phone in phones)):
                continue
        fields={key:str(values.get(field) if values.get(field) is not None else '').strip() for key,field in FILTER_FIELDS.items()}
        fields['partner']=partner_name
        for key,value in fields.items():
            if len(value)<=255:options[key].add(value)
        try:code=currency(row['currency'])
        except CRMError:code=None
        if code:options['debt_currency'].add(code)
        if any(value is not None and value!=fields[key] for key,value in choices.items()) or (unit and unit!=code):
            continue
        if lower is not None or upper is not None:
            debt=debt_value(values)
            if debt is None or (lower is not None and debt<lower) or (upper is not None and debt>upper):
                continue
        if work=='call':
            row['call_schedule']=call_schedule(values,today)
            if call_queue=='today' and row['call_schedule']['reason']=='scheduled':
                continue
        selected.append(row)
    def order(row):
        if sort == 'debt_desc':
            value = debt_value(row['fields'])
            return (value is None, -(value or 0), row['client_name'].casefold(), row['contract_uuid'])
        if sort == 'call_date':
            schedule = row.get('call_schedule') or call_schedule(row['fields'], today)
            return (not schedule['date'], schedule['date'] or '9999-12-31', row['client_name'].casefold(), row['contract_uuid'])
        if sort == 'client':
            return (row['client_name'].casefold(), row['external_id'], row['creditor_id'], row['contract_uuid'])
        schedule=row.get('call_schedule')
        priority=0 if not schedule else {'overdue':0,'no_status':1,'today':2,'no_date':3,'scheduled':4}[schedule['reason']]
        return (priority,-schedule['overdue_days'] if schedule else 0,row['client_name'].casefold(),
                row['external_id'],row['creditor_id'],row['contract_uuid'])
    selected.sort(key=order)
    sums=defaultdict(Decimal);unknown=0
    for row in selected:
        value=debt_value(row['fields'])
        if value is None:
            unknown+=1
            continue
        try:sums[currency(row['currency'])]+=value
        except CRMError:unknown+=1
    if courier_calls or work == 'logistics':
        counts['logistics'] = len(courier_calls)
    return dict(employee_uuid=employee,employee=person['name'],work=work,search=search,as_of=at,counts=dict(counts),
        filter_options={key:sorted(values,key=lambda value:(value.casefold(),value)) for key,values in options.items()},
        total_contracts=len(selected),total_people=len({row['person_uuid'] for row in selected}),offset=offset,limit=limit,
        has_more=offset+limit<len(selected),contracts=selected[offset:offset+limit],
        currencies={code:format(value,'.2f') for code,value in sorted(sums.items())},unknown_amounts=unknown)
