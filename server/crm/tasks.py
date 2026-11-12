"""Client tasks and explicit work authority delegated by a manager."""
import json
from .contract import CRMError, now

WORK_ACTIONS = {
    'call': {'call', 'note', 'contact', 'promise', 'reported_payment', 'document', 'task'},
    'visit': {'visit', 'note', 'contact', 'promise', 'reported_payment', 'document', 'task'},
    'delivery': {'delivery', 'task'},
}


def grants(conn, principal):
    result = {}
    if not principal.employee_scope:
        return result
    for row in conn.execute(
        "SELECT x.contract_uuid,x.data_json,e.role,e.employee_uuid FROM crm_entities x "
        "JOIN crm_events e ON e.event_id=x.event_id WHERE x.kind='task' AND x.status='open'"):
        data = json.loads(row['data_json'])
        if (row['role'] not in {'manager', 'owner'} or not data.get('assignment_grant')
                or data.get('assignee_uuid') not in principal.employee_scope):
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
    return [dict(employee_uuid=row['employee_uuid'], name=row['name'], role=row['role'],
                 work_kinds=sorted(set(json.loads(row['capabilities_json'])) & set(WORK_ACTIONS)))
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
