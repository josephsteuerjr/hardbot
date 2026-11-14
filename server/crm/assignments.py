"""Permanent call/visit allocation, separate from work facts and temporary tasks.

All commits share the exclusive maintenance barrier with every work writer.
Preview freezes identities and revisions; retries bind to the original actor/body.
The legacy mixed deployment stays disabled until its independent fence is ready.
"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, InvalidOperation
import json
import uuid

from . import access, storage
from .contract import CRMError, canonical, digest, instant, now, operation_id

WORK = {'call', 'visit'}
MAX_SLOTS = 200


def effective_fields(conn, contract_id, values):
    """Current responsibility is a projection, never an editable source grant."""
    values=dict(values)
    for r in conn.execute('SELECT h.capability,h.employee_uuid,e.name FROM crm_assignment_heads h '
            'LEFT JOIN crm_employees e USING(employee_uuid) WHERE h.contract_uuid=?',(contract_id,)):
        values['responsible_soft' if r['capability']=='call' else 'responsible']=r['name'] if r['employee_uuid'] else 'Не назначен'
    return values


def readiness(conn, principal):
    mode = conn.execute("SELECT value FROM crm_meta WHERE key='distribution_mode'").fetchone()
    enabled = bool(mode and mode[0] == 'isolated')
    # There is intentionally no 'production ready' value yet. An old primary
    # SQLite endpoint cannot be fenced by a PostgreSQL advisory lock alone.
    managed = conn.execute('SELECT 1 FROM crm_distribution_managers WHERE employee_uuid=?',
                           (principal.employee_uuid,)).fetchone()
    permitted = principal.role in {'manager', 'owner'} and (principal.owner or 'assign' in principal.capabilities) and bool(managed)
    return dict(enabled=enabled and permitted, environment='isolated' if enabled else 'mixed',
                reason='' if enabled and permitted else
                'Распределение в рабочем смешанном контуре выключено: защита старых писателей ещё не подтверждена.' if not enabled else
                'Нет полномочия распределения в управляемом портфеле.',
                max_atomic_slots=MAX_SLOTS, all_selection=True, durable_jobs=False)


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


def targets(conn, principal):
    at = now()
    teams = {r[0] for r in conn.execute('SELECT DISTINCT team_id FROM crm_distribution_managers WHERE employee_uuid=?',
                                      (principal.employee_uuid,))}
    result = {}
    rows = conn.execute('SELECT e.employee_uuid,e.name,e.staff_id,e.active,m.team_id,m.role,m.capabilities_json '
        'FROM crm_employees e LEFT JOIN crm_memberships m ON m.employee_uuid=e.employee_uuid '
        'AND m.starts_at<=? AND (m.ends_at IS NULL OR m.ends_at>?) ORDER BY e.name,e.employee_uuid', (at,at))
    memberships = defaultdict(list)
    for r in rows:
        memberships[r['employee_uuid']].append(dict(r))
    for eid, members in memberships.items():
        visible = [r for r in members if r['team_id'] in teams]
        if not visible:
            continue
        row = visible[0]
        kinds = sorted(set(json.loads(row['capabilities_json'])) & WORK) if len(members)==1 else []
        reason = ('Сотрудник отключён' if not row['active'] else 'Нет постоянной кадровой личности' if not row['staff_id']
                  else 'Требует сверки членства' if len(members)!=1 else 'Нет допустимых видов работ' if not kinds else '')
        result[eid] = dict(employee_uuid=eid,name=row['name'],team_id=row['team_id'],work_kinds=kinds,
                           eligible=not reason,reason=reason,has_account=False,contracts=0,people=0,slots=0)
    for r in conn.execute('SELECT l.employee_uuid FROM crm_actor_links l WHERE l.active=1'):
        if r[0] in result:
            result[r[0]]['has_account'] = True
    clause,args = scope_sql(principal)
    for r in conn.execute('SELECT a.employee_uuid,COUNT(DISTINCT a.contract_uuid) AS contracts,'
            'COUNT(DISTINCT c.person_uuid) AS people,COUNT(*) AS slots FROM crm_effective_assignments a '
            'JOIN crm_contracts c USING(contract_uuid) WHERE '+clause+
            ' AND a.starts_at<=? AND (a.ends_at IS NULL OR a.ends_at>?) GROUP BY a.employee_uuid', (*args,at,at)):
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


def portfolio(principal, *, offset=0, limit=50, **filters):
    try:offset,limit=int(offset),int(limit)
    except (ValueError,TypeError):raise CRMError('Некорректная страница.')
    if offset<0 or offset>1000000 or not 1<=limit<=100:raise CRMError('Некорректная страница.')
    with storage.connect() as conn:
        storage.begin(conn)
        authorize(conn, principal)
        clause,args=_filters(principal,**filters)
        base=' FROM crm_contracts c JOIN crm_people p USING(person_uuid) WHERE '+clause
        total=conn.execute('SELECT COUNT(DISTINCT c.person_uuid)'+base,args).fetchone()[0]
        people=conn.execute('SELECT c.person_uuid,p.name,COUNT(*) AS filtered_contracts'+base+
            ' GROUP BY c.person_uuid,p.name ORDER BY p.name,c.person_uuid LIMIT ? OFFSET ?',(*args,limit,offset)).fetchall()
        names={r['employee_uuid']:r['name'] for r in conn.execute('SELECT employee_uuid,name FROM crm_employees')}
        result=[]
        for p in people:
            person_clause,person_args=scope_sql(principal)
            all_rows=conn.execute('SELECT c.* FROM crm_contracts c WHERE c.person_uuid=? AND '+person_clause+
                ' ORDER BY c.creditor_id,c.external_id,c.contract_uuid',(p['person_uuid'],*person_args)).fetchall()
            # A page is bounded in clients and contracts; giant people are
            # explicitly refused rather than silently partially transferred.
            if len(all_rows)>500:
                raise CRMError('У клиента более 500 договоров: уточните отбор.','selection_too_large',409)
            ids=[r['contract_uuid'] for r in all_rows];heads=states(conn,ids);permitted=allowed_slots(conn,principal,ids)
            matching={r[0] for r in conn.execute('SELECT c.contract_uuid'+base+' AND c.person_uuid=?',(*args,p['person_uuid']))}
            rows=[];money=defaultdict(Decimal);unknown=0
            for r in all_rows:
                f=json.loads(r['fields_json']);slots=[]
                for kind in sorted(WORK):
                    s=heads[r['contract_uuid'],kind]
                    slots.append(dict(s,name=names.get(s['employee_uuid']),manageable=(r['contract_uuid'],kind) in permitted))
                value=f.get('debt_today',f.get('debt_total'))
                try:
                    number=Decimal(str(value))
                    if not r['currency'] or not number.is_finite():raise InvalidOperation()
                    money[r['currency']]+=number
                except (InvalidOperation,ValueError,TypeError):unknown+=1
                rows.append(dict(contract_uuid=r['contract_uuid'],external_id=r['external_id'],creditor_id=r['creditor_id'],
                    currency=r['currency'],fields=f,slots=slots,in_filter=r['contract_uuid'] in matching))
            whole=conn.execute('SELECT COUNT(*) FROM crm_contracts WHERE person_uuid=?',(p['person_uuid'],)).fetchone()[0]
            result.append(dict(person_uuid=p['person_uuid'],name=p['name'],contracts=rows,
                managed_contracts=len(rows),filtered_contracts=p['filtered_contracts'],partially_managed=whole!=len(rows),
                currencies={k:format(v,'f') for k,v in money.items()},unknown_amounts=unknown))
        return dict(schema_version=1,people=result,total=total,offset=offset,has_more=offset+len(result)<total,
                    watermark=watermark(conn),as_of=now(),readiness=readiness(conn,principal))


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
        if len(ids)*len(works)>MAX_SLOTS:
            raise CRMError('Атомарный пакет: до 200 рабочих мест. Большое серверное задание ещё недоступно.',
                           'selection_too_large',409,dict(contracts=len(ids),slots=len(ids)*len(works),max_slots=MAX_SLOTS))
        permitted=allowed_slots(conn,principal,ids);current=states(conn,ids)
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
        return dict(preview_id=token,expires_at=expires,**frozen,
                    no_op=sum(s['employee_uuid']==target for s in slots),watermark=watermark(conn))


def _validate(conn, principal, frozen):
    slots=frozen['slots'];target=frozen['employee_uuid'];action=frozen['action']
    ids=sorted({s['contract_uuid'] for s in slots});permitted=allowed_slots(conn,principal,ids);current=states(conn,ids)
    if any((s['contract_uuid'],s['capability']) not in permitted for s in slots):
        raise CRMError('Изменилась управляемая область.','scope_changed',403)
    if target:
        candidates={r['employee_uuid']:r for r in targets(conn,principal)};employee=candidates.get(target)
        works={s['capability'] for s in slots}
        if not employee or not employee['eligible'] or not works<=set(employee['work_kinds']):
            raise CRMError('Сотрудник недоступен для выбранной работы.','target_unavailable',403)
        # Target's team must own every selected work place, not merely another
        # contract in the manager's multi-team scope.
        for s in slots:
            if not conn.execute('SELECT 1 FROM crm_managed_contracts WHERE team_id=? AND contract_uuid=? AND capability=?',
                (employee['team_id'],s['contract_uuid'],s['capability'])).fetchone():
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
        changed=[];target=frozen['employee_uuid']
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
        result=dict(status='applied',operation_id=op,applied_at=at,changes=changed,no_op=len(frozen['slots'])-len(changed),
                    watermark=watermark(conn),projection_status='isolated',comment=frozen['comment'])
        conn.execute('INSERT INTO crm_assignment_operations VALUES(?,?,?,?,?)',
                     (principal.actor_uid,op,digest(body),canonical(result),at))
        return result


def receipt(principal, *, operation_id):
    op=globals()['operation_id'](operation_id)
    with storage.connect() as conn:
        row=conn.execute('SELECT result_json FROM crm_assignment_operations WHERE actor_uid=? AND operation_id=?',
                         (principal.actor_uid,op)).fetchone()
        if not row:raise CRMError('Операция пока не найдена.','operation_unknown',404)
        return json.loads(row[0])


def changes(principal, *, assignments='0', facts='0'):
    try:cursor=dict(assignments=int(assignments),facts=int(facts))
    except (TypeError,ValueError):raise CRMError('Некорректный курсор.')
    if min(cursor.values())<0:raise CRMError('Некорректный курсор.')
    with storage.connect() as conn:
        # All writers take the shared side; this short exclusive snapshot
        # prevents a sequence allocated by an uncommitted writer being skipped.
        # REPEATABLE READ snapshot is obtained *after* the advisory barrier.
        # Live cursor therefore uses a separate read-write transaction.
        storage.begin(conn)
        return _changes(conn,principal,cursor)


def _changes(conn, principal, cursor):
    end=watermark(conn)
    if any(cursor[k]>end[k] for k in end):
        return dict(cursor=end,resync=True,dirty=True,as_of=now())
    read=access.readable_contracts(conn,principal)
    managed_clause,args=scope_sql(principal)
    managed={r[0] for r in conn.execute('SELECT c.contract_uuid FROM crm_contracts c WHERE '+managed_clause,args)}
    dirty=False
    for r in conn.execute('SELECT * FROM crm_assignment_changes WHERE sequence>? AND sequence<=? ORDER BY sequence',
                          (cursor['assignments'],end['assignments'])):
        if r['contract_uuid'] in managed or r['before_employee_uuid'] in principal.employee_scope or r['after_employee_uuid'] in principal.employee_scope:
            dirty=True
    if not dirty:
        for r in conn.execute('SELECT ec.contract_uuid,e.employee_uuid FROM crm_events e JOIN crm_event_contracts ec USING(event_id) '
                              'WHERE e.sequence>? AND e.sequence<=?',(cursor['facts'],end['facts'])):
            if r['contract_uuid'] in read or r['contract_uuid'] in managed or r['employee_uuid']==principal.employee_uuid:
                dirty=True;break
    # No inaccessible contract IDs, assignees, names or fact payloads on channel.
    return dict(cursor=end,resync=False,dirty=dirty,as_of=now())


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
            'WHERE '+clause+' ORDER BY x.sequence DESC LIMIT ? OFFSET ?',(*args,limit,offset)).fetchall()
        return dict(rows=[dict(r) for r in rows],offset=offset)


def statistics(principal, *, month=None):
    from datetime import date
    from zoneinfo import ZoneInfo
    if month is None:month=instant(now()).astimezone(ZoneInfo(principal.timezone)).strftime('%Y-%m')
    try:
        first=date.fromisoformat(month+'-01')
        following=date(first.year+(first.month==12),first.month%12+1,1)
    except (ValueError,TypeError):raise CRMError('Период: ГГГГ-ММ.')
    with storage.connect() as conn:
        storage.begin(conn)
        authorize(conn,principal)
        clause,args=scope_sql(principal)
        rows=conn.execute('SELECT c.*,p.name AS client FROM crm_contracts c JOIN crm_people p USING(person_uuid) WHERE '+clause,args).fetchall()
        ids=[r['contract_uuid'] for r in rows]
        # Chunked predicates also support SQLite's bounded parameter counts.
        current={};permitted=set()
        for start in range(0,len(ids),200):
            current.update(states(conn,ids[start:start+200]));permitted.update(allowed_slots(conn,principal,ids[start:start+200]))
        unassigned=set();ambiguous=set();free_slots=0;people=set();debt=defaultdict(Decimal);unknown_debt=0
        for r in rows:
            cid=r['contract_uuid'];people.add(r['person_uuid'])
            for work in WORK:
                if (cid,work) not in permitted:continue
                s=current[cid,work]
                if s['ambiguous']:ambiguous.add(cid)
                elif s['employee_uuid'] is None:unassigned.add(cid);free_slots+=1
            f=json.loads(r['fields_json']);value=f.get('debt_today',f.get('debt_total'))
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
        roster=targets(conn,principal);staff_ids={r['employee_uuid'] for r in roster}
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
        return dict(as_of=now(),watermark=watermark(conn),month=month,people=len(people),contracts=len(ids),
            unassigned_contracts=len(unassigned),unassigned_slots=free_slots,ambiguous_contracts=len(ambiguous),
            debt={k:format(v,'f') for k,v in debt.items()},unknown_debt=unknown_debt,staff=roster,
            payments=dict(totals={k:format(v,'f') for k,v in payments.items()},known_contracts=known,
                missing_contracts=len(ids)-known,invalid_or_unknown_currency=unknown_payment,source_as_of=sorted(sources),
                attribution='Поступления в текущем управляемом портфеле; закрепление на '+now()),
            facts_attribution='Исходные авторы в текущем кадровом составе; история не ограничена текущими закреплениями. История без достоверного дня не включена.',
            overlapping_staff_totals=True)
