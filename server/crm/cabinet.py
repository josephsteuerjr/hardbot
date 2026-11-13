"""Monthly employee work and source-bound payments in the common portfolio."""
from collections import Counter,defaultdict
from dataclasses import replace
from datetime import date,datetime,time,timedelta
from decimal import Decimal
import json
import re
from zoneinfo import ZoneInfo
from . import access,storage
from .contract import CRMError,fields as field_definitions,instant,now


def operation_text(conn, kind, payload, titles):
    text=payload.get('text') or payload.get('title') or payload.get('delivery_comment')
    if text:return text
    if kind=='task_complete':
        row=conn.execute('SELECT data_json FROM crm_entities WHERE entity_id=?',(payload.get('entity_id'),)).fetchone()
        return json.loads(row['data_json']).get('title','Задача выполнена') if row else 'Задача выполнена'
    parts=[f'{titles.get(key,key)}: {value}' for key,value in payload.get('values',{}).items() if value not in (None,'')]
    parts.extend(f'{titles.get(key,key)}: очищено' for key in payload.get('clear',[]))
    if payload.get('amount') is not None:parts.append(f"Сумма: {payload['amount']} {payload.get('currency') or ''}".strip())
    if payload.get('due_at'):parts.append('Срок: '+payload['due_at'])
    return '\n'.join(parts)


def dashboard(principal,month,employee=None):
    at=now();zone=ZoneInfo(principal.timezone)
    try:
        if not isinstance(month,str) or not re.fullmatch(r'\d{4}-\d{2}',month):raise ValueError()
        start=date.fromisoformat(month+'-01')
        if start>instant(at).astimezone(zone).date():raise ValueError()
    except (ValueError,TypeError):raise CRMError('Выберите прошедший или текущий месяц.')
    next_month=date(start.year+(start.month==12),start.month%12+1,1)
    first=datetime.combine(start,time.min,zone);last=datetime.combine(next_month,time.min,zone)
    employee=employee or principal.employee_uuid
    from employee_workspace import month_columns,amount
    with storage.connect(readonly=True) as conn:
        person=access.employee(conn,principal,employee)
        # Payments retain the existing attribution to the current permanent portfolio.
        # A temporary client task does not silently move monthly payments.
        view=replace(principal,employee_scope=frozenset({employee}),all_contracts=False,role='employee')
        ids=access.scoped_contracts(conn,view,include_tasks=False)
        rows=[]
        for offset in range(0,len(ids),400):
            chunk=sorted(ids)[offset:offset+400]
            rows.extend(dict(row) for row in conn.execute('SELECT c.*,p.name,r.record_json,s.imported_at FROM crm_contracts c JOIN crm_people p USING(person_uuid) '
                'LEFT JOIN crm_registry_rows r USING(contract_uuid) LEFT JOIN crm_registry_sources s ON s.source_id=r.source_id '
                'WHERE c.contract_uuid IN ('+','.join('?' for _ in chunk)+')',chunk))
        payments=[];totals=defaultdict(Decimal);missing=0;months=set();sources=set()
        observations={}
        for offset in range(0,len(ids),400):
            chunk=sorted(ids)[offset:offset+400]
            for observed in conn.execute('SELECT * FROM crm_monthly_totals WHERE contract_uuid IN ('+','.join('?' for _ in chunk)+')',chunk):
                months.add(observed['month'])
                if observed['month']==month:observations[observed['contract_uuid']]=dict(observed)
        for row in rows:
            raw=json.loads(row['record_json']) if row['record_json'] else {}
            extra=raw.get('extra_columns',[])
            mapping=month_columns([item.get('title','') for item in extra])
            months.update(item[0] for item in mapping.values())
            candidates=[extra[index].get('value') for index,(period,_) in mapping.items() if period==month]
            value=amount(candidates[0] if candidates[0] not in (None,'') else 0) if len(candidates)==1 else None
            observation=observations.get(row['contract_uuid'])
            if observation:
                value=amount(observation['amount']);row['currency']=observation['currency'];row['imported_at']=observation['observed_at']
            if value is None:missing+=1;continue
            fields=json.loads(row['fields_json']);creditor=fields.get('partner') or row['creditor_id']
            unit=row['currency'] or 'unconfirmed:'+row['creditor_id']
            totals[unit]+=value
            if row['imported_at']:sources.add(row['imported_at'])
            if value:
                payments.append(dict(contract_uuid=row['contract_uuid'],external_id=row['external_id'],person_uuid=row['person_uuid'],client=row['name'],
                    creditor=creditor,amount=format(value,'f'),currency=row['currency'],unit=unit,observed_at=row['imported_at']))
        operations=[];unknown=0;counts=Counter();processed=set();titles={row['key']:row['title'] for row in field_definitions()}
        for event in conn.execute('SELECT * FROM crm_events WHERE employee_uuid=? ORDER BY sequence DESC',(employee,)):
            if event['time_quality']=='legacy_unknown':unknown+=1;continue
            payload=json.loads(event['payload_json'])
            workday=payload.get('legacy_workday') if event['time_quality']=='legacy_day' else None
            if workday:
                if not start<=date.fromisoformat(workday)<next_month:continue
            elif not first<=instant(event['occurred_at'])<last:continue
            if event['kind'] in {'correction','manual_edit','payment_confirmed','source_sync','registry_edit'}:continue
            contracts=json.loads(event['contracts_json']);counts[event['kind']]+=1;processed.update(contracts)
            operations.append(dict(event_id=event['event_id'],kind=event['kind'],occurred_at=None if workday else event['occurred_at'],workday=workday,
                contract_ids=contracts,text=operation_text(conn,event['kind'],payload,titles),origin=event['origin']))
        reliable=conn.execute("SELECT value FROM crm_meta WHERE key='reliable_collection_since'").fetchone()
    return dict(month=month,employee_uuid=employee,employee=person['name'],timezone=principal.timezone,as_of=at,
        stats=dict(counts,processed_contracts=len(processed),portfolio_contracts=len(ids),portfolio_persons=len({row['person_uuid'] for row in rows})),
        payments=dict(totals=[dict(unit=unit,currency=None if unit.startswith('unconfirmed:') else unit,amount=format(total,'f')) for unit,total in sorted(totals.items())],
            known_contracts=len(rows)-missing,missing_contracts=missing,rows=payments,attribution='current_permanent_portfolio',basis='registry_month_total',source_as_of=sorted(sources)),
        months=sorted(months,reverse=True),operations=operations[:500],operations_truncated=len(operations)>500,
        history_without_time=unknown,reliable_collection_since=reliable[0] if reliable else None)
