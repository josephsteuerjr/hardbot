"""Captured three-hour reports and durable work, with the original bounded authority.

Snapshots are observations, never reconstructed observations for missed slots.
Background writes run only in the explicitly isolated distribution contour.
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
    if not member or member['role']!=p.role or member['team_id']!=p.team_id:
        raise CRMError('Роль автора задания изменилась.','scope_changed',403)
    active={r[0] for r in conn.execute('SELECT m.employee_uuid FROM crm_memberships m JOIN crm_employees e USING(employee_uuid) '
        'WHERE e.active=1 AND m.team_id=? AND m.starts_at<=? AND (m.ends_at IS NULL OR m.ends_at>?)',(p.team_id,at,at))}
    return replace(p,capabilities=p.capabilities & frozenset(json.loads(member['capabilities_json'])),
                   employee_scope=p.employee_scope & active)


def scope_key(p):
    return hashlib.sha256(canonical([p.employee_uuid,p.team_id,p.role,sorted(p.employee_scope),
                                   sorted(p.capabilities)]).encode()).hexdigest()


def require(p):
    if p.role not in {'manager','owner'}:raise CRMError('Отчёт доступен руководителю.','forbidden',403)
    access.require(p,'analytics')


def capture(p):
    require(p)
    with storage.connect(readonly=True) as conn:
        p=access.refresh_write(conn,p)
        require(p)
    report=three_hour.report(p)
    point=instant(report['as_of']).astimezone(ZoneInfo(p.timezone))
    slot=point.replace(hour=point.hour//3*3,minute=0,second=0,microsecond=0).isoformat()
    key=scope_key(p)
    with storage.connect() as conn:
        storage.begin(conn)
        refreshed=access.refresh_write(conn,p)
        require(refreshed)
        if refreshed.capabilities!=p.capabilities:raise CRMError('Права изменились во время расчёта. Повторите запрос.','scope_changed',403)
        remember_authority(conn,p,'report:'+str(p.actor_uid),'report')
        existing=conn.execute('SELECT snapshot_id FROM crm_report_snapshots WHERE actor_uid=? AND scope_key=? AND slot=?',
                              (p.actor_uid,key,slot)).fetchone()
        if not existing:
            conn.execute('INSERT INTO crm_report_snapshots VALUES(?,?,?,?,?,?)',
                         (str(uuid.uuid4()),p.actor_uid,key,slot,report['as_of'],canonical(report)))
        conn.commit()
    return report


def read(p, *, snapshot_id=None, day=None):
    require(p)
    # GET current also registers the authoritative report schedule. A repeated
    # read cannot rewrite a captured slot or manufacture an earlier observation.
    report=None if snapshot_id else capture(p)
    with storage.connect(readonly=True) as conn:
        key=scope_key(p)
        if snapshot_id:
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
                      sheets=json.loads(r['report_json'])['sheets']) for r in rows]
    return dict(report=report,snapshots=history,selected_snapshot=snapshot_id,day=selected_day,
                schedule_hours=3,history_policy='captured_only')


def tick():
    """One bounded pass. Job commit/step owns the exclusive DB transaction.

    Multiple API processes may race; unique slots and idempotent packs serialize
    their work. A failed authority leaves the pending work visible and intact.
    """
    from . import assignments
    with storage.connect(readonly=True) as conn:
        mode=conn.execute("SELECT value FROM crm_meta WHERE key='distribution_mode'").fetchone()
        if not mode or mode[0]!='isolated':return
        work=[dict(r) for r in conn.execute('SELECT * FROM crm_background_authorities')]
    for item in work:
        try:
            with storage.connect(readonly=True) as conn:
                p=current_authority(conn,item['authority_json'])
                if item['kind']=='assignment':
                    job=conn.execute('SELECT * FROM crm_assignment_jobs WHERE job_id=? AND state=?',
                                      (item['work_id'].removeprefix('assignment:'),'running')).fetchone()
                    if not job:continue
                    job=dict(job)
                else:
                    latest=conn.execute('SELECT MAX(captured_at) FROM crm_report_snapshots WHERE actor_uid=? AND scope_key=?',
                                        (p.actor_uid,scope_key(p))).fetchone()[0]
                    point=instant(now()).astimezone(ZoneInfo(p.timezone))
                    boundary=point.replace(hour=point.hour//3*3,minute=0,second=0,microsecond=0)
                    if latest and instant(latest)>=boundary:continue
            if item['kind']=='assignment':
                assignments.job_step(p,dict(schema_version=1,job_id=job['job_id'],pack_index=job['next_pack']))
            else:capture(p)
        except CRMError:
            # Scope/versions are rechecked in the next pass; never bypass a denial.
            continue
