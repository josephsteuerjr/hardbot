"""Captured three-hour reports and durable work, with the original bounded authority.

Snapshots are observations, never reconstructed observations for missed slots.
Background writes require isolated mode or a verified primary cutover.
"""
from dataclasses import asdict, replace
import hashlib
import json
import uuid
from zoneinfo import ZoneInfo

from . import access, storage, three_hour
from .contract import CRMError, canonical, instant, now


def remember_authority(conn, principal, work_id, kind):
    value=asdict(principal)
    for key in ('capabilities','employee_scope'):value[key]=sorted(value[key])
    conn.execute('INSERT INTO crm_background_authorities VALUES(?,?,?,?,?) '
                 'ON CONFLICT(work_id) DO UPDATE SET authority_json=excluded.authority_json,updated_at=excluded.updated_at',
                 (work_id,principal.actor_uid,kind,canonical(value),now()))


def current_authority(conn, raw):
    value=json.loads(raw)
    for key in ('capabilities','employee_scope'):value[key]=frozenset(value[key])
    p=access.Principal(**value)
    # Persisting an old authenticated role must never keep a removed manager alive.
    at=now()
    member=conn.execute('SELECT m.* FROM crm_memberships m JOIN crm_actor_links l USING(employee_uuid) '
        'JOIN crm_employees e USING(employee_uuid) WHERE l.actor_uid=? AND l.active=1 AND e.active=1 '
        'AND m.employee_uuid=? AND m.starts_at<=? AND (m.ends_at IS NULL OR m.ends_at>?) ORDER BY m.starts_at DESC',
        (p.actor_uid,p.employee_uuid,at,at)).fetchone()
    reduced_analyst=member and member['role']=='manager' and p.role=='analyst'
    if not member or (member['role']!=p.role and not reduced_analyst) or member['team_id']!=p.team_id:
        raise CRMError('Роль автора задания изменилась.','scope_changed',403)
    active={r[0] for r in conn.execute('SELECT m.employee_uuid FROM crm_memberships m JOIN crm_employees e USING(employee_uuid) '
        'WHERE e.active=1 AND m.team_id=? AND m.starts_at<=? AND (m.ends_at IS NULL OR m.ends_at>?)',(p.team_id,at,at))}
    return replace(p,capabilities=p.capabilities & frozenset(json.loads(member['capabilities_json'])),
                   employee_scope=p.employee_scope & active,timezone=member['timezone'])


def scope_key(p, country='', members=None):
    scope = [p.employee_uuid,p.team_id,p.role,sorted(p.employee_scope),sorted(p.capabilities)]
    if country:
        scope.extend((country, sorted(members)))
    return hashlib.sha256(canonical(scope).encode()).hexdigest()


def require(p):
    if p.role not in {'manager','owner','analyst'}:raise CRMError('Отчёт доступен руководителю или аналитику.','forbidden',403)
    access.require(p,'analytics')


def country_members(conn, p, country):
    if not country:
        return None
    scopes = three_hour.country_scopes(conn, p)
    if country not in scopes:
        raise CRMError('Отчёт по этой стране недоступен.','forbidden',403)
    return scopes[country]


def _capture(p, country, *, register=True):
    require(p)
    with storage.connect(readonly=True) as conn:
        p=access.refresh_write(conn,p)
        require(p)
        members=country_members(conn,p,country)
    report=three_hour.report(p,country=country,members=members)
    point=instant(report['as_of']).astimezone(ZoneInfo(p.timezone))
    slot=point.replace(hour=point.hour//3*3,minute=0,second=0,microsecond=0).isoformat()
    key=scope_key(p,country,members)
    with storage.connect() as conn:
        storage.begin(conn)
        refreshed=access.refresh_write(conn,p)
        require(refreshed)
        if refreshed.capabilities!=p.capabilities:raise CRMError('Права изменились во время расчёта. Повторите запрос.','scope_changed',403)
        if country_members(conn,refreshed,country)!=members:
            raise CRMError('Состав страны изменился во время расчёта. Повторите запрос.','scope_changed',403)
        if register:
            remember_authority(conn,p,'report:'+str(p.actor_uid),'report')
        conn.execute('INSERT INTO crm_report_snapshots VALUES(?,?,?,?,?,?) '
                     'ON CONFLICT(actor_uid,scope_key,slot) DO NOTHING',
                     (str(uuid.uuid4()),p.actor_uid,key,slot,report['as_of'],canonical(report)))
        conn.commit()
    return report, key


def capture(p, country=''):
    return _capture(p, country)[0]


def snapshot_summary(sheets):
    """Dynamics and Excel totals use metrics. Contract details come with the selected snapshot."""
    return [{**sheet, 'rows': [{key: value for key, value in row.items()
                               if key not in {'current_records', 'yesterday_records'}}
                              for row in sheet['rows']]} for sheet in sheets]


def read(p, *, snapshot_id=None, day=None, country='', options=None):
    require(p)
    if options == '1':
        from country_scope import info
        with storage.connect(readonly=True) as conn:
            return {'countries': [info(code) for code in three_hour.country_scopes(conn,p)]}
    # GET current also registers the authoritative report schedule. A repeated
    # read cannot rewrite a captured slot or manufacture an earlier observation.
    report, key = (None, None) if snapshot_id else _capture(p,country)
    with storage.connect(readonly=True) as conn:
        if snapshot_id:
            key=scope_key(p,country,country_members(conn,p,country))
            row=conn.execute('SELECT * FROM crm_report_snapshots WHERE snapshot_id=? AND actor_uid=? AND scope_key=?',
                             (snapshot_id,p.actor_uid,key)).fetchone()
            if not row:raise CRMError('Срез недоступен при текущем составе команды.','snapshot_missing',404)
            report=json.loads(row['report_json'])
        selected_day=day or report['today']
        try:
            from datetime import date
            date.fromisoformat(selected_day)
        except (ValueError,TypeError):raise CRMError('Укажите дату отчёта.')
        rows=conn.execute('SELECT snapshot_id,slot,captured_at,report_json FROM crm_report_snapshots '
            'WHERE actor_uid=? AND scope_key=? AND slot>=? AND slot<? ORDER BY captured_at',
            (p.actor_uid,key,selected_day,selected_day+'T~')).fetchall()
        history=[dict(snapshot_id=r['snapshot_id'],slot=r['slot'],captured_at=r['captured_at'],
                      sheets=snapshot_summary(json.loads(r['report_json'])['sheets'])) for r in rows]
    return dict(report=report,snapshots=history,selected_snapshot=snapshot_id,day=selected_day,
                schedule_hours=3,history_policy='captured_only',country=country)


def pending(conn):
    """Do not poll primary credentials for completed jobs or a captured slot."""
    work=[]
    for row in conn.execute("SELECT a.* FROM crm_background_authorities a LEFT JOIN crm_assignment_jobs j ON a.work_id='assignment:' || j.job_id "
                            "WHERE a.kind='report' OR j.state='running'"):
        item=dict(row)
        if item['kind']=='report':
            value=json.loads(item['authority_json'])
            point=instant(now()).astimezone(ZoneInfo(value['timezone']))
            boundary=point.replace(hour=point.hour//3*3,minute=0,second=0,microsecond=0)
            if instant(item['updated_at'])>=boundary:continue
        work.append(item)
    return work


def tick(verified=None):
    """One bounded pass. Job commit/step owns the exclusive DB transaction.

    Multiple API processes may race; unique slots and idempotent packs serialize
    their work. A failed authority leaves the pending work visible and intact.
    """
    from . import assignments
    with storage.connect(readonly=True) as conn:
        mode=conn.execute("SELECT value FROM crm_meta WHERE key='distribution_mode'").fetchone()
        if not mode or mode[0] not in {'isolated','cutover'}:return
        shared=storage.postgres_enabled() or mode[0]=='cutover'
        if shared and verified is None:return
        work=pending(conn)
    completed=0;blocked=0
    for item in work:
        try:
            with storage.connect(readonly=True) as conn:
                p=current_authority(conn,item['authority_json'])
                if shared:
                    fresh=(verified or {}).get(p.actor_uid)
                    if not fresh or fresh.employee_uuid!=p.employee_uuid or fresh.role!=p.role or fresh.team_id!=p.team_id:
                        blocked+=1;continue
                    p=replace(p,capabilities=p.capabilities & fresh.capabilities,
                              employee_scope=fresh.employee_scope if item['kind']=='report' else p.employee_scope & fresh.employee_scope,
                              all_contracts=p.all_contracts and fresh.all_contracts,
                              read_all_contracts=p.read_all_contracts and fresh.read_all_contracts)
                if item['kind']=='assignment':
                    job=conn.execute('SELECT * FROM crm_assignment_jobs WHERE job_id=? AND state=?',
                                      (item['work_id'].removeprefix('assignment:'),'running')).fetchone()
                    if not job:continue
                    job=dict(job)
                else:
                    scopes = {'': None, **three_hour.country_scopes(conn,p)}
                    point=instant(now()).astimezone(ZoneInfo(p.timezone))
                    boundary=point.replace(hour=point.hour//3*3,minute=0,second=0,microsecond=0)
                    missing=[]
                    for country,members in scopes.items():
                        latest=conn.execute('SELECT MAX(captured_at) FROM crm_report_snapshots WHERE actor_uid=? AND scope_key=?',
                                            (p.actor_uid,scope_key(p,country,members))).fetchone()[0]
                        if not latest or instant(latest)<boundary:
                            missing.append(country)
            if item['kind']=='assignment':
                assignments.job_step(p,dict(schema_version=1,job_id=job['job_id'],pack_index=job['next_pack']))
            else:
                for country in missing:
                    try:capture(p,country)
                    except CRMError as exc:
                        if exc.code!='mixed_currencies':raise
            completed+=1
        except CRMError:
            # Scope/versions are rechecked in the next pass; never bypass a denial.
            blocked+=1
    return dict(completed=completed,blocked=blocked)
