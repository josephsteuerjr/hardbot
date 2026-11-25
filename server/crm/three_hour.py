"""The existing 31-column calls/SMS summary, from the common portfolio."""
from collections import defaultdict
from datetime import datetime,time,timedelta
from decimal import Decimal
import json
import os
from zoneinfo import ZoneInfo
from . import access,storage
from .contract import instant,now,CRMError

GROUPS=[('Встречи',1),('Звонок',1),('Обещает оплатить',2),('В работе',2),('Передана инф 3 лицу',2),('Оплачен',2)]


def promise_amount(row):
    from employee_workspace import amount
    value=amount(row.get('forecast_sum'))
    return amount(row.get('agreed_payment')) if value is None else value


def metrics(records,day,sms):
    from employee_workspace import amount
    from db import _as_date
    result=[0,0,0,Decimal(0),0,Decimal(0),0,Decimal(0),0,Decimal(0)]
    names=set()
    for row in records:
        def on_day(value):
            if not value:return False
            parsed=_as_date(value)
            if hasattr(parsed,'date'):parsed=parsed.date()
            return parsed==day
        if on_day(row.get('soft_next_call_date', row.get('meeting_date'))):result[0]+=1
        status=str(row.get('soft_call_status') or '')
        if ('sms' in status.casefold())!=sms or not on_day(row.get('soft_comm_date')):continue
        name=str(row.get('client') or '').strip()
        if name:names.add(name.casefold())
        status=status.casefold().removesuffix(' sms').strip()
        debt=amount(row.get('debt_today'))
        if status in {'обещает оплатить','рассрочка'}:result[2]+=1
        if status=='обещает оплатить':
            value=promise_amount(row)
            if value is not None:result[3]+=value
        for label,index in [('в работе',4),('передана инф 3 лицу',6),('оплачен',8)]:
            if status==label:
                result[index]+=1
                if debt is not None:result[index+1]+=debt
    result[1]=len(names)
    return [format(value,'f') if isinstance(value,Decimal) else value for value in result]


def country_scopes(conn, principal):
    from . import team_territories
    from country_scope import COUNTRIES

    # Standalone demo databases have no primary roster.
    if not os.environ.get('HARD_CRM_PRIMARY_DB'):
        return {}
    roster = [dict(row) for row in conn.execute(
        'SELECT employee_uuid,staff_id,name FROM crm_employees WHERE active=1'
    ) if principal.owner or row['employee_uuid'] in principal.employee_scope]
    geography = team_territories.staff(principal, roster, shared=True)
    codes = {country['code']: set() for country in COUNTRIES}
    for identifier, person in geography.items():
        if person['country'] in codes:
            codes[person['country']].add(identifier)
    return {code: frozenset(members) for code, members in codes.items() if members}


def report(principal, *, country='', members=None):
    from . import manager_reports
    manager_reports.require(principal)
    at=now();zone=ZoneInfo(principal.timezone);today=instant(at).astimezone(zone).date()
    yesterday=today-timedelta(days=1)
    boundary=datetime.combine(today,time.min,zone)
    current_point=instant(at)
    prior_point=boundary-timedelta(microseconds=1)
    from .assignments import snapshot
    with snapshot() as (conn,_watermark):
        scope=principal.employee_scope
        if principal.owner:scope={row[0] for row in conn.execute('SELECT employee_uuid FROM crm_employees WHERE active=1')}
        if members is not None:scope=scope & members
        employees={key:access.employee(conn,principal,key) for key in scope}
        people={};assigned=defaultdict(lambda:defaultdict(set))
        scoped=sorted(scope)
        for offset in range(0,len(scoped),400):
            chunk=scoped[offset:offset+400]
            placeholders=','.join('?' for _ in chunk)
            # Current assignments include explicit removals in the effective view.
            # Yesterday still uses the historical assignment intervals.
            for day,point,table in ((yesterday,prior_point,'crm_assignments'),
                                    (today,current_point,'crm_effective_assignments')):
                for row in conn.execute(f"SELECT employee_uuid,contract_uuid,starts_at,ends_at FROM {table} "
                                        f"WHERE capability='call' AND employee_uuid IN ({placeholders})",chunk):
                    key=row['employee_uuid']
                    if instant(row['starts_at'])<=point and (not row['ends_at'] or instant(row['ends_at'])>point):
                        assigned[day][key].add(row['contract_uuid'])
                        people[key]=employees[key]
                    elif day==yesterday and instant(row['starts_at'])<=current_point and (
                            not row['ends_at'] or instant(row['ends_at'])>current_point):
                        # Preserve the employee row even if the current view masks
                        # their last legacy assignment with an explicit replacement.
                        people[key]=employees[key]
        ids=sorted({cid for days in assigned.values() for contracts in days.values() for cid in contracts})
        current={};previous={};unknown=set();currencies=set()
        for offset in range(0,len(ids),400):
            chunk=ids[offset:offset+400]
            for row in conn.execute('SELECT contract_uuid,fields_json,baseline_json,provenance_json,created_at,currency '
                                    'FROM crm_contracts WHERE contract_uuid IN ('+','.join('?' for _ in chunk)+')',chunk):
                key=row['contract_uuid'];current[key]=json.loads(row['fields_json'])
                previous[key]=json.loads(row['baseline_json'])
                baseline=json.loads(row['provenance_json']).get('source_baseline',{}).get('captured_at',row['created_at'])
                if instant(baseline)>=boundary:unknown.add(key)
                # Match the existing display policy; explicit canonical currencies win.
                import debt
                currency=row['currency'] or ('RUB' if debt.unit([current[key].get('partner')])=='₽' else None)
                if currency:currencies.add(currency)
                else:unknown.add(key)
        if len(currencies)>1:raise CRMError('В портфеле разные валюты. Общая сводка требует раздельных сумм.','mixed_currencies',409)
        boundary_utc=boundary.astimezone(ZoneInfo('UTC')).isoformat()
        time_clause='e.occurred_at::timestamptz<?' if storage.is_postgres(conn) else 'datetime(e.occurred_at)<datetime(?)'
        for offset in range(0,len(ids),100000):
            chunk=ids[offset:offset+100000]
            membership='=ANY(?::text[])' if storage.is_postgres(conn) else 'IN (SELECT value FROM json_each(?))'
            for row in conn.execute(
                    'SELECT e.event_id,e.payload_json,e.occurred_at,ec.contract_uuid FROM crm_events e '
                    'JOIN crm_event_contracts ec ON ec.event_id=e.event_id '
                    "WHERE e.projection_status='applied' AND e.time_quality<>'legacy_unknown' AND "
                    +time_clause+' AND ec.contract_uuid '+membership+' ORDER BY e.sequence',
                    (boundary_utc,chunk) if storage.is_postgres(conn) else (boundary_utc,json.dumps(chunk))):
                payload=json.loads(row['payload_json']);values=payload.get('values',{})
                previous[row['contract_uuid']].update(values)
        sheets=[]
        for sms,title in [(False,'Статистика звонки'),(True,'Статистика sms')]:
            rows=[]
            for key,person in sorted(people.items(),key=lambda item:(item[1]['name'],item[0])):
                old_ids=assigned[yesterday][key];new_ids=assigned[today][key]
                before=metrics([previous[cid] for cid in old_ids if cid in previous],yesterday,sms)
                after=metrics([current[cid] for cid in new_ids if cid in current],today,sms)
                if old_ids & unknown:before=[None]*10
                def evidence(keys,values):
                    return [dict(contract_uuid=cid,client=values[cid].get('client'),contract_id=values[cid].get('contract_id'),
                        status=values[cid].get('soft_call_status'),date=values[cid].get('soft_comm_date'),
                        debt=values[cid].get('debt_today'),promise=format(value,'f') if (value:=promise_amount(values[cid])) is not None else None)
                        for cid in sorted(keys) if cid in values and ('sms' in str(values[cid].get('soft_call_status') or '').casefold())==sms]
                rows.append(dict(employee_uuid=key,employee=person['name'],yesterday=before,current=after,
                                 current_records=evidence(new_ids,current),yesterday_records=evidence(old_ids-unknown,previous)))
            sheets.append(dict(name=title,sms=sms,rows=rows))
    from country_scope import info
    return dict(schema_version=1,as_of=at,today=today.isoformat(),yesterday=yesterday.isoformat(),timezone=principal.timezone,
        **({'country': country, 'country_name': info(country)['name']} if country else {}),
        currency=next(iter(currencies),None),groups=GROUPS,sheets=sheets,yesterday_incomplete_contracts=len(unknown),
        basis='current_portfolio_states',layout='existing_three_hour_31_columns')
