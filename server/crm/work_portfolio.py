"""One personal work queue; a contract may have several independent work kinds."""
from collections import Counter,defaultdict
from dataclasses import replace
from decimal import Decimal
import json
from . import access,storage,tasks
from .contract import CRMError,currency,money,now
from .roles import ROLE_CAPABILITIES

WORK={'call','visit','delivery'}


def read(principal, search='', work='all', employee=None, offset=0, limit=100):
    if work not in {'all','call','visit','both','delivery'}:
        raise CRMError('Неизвестный фильтр работы.')
    if not isinstance(search,str) or len(search)>200:
        raise CRMError('Поиск: не более 200 символов.')
    try:offset=int(offset);limit=int(limit)
    except (TypeError,ValueError):raise CRMError('Некорректная страница портфеля.')
    if offset<0 or offset>100000 or not 1<=limit<=200:
        raise CRMError('Некорректная страница портфеля.')
    employee=employee or principal.employee_uuid;at=now()
    with storage.connect(readonly=True) as conn:
        person=access.employee(conn,principal,employee)
        if not person['active']:raise CRMError('Доступ сотрудника приостановлен.','forbidden',403)
        member=conn.execute('SELECT * FROM crm_memberships WHERE employee_uuid=? AND starts_at<=? '
            'AND (ends_at IS NULL OR ends_at>?) ORDER BY starts_at DESC',(employee,at,at)).fetchone()
        if not member:raise CRMError('Нет действующего доступа сотрудника.','identity_unmapped',403)
        capabilities=set(json.loads(member['capabilities_json'])) & set(ROLE_CAPABILITIES.get(member['role'],()))
        if employee==principal.employee_uuid and not principal.owner:capabilities &= principal.capabilities
        kinds=defaultdict(set)
        for row in conn.execute('SELECT contract_uuid,capability FROM crm_effective_assignments WHERE employee_uuid=? '
            'AND starts_at<=? AND (ends_at IS NULL OR ends_at>?)',(employee,at,at)):
            if row['capability'] in WORK & capabilities:kinds[row['contract_uuid']].add(row['capability'])
        target=replace(principal,employee_uuid=employee,employee_scope=frozenset({employee}),all_contracts=False)
        for cid,granted in tasks.grants(conn,target).items():
            kinds[cid].update(granted & WORK & capabilities)
        ids=sorted(cid for cid,work_kinds in kinds.items() if work_kinds)
        rows=[]
        for start in range(0,len(ids),400):
            batch=ids[start:start+400]
            for source in conn.execute('SELECT c.contract_uuid,c.person_uuid,c.external_id,c.creditor_id,c.currency,c.fields_json,p.name AS client_name '
                'FROM crm_contracts c JOIN crm_people p USING(person_uuid) WHERE c.contract_uuid IN ('+','.join('?' for _ in batch)+')',batch):
                row=dict(source);row['fields']=json.loads(row.pop('fields_json'))
                row['work_kinds']=sorted(kinds[row['contract_uuid']]);rows.append(row)
    counts=Counter(all=len(rows),call=0,visit=0,both=0,delivery=0,only_call=0,only_visit=0)
    selected=[]
    for row in rows:
        assigned=set(row['work_kinds']);both={'call','visit'}<=assigned
        for kind in assigned:counts[kind]+=1
        counts['both']+=both;counts['only_call']+=assigned=={'call'};counts['only_visit']+=assigned=={'visit'}
        if work=='both' and not both or work not in {'all','both'} and work not in assigned:continue
        values=row['fields']
        if search.casefold() not in ' '.join(str(v or '') for v in (row['client_name'],row['external_id'],row['creditor_id'],values.get('phone'),values.get('address'))).casefold():continue
        selected.append(row)
    selected.sort(key=lambda row:(row['client_name'].casefold(),row['external_id'],row['creditor_id'],row['contract_uuid']))
    sums=defaultdict(Decimal);unknown=0
    for row in selected:
        values=row['fields'];value=values.get('debt_today')
        if value in (None,''):value=values.get('debt_total')
        try:sums[currency(row['currency'])]+=Decimal(money(value))
        except CRMError:unknown+=1
    return dict(employee_uuid=employee,employee=person['name'],work=work,search=search,as_of=at,counts=dict(counts),
        total_contracts=len(selected),total_people=len({row['person_uuid'] for row in selected}),offset=offset,limit=limit,
        has_more=offset+limit<len(selected),contracts=selected[offset:offset+limit],
        currencies={code:format(value,'.2f') for code,value in sorted(sums.items())},unknown_amounts=unknown)
