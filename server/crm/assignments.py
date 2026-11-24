"""Permanent call/visit allocation, separate from work facts and temporary tasks.

All commits share the exclusive maintenance barrier with every work writer.
Preview freezes identities and revisions; retries bind to the original actor/body.
The legacy mixed deployment stays disabled until its independent fence is ready.
"""
from collections import defaultdict
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal, InvalidOperation
import json
import uuid

from . import access, storage
from .contract import CRMError, canonical, digest, instant, now, operation_id

WORK = {'call', 'visit'}
MAX_SLOTS = 200
MAX_JOB_SLOTS = 200000


def effective_fields(conn, contract_id, values):
    """Current responsibility is a projection, never an editable source grant."""
    return dict(values, **responsibility_fields(conn, [contract_id]).get(contract_id, {}))


def responsibility_fields(conn, ids):
    result = defaultdict(dict)
    if ids:
        placeholders = ','.join('?' for _ in ids)
        for row in conn.execute('SELECT h.contract_uuid,h.capability,h.employee_uuid,e.name FROM crm_assignment_heads h '
                                'LEFT JOIN crm_employees e USING(employee_uuid) WHERE h.contract_uuid IN ('
                                + placeholders + ')', ids):
            field = 'responsible_soft' if row['capability'] == 'call' else 'responsible'
            result[row['contract_uuid']][field] = row['name'] if row['employee_uuid'] else 'Не назначен'
    return result


def readiness(conn, principal):
    mode = conn.execute("SELECT value FROM crm_meta WHERE key='distribution_mode'").fetchone()
    from . import primary_fence
    enabled = bool(mode and (mode[0] == 'isolated' or mode[0] == 'cutover' and primary_fence.ready(conn)))
    managed = conn.execute('SELECT 1 FROM crm_distribution_managers WHERE employee_uuid=?',
                           (principal.employee_uuid,)).fetchone()
    permitted = principal.role in {'manager', 'owner'} and (principal.owner or 'assign' in principal.capabilities) and bool(managed)
    return dict(enabled=enabled and permitted, environment=mode[0] if enabled else 'mixed',
                reason='' if enabled and permitted else
                'Распределение в рабочем смешанном контуре выключено: защита старых писателей ещё не подтверждена.' if not enabled else
                'Нет полномочия распределения в управляемом портфеле.',
                max_atomic_slots=MAX_SLOTS, max_job_slots=MAX_JOB_SLOTS,all_selection=True, durable_jobs=True)


def authorize(conn, principal, *, write=False):
    if write:
        principal = access.refresh_write(conn, principal)
    state = readiness(conn, principal)
    if not state['enabled']:
        raise CRMError(state['reason'], 'distribution_unavailable', 403, state)
    return principal


def scope_sql(principal, alias='c', work=None):
    clause = ('EXISTS(SELECT 1 FROM crm_managed_contracts mc '
              'JOIN crm_distribution_managers dm ON dm.team_id=mc.team_id AND dm.capability=mc.capability '
              'WHERE dm.employee_uuid=? AND mc.contract_uuid=' + alias + '.contract_uuid')
    args = [principal.employee_uuid]
    if work:
        clause += ' AND mc.capability=?'; args.append(work)
    return clause + ')', args


def allowed_slots(conn, principal, ids):
    if not ids:
        return set()
    placeholders = ','.join('?' for _ in ids)
    return {(r['contract_uuid'], r['capability']) for r in conn.execute(
        'SELECT DISTINCT mc.contract_uuid,mc.capability FROM crm_managed_contracts mc '
        'JOIN crm_distribution_managers dm ON dm.team_id=mc.team_id AND dm.capability=mc.capability '
        'WHERE dm.employee_uuid=? AND mc.contract_uuid IN (' + placeholders + ')',
        (principal.employee_uuid, *ids))}


def states(conn, ids):
    result = {(cid, kind):dict(contract_uuid=cid, capability=kind, employee_uuid=None,
                              revision=0, ambiguous=False) for cid in ids for kind in sorted(WORK)}
    if not ids:
        return result
    placeholders = ','.join('?' for _ in ids)
    at = now()
    raw = defaultdict(set)
    for r in conn.execute('SELECT contract_uuid,capability,employee_uuid FROM crm_assignments '
            'WHERE contract_uuid IN ('+placeholders+') AND capability IN (\'call\',\'visit\') '
            'AND starts_at<=? AND (ends_at IS NULL OR ends_at>?)', (*ids, at, at)):
        raw[r['contract_uuid'], r['capability']].add(r['employee_uuid'])
    for key, people in raw.items():
        result[key].update(employee_uuid=next(iter(people)) if len(people)==1 else None,
                           ambiguous=len(people)>1)
    for r in conn.execute('SELECT * FROM crm_assignment_heads WHERE contract_uuid IN ('+placeholders+')', ids):
        result[r['contract_uuid'],r['capability']].update(employee_uuid=r['employee_uuid'],
            revision=r['revision'], ambiguous=False)
    return result


def _authority_targets(conn, principal):
    row = conn.execute('SELECT employees_json FROM crm_authority_scopes WHERE actor_uid=?',
                       (principal.actor_uid,)).fetchone()
    return None if row is None else frozenset(json.loads(row['employees_json'])) & principal.employee_scope


def targets(conn, principal):
    at = now()
    authorized_staff = _authority_targets(conn, principal)
    teams = {r[0] for r in conn.execute('SELECT DISTINCT team_id FROM crm_distribution_managers WHERE employee_uuid=?',
                                      (principal.employee_uuid,))}
    result = {}
    rows = conn.execute('SELECT e.employee_uuid,e.name,e.staff_id,e.active,m.team_id,m.role,m.capabilities_json '
        'FROM crm_employees e LEFT JOIN crm_memberships m ON m.employee_uuid=e.employee_uuid '
        'AND m.starts_at<=? AND (m.ends_at IS NULL OR m.ends_at>?) ORDER BY e.name,e.employee_uuid', (at,at))
    memberships = defaultdict(list)
    for r in rows:
        if authorized_staff is None or r['employee_uuid'] in authorized_staff:
            memberships[r['employee_uuid']].append(dict(r))
    for eid, members in memberships.items():
        visible = [r for r in members if r['team_id'] in teams]
        if not visible:
            continue
        row = visible[0]
        kinds = sorted(set(json.loads(row['capabilities_json'])) & WORK) if len(members)==1 else []
        reason = ('Сотрудник отключён' if not row['active'] else 'Нет постоянной кадровой личности' if not row['staff_id']
                  else 'Требует сверки членства' if len(members)!=1 else 'Нет допустимых видов работ' if not kinds else '')
        result[eid] = dict(employee_uuid=eid,name=row['name'],team_id=row['team_id'],role=row['role'],actor_uids=[],work_kinds=kinds,
                           eligible=not reason,reason=reason,has_account=False,contracts=0,people=0,slots=0)
    for r in conn.execute('SELECT l.employee_uuid,l.actor_uid FROM crm_actor_links l WHERE l.active=1'):
        if r[0] in result:
            result[r[0]]['has_account'] = True
            result[r[0]]['actor_uids'].append(r['actor_uid'])
    clause,args = scope_sql(principal)
    for r in conn.execute('SELECT a.employee_uuid,COUNT(DISTINCT a.contract_uuid) AS contracts,'
            'COUNT(DISTINCT c.person_uuid) AS people,COUNT(*) AS slots FROM crm_effective_assignments a '
            'JOIN crm_contracts c USING(contract_uuid) WHERE '+clause+
            " AND a.capability IN ('call','visit') AND a.starts_at<=? AND (a.ends_at IS NULL OR a.ends_at>?) GROUP BY a.employee_uuid", (*args,at,at)):
        if r['employee_uuid'] in result:
            result[r['employee_uuid']].update(contracts=r['contracts'],people=r['people'],slots=r['slots'])
    return list(result.values())


def _filters(principal, *, search='', employee_uuid='', work='all', unassigned='0'):
    if not isinstance(search,str) or len(search)>200 or work not in {'all',*WORK} or str(unassigned) not in {'0','1'}:
        raise CRMError('Некорректный отбор портфеля.')
    if not isinstance(employee_uuid,str) or len(employee_uuid)>100:
        raise CRMError('Некорректный сотрудник.')
    clause,args = scope_sql(principal,work=None if work=='all' else work)
    clauses=[clause]
    if search:
        clauses.append("lower(p.name || ' ' || c.external_id || ' ' || c.creditor_id || ' ' || c.fields_json) LIKE lower(?) ESCAPE '!' ")
        args.append('%'+search.replace('!','!!').replace('%','!%').replace('_','!_')+'%')
    if employee_uuid:
        clauses.append('EXISTS(SELECT 1 FROM crm_effective_assignments a WHERE a.contract_uuid=c.contract_uuid '
                       'AND a.employee_uuid=? AND a.ends_at IS NULL'+(' AND a.capability=?' if work!='all' else '')+')')
        args.append(employee_uuid)
        if work!='all':args.append(work)
    if str(unassigned)=='1':
        clauses.append('EXISTS(SELECT 1 FROM crm_managed_contracts free WHERE free.contract_uuid=c.contract_uuid '
            'AND EXISTS(SELECT 1 FROM crm_distribution_managers dm WHERE dm.employee_uuid=? AND dm.team_id=free.team_id AND dm.capability=free.capability) '
            + ('AND free.capability=? ' if work!='all' else '') +
            'AND NOT EXISTS(SELECT 1 FROM crm_effective_assignments a WHERE a.contract_uuid=c.contract_uuid '
            'AND a.capability=free.capability AND a.ends_at IS NULL))')
        args.append(principal.employee_uuid)
        if work!='all':args.append(work)
    return ' AND '.join(clauses),args


def watermark(conn):
    return dict(assignments=conn.execute('SELECT COALESCE(MAX(sequence),0) FROM crm_assignment_changes').fetchone()[0],
                facts=conn.execute('SELECT COALESCE(MAX(sequence),0) FROM crm_events').fetchone()[0])


@contextmanager
def snapshot(*, wait=True):
    """Establish a gap-free read snapshot under a *short* writer barrier.

    PostgreSQL REPEATABLE READ keeps this snapshot after the barrier is released.
    Large reports never hold up work/assignment commits for their entire read.
    """
    with storage.connect(readonly=True) as reader:
        if storage.is_postgres(reader):
            with storage.connect() as gate:
                if wait:
                    storage.begin(gate)
                    mark=watermark(reader)
                else:
                    mark=watermark(reader) if storage.try_maintenance_barrier(gate) else None
        else:
            reader.execute('BEGIN')
            mark=watermark(reader)
        yield reader,mark


def portfolio(principal, *, offset=0, limit=50, **filters):
    try:offset,limit=int(offset),int(limit)
    except (ValueError,TypeError):raise CRMError('Некорректная страница.')
    if offset<0 or offset>1000000 or not 1<=limit<=100:raise CRMError('Некорректная страница.')
    with snapshot() as (conn,mark):
        authorize(conn, principal)
        clause,args=_filters(principal,**filters)
        base=' FROM crm_contracts c JOIN crm_people p USING(person_uuid) WHERE '+clause
        total=conn.execute('SELECT COUNT(DISTINCT c.person_uuid)'+base,args).fetchone()[0]
        people=conn.execute('SELECT c.person_uuid,p.name,COUNT(*) AS filtered_contracts'+base+
            ' GROUP BY c.person_uuid,p.name ORDER BY p.name,c.person_uuid LIMIT ? OFFSET ?',(*args,limit,offset)).fetchall()
        names={r['employee_uuid']:r['name'] for r in conn.execute('SELECT employee_uuid,name FROM crm_employees')}
        grouped=defaultdict(list);heads={};permitted=set();matching=set();whole={}
        if people:
            page_ids=[p['person_uuid'] for p in people];marks=','.join('?' for _ in page_ids)
            person_clause,person_args=scope_sql(principal)
            page_where='c.person_uuid IN ('+marks+') AND '+person_clause
            counts=conn.execute('SELECT c.person_uuid,COUNT(*) FROM crm_contracts c WHERE '+page_where+
                ' GROUP BY c.person_uuid',(*page_ids,*person_args)).fetchall()
            if any(r[1]>500 for r in counts):
                raise CRMError('У клиента более 500 договоров: уточните отбор.','selection_too_large',409)
            page_rows=conn.execute('SELECT c.* FROM crm_contracts c WHERE '+page_where+
                ' ORDER BY c.creditor_id,c.external_id,c.contract_uuid',(*page_ids,*person_args)).fetchall()
            ids=[r['contract_uuid'] for r in page_rows]
            for start in range(0,len(ids),200):
                batch=ids[start:start+200];heads.update(states(conn,batch));permitted.update(allowed_slots(conn,principal,batch))
            for row in page_rows:grouped[row['person_uuid']].append(row)
            matching={r[0] for r in conn.execute('SELECT c.contract_uuid'+base+' AND c.person_uuid IN ('+marks+')',(*args,*page_ids))}
            whole={r[0]:r[1] for r in conn.execute('SELECT person_uuid,COUNT(*) FROM crm_contracts WHERE person_uuid IN ('+marks+') GROUP BY person_uuid',page_ids)}
        result=[]
        for p in people:
            all_rows=grouped[p['person_uuid']]
            rows=[];money=defaultdict(Decimal);unknown=0
            for r in all_rows:
                f=json.loads(r['fields_json']);slots=[]
                for kind in sorted(WORK):
                    s=heads[r['contract_uuid'],kind]
                    slots.append(dict(s,name=names.get(s['employee_uuid']),manageable=(r['contract_uuid'],kind) in permitted))
                value=f.get('debt_today')
                if value is None or value=='':value=f.get('debt_total')
                try:
                    number=Decimal(str(value))
                    if not r['currency'] or not number.is_finite():raise InvalidOperation()
                    money[r['currency']]+=number
                except (InvalidOperation,ValueError,TypeError):unknown+=1
                rows.append(dict(contract_uuid=r['contract_uuid'],external_id=r['external_id'],creditor_id=r['creditor_id'],
                    currency=r['currency'],fields=f,slots=slots,in_filter=r['contract_uuid'] in matching))
            result.append(dict(person_uuid=p['person_uuid'],name=p['name'],contracts=rows,
                managed_contracts=len(rows),filtered_contracts=p['filtered_contracts'],partially_managed=whole[p['person_uuid']]!=len(rows),
                currencies={k:format(v,'f') for k,v in money.items()},unknown_amounts=unknown))
        return dict(schema_version=1,people=result,total=total,offset=offset,has_more=offset+len(result)<total,
                    watermark=mark,as_of=now(),readiness=readiness(conn,principal))


def _selection(conn, principal, selection):
    if not isinstance(selection,dict) or set(selection)-{'kind','ids','filters','part'}:
        raise CRMError('Неизвестный состав выбора.')
    kind=selection.get('kind');part=selection.get('part','whole')
    if kind not in {'people','contracts','filter'} or part not in {'whole','filtered'}:
        raise CRMError('Выберите клиентов или договоры.')
    filters=selection.get('filters',{})
    if not isinstance(filters,dict) or set(filters)-{'search','employee_uuid','work','unassigned'}:
        raise CRMError('Неизвестный отбор портфеля.')
    clause,args=_filters(principal,**filters)
    base='SELECT c.contract_uuid,c.person_uuid FROM crm_contracts c JOIN crm_people p USING(person_uuid) WHERE '
    if kind=='filter':
        if part=='whole':
            own,own_args=scope_sql(principal)
            rows=conn.execute(base+own+' AND c.person_uuid IN (SELECT DISTINCT c.person_uuid '
                'FROM crm_contracts c JOIN crm_people p USING(person_uuid) WHERE '+clause+') ORDER BY c.contract_uuid',
                (*own_args,*args)).fetchall()
        else:
            rows=conn.execute(base+clause+' ORDER BY c.contract_uuid',args).fetchall()
    else:
        ids=selection.get('ids')
        if not isinstance(ids,list) or not 1<=len(ids)<=100 or any(not isinstance(x,str) for x in ids) or len(set(ids))!=len(ids):
            raise CRMError('Выберите от 1 до 100 разных клиентов/договоров.')
        column='c.person_uuid' if kind=='people' else 'c.contract_uuid'
        own,own_args=scope_sql(principal)
        rows=conn.execute(base+(clause if part=='filtered' else own)+' AND '+column+' IN ('+','.join('?' for _ in ids)+') ORDER BY c.contract_uuid',
                         (*(args if part=='filtered' else own_args),*ids)).fetchall()
        actual={r['person_uuid'] if kind=='people' else r['contract_uuid'] for r in rows}
        if actual!=set(ids):raise CRMError('Часть выбора недоступна.','scope_changed',403)
    if not rows:raise CRMError('В выборе нет договоров.')
    return [dict(r) for r in rows]


def preview(principal, body):
    if not isinstance(body,dict) or set(body)!={'schema_version','action','employee_uuid','work_kinds','selection','comment'} or body['schema_version']!=1:
        raise CRMError('Неизвестный формат распределения.')
    action=body['action'];target=body['employee_uuid'];works=body['work_kinds'];comment=body['comment']
    if action not in {'assign','replace','remove'} or not isinstance(works,list) or not works or any(not isinstance(w,str) for w in works) or len(set(works))!=len(works) or not set(works)<=WORK:
        raise CRMError('Выберите действие и виды работ.')
    if (action=='remove' and target is not None) or (action!='remove' and (not isinstance(target,str) or not target or len(target)>100)):
        raise CRMError('Выберите кадрового сотрудника.')
    if not isinstance(comment,str) or len(comment)>1000:raise CRMError('Комментарий: до 1000 символов.')
    with storage.connect() as conn:
        storage.begin(conn, ['assignment-preview:'+str(principal.actor_uid)])
        principal=authorize(conn,principal,write=True)
        rows=_selection(conn,principal,body['selection']);ids=[r['contract_uuid'] for r in rows]
        if len(ids)*len(works)>MAX_JOB_SLOTS:
            raise CRMError('Выбор превышает 200 000 рабочих мест. Уточните отбор.',
                           'selection_too_large',409,dict(contracts=len(ids),slots=len(ids)*len(works),max_slots=MAX_JOB_SLOTS))
        permitted=set();current={}
        for start in range(0,len(ids),200):
            permitted.update(allowed_slots(conn,principal,ids[start:start+200]));current.update(states(conn,ids[start:start+200]))
        slots=[]
        for cid in ids:
            for work in sorted(works):
                if (cid,work) not in permitted:raise CRMError('Нет права на все выбранные договоры/работы.','scope_changed',403)
                slots.append(current[cid,work])
        frozen=dict(action=action,employee_uuid=target,slots=slots,comment=comment,
                    selection=body['selection'],people=len({r['person_uuid'] for r in rows}),contracts=len(ids))
        _validate(conn,principal,frozen)
        token=str(uuid.uuid4());created=now();expires=(instant(created)+timedelta(minutes=10)).isoformat()
        conn.execute('INSERT INTO crm_assignment_previews VALUES(?,?,?,?,?)',
                     (token,principal.actor_uid,canonical(frozen),created,expires))
        return dict(preview_id=token,expires_at=expires,**dict(frozen,slots=slots[:MAX_SLOTS]),
                    slots_count=len(slots),large=len(slots)>MAX_SLOTS,
                    no_op=sum(s['employee_uuid']==target for s in slots),watermark=watermark(conn))


def _validate(conn, principal, frozen):
    slots=frozen['slots'];target=frozen['employee_uuid'];action=frozen['action']
    ids=sorted({s['contract_uuid'] for s in slots});permitted=set();current={}
    from .primary_fence import require_contracts
    require_contracts(conn,ids)
    if target:
        from . import withdrawals
        withdrawals.require_contactable(conn, ids)
    for start in range(0,len(ids),200):
        batch=ids[start:start+200]
        permitted.update(allowed_slots(conn,principal,batch));current.update(states(conn,batch))
    if any((s['contract_uuid'],s['capability']) not in permitted for s in slots):
        raise CRMError('Изменилась управляемая область.','scope_changed',403)
    if target:
        authorized_staff = _authority_targets(conn, principal)
        if authorized_staff is not None and target not in authorized_staff:
            raise CRMError('Сотрудник вне вашей области распределения.','target_unavailable',403)
        at=now();members=conn.execute('SELECT e.active,e.staff_id,m.team_id,m.capabilities_json FROM crm_employees e '
            'JOIN crm_memberships m USING(employee_uuid) WHERE e.employee_uuid=? AND m.starts_at<=? '
            'AND (m.ends_at IS NULL OR m.ends_at>?)',(target,at,at)).fetchall()
        works={s['capability'] for s in slots}
        if len(members)!=1 or not members[0]['active'] or not members[0]['staff_id'] or not works<=set(json.loads(members[0]['capabilities_json'])):
            raise CRMError('Сотрудник недоступен для выбранной работы.','target_unavailable',403)
        # Target's team must own every selected work place, not merely another
        # contract in the manager's multi-team scope.
        team=members[0]['team_id'];target_slots=set()
        for start in range(0,len(ids),200):
            batch=ids[start:start+200]
            target_slots.update((r[0],r[1]) for r in conn.execute('SELECT contract_uuid,capability FROM crm_managed_contracts '
                'WHERE team_id=? AND contract_uuid IN ('+','.join('?' for _ in batch)+')',(team,*batch)))
        if any((s['contract_uuid'],s['capability']) not in target_slots for s in slots):
            raise CRMError('Сотрудник не принадлежит управляемому портфелю.','target_unavailable',403)
    changed=[]
    for s in slots:
        actual=current[s['contract_uuid'],s['capability']]
        if actual!=s:changed.append(actual)
        if actual['ambiguous']:
            raise CRMError('Исходное закрепление неоднозначно. Требует сверки.','assignment_ambiguous',409)
        if action=='assign' and actual['employee_uuid'] not in (None,target):
            raise CRMError('Место занято. Выберите «Заменить».','assignment_occupied',409,actual)
    if changed:raise CRMError('Закрепления изменились после просмотра.','assignment_conflict',409,changed)


def _apply_slots(conn, principal, op, frozen, at):
    changed=[];target=frozen['employee_uuid']
    projected = False
    for s in frozen['slots']:
        if s['employee_uuid']==target:continue
        cid=s['contract_uuid'];work=s['capability'];revision=s['revision']+1
        old=conn.execute('SELECT baseline_employee_uuid FROM crm_assignment_heads WHERE contract_uuid=? AND capability=?',(cid,work)).fetchone()
        baseline=old[0] if old else s['employee_uuid']
        conn.execute('UPDATE crm_assignments SET ends_at=? WHERE contract_uuid=? AND capability=? AND (ends_at IS NULL OR ends_at>?)',
                     (at,cid,work,at))
        if target:
            conn.execute('INSERT INTO crm_assignments VALUES(?,?,?,?,?,?,?)',
                         (str(uuid.uuid4()),target,cid,work,at,None,'native-distribution:'+op))
        conn.execute('INSERT INTO crm_assignment_heads VALUES(?,?,?,?,?,?,?,?) '
            'ON CONFLICT(contract_uuid,capability) DO UPDATE SET employee_uuid=excluded.employee_uuid,revision=excluded.revision,'
            'updated_at=excluded.updated_at,operation_id=excluded.operation_id,source=excluded.source',
            (cid,work,target,revision,at,op,'native-distribution',baseline))
        if s['employee_uuid']:
            for task in conn.execute("SELECT entity_id,data_json FROM crm_entities WHERE kind='task' AND contract_uuid=? AND status='open'",(cid,)):
                data=json.loads(task['data_json'])
                if data.get('assignee_uuid')==s['employee_uuid'] and data.get('work_kind')==work and data.get('assignment_grant'):
                    conn.execute('INSERT INTO crm_task_grant_revocations VALUES(?,?,?,?) ON CONFLICT(entity_id,capability) DO NOTHING',
                                 (task['entity_id'],work,op,at))
        conn.execute('INSERT INTO crm_assignment_changes(actor_uid,operation_id,team_id,contract_uuid,capability,'
            'before_employee_uuid,after_employee_uuid,revision,at,source) VALUES(?,?,?,?,?,?,?,?,?,?)',
            (principal.actor_uid,op,principal.team_id,cid,work,s['employee_uuid'],target,revision,at,'native-distribution'))
        changed.append(dict(contract_uuid=cid,capability=work,employee_uuid=target,revision=revision))
        from registry_postgres import project_assignment
        if project_assignment(conn, cid, work, target):
            projected = True
            continue
        if conn.execute('SELECT 1 FROM crm_primary_fences WHERE contract_uuid=?',(cid,)).fetchone():
            conn.execute('INSERT INTO crm_primary_pending VALUES(?,?) ON CONFLICT(contract_uuid) DO UPDATE SET changed_at=excluded.changed_at',(cid,at))
    result=dict(status='applied',operation_id=op,actor_employee_uuid=principal.employee_uuid,team_id=principal.team_id,applied_at=at,changes=changed,no_op=len(frozen['slots'])-len(changed),
                watermark=watermark(conn),projection_status='pending' if any(conn.execute('SELECT 1 FROM crm_primary_pending WHERE contract_uuid=?',(s['contract_uuid'],)).fetchone() for s in changed) else ('projected' if projected else 'isolated'),comment=frozen['comment'])
    return result


def commit(principal, body):
    if not isinstance(body,dict) or set(body)!={'schema_version','operation_id','preview_id'} or body['schema_version']!=1:
        raise CRMError('Неизвестный формат применения.')
    op=operation_id(body['operation_id']);token=operation_id(body['preview_id']);at=now()
    with storage.connect() as conn:
        storage.begin(conn)  # exclusive: valid through work writers' commits
        principal=access.refresh_write(conn,principal)
        previous=conn.execute('SELECT request_hash,result_json FROM crm_assignment_operations WHERE actor_uid=? AND operation_id=?',
                              (principal.actor_uid,op)).fetchone()
        if previous:
            if previous['request_hash']!=digest(body):raise CRMError('Идентификатор уже использован с другим телом.','operation_reused',409)
            return json.loads(previous['result_json'])
        authorize(conn,principal)
        row=conn.execute('SELECT * FROM crm_assignment_previews WHERE preview_id=? AND actor_uid=?',
                         (token,principal.actor_uid)).fetchone()
        if not row:raise CRMError('Предпросмотр недоступен.','preview_missing',404)
        if row['expires_at']<at:raise CRMError('Предпросмотр истёк. Обновите состав.','preview_expired',409)
        frozen=json.loads(row['body_json']);_validate(conn,principal,frozen)
        result = _start_job(conn,principal,op,frozen,at) if len(frozen['slots'])>MAX_SLOTS else _apply_slots(conn,principal,op,frozen,at)
        conn.execute('INSERT INTO crm_assignment_operations VALUES(?,?,?,?,?)',
                     (principal.actor_uid,op,digest(body),canonical(result),at))
        return result


def _start_job(conn,principal,op,frozen,at):
    job_id=op
    from .manager_reports import remember_authority
    remember_authority(conn,principal,'assignment:'+job_id,'assignment')
    conn.execute('INSERT INTO crm_assignment_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
        (job_id,principal.actor_uid,op,'running',len(frozen['slots']),0,0,0,0,0,at,at))
    for index,start in enumerate(range(0,len(frozen['slots']),MAX_SLOTS)):
        slots=frozen['slots'][start:start+MAX_SLOTS]
        pack=dict(frozen,slots=slots)
        pack_op=str(uuid.uuid5(uuid.UUID(job_id),'pack:'+str(index)))
        conn.execute('INSERT INTO crm_assignment_packs VALUES(?,?,?,?,?,?,?)',
            (job_id,index,pack_op,canonical(pack),'pending',None,len(slots)))
    return dict(status='job_started',operation_id=op,actor_employee_uuid=principal.employee_uuid,team_id=principal.team_id,job_id=job_id,total_slots=len(frozen['slots']),
                changes=[],no_op=0,applied_at=at,projection_status='isolated',watermark=watermark(conn))


def _job(conn,principal,job_id):
    identifier=operation_id(job_id)
    row=conn.execute('SELECT * FROM crm_assignment_jobs WHERE job_id=? AND actor_uid=?',(identifier,principal.actor_uid)).fetchone()
    if not row:raise CRMError('Задание недоступно.','job_missing',404)
    result=dict(row)
    result['pending_slots']=result['total_slots']-sum(result[k] for k in ('applied_slots','no_op_slots','conflict_slots','cancelled_slots'))
    return result


def job(principal, *, job_id):
    with storage.connect(readonly=True) as conn:
        return _job(conn,principal,job_id)


def job_step(principal, body):
    if (not isinstance(body,dict) or set(body)!={'schema_version','job_id','pack_index'} or body['schema_version']!=1
            or type(body['pack_index']) is not int or body['pack_index']<0):
        raise CRMError('Неизвестный пакет задания.')
    at=now()
    with storage.connect() as conn:
        storage.begin(conn)
        principal=authorize(conn,principal,write=True)
        state=_job(conn,principal,body['job_id'])
        if body['pack_index']>state['next_pack']:raise CRMError('Нарушен порядок пакетов.','pack_out_of_order',409)
        if state['state']!='running' or body['pack_index']<state['next_pack']:
            return state
        pack=conn.execute('SELECT * FROM crm_assignment_packs WHERE job_id=? AND pack_index=?',
                          (state['job_id'],state['next_pack'])).fetchone()
        if not pack:raise CRMError('Пакет задания не найден.','pack_missing',409)
        frozen=json.loads(pack['body_json'])
        try:_validate(conn,principal,frozen)
        except CRMError as error:
            if error.status==403:
                # No partial execution after authority/target revocation.
                raise
            result=dict(status='conflict',error=error.code,text=str(error))
            conn.execute('UPDATE crm_assignment_packs SET state=?,result_json=? WHERE job_id=? AND pack_index=?',
                ('conflict',canonical(result),state['job_id'],state['next_pack']))
            conn.execute('UPDATE crm_assignment_jobs SET conflict_slots=conflict_slots+?,next_pack=next_pack+1,updated_at=? WHERE job_id=?',
                (pack['slot_count'],at,state['job_id']))
        else:
            result=_apply_slots(conn,principal,pack['operation_id'],frozen,at)
            conn.execute('INSERT INTO crm_assignment_operations VALUES(?,?,?,?,?)',
                (principal.actor_uid,pack['operation_id'],digest(dict(job_id=state['job_id'],pack_index=state['next_pack'])),canonical(result),at))
            conn.execute('UPDATE crm_assignment_packs SET state=?,result_json=? WHERE job_id=? AND pack_index=?',
                ('applied',canonical(result),state['job_id'],state['next_pack']))
            conn.execute('UPDATE crm_assignment_jobs SET applied_slots=applied_slots+?,no_op_slots=no_op_slots+?,next_pack=next_pack+1,updated_at=? WHERE job_id=?',
                (len(result['changes']),result['no_op'],at,state['job_id']))
        state=_job(conn,principal,state['job_id'])
        if state['pending_slots']==0:
            conn.execute('UPDATE crm_assignment_jobs SET state=? WHERE job_id=?',
                ('completed_with_conflicts' if state['conflict_slots'] else 'completed',state['job_id']))
        return _job(conn,principal,state['job_id'])


def job_cancel(principal,body):
    if not isinstance(body,dict) or set(body)!={'schema_version','job_id'} or body['schema_version']!=1:
        raise CRMError('Неизвестный формат остановки.')
    with storage.connect() as conn:
        storage.begin(conn)
        principal=access.refresh_write(conn,principal)
        state=_job(conn,principal,body['job_id'])
        if state['state']=='running':
            conn.execute("UPDATE crm_assignment_packs SET state='cancelled' WHERE job_id=? AND state='pending'",(state['job_id'],))
            conn.execute("UPDATE crm_assignment_jobs SET state='cancelled',cancelled_slots=?,updated_at=? WHERE job_id=?",
                (state['pending_slots'],now(),state['job_id']))
        return _job(conn,principal,state['job_id'])


def receipt(principal, *, operation_id):
    op=globals()['operation_id'](operation_id)
    with storage.connect() as conn:
        row=conn.execute('SELECT result_json FROM crm_assignment_operations WHERE actor_uid=? AND operation_id=?',
                         (principal.actor_uid,op)).fetchone()
        if not row:raise CRMError('Операция пока не найдена.','operation_unknown',404)
        return json.loads(row[0])


def changes(principal, *, assignments='0', facts='0', scope=''):
    try:cursor=dict(assignments=int(assignments),facts=int(facts))
    except (TypeError,ValueError):raise CRMError('Некорректный курсор.')
    if min(cursor.values())<0:raise CRMError('Некорректный курсор.')
    with snapshot(wait=False) as (conn,mark):
        if mark is None:
            return dict(cursor=dict(cursor, scope=scope), resync=False, dirty=False,
                        pending=True, as_of=now())
        return _changes(conn,principal,cursor,scope)


def _changes(conn, principal, cursor, previous_scope=''):
    end=watermark(conn)
    current_scope=digest([principal.employee_uuid,principal.role,principal.team_id,
        sorted(principal.capabilities),sorted(principal.employee_scope),principal.all_contracts,principal.read_all_contracts,
        [dict(r) for r in conn.execute('SELECT team_id,capability FROM crm_distribution_managers WHERE employee_uuid=? ORDER BY team_id,capability',
                                      (principal.employee_uuid,))],
        [dict(r) for r in conn.execute('SELECT country,work FROM crm_logistics_staff WHERE employee_uuid=? ORDER BY country,work',
                                      (principal.employee_uuid,))],
        conn.execute('SELECT MAX(recorded_at) FROM crm_contact_status').fetchone()[0]])
    new_cursor=dict(end,scope=current_scope)
    if previous_scope and previous_scope!=current_scope:
        return dict(cursor=new_cursor,resync=True,dirty=True,as_of=now())
    if any(cursor[k]>end[k] for k in end):
        return dict(cursor=new_cursor,resync=True,dirty=True,as_of=now())
    if cursor==end:
        return dict(cursor=new_cursor,resync=False,dirty=False,as_of=now())
    # Filter the changed rows in SQL. Reading the entire 50k portfolio for each
    # invalidation made the one-second client polling miss its latency budget.
    staff=sorted(principal.employee_scope);marks=','.join('?' for _ in staff)
    clause,args=scope_sql(principal,alias='d')
    if staff:
        clause+=' OR d.before_employee_uuid IN ('+marks+') OR d.after_employee_uuid IN ('+marks+')'
        args.extend(staff+staff)
    if principal.role == 'logistic' and 'logistics' in principal.capabilities:
        clause += (" OR EXISTS(SELECT 1 FROM crm_logistics_cases lc JOIN crm_logistics_staff ls "
                   "ON ls.country=lc.country AND ls.work='logist' "
                   "WHERE lc.contract_uuid=d.contract_uuid AND ls.employee_uuid=?)")
        args.append(principal.employee_uuid)
    dirty=bool(conn.execute('SELECT 1 FROM crm_assignment_changes d WHERE d.sequence>? AND d.sequence<=? AND ('
        +clause+') LIMIT 1',(cursor['assignments'],end['assignments'],*args)).fetchone())
    if not dirty and cursor['facts']<end['facts']:
        clause,args=scope_sql(principal,alias='ec')
        clause+=' OR e.employee_uuid=?';args.append(principal.employee_uuid)
        if access.reads_all(principal):clause='1=1';args=[]
        elif principal.role == 'logistic' and 'logistics' in principal.capabilities:
            clause += (" OR EXISTS(SELECT 1 FROM crm_logistics_cases lc JOIN crm_logistics_staff ls "
                       "ON ls.country=lc.country AND ls.work='logist' "
                       "WHERE lc.contract_uuid=ec.contract_uuid AND ls.employee_uuid=?)")
            args.append(principal.employee_uuid)
        elif staff:
            at=now()
            clause+=' OR EXISTS(SELECT 1 FROM crm_effective_assignments a WHERE a.contract_uuid=ec.contract_uuid '
            clause+='AND a.employee_uuid IN ('+marks+') AND a.starts_at<=? AND (a.ends_at IS NULL OR a.ends_at>?))'
            args.extend([*staff,at,at])
            from . import tasks
            delegated=sorted(tasks.grants(conn,principal))
            if delegated:
                clause+=' OR ec.contract_uuid IN ('+','.join('?' for _ in delegated)+')';args.extend(delegated)
        dirty=bool(conn.execute('SELECT 1 FROM crm_events e JOIN crm_event_contracts ec USING(event_id) '
            'WHERE e.sequence>? AND e.sequence<=? AND ('+clause+') LIMIT 1',
            (cursor['facts'],end['facts'],*args)).fetchone())
    # No inaccessible contract IDs, assignees, names or fact payloads on channel.
    return dict(cursor=new_cursor,resync=False,dirty=dirty,as_of=now())


def staff(principal):
    with storage.connect(readonly=True) as conn:
        authorize(conn,principal)
        return dict(staff=targets(conn,principal),readiness=readiness(conn,principal))


def history(principal, *, offset=0, limit=50):
    try:offset,limit=int(offset),int(limit)
    except (ValueError,TypeError):raise CRMError('Некорректная страница истории.')
    if offset<0 or not 1<=limit<=100:raise CRMError('Некорректная страница истории.')
    with storage.connect(readonly=True) as conn:
        authorize(conn,principal)
        clause,args=scope_sql(principal)
        rows=conn.execute('SELECT x.*,c.person_uuid,c.external_id,c.creditor_id,p.name AS client,'
            'b.name AS before_name,a.name AS after_name FROM crm_assignment_changes x '
            'JOIN crm_contracts c USING(contract_uuid) JOIN crm_people p USING(person_uuid) '
            'LEFT JOIN crm_employees b ON b.employee_uuid=x.before_employee_uuid '
            'LEFT JOIN crm_employees a ON a.employee_uuid=x.after_employee_uuid '
            "WHERE x.capability IN ('call','visit') AND ("+clause+') ORDER BY x.sequence DESC LIMIT ? OFFSET ?',(*args,limit,offset)).fetchall()
        return dict(rows=[dict(r) for r in rows],offset=offset)


def report_authorize(conn,principal):
    if principal.role not in {'manager','owner','analyst'}:
        raise CRMError('Сводка доступна руководителю или аналитику.','forbidden',403)
    if principal.owner:return principal
    access.require(principal,'analytics')
    current=access.refresh_write(conn,principal)
    at=now()
    member=conn.execute('SELECT role FROM crm_memberships WHERE employee_uuid=? AND team_id=? AND starts_at<=? '
                        'AND (ends_at IS NULL OR ends_at>?) ORDER BY starts_at DESC LIMIT 1',
                        (principal.employee_uuid,principal.team_id,at,at)).fetchone()
    # Some Analyst logins use a manager membership with reduced capabilities.
    allowed={'manager','analyst'} if principal.role=='analyst' else {'manager'}
    if not member or member['role'] not in allowed:
        raise CRMError('Полномочия для отчёта изменились.','forbidden',403)
    access.require(current,'analytics')
    if current.role=='manager' and not report_distribution_scope(conn,current):
        from dataclasses import replace
        current=replace(current,employee_scope=frozenset(team_results_roster(conn,current)))
    return current


def report_distribution_scope(conn, principal):
    return principal.role in {'manager','owner'} and (principal.owner or 'assign' in principal.capabilities) and bool(
        conn.execute('SELECT 1 FROM crm_distribution_managers WHERE employee_uuid=? LIMIT 1',
                     (principal.employee_uuid,)).fetchone())


def report_scope(conn,principal):
    if report_distribution_scope(conn,principal):return scope_sql(principal)
    if principal.owner or principal.role=='analyst' and access.reads_all(principal):
        return '1=1',[]
    if principal.role=='analyst':
        ids=sorted(access.readable_contracts(conn,principal))
        return ('c.contract_uuid IN ('+','.join('?' for _ in ids)+')',ids) if ids else ('1=0',[])
    scope=sorted(principal.employee_scope)
    if not scope:return '1=0',[]
    at=now()
    return ("c.contract_uuid IN (SELECT scoped.contract_uuid FROM crm_effective_assignments scoped "
            "WHERE scoped.capability IN ('call','visit') AND scoped.starts_at<=? "
            "AND (scoped.ends_at IS NULL OR scoped.ends_at>?) "
            "AND scoped.employee_uuid IN ("+','.join('?' for _ in scope)+'))',[at,at,*scope])


def report_targets(conn,principal):
    if report_distribution_scope(conn,principal):return targets(conn,principal)
    at=now();result={}
    for row in conn.execute('SELECT e.employee_uuid,e.name,m.role,m.capabilities_json FROM crm_employees e JOIN crm_memberships m USING(employee_uuid) WHERE e.active=1 AND m.starts_at<=? AND (m.ends_at IS NULL OR m.ends_at>?)',(at,at)):
        if row['employee_uuid'] not in principal.employee_scope and not principal.owner:continue
        result[row['employee_uuid']]=dict(employee_uuid=row['employee_uuid'],name=row['name'],role=row['role'],contracts=0,people=0)
    clause,args=report_scope(conn,principal)
    for row in conn.execute('SELECT a.employee_uuid,COUNT(DISTINCT a.contract_uuid) AS contracts,COUNT(DISTINCT c.person_uuid) AS people FROM crm_effective_assignments a JOIN crm_contracts c USING(contract_uuid) WHERE '+clause+' AND a.starts_at<=? AND (a.ends_at IS NULL OR a.ends_at>?) GROUP BY a.employee_uuid',(*args,at,at)):
        if row['employee_uuid'] in result:result[row['employee_uuid']].update(contracts=row['contracts'],people=row['people'])
    return list(result.values())


def statistics(principal, *, month=None,period=None,employee_uuid=None,kind=None,offset=0,country='',office='',
               date_from=None,date_to=None,download=None,employee_search=''):
    from datetime import date
    from zoneinfo import ZoneInfo
    if period is not None:
        if month is not None:
            raise CRMError('Выберите один период отчёта.')
        return statistics_period(principal, period, employee_uuid, kind, offset, country, office,
                                 date_from, date_to, download, employee_search)
    if country or office or date_from is not None or date_to is not None or download or employee_search:
        raise CRMError('Выберите период результатов команды.')
    if month is None:month=instant(now()).astimezone(ZoneInfo(principal.timezone)).strftime('%Y-%m')
    try:
        first=date.fromisoformat(month+'-01')
        following=date(first.year+(first.month==12),first.month%12+1,1)
    except (ValueError,TypeError):raise CRMError('Период: ГГГГ-ММ.')
    if employee_uuid is not None:
        return statistics_detail(principal,employee_uuid,kind,first.isoformat(),following.isoformat(),offset)
    with snapshot() as (conn,mark):
        principal=report_authorize(conn,principal)
        clause,args=report_scope(conn,principal)
        rows=conn.execute('SELECT c.contract_uuid,c.person_uuid,c.fields_json,c.currency '
                          'FROM crm_contracts c WHERE '+clause,args).fetchall()
        ids=[r['contract_uuid'] for r in rows]
        unassigned=set();ambiguous=set();free_slots=0;people=set();debt=defaultdict(Decimal);unknown_debt=0
        at=now()
        managed=report_distribution_scope(conn,principal)
        slots=conn.execute('SELECT mc.contract_uuid,mc.capability,h.contract_uuid AS head_id,h.employee_uuid,'
                'COUNT(DISTINCT a.employee_uuid) AS candidates FROM crm_managed_contracts mc '
                'JOIN crm_distribution_managers dm ON dm.team_id=mc.team_id AND dm.capability=mc.capability '
                'LEFT JOIN crm_assignment_heads h ON h.contract_uuid=mc.contract_uuid AND h.capability=mc.capability '
                'LEFT JOIN crm_assignments a ON a.contract_uuid=mc.contract_uuid AND a.capability=mc.capability '
                'AND h.contract_uuid IS NULL AND a.starts_at<=? AND (a.ends_at IS NULL OR a.ends_at>?) '
                'WHERE dm.employee_uuid=? GROUP BY mc.contract_uuid,mc.capability,h.contract_uuid,h.employee_uuid',
                (at,at,principal.employee_uuid)) if managed else ()
        for slot in slots:
            if not slot['head_id'] and slot['candidates']>1:ambiguous.add(slot['contract_uuid'])
            elif (slot['head_id'] and slot['employee_uuid'] is None) or (not slot['head_id'] and slot['candidates']==0):
                unassigned.add(slot['contract_uuid']);free_slots+=1
        for r in rows:
            cid=r['contract_uuid'];people.add(r['person_uuid'])
            f=json.loads(r['fields_json']);value=f.get('debt_today')
            if value is None or value=='':value=f.get('debt_total')
            try:
                number=Decimal(str(value))
                if not r['currency'] or not number.is_finite():raise InvalidOperation()
                debt[r['currency']]+=number
            except (InvalidOperation,TypeError,ValueError):unknown_debt+=1
        payments=defaultdict(Decimal);known=0;unknown_payment=0;sources=set()
        for r in conn.execute('SELECT t.* FROM crm_monthly_totals t JOIN crm_contracts c USING(contract_uuid) WHERE t.month=? AND '+clause,(month,*args)):
            try:
                value=Decimal(str(r['amount']))
                if not r['currency'] or not value.is_finite():raise InvalidOperation()
                payments[r['currency']]+=value;known+=1;sources.add(r['observed_at'])
            except (InvalidOperation,TypeError,ValueError):unknown_payment+=1
        if not managed:
            visible=set(ids);counts=defaultdict(set)
            for slot in conn.execute("SELECT contract_uuid,capability,employee_uuid FROM crm_effective_assignments WHERE capability IN ('call','visit') AND starts_at<=? AND (ends_at IS NULL OR ends_at>?)",(at,at)):
                if slot['contract_uuid'] in visible:counts[(slot['contract_uuid'],slot['capability'])].add(slot['employee_uuid'])
            for cid in ids:
                for capability in WORK:
                    found=counts[(cid,capability)]
                    if not found:unassigned.add(cid);free_slots+=1
                    elif len(found)>1:ambiguous.add(cid)
        roster=report_targets(conn,principal);staff_ids={r['employee_uuid'] for r in roster}
        facts=defaultdict(lambda:dict(call=0,visit=0,contracts=set()))
        since=first.isoformat();until=following.isoformat();zone=ZoneInfo(principal.timezone)
        for r in conn.execute("SELECT e.employee_uuid,e.kind,e.received_at,e.occurred_at,e.time_quality,e.payload_json,ec.contract_uuid "
            "FROM crm_events e LEFT JOIN crm_event_contracts ec USING(event_id) WHERE e.kind IN ('call','visit')"):
            if r['employee_uuid'] not in staff_ids:continue
            try:
                if r['time_quality']=='legacy_day':day=json.loads(r['payload_json']).get('legacy_workday','')
                elif r['time_quality']=='legacy_unknown':continue
                else:day=instant(r['occurred_at']).astimezone(zone).date().isoformat()
            except (CRMError,ValueError,TypeError):continue
            if not since<=day<until:continue
            # One multi-contract event is a single call/visit, but each link is
            # a processed contract. Distinct event IDs are counted below.
            if r['contract_uuid']:facts[r['employee_uuid']]['contracts'].add(r['contract_uuid'])
        for r in conn.execute("SELECT employee_uuid,kind,occurred_at,time_quality,payload_json FROM crm_events WHERE kind IN ('call','visit')"):
            if r['employee_uuid'] not in staff_ids:continue
            try:
                day=json.loads(r['payload_json']).get('legacy_workday','') if r['time_quality']=='legacy_day' else '' if r['time_quality']=='legacy_unknown' else instant(r['occurred_at']).astimezone(zone).date().isoformat()
            except (CRMError,ValueError,TypeError):continue
            if since<=day<until:facts[r['employee_uuid']][r['kind']]+=1
        for r in roster:
            f=facts[r['employee_uuid']]
            r['facts']=dict(call=f['call'],visit=f['visit'],processed_contracts=len(f['contracts']))
        return dict(as_of=now(),watermark=mark,month=month,people=len(people),contracts=len(ids),
            unassigned_contracts=len(unassigned),unassigned_slots=free_slots,ambiguous_contracts=len(ambiguous),
            debt={k:format(v,'f') for k,v in debt.items()},unknown_debt=unknown_debt,staff=roster,
            payments=dict(totals={k:format(v,'f') for k,v in payments.items()},known_contracts=known,
                missing_contracts=len(ids)-known,invalid_or_unknown_currency=unknown_payment,source_as_of=sorted(sources),
                attribution='Поступления в текущем управляемом портфеле; закрепление на '+now()),
            facts_attribution='Исходные авторы в текущем кадровом составе; история не ограничена текущими закреплениями. История без достоверного дня не включена.',
            overlapping_staff_totals=True)


def fact_day(row, zone):
    if row['time_quality']=='legacy_day':
        return json.loads(row['payload_json']).get('legacy_workday','')
    if row['time_quality']=='legacy_unknown':
        return ''
    return instant(row['occurred_at']).astimezone(zone).date().isoformat()


def team_results_roster(conn, principal):
    from . import team_territories
    if principal.role not in {'manager','owner'}:
        raise CRMError('Результаты команды доступны руководителю.','forbidden',403)
    principal=access.refresh_write(conn,principal)
    access.require(principal,'analytics')
    at=now()
    rows = [row for row in conn.execute(
        'SELECT DISTINCT e.employee_uuid,e.name,e.staff_id FROM crm_employees e JOIN crm_memberships m USING(employee_uuid) '
        'WHERE e.active=1 AND m.starts_at<=? AND (m.ends_at IS NULL OR m.ends_at>?) ORDER BY e.name,e.employee_uuid',
        (at,at)) if principal.owner or row['employee_uuid'] in principal.employee_scope]
    shared = conn.execute("SELECT 1 FROM crm_meta WHERE key='deployment_namespace' AND value='crm-shared-pilot-v1'").fetchone()
    return team_territories.staff(principal, rows, shared=bool(shared))


def statistics_period(principal, period, employee_uuid, kind, offset, country='', office='',
                      date_from=None,date_to=None,download=None,employee_search=''):
    from datetime import date, datetime, time, timezone
    from zoneinfo import ZoneInfo

    zone=ZoneInfo(principal.timezone)
    if download not in (None,'visits','daily') or (download and (employee_uuid is not None or kind is not None)):
        raise CRMError('Неизвестная выгрузка результатов команды.')
    if not isinstance(employee_search,str) or len(employee_search)>150 or (employee_search and not download):
        raise CRMError('Некорректный поиск сотрудника.')
    employee_search=employee_search.strip().casefold()
    today=instant(now()).astimezone(zone).date()
    end=today+timedelta(days=1)
    if period!='custom' and (date_from is not None or date_to is not None):
        raise CRMError('Выберите один период отчёта.')
    if period=='today':
        start=today
    elif period=='yesterday':
        start=today-timedelta(days=1)
        end=today
    elif period=='week':
        start=today-timedelta(days=6)
    elif period=='month':
        start=today.replace(day=1)
    elif period=='custom':
        try:
            start, last = date.fromisoformat(date_from), date.fromisoformat(date_to)
        except (ValueError, TypeError):
            raise CRMError('Укажите начало и конец периода.') from None
        if not start <= last <= today or (last-start).days >= 31:
            raise CRMError('Выберите до 31 дня включительно, без будущих дат.')
        end=last+timedelta(days=1)
    else:
        raise CRMError('Неизвестный период отчёта.')
    since,until=start.isoformat(),end.isoformat()
    lower=datetime.combine(start,time.min,zone).astimezone(timezone.utc).isoformat()
    upper=datetime.combine(end,time.min,zone).astimezone(timezone.utc).isoformat()
    workdays=sum((start+timedelta(days=offset)).weekday()<5 for offset in range((end-start).days))
    with snapshot() as (conn,mark):
        roster = team_results_roster(conn,principal)
        territories = defaultdict(set)
        groups = {}
        authors = {}
        for uid, person in roster.items():
            region = person['country'] or '!none'
            cities = person['offices'] or ['!none']
            territories[region].update(cities)
            if (country and region != country) or (office and office not in cities):
                continue
            if employee_search and employee_search not in person['name'].casefold():
                continue
            group = person.get('group',uid)
            authors[uid] = group
            if group not in groups:
                norm=person.get('visit_norm')
                groups[group] = dict(employee_uuid=uid,name=person['name'],country=region,offices=cities,
                                     call=0,visit=0,by_day={},outcomes={},visit_norm=norm,
                                     personal_norm=person.get('personal_norm'),visit_plan=norm*workdays if norm else None)
        if employee_uuid is not None:
            if employee_uuid not in authors:
                raise CRMError('Сотрудник недоступен.','forbidden',403)
            related = [uid for uid, group in authors.items() if group == authors[employee_uuid]]
            return team_detail(conn,principal,related,groups[authors[employee_uuid]]['name'],kind,since,until,offset)
        visits=[]
        if authors:
            employees=','.join('?' for _ in authors)
            events=conn.execute(
                "SELECT event_id,employee_uuid,kind,time_quality,occurred_at,received_at,origin,payload_json FROM crm_events "
                "WHERE employee_uuid IN ("+employees+") AND kind IN ('call','visit') "
                "AND (time_quality='legacy_day' OR (time_quality<>'legacy_unknown' AND occurred_at>=? AND occurred_at<?))",
                (*authors,lower,upper))
            for row in events:
                try:day=fact_day(row,zone)
                except (CRMError,ValueError,TypeError):continue
                if since<=day<until:
                    summary = groups[authors[row['employee_uuid']]]
                    summary[row['kind']] += 1
                    if row['kind']=='visit':
                        daily = summary['by_day']
                        daily[day] = daily.get(day,0)+1
                        values = json.loads(row['payload_json']).get('values') or {}
                        outcome = str(values.get('call_status') or '').strip() or 'Без результата'
                        counts = summary['outcomes']
                        counts[outcome] = counts.get(outcome,0)+1
                        if download:
                            visits.append((row,day,summary))
        report=dict(as_of=now(),watermark=mark,since=since,through=(end-timedelta(days=1)).isoformat(),
                    timezone=principal.timezone,workdays=workdays,territories={key:sorted(value) for key,value in territories.items()},
                    staff=list(groups.values()))
        if download:
            from . import team_visits_export
            principal = access.refresh_write(conn,principal)
            return team_visits_export.workbook(report,team_visits_export.details(conn,principal,visits,zone),
                                              daily=download=='daily')
        return report


def statistics_detail(principal,employee_uuid,kind,since,until,offset):
    with snapshot() as (conn,mark):
        principal=report_authorize(conn,principal)
        name=next((r['name'] for r in report_targets(conn,principal) if r['employee_uuid']==employee_uuid),None)
        if name is None:raise CRMError('Сотрудник недоступен.','forbidden',403)
        return team_detail(conn,principal,[employee_uuid],name,kind,since,until,offset)


def team_detail(conn,principal,authors,name,kind,since,until,offset):
    from zoneinfo import ZoneInfo
    try:offset=int(offset)
    except (TypeError,ValueError):raise CRMError('Некорректная страница.')
    if kind not in WORK or not 0<=offset<=1000000:raise CRMError('Некорректный отбор фактов.')
    zone=ZoneInfo(principal.timezone);events=[]
    placeholders=','.join('?' for _ in authors)
    for r in conn.execute('SELECT event_id,occurred_at,time_quality,payload_json FROM crm_events WHERE employee_uuid IN ('+placeholders+') AND kind=? ORDER BY sequence DESC',(*authors,kind)):
        try:
            day=fact_day(r,zone)
        except (CRMError,ValueError,TypeError):continue
        if since<=day<until:events.append(dict(r,day=day))
    visible=access.readable_contracts(conn,principal) if not access.reads_all(principal) else None
    rows=[]
    for event in events[offset:offset+50]:
        links=conn.execute('SELECT c.contract_uuid,c.person_uuid,c.external_id,p.name FROM crm_event_contracts ec JOIN crm_contracts c USING(contract_uuid) JOIN crm_people p USING(person_uuid) WHERE ec.event_id=?',(event['event_id'],)).fetchall()
        rows.append(dict(event_id=event['event_id'],day=event['day'],at=event['occurred_at'],time_quality=event['time_quality'],
            contracts=[dict(r) if visible is None or r['contract_uuid'] in visible else dict(name='Договор вне текущей области') for r in links]))
    return dict(employee=name,kind=kind,rows=rows,total=len(events),offset=offset,has_more=offset+len(rows)<len(events))
