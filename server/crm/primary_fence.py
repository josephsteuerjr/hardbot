"""Explicit contract cutover: primary keeps readers/import, CRM owns new work.

The primary fence commits BEFORE canonical enablement. A failed activation leaves
old writes closed, never two enabled writers. Projections use a permit inserted and
removed in the same SQLite write transaction, invisible to other writers. No SQL
function is required in old clients. The selected database is never inferred.
"""
from collections import defaultdict
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import uuid

from . import storage
from .contract import CRMError, canonical, digest, now, CALL_KEYS, VISIT_KEYS, PROMISE_KEYS

MESSAGE = 'Работа по этому договору перенесена в CRM. Откройте новую программу; прежний результат сохранён.'
WORK_COLUMNS=('bot_call_at','bot_call_data','bot_visit_at','bot_visit_data','bot_visit_event_at',
              'call_value_log','visit_value_log','app_call_edited_at','app_visit_edited_at')
WORK_COLUMNS=tuple(sorted(set(WORK_COLUMNS)|CALL_KEYS|VISIT_KEYS|PROMISE_KEYS|{
    'call_office_data','office_edit_data','overall_result','soft_next_call_date','soft_extra_phone'}))
DDL = """
CREATE TABLE IF NOT EXISTS crm_primary_guard_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS crm_primary_contracts(
 legacy_id TEXT PRIMARY KEY, contract_uuid TEXT NOT NULL UNIQUE,
 creditor_id TEXT NOT NULL, responsible TEXT NOT NULL, responsible_soft TEXT NOT NULL,
 baseline_visit TEXT NOT NULL, baseline_call TEXT NOT NULL, projected_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS crm_primary_permit(id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS crm_primary_source_baselines(legacy_id TEXT PRIMARY KEY,record_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS crm_primary_source_conflicts(
 legacy_id TEXT NOT NULL, field TEXT NOT NULL, source_value TEXT NOT NULL,
 expected_value TEXT NOT NULL, observed_at TEXT NOT NULL,
 PRIMARY KEY(legacy_id,field,source_value));
"""


@contextmanager
def connect(path=None, *, readonly=False):
    value=path or os.environ.get('HARD_CRM_PRIMARY_DB')
    if not value or not Path(value).is_absolute() or not Path(value).is_file():
        raise CRMError('Не задана существующая база прежней системы.','primary_fence_unavailable',503)
    conn=sqlite3.connect(Path(value).resolve().as_uri()+('?mode=ro' if readonly else '?mode=rw'),uri=True,timeout=10)
    conn.row_factory=sqlite3.Row
    try:
        with conn:yield conn
    finally:conn.close()


def installed(conn):
    if getattr(conn, "dialect", None) == "postgresql":
        return conn.execute("SELECT to_regclass('crm_primary_guard_meta')").fetchone()[0] is not None
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='crm_primary_guard_meta'").fetchone())


@contextmanager
def projection(conn):
    """Caller owns BEGIN/commit; a crash rolls the permit back with the writes."""
    if not installed(conn):
        yield
        return
    if not conn.in_transaction:raise RuntimeError('Projection requires a write transaction')
    if conn.execute('SELECT 1 FROM crm_primary_permit').fetchone():
        raise CRMError('Незавершённое разрешение проекции.','primary_fence_invalid',503)
    token=uuid.uuid4().hex
    conn.execute('INSERT INTO crm_primary_permit VALUES(?)',(token,))
    try:yield
    finally:conn.execute('DELETE FROM crm_primary_permit WHERE id=?',(token,))


def _install(conn):
    for sql in DDL.split(';'):
        if sql.strip():conn.execute(sql)
    if conn.execute('SELECT 1 FROM crm_primary_permit').fetchone():
        raise CRMError('Защита прежней базы требует сверки.','primary_fence_invalid',503)
    tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    names=[]
    # Source registry/import tables stay writable. These are user work journals.
    for table in ('calls','visits','receipts','reported_payments','photos','photo_geo','notes','payment_evidence'):
        if table not in tables:continue
        columns={r[1] for r in conn.execute('PRAGMA table_info('+table+')')}
        if 'contract_id' not in columns:continue
        for action in ('INSERT','UPDATE','DELETE'):
            name='crm_primary_guard_'+table+'_'+action.lower();names.append(name)
            refs=['OLD'] if action=='DELETE' else ['NEW'] if action=='INSERT' else ['OLD','NEW']
            scope=' OR '.join('EXISTS(SELECT 1 FROM crm_primary_contracts WHERE legacy_id='+r+'.contract_id)' for r in refs)
            if 'contracts_json' in columns:
                scope+=' OR '+' OR '.join("EXISTS(SELECT 1 FROM json_each(CASE WHEN json_valid("+r+".contracts_json) THEN "+r+".contracts_json ELSE '[]' END) j JOIN crm_primary_contracts f ON f.legacy_id=j.value)" for r in refs)
            conn.execute(f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE {action} ON {table} "
                f"WHEN ({scope}) AND NOT EXISTS(SELECT 1 FROM crm_primary_permit) "
                f"BEGIN SELECT RAISE(ABORT,'{MESSAGE}'); END")
    for action,ref in [('DELETE','OLD'),('INSERT','NEW')]:
        name='crm_primary_guard_clients_'+action.lower();names.append(name)
        conn.execute(f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE {action} ON clients "
            f"WHEN EXISTS(SELECT 1 FROM crm_primary_contracts WHERE legacy_id={ref}.contract_id) "
            f"AND NOT EXISTS(SELECT 1 FROM crm_primary_permit) BEGIN SELECT RAISE(ABORT,'{MESSAGE}'); END")
    columns={r[1] for r in conn.execute('PRAGMA table_info(clients)')}
    name='crm_primary_guard_identity';names.append(name)
    conn.execute(f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE UPDATE OF contract_id,partner ON clients "
        "WHEN (NEW.contract_id IS NOT OLD.contract_id OR NEW.partner IS NOT OLD.partner) "
        "AND EXISTS(SELECT 1 FROM crm_primary_contracts WHERE legacy_id IN (OLD.contract_id,NEW.contract_id)) "
        f"BEGIN SELECT RAISE(ABORT,'{MESSAGE}'); END")
    protected=[k for k in WORK_COLUMNS if k in columns]
    if protected:
        name='crm_primary_guard_work_fields';names.append(name)
        changed=' OR '.join('NEW.'+k+' IS NOT OLD.'+k for k in protected)
        conn.execute(f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE UPDATE OF {','.join(protected)} ON clients "
            f"WHEN ({changed}) AND EXISTS(SELECT 1 FROM crm_primary_contracts WHERE legacy_id=OLD.contract_id) "
            f"AND NOT EXISTS(SELECT 1 FROM crm_primary_permit) BEGIN SELECT RAISE(ABORT,'{MESSAGE}'); END")
    for field,baseline in [('responsible','baseline_visit'),('responsible_soft','baseline_call')]:
        name='crm_primary_guard_'+field;names.append(name)
        conn.execute(f"""CREATE TRIGGER IF NOT EXISTS {name} AFTER UPDATE OF {field} ON clients
          WHEN EXISTS(SELECT 1 FROM crm_primary_contracts WHERE legacy_id=NEW.contract_id
            AND {field}!=COALESCE(NEW.{field},'')) AND NOT EXISTS(SELECT 1 FROM crm_primary_permit)
          BEGIN
            INSERT INTO crm_primary_source_conflicts
              SELECT legacy_id,'{field}',COALESCE(NEW.{field},''),{field},strftime('%Y-%m-%dT%H:%M:%fZ','now')
              FROM crm_primary_contracts WHERE legacy_id=NEW.contract_id AND {baseline}!=COALESCE(NEW.{field},'')
              ON CONFLICT(legacy_id,field,source_value) DO NOTHING;
            UPDATE clients SET {field}=(SELECT {field} FROM crm_primary_contracts WHERE legacy_id=NEW.contract_id)
              WHERE contract_id=NEW.contract_id;
          END""")
    conn.execute("INSERT INTO crm_primary_guard_meta VALUES('triggers',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(canonical(sorted(names)),))
    definitions={r[0]:r[1] for r in conn.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'") if r[0] in names}
    conn.execute("INSERT INTO crm_primary_guard_meta VALUES('trigger_digest',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(digest(definitions),))


def _validate(primary, fence_id, instance_id):
    if not installed(primary):raise CRMError('Защита прежних писателей не установлена.','primary_fence_unavailable',503)
    meta=dict(primary.execute('SELECT key,value FROM crm_primary_guard_meta'))
    triggers={r[0]:r[1] for r in primary.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'")}
    required=set(json.loads(meta.get('triggers','[]')))
    if (meta.get('fence_id')!=fence_id or meta.get('canonical_instance')!=instance_id or not required or not required<=triggers.keys()
        or meta.get('trigger_digest')!=digest({k:triggers[k] for k in required if k in triggers})
        or primary.execute('SELECT 1 FROM crm_primary_permit').fetchone()):
        raise CRMError('Защита прежних писателей не подтверждена.','primary_fence_invalid',503)


def ready(conn):
    from registry_postgres import active
    if active(conn):return True
    row=conn.execute("SELECT value FROM crm_meta WHERE key='primary_fence_id'").fetchone()
    if not row:return False
    instance=conn.execute("SELECT value FROM crm_meta WHERE key='instance_id'").fetchone()
    if not instance:return False
    try:
        with connect(readonly=True) as primary:_validate(primary,row[0],instance[0])
        return True
    except (CRMError,sqlite3.Error,OSError):return False


def _native_contracts(conn, ids):
    return {r[0] for r in conn.execute("""
        SELECT c.contract_uuid FROM public.crm_contracts c JOIN crm_registry.clients r
          ON r.contract_id=c.legacy_id AND c.creditor_id='registry-partner:' || trim(r.partner)
         WHERE c.contract_uuid=ANY(?)
        """, (sorted(ids),))}


def writable_contracts(conn, ids):
    """Batch availability for read DTOs; commands still verify both databases."""
    ids=set(ids)
    mode=conn.execute("SELECT value FROM crm_meta WHERE key='distribution_mode'").fetchone()
    if not mode or mode[0]!='cutover':return ids
    from registry_postgres import active
    if active(conn):return _native_contracts(conn, ids)
    if not ready(conn):return set()
    result=set();ordered=sorted(ids)
    for start in range(0,len(ordered),200):
        batch=ordered[start:start+200]
        result.update(r[0] for r in conn.execute('SELECT contract_uuid FROM crm_primary_fences WHERE '
            "fence_id=(SELECT value FROM crm_meta WHERE key='primary_fence_id') AND contract_uuid IN ("
            +','.join('?' for _ in batch)+')',batch))
    return result


def require_contracts(conn, ids):
    mode=conn.execute("SELECT value FROM crm_meta WHERE key='distribution_mode'").fetchone()
    if not mode or mode[0]!='cutover':return
    from registry_postgres import active
    if active(conn):
        if _native_contracts(conn, ids) != set(ids):
            raise CRMError("Договор отсутствует в рабочем реестре PostgreSQL.", "cutover_unmapped", 409)
        return
    if not ready(conn):raise CRMError('Защита прежней системы недоступна.','primary_fence_unavailable',503)
    fence_id=conn.execute("SELECT value FROM crm_meta WHERE key='primary_fence_id'").fetchone()[0]
    ids=sorted(set(ids))
    with connect(readonly=True) as primary:
        for start in range(0,len(ids),200):
            batch=ids[start:start+200];marks=','.join('?' for _ in batch)
            rows={r['contract_uuid']:r for r in conn.execute('SELECT c.contract_uuid,c.legacy_id,f.fence_id FROM crm_contracts c '
                'LEFT JOIN crm_primary_fences f USING(contract_uuid) WHERE c.contract_uuid IN ('+marks+')',batch)}
            bound=dict(primary.execute('SELECT contract_uuid,legacy_id FROM crm_primary_contracts WHERE contract_uuid IN ('+marks+')',batch))
            if any(cid not in rows or rows[cid]['fence_id']!=fence_id or bound.get(cid)!=rows[cid]['legacy_id'] for cid in batch):
                raise CRMError('Договор ещё не переведён в CRM.','contract_not_cutover',409)


def check_old_work(conn, ids):
    if installed(conn) and any(conn.execute('SELECT 1 FROM crm_primary_contracts WHERE legacy_id=?',(cid,)).fetchone() for cid in ids):
        raise ValueError(MESSAGE)


def import_rows(conn, grouped, *, monthly_raw=None, monthly_columns=(), extra_columns=None, extra_values=None):
    """Consume cutover rows before old work-history reconciliation; keep source data."""
    if not installed(conn):return dict(updated=0,extra_cells=0)
    import db, employee_workspace, workday
    enrolled={r[0] for r in conn.execute('SELECT legacy_id FROM crm_primary_contracts')}
    count=0;extras=0
    for cid in list(grouped):
        if cid not in enrolled:continue
        data=grouped.pop(cid)
        current=conn.execute('SELECT * FROM clients WHERE contract_id=?',(cid,)).fetchone()
        if not current:raise CRMError('Договор исчез из прежней базы.','cutover_identity_changed',409)
        if 'partner' in data and str(data['partner'] or '').strip()!=str(current['partner'] or '').strip():
            raise ValueError('Кредитор договора CRM изменился. Требуется сверка личности договора.')
        baseline=conn.execute('SELECT record_json FROM crm_primary_source_baselines WHERE legacy_id=?',(cid,)).fetchone()
        if not baseline:raise CRMError('Исходный снимок договора не подтверждён.','primary_fence_invalid',503)
        baseline=json.loads(baseline[0]);text=lambda value:'' if value is None else str(value)
        for key in set(data)&set(WORK_COLUMNS)&set(current.keys()):
            incoming=text(data[key]);expected=text(current[key])
            if incoming not in {text(baseline.get(key)),expected}:
                conn.execute('INSERT INTO crm_primary_source_conflicts VALUES(?,?,?,?,?) ON CONFLICT(legacy_id,field,source_value) DO NOTHING',
                    (cid,key,incoming,expected,now()))
        values={k:v for k,v in data.items() if k!='contract_id' and k not in WORK_COLUMNS}
        if values:
            conn.execute('UPDATE clients SET '+','.join('"'+k+'"=?' for k in values)+' WHERE contract_id=?',(*values.values(),cid))
        if monthly_columns:
            employee_workspace.remember_monthly(conn,cid,monthly_raw[cid],monthly_columns,current['partner'])
        if extra_columns:
            slot_values=(extra_values or {}).get(cid) or {}
            slots=set(slot_values)
            if slots:
                conn.execute('DELETE FROM client_extra WHERE contract_id=? AND slot NOT IN ('+','.join('?' for _ in slots)+')',(cid,*slots))
            else:conn.execute('DELETE FROM client_extra WHERE contract_id=?',(cid,))
            for item in extra_columns.values():
                value=slot_values.get(db._extra_slot(item['title']))
                if not value:continue
                conn.execute('INSERT INTO client_extra(contract_id,slot,title,label,value,updated_at) VALUES(?,?,?,?,?,?) '
                    'ON CONFLICT(contract_id,slot) DO UPDATE SET title=excluded.title,label=excluded.label,value=excluded.value,updated_at=excluded.updated_at',
                    (cid,db._extra_slot(item['title']),item['title'],item['label'],value,workday.today().isoformat()))
                extras+=1
        count+=1
    return dict(updated=count,extra_cells=extras)


def source_removal(conn,cid,field):
    """An authoritative old file cannot revoke a canonical assignment implicitly."""
    if not installed(conn):return False
    row=conn.execute('SELECT responsible,responsible_soft FROM crm_primary_contracts WHERE legacy_id=?',(cid,)).fetchone()
    if not row:return False
    conn.execute('INSERT INTO crm_primary_source_conflicts VALUES(?,?,?,?,?) ON CONFLICT(legacy_id,field,source_value) DO NOTHING',
        (cid,field,'<absent from authoritative source>',row[field],now()))
    return True


ASSIGNMENT_FIELDS = {'call': ('responsible_soft', 'soft'), 'visit': ('responsible', 'hard')}
NO_ASSIGNEE = {'', 'не закреплен', 'нет', 'не назначен'}


def _label(value):
    return ' '.join(str(value or '').casefold().replace('ё', 'е').split())


def _primary_aliases(conn, primary):
    aliases = defaultdict(set)
    declared = defaultdict(set)
    staff = {}
    def add(employee, name, products=('soft', 'hard')):
        if _label(name) not in NO_ASSIGNEE:
            for product in products:
                aliases[product, _label(name)].add(employee)
    for row in conn.execute('SELECT employee_uuid,staff_id,name FROM crm_employees'):
        staff[row['staff_id']] = row['employee_uuid']
        add(row['employee_uuid'], row['name'])
    tables = {row[0] for row in primary.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if {'employee_identities', 'staff_roster'} <= tables:
        for row in primary.execute('SELECT i.id,i.display_name,s.product_names,s.active FROM employee_identities i '
                                   'LEFT JOIN staff_roster s ON i.id=s.employee_id'):
            employee = staff.get('authority-staff:' + str(row['id']))
            if employee:
                add(employee, row['display_name'])
                for product, value in json.loads(row['product_names'] or '{}').items():
                    if product in {'soft', 'hard'}:
                        add(employee, value, (product,))
                        if row['active'] and _label(value) not in NO_ASSIGNEE:
                            declared[product, _label(value)].add(employee)
    if 'employee_aliases' in tables:
        for row in primary.execute('SELECT employee_id,name FROM employee_aliases'):
            employee = staff.get('authority-staff:' + str(row['employee_id']))
            if employee:
                add(employee, row['name'])
    if 'accounts' in tables:
        actors = {row[0]: row[1] for row in conn.execute(
            'SELECT l.actor_uid,l.employee_uuid FROM crm_actor_links l JOIN crm_employees e USING(employee_uuid) '
            'WHERE l.active=1 AND e.active=1')}
        for row in primary.execute("SELECT telegram_id,employee FROM accounts WHERE status='active' AND role='agent'"):
            employee = actors.get(row['telegram_id'])
            if employee:
                add(employee, row['employee'])
    # The approved spelling for a product is more specific than a display
    # name or older generic alias. Multiple declarations still require review.
    aliases.update(declared)
    return aliases


def _projection_rows(conn, ids, *, primary=None, aliases=None):
    from .assignments import states
    values=states(conn,ids);names={r[0]:r[1] for r in conn.execute('SELECT employee_uuid,name FROM crm_employees')}
    rows=[]
    records={r['contract_uuid']:r for r in conn.execute('SELECT contract_uuid,legacy_id,creditor_id FROM crm_contracts WHERE contract_uuid IN ('+','.join('?' for _ in ids)+')',ids)}
    for cid in ids:
        row=records.get(cid)
        if not row or not row['legacy_id']:raise CRMError('Договор не сопоставлен с прежним реестром.','cutover_unmapped',409)
        item=dict(row)
        if primary is not None:
            current = primary.execute('SELECT partner,responsible,responsible_soft FROM clients WHERE contract_id=?',
                                      (row['legacy_id'],)).fetchone()
            if not current or 'registry-partner:'+str(current['partner'] or '').strip() != row['creditor_id']:
                raise CRMError('Изменилась личность договора в прежнем реестре.','cutover_identity_changed',409)
            item['primary_assignments'] = {field: current[field] or '' for field, _ in ASSIGNMENT_FIELDS.values()}
            item['assignment_employee_uuids'] = {}
            item['ambiguous_spellings'] = []
        for work, (field, product) in ASSIGNMENT_FIELDS.items():
            state=values[cid,work]
            if state['ambiguous']:raise CRMError('Неоднозначное исходное назначение.','cutover_ambiguous',409)
            employee = state['employee_uuid']
            item[field] = names[employee] if employee else ''
            if primary is not None:
                item['assignment_employee_uuids'][field] = employee
                existing = item['primary_assignments'][field]
                choices = aliases.get((product, _label(existing)), set())
                if choices and choices != {employee}:
                    item['ambiguous_spellings'].append(field)
                if ((employee and choices == {employee})
                        or (not employee and _label(existing) in NO_ASSIGNEE)):
                    item[field] = existing
        rows.append(item)
    return rows


def _cutover_rows(conn, primary, ids):
    aliases = _primary_aliases(conn, primary)
    rows = []
    for start in range(0, len(ids), 200):
        rows.extend(_projection_rows(conn, ids[start:start + 200], primary=primary, aliases=aliases))
    return rows


def cutover_review(rows):
    differences = {
        field: sum(row[field] != row['primary_assignments'][field] or field in row['ambiguous_spellings'] for row in rows)
        for field, _ in ASSIGNMENT_FIELDS.values()
    }
    return dict(assignment_differences=differences, selection_sha256=digest(rows))


def _project(primary, rows):
    with projection(primary):
        for row in rows:
            raw=primary.execute('SELECT partner FROM clients WHERE contract_id=?',(row['legacy_id'],)).fetchone()
            if not raw or 'registry-partner:'+str(raw['partner'] or '').strip()!=row['creditor_id']:
                raise CRMError('Изменилась личность договора в прежнем реестре.','cutover_identity_changed',409)
            primary.execute('UPDATE crm_primary_contracts SET responsible=?,responsible_soft=?,projected_at=? WHERE contract_uuid=?',
                (row['responsible'],row['responsible_soft'],now(),row['contract_uuid']))
            primary.execute('UPDATE clients SET responsible=?,responsible_soft=? WHERE contract_id=?',
                (row['responsible'],row['responsible_soft'],row['legacy_id']))


def activate(ids, *, expected_sha256=None):
    """Operator-selected exact scope; restart-safe activation, no implicit expansion."""
    ids=sorted(set(ids))
    if not ids or len(ids)>100000:raise CRMError('Требуется явный состав перехода.')
    with storage.connect() as conn:
        storage.begin(conn)
        prior=conn.execute("SELECT value FROM crm_meta WHERE key='primary_fence_id'").fetchone()
        instance=conn.execute("SELECT value FROM crm_meta WHERE key='instance_id'").fetchone()
        if not instance:raise CRMError('Сначала выполните миграцию CRM.','schema_mismatch',409)
        fence_id=prior[0] if prior else str(uuid.uuid4())
        with connect() as primary:
            primary.execute('BEGIN IMMEDIATE')
            rows = _cutover_rows(conn, primary, ids)
            if expected_sha256 is not None:
                review = cutover_review(rows)
                if review['selection_sha256'] != expected_sha256:
                    raise CRMError('Состав или назначения изменились после сверки.','review_stale',409)
                if any(review['assignment_differences'].values()):
                    raise CRMError('Сверьте назначения перед включением CRM.','cutover_assignment_drift',409)
            if installed(primary):
                bound=primary.execute("SELECT value FROM crm_primary_guard_meta WHERE key='fence_id'").fetchone()
                if bound:_validate(primary,bound[0],instance[0])
            _install(primary)
            existing=primary.execute("SELECT value FROM crm_primary_guard_meta WHERE key='fence_id'").fetchone()
            if existing:
                if prior and existing[0]!=fence_id:raise CRMError('База уже связана с другой CRM.','cutover_identity_changed',409)
                fence_id=existing[0]  # recover after primary commit, before CRM commit
            bound=primary.execute("SELECT value FROM crm_primary_guard_meta WHERE key='canonical_instance'").fetchone()
            if bound and bound[0]!=instance[0]:raise CRMError('Прежняя база связана с другой CRM.','cutover_identity_changed',409)
            primary.execute("INSERT INTO crm_primary_guard_meta VALUES('canonical_instance',?) ON CONFLICT(key) DO NOTHING",(instance[0],))
            primary.execute("INSERT INTO crm_primary_guard_meta VALUES('fence_id',?) ON CONFLICT(key) DO NOTHING",(fence_id,))
            for row in rows:
                raw=primary.execute('SELECT * FROM clients WHERE contract_id=?',(row['legacy_id'],)).fetchone()
                if not raw:raise CRMError('Исходный договор отсутствует.','cutover_unmapped',409)
                primary.execute('INSERT INTO crm_primary_source_baselines VALUES(?,?) ON CONFLICT(legacy_id) DO NOTHING',
                    (row['legacy_id'],canonical({k:raw[k] for k in WORK_COLUMNS if k in raw.keys()})))
                primary.execute('INSERT INTO crm_primary_contracts VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(legacy_id) DO NOTHING',
                    (row['legacy_id'],row['contract_uuid'],row['creditor_id'],row['responsible'],row['responsible_soft'],raw['responsible'] or '',raw['responsible_soft'] or '',now()))
                bound=primary.execute('SELECT contract_uuid FROM crm_primary_contracts WHERE legacy_id=?',(row['legacy_id'],)).fetchone()
                if bound[0]!=row['contract_uuid']:raise CRMError('Договор связан с другой личностью.','cutover_identity_changed',409)
            _project(primary,rows)
        for row in rows:
            conn.execute('INSERT INTO crm_primary_fences VALUES(?,?,?) ON CONFLICT(contract_uuid) DO NOTHING',(row['contract_uuid'],fence_id,now()))
        for key,value in [('primary_fence_id',fence_id),('distribution_mode','cutover')]:
            conn.execute('INSERT INTO crm_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,value))
    return dict(fence_id=fence_id,contracts=len(ids),old_work_writes='closed',source_import='retained')


def project_pending(limit=200):
    from registry_postgres import active
    with storage.connect() as conn:
        if active(conn):return dict(projected=0,ready=True)
        storage.begin(conn)
        if not ready(conn):return dict(projected=0,ready=False)
        ids=[r[0] for r in conn.execute('SELECT contract_uuid FROM crm_primary_pending ORDER BY changed_at,contract_uuid LIMIT ?',(limit,))]
        if not ids:return dict(projected=0,ready=True)
        require_contracts(conn,ids)
        with connect() as primary:
            primary.execute('BEGIN IMMEDIATE')
            _project(primary,_cutover_rows(conn,primary,ids))
        conn.executemany('DELETE FROM crm_primary_pending WHERE contract_uuid=?',[(cid,) for cid in ids])
        return dict(projected=len(ids),ready=True)


def sync_source_conflicts(limit=200):
    """Copy the append-only source journal in bounded, restartable batches."""
    from registry_postgres import active
    with storage.connect() as conn:
        if active(conn):return 0
        storage.begin(conn)
        state=conn.execute("SELECT detail_json FROM crm_runtime_status WHERE worker='source-conflicts'").fetchone()
        cursor=json.loads(state[0]).get('cursor',0) if state else 0
        with connect(readonly=True) as primary:
            if not installed(primary):return 0
            rows=[dict(r) for r in primary.execute('SELECT s.rowid AS source_sequence,s.*,f.contract_uuid '
                'FROM crm_primary_source_conflicts s JOIN crm_primary_contracts f USING(legacy_id) '
                'WHERE s.rowid>? ORDER BY s.rowid LIMIT ?',(cursor,limit))]
        for row in rows:
            cursor=row.pop('source_sequence')
            key=row['contract_uuid']+':'+row['field']
            conn.execute('INSERT INTO crm_migration_issues VALUES(?,?,?,?,NULL,NULL) ON CONFLICT(issue_id) DO NOTHING',
                ('primary:'+digest([key,row['source_value']]),'primary_assignment_conflict' if row['field'] in {'responsible','responsible_soft'} else 'primary_work_conflict',key,canonical(row)))
        if rows:
            conn.execute('INSERT INTO crm_runtime_status VALUES(?,?,?,?,?) ON CONFLICT(worker) DO UPDATE SET '
                'heartbeat_at=excluded.heartbeat_at,ok=excluded.ok,code=excluded.code,detail_json=excluded.detail_json',
                ('source-conflicts',now(),1,'',canonical(dict(cursor=cursor))))
    return len(rows)
