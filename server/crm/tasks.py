"""Client tasks and explicit work authority delegated by a manager."""
import json
from .contract import CRMError, now, instant

WORK_ACTIONS = {
    'call': {'call', 'note', 'contact', 'promise', 'reported_payment', 'document', 'task'},
    'visit': {'visit', 'note', 'contact', 'promise', 'reported_payment', 'document', 'task'},
    'delivery': {'delivery', 'task'},
}


def grants(conn, principal):
    result = {}
    if not principal.employee_scope:
        return result
    assignees = principal.employee_scope
    if principal.access_model == 2 and principal.role == 'employee':
        assignees = frozenset({principal.employee_uuid}) & assignees
    if principal.role == 'courier' and 'delivery' in principal.capabilities:
        from . import logistics
        result.update((cid, {'delivery'}) for cid in logistics.for_employee(conn, principal.employee_uuid, 'delivery'))
    for row in conn.execute(
        "SELECT x.entity_id,x.contract_uuid,x.data_json,e.role,e.employee_uuid FROM crm_entities x "
        "JOIN crm_events e ON e.event_id=x.event_id WHERE x.kind='task' AND x.status='open' "
        "AND NOT EXISTS(SELECT 1 FROM crm_contact_withdrawals w WHERE w.contract_uuid=x.contract_uuid)"):
        data = json.loads(row['data_json'])
        if (row['role'] not in {'manager', 'owner'} or not data.get('assignment_grant')
                or data.get('assignee_uuid') not in assignees):
            continue
        if conn.execute('SELECT 1 FROM crm_task_grant_revocations WHERE entity_id=? AND capability=?',
                        (row['entity_id'],data.get('work_kind'))).fetchone():
            continue
        result.setdefault(row['contract_uuid'], set()).update(WORK_ACTIONS.get(data.get('work_kind'), ()))
    return result


def prepare(conn, principal, payload):
    from . import access
    if principal.role == 'courier':
        raise CRMError('Задачи курьеру назначает руководитель.', 'forbidden', 403)
    assignee = payload.get('assignee_uuid') or principal.employee_uuid
    if not isinstance(assignee, str) or len(assignee) > 100:
        raise CRMError('Выберите исполнителя.')
    access.employee(conn, principal, assignee)
    at = now()
    member = conn.execute('SELECT role,capabilities_json FROM crm_memberships WHERE employee_uuid=? '
        'AND starts_at<=? AND (ends_at IS NULL OR ends_at>?) ORDER BY starts_at DESC', (assignee, at, at)).fetchone()
    if not member:
        raise CRMError('У исполнителя нет действующего доступа.', 'identity_unmapped', 403)
    work = payload.get('work_kind') or ('delivery' if member['role'] == 'courier' else 'call')
    if not isinstance(work, str) or work not in WORK_ACTIONS:
        raise CRMError('Выберите вид работы по клиенту.')
    # Old self-tasks remain valid. Only an explicit task from a manager creates
    # delegated authority; creating a personal reminder never expands access.
    delegated = principal.role in {'manager', 'owner'}
    if delegated and principal.access_model == 2 and work not in principal.capabilities:
        raise CRMError('Полномочие не включает выбранный вид работы.', 'forbidden', 403)
    if delegated and work not in json.loads(member['capabilities_json']):
        raise CRMError('У исполнителя нет права на выбранный вид работы.', 'forbidden', 403)
    if assignee != principal.employee_uuid and not delegated:
        raise CRMError('Назначать задачу другому сотруднику может руководитель.', 'forbidden', 403)
    payload.update(assignee_uuid=assignee, work_kind=work, assignment_grant=delegated)


def assignee(conn, entity):
    data = json.loads(entity['data_json'])
    if data.get('assignee_uuid'):
        return data['assignee_uuid']
    author = conn.execute('SELECT employee_uuid FROM crm_events WHERE event_id=?', (entity['event_id'],)).fetchone()
    return author['employee_uuid'] if author else None


def completion(conn, principal, entity):
    if principal.role not in {'manager', 'owner'} and assignee(conn, entity) != principal.employee_uuid:
        raise CRMError('Задачу завершает её исполнитель.', 'forbidden', 403)


def staff(conn, principal):
    at = now()
    grant_mode = principal.access_model == 2
    actor_links = {}
    for link in conn.execute('SELECT actor_uid,employee_uuid FROM crm_actor_links WHERE active=1'):
        actor_links.setdefault(link['employee_uuid'], []).append(link['actor_uid'])
    return [dict(employee_uuid=row['employee_uuid'], name=row['name'], role=row['role'],
                 actor_uids=sorted(actor_links.get(row['employee_uuid'], [])),
                 work_kinds=sorted((set(json.loads(row['capabilities_json']))
                                    & (principal.capabilities if grant_mode else set(WORK_ACTIONS)))
                                   & set(WORK_ACTIONS)))
        for row in conn.execute('SELECT e.employee_uuid,e.name,m.role,m.capabilities_json FROM crm_employees e '
            'JOIN crm_memberships m USING(employee_uuid) WHERE e.active=1 AND m.starts_at<=? '
            'AND (m.ends_at IS NULL OR m.ends_at>?) ORDER BY e.name', (at, at))
        if principal.owner or row['employee_uuid'] in principal.employee_scope]


def describe(conn, principal, entity, scope):
    assigned = entity['data'].get('assignee_uuid')
    if not assigned:
        source = conn.execute('SELECT employee_uuid FROM crm_events WHERE event_id=?', (entity['event_id'],)).fetchone()
        assigned = source['employee_uuid'] if source else None
    person = conn.execute('SELECT name FROM crm_employees WHERE employee_uuid=?', (assigned,)).fetchone()
    entity['assignee'] = person['name'] if person else None
    entity['can_complete'] = bool(entity['status'] == 'open' and entity['contract_uuid'] in scope
        and (principal.owner or 'task' in principal.capabilities)
        and (principal.role in {'manager','owner'} or assigned == principal.employee_uuid))


def overview(principal, *, status='open', employee=None, work='all', search='', offset=0, limit=100):
    """Personal plan or a supervisor's team, independent of common client read."""
    from collections import Counter
    from zoneinfo import ZoneInfo
    from . import access,storage,store
    if status not in {'open','today','overdue','done','all'} or work not in {'all',*WORK_ACTIONS}:
        raise CRMError('Неизвестный фильтр задач.')
    if not isinstance(search,str) or len(search)>200:raise CRMError('Поиск: не более 200 символов.')
    try:offset=int(offset);limit=int(limit)
    except (TypeError,ValueError):raise CRMError('Некорректная страница задач.')
    if offset<0 or offset>100000 or not 1<=limit<=200:raise CRMError('Некорректная страница задач.')
    at=now();clock=instant(at);zone=ZoneInfo(principal.timezone);today=clock.astimezone(zone).date()
    team=principal.role in {'manager','owner','analyst','observer'}
    target=employee or ('all' if team else principal.employee_uuid)
    with storage.connect(readonly=True) as conn:
        if target!='all':access.employee(conn,principal,target)
        if not team and target!=principal.employee_uuid:
            raise CRMError('Доступен собственный список задач.','forbidden',403)
        allowed=None if principal.owner else set(principal.employee_scope) if team else {principal.employee_uuid}
        if target!='all':allowed={target}
        readable=None if access.reads_all(principal) else store.readable_contracts(conn,principal)
        writer=store.write_principal(principal)
        writable=access.scoped_contracts(conn,writer)
        names={r['employee_uuid']:r['name'] for r in conn.execute('SELECT employee_uuid,name FROM crm_employees')}
        rows=[];counts=Counter(all=0,open=0,today=0,overdue=0,done=0)
        for source in conn.execute("SELECT x.*,e.employee_uuid AS source_employee,c.person_uuid,c.external_id,c.creditor_id,c.fields_json,p.name AS client "
                "FROM crm_entities x JOIN crm_events e ON e.event_id=x.event_id JOIN crm_contracts c USING(contract_uuid) "
                "JOIN crm_people p USING(person_uuid) WHERE x.kind='task' "
                "AND (x.status<>'open' OR NOT EXISTS("
                "SELECT 1 FROM crm_contact_withdrawals w WHERE w.contract_uuid=x.contract_uuid))"):
            row=dict(source);data=json.loads(row['data_json']);assigned=data.get('assignee_uuid') or row['source_employee']
            if allowed is not None and assigned not in allowed:continue
            if readable is not None and row['contract_uuid'] not in readable:continue
            kind=data.get('work_kind') or 'call'
            if work!='all' and work!=kind:continue
            fields=json.loads(row['fields_json']);assignee_name=names.get(assigned,'Исполнитель не указан')
            if search.casefold() not in ' '.join(str(v or '') for v in (data.get('title'),row['client'],row['external_id'],fields.get('partner'),assignee_name)).casefold():continue
            due=None
            try:due=instant(data.get('due_at'))
            except CRMError:pass
            open_task=row['status']=='open'
            overdue=bool(open_task and due and due<clock);due_today=bool(open_task and due and due.astimezone(zone).date()==today)
            counts['all']+=1;counts['open']+=open_task;counts['done']+=row['status']=='done';counts['today']+=due_today;counts['overdue']+=overdue
            if status=='today' and not due_today or status=='overdue' and not overdue:continue
            if status in {'open','done'} and row['status']!=status:continue
            entity=dict(row,data=data);describe(conn,writer,entity,writable)
            rows.append(dict(entity_id=row['entity_id'],revision=row['revision'],contract_uuid=row['contract_uuid'],
                person_uuid=row['person_uuid'],client=row['client'],external_id=row['external_id'],
                creditor=fields.get('partner') or row['creditor_id'],title=data.get('title') or 'Задача',
                assignee_uuid=assigned,assignee=assignee_name,work_kind=kind,status=row['status'],
                due_at=data.get('due_at') if due else None,due_today=due_today,overdue=overdue,
                updated_at=row['updated_at'],can_complete=entity['can_complete']))
    rows.sort(key=lambda r:(r['status']!='open',instant(r['due_at']).timestamp() if r['due_at'] else float('inf'),r['client'].casefold(),r['entity_id']))
    return dict(as_of=at,timezone=principal.timezone,today=today.isoformat(),counts=dict(counts),status=status,
        employee_uuid=target,work=work,search=search,total=len(rows),offset=offset,limit=limit,
        has_more=offset+limit<len(rows),tasks=rows[offset:offset+limit])
