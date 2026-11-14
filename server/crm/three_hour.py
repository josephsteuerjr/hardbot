"""The existing 31-column calls/SMS summary, from the common portfolio."""
from collections import defaultdict
from datetime import datetime,time,timedelta
from decimal import Decimal
import json
from zoneinfo import ZoneInfo
from . import access,storage
from .contract import instant,now,CRMError

GROUPS=[('Встречи',1),('Звонок',1),('Обещает оплатить',2),('В работе',2),('Передана инф 3 лицу',2),('Оплачен',2)]


def metrics(records,day,sms):
    from employee_workspace import amount
    from db import _as_date
    result=[0,0,0,Decimal(0),0,Decimal(0),0,Decimal(0),0,Decimal(0)]
    names=set()
    for row in records:
        def on_day(value):
            parsed=_as_date(value)
            if hasattr(parsed,'date'):parsed=parsed.date()
            return parsed==day
        if on_day(row.get('meeting_date')):result[0]+=1
        status=str(row.get('soft_call_status') or '')
        if ('sms' in status.casefold())!=sms or not on_day(row.get('soft_comm_date')):continue
        name=str(row.get('client') or '').strip()
        if name:names.add(name.casefold())
        status=status.casefold().removesuffix(' sms').strip()
        debt=amount(row.get('debt_today'))
        if status in {'обещает оплатить','рассрочка'}:result[2]+=1
        if status=='обещает оплатить':
            value=amount(row.get('agreed_payment'))
            if value is not None:result[3]+=value
        for label,index in [('в работе',4),('передана инф 3 лицу',6),('оплачен',8)]:
            if status==label:
                result[index]+=1
                if debt is not None:result[index+1]+=debt
    result[1]=len(names)
    return [format(value,'f') if isinstance(value,Decimal) else value for value in result]


def report(principal):
    access.require(principal,'analytics')
    at=now();zone=ZoneInfo(principal.timezone);today=instant(at).astimezone(zone).date()
    yesterday=today-timedelta(days=1)
    boundary=datetime.combine(today,time.min,zone)
    from .assignments import snapshot
    with snapshot() as (conn,_watermark):
        scope=principal.employee_scope
        if principal.owner:scope={row[0] for row in conn.execute('SELECT employee_uuid FROM crm_employees WHERE active=1')}
        employees={key:access.employee(conn,principal,key) for key in scope}
        people={};assigned=defaultdict(lambda:defaultdict(set))
        for row in conn.execute("SELECT * FROM crm_assignments WHERE capability='call'"):
            if row['employee_uuid'] not in scope:continue
            for day in (yesterday,today):
                point=instant(at) if day==today else boundary-timedelta(microseconds=1)
                if instant(row['starts_at'])<=point and (not row['ends_at'] or instant(row['ends_at'])>point):
                    assigned[day][row['employee_uuid']].add(row['contract_uuid']);people[row['employee_uuid']]=employees[row['employee_uuid']]
        # Current authority is the effective view, including explicit removals.
        # Keep closed legacy assignments only for yesterday's historical boundary.
        assigned[today].clear()
        for row in conn.execute("SELECT * FROM crm_effective_assignments WHERE capability='call'"):
            if row['employee_uuid'] in scope and instant(row['starts_at'])<=instant(at) and (not row['ends_at'] or instant(row['ends_at'])>instant(at)):
                assigned[today][row['employee_uuid']].add(row['contract_uuid'])
                people[row['employee_uuid']]=employees[row['employee_uuid']]
        ids=set().union(*(ids for days in assigned.values() for ids in days.values())) if assigned else set()
        current={};previous={};unknown=set();currencies=set()
        for offset in range(0,len(ids),400):
            chunk=sorted(ids)[offset:offset+400]
            for row in conn.execute('SELECT * FROM crm_contracts WHERE contract_uuid IN ('+','.join('?' for _ in chunk)+')',chunk):
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
        for row in conn.execute("SELECT e.contracts_json,e.payload_json,e.occurred_at,e.time_quality FROM crm_events e WHERE e.projection_status='applied' ORDER BY e.sequence"):
            if row['time_quality']=='legacy_unknown' or instant(row['occurred_at'])>=boundary:continue
            payload=json.loads(row['payload_json']);values=payload.get('values',{})
            for key in json.loads(row['contracts_json']):
                if key in previous:previous[key].update(values)
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
                        debt=values[cid].get('debt_today'),promise=values[cid].get('agreed_payment'))
                        for cid in sorted(keys) if cid in values and ('sms' in str(values[cid].get('soft_call_status') or '').casefold())==sms]
                rows.append(dict(employee_uuid=key,employee=person['name'],yesterday=before,current=after,
                                 current_records=evidence(new_ids,current),yesterday_records=evidence(old_ids-unknown,previous)))
            sheets.append(dict(name=title,sms=sms,rows=rows))
    return dict(schema_version=1,as_of=at,today=today.isoformat(),yesterday=yesterday.isoformat(),timezone=principal.timezone,
        currency=next(iter(currencies),None),groups=GROUPS,sheets=sheets,yesterday_incomplete_contracts=len(unknown),
        basis='current_portfolio_states',layout='existing_three_hour_31_columns')
