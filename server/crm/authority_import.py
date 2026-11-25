"""Operator-only initial reconciliation of a verified business/roster snapshot.

There is no HTTP entry. A reviewed preview is bound to the complete target
fingerprint. Existing canonical work blocks baseline replacement. Source rows
and legacy events keep their original evidence; no login is created here.
"""
import hashlib,json,re,uuid
from datetime import date
from zoneinfo import ZoneInfo
from . import schema,storage
from .contract import CRMError,canonical,digest,identifier,instant,now
from .shared_auth import NAMESPACE,ROLE_CAPABILITIES

TABLES={'crm_people':'person_uuid','crm_contracts':'contract_uuid','crm_employees':'employee_uuid',
        'crm_actor_links':'actor_uid','crm_memberships':'membership_id',
        'crm_assignments':'assignment_id','crm_events':'sequence'}
OPTIONAL={'crm_monthly_totals':'contract_uuid,month','crm_authority_rows':'contract_uuid','crm_authority_scopes':'actor_uid'}


def fingerprint(conn):
    result=hashlib.sha256();counts={}
    for table,key in {**TABLES,**OPTIONAL}.items():
        result.update(table.encode());count=0
        if table in OPTIONAL:
            exists=conn.execute('SELECT to_regclass(?)',(table,)).fetchone()[0] if storage.is_postgres(conn) else conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()
            if not exists:counts[table]=0;continue
        for row in conn.execute('SELECT * FROM '+table+' ORDER BY '+key):
            result.update(canonical(dict(row)).encode());result.update(b'\n');count+=1
        counts[table]=count
    return result.hexdigest(),counts


def validate(plan):
    required={'namespace','source_sha256','captured_at','employees','actors','contracts','assignments','events','monthly','scopes'}
    if not isinstance(plan,dict) or set(plan)!=required or plan['namespace']!=NAMESPACE:
        raise CRMError('Нужен точный план сверки общей копии.')
    if not re.fullmatch('[a-f0-9]{64}',plan['source_sha256']):raise CRMError('Не подтверждён источник.')
    age=(instant(now())-instant(plan['captured_at'])).total_seconds()
    if not -60<=age<=86400:raise CRMError('Нужен свежий срез источника.')
    for name in ('employees','actors','contracts','assignments','events','monthly','scopes'):
        if not isinstance(plan[name],list):raise CRMError('Некорректная группа '+name)
    if len(plan['contracts'])>100000 or len(plan['events'])>1000000 or len(plan['employees'])>2000:
        raise CRMError('Размер источника превышает предел сверки.')
    staff=set();uids=set();contracts=set();sources=set()
    for row in plan['employees']:
        if row['employee_uuid'] in staff:raise CRMError('Повтор кадровой личности.')
        uuid.UUID(row['employee_uuid']);ZoneInfo(row['timezone']);staff.add(row['employee_uuid'])
        if row['role'] not in ROLE_CAPABILITIES or not set(row['capabilities'])<=ROLE_CAPABILITIES[row['role']]:
            raise CRMError('Полномочия превышают роль источника.')
        if row['active'] not in (0,1) or not row['evidence'] or not row['staff_id']:raise CRMError('Не подтверждена личность.')
    for row in plan['actors']:
        if type(row['actor_uid']) is not int or not 0<abs(row['actor_uid'])<2**63 or row['actor_uid'] in uids:
            raise CRMError('Неоднозначное соответствие действующего входа.')
        if row['employee_uuid'] not in staff or row['active'] not in (0,1):raise CRMError('Вход не связан с кадровой личностью.')
        uids.add(row['actor_uid'])
    scope_uids=set()
    for row in plan['scopes']:
        if row['actor_uid'] not in uids or row['actor_uid'] in scope_uids or not set(row['employee_ids'])<=staff or row['all_contracts'] not in (0,1):
            raise CRMError('Не подтверждена область руководителя или аналитика.')
        scope_uids.add(row['actor_uid'])
    for row in plan['contracts']:
        uuid.UUID(row['contract_uuid']);uuid.UUID(row['person_uuid'])
        if row['contract_uuid'] in contracts or not row['creditor_id'] or not row['external_id']:
            raise CRMError('Повтор или неполная идентичность договора.')
        if not isinstance(row['fields'],dict):raise CRMError('Нет полей договора.')
        contracts.add(row['contract_uuid'])
    for row in plan['assignments']:
        if row['employee_uuid'] not in staff or row['contract_uuid'] not in contracts or row['capability'] not in {'call','visit','delivery'}:
            raise CRMError('Назначение не связано с точной личностью и договором.')
        instant(row['starts_at'])
        if row.get('ends_at'):instant(row['ends_at'])
    for row in plan['events']:
        if row['legacy_source'] in sources or row['kind'] not in {'call','visit'} or not set(row['contract_ids'])<=contracts:
            raise CRMError('Неоднозначная рабочая история.')
        sources.add(row['legacy_source']);instant(row['occurred_at']);instant(row['received_at'])
        if row['time_quality'] not in {'legacy_recorded','legacy_day','legacy_unknown'}:raise CRMError('Неизвестный источник времени истории.')
        if row['time_quality']=='legacy_day':
            try:date.fromisoformat(row['payload']['legacy_workday'])
            except (KeyError,TypeError,ValueError):raise CRMError('Не подтверждён рабочий день истории.')
    for row in plan['monthly']:
        if row['contract_uuid'] not in contracts or not re.fullmatch(r'\d{4}-\d{2}',row['month']):
            raise CRMError('Месячная сумма не связана с договором и месяцем.')
    return digest(plan)


def preview(plan):
    plan_hash=validate(plan)
    with storage.connect(readonly=True) as conn:
        marker=conn.execute("SELECT value FROM crm_meta WHERE key='deployment_namespace'").fetchone()
        if not marker or marker[0]!=NAMESPACE:raise CRMError('Другой контур базы.')
        before,counts=fingerprint(conn)
        if counts['crm_events']:raise CRMError('В общей копии уже есть работа. Требуется отдельная сверка, без замены исходных состояний.','canonical_work_exists',409)
    return dict(plan_sha256=plan_hash,before_sha256=before,before_counts=counts,
        source_sha256=plan['source_sha256'],captured_at=plan['captured_at'],
        proposed={key:len(plan[key]) for key in ('employees','actors','contracts','assignments','events','monthly','scopes')})


def apply(plan,review):
    plan_hash=validate(plan);at=now()
    with storage.connect() as conn:
        storage.begin(conn)
        # Schema upgrade is additive and remains in this transaction.
        schema.initialize(conn)
        previous=conn.execute('SELECT * FROM crm_authority_imports WHERE source_sha256=?',(plan['source_sha256'],)).fetchone()
        if previous:
            if previous['plan_sha256']!=plan_hash:raise CRMError('Источник уже применён с другим планом.')
            return json.loads(previous['receipt_json'])
        if conn.execute('SELECT 1 FROM crm_assignment_heads LIMIT 1').fetchone():
            raise CRMError('После интерактивного распределения исходный baseline не заменяется. Используйте трёхсторонний обмен.','native_assignments_exist',409)
        before,counts=fingerprint(conn)
        if counts['crm_events'] or review.get('before_sha256')!=before or review.get('plan_sha256')!=plan_hash:
            raise CRMError('План или общая база изменились после просмотра.','review_stale',409)
        for row in plan['employees']:
            current=conn.execute('SELECT staff_id FROM crm_employees WHERE employee_uuid=?',(row['employee_uuid'],)).fetchone()
            if current and current['staff_id']!=row['staff_id']:raise CRMError('Изменена стабильная кадровая идентичность.')
            conn.execute('INSERT INTO crm_employees VALUES(?,?,?,?,?) ON CONFLICT(employee_uuid) DO UPDATE SET name=excluded.name,source=excluded.source,active=excluded.active',
                (row['employee_uuid'],row['staff_id'],row['name'],row['evidence'],row['active']))
            # Close old membership intervals explicitly; keep their history.
            membership=conn.execute('SELECT * FROM crm_memberships WHERE employee_uuid=? AND ends_at IS NULL ORDER BY starts_at DESC',(row['employee_uuid'],)).fetchone()
            caps=canonical(sorted(row['capabilities']))
            if not membership or (membership['team_id'],membership['role'],membership['capabilities_json'],membership['timezone'])!=(row['team_id'],row['role'],caps,row['timezone']):
                conn.execute('UPDATE crm_memberships SET ends_at=? WHERE employee_uuid=? AND ends_at IS NULL',(at,row['employee_uuid']))
                conn.execute('INSERT INTO crm_memberships VALUES(?,?,?,?,?,?,?,NULL)',
                    (identifier('membership',plan_hash+row['employee_uuid']),row['employee_uuid'],row['team_id'],row['role'],caps,row['timezone'],at))
        for row in plan['actors']:
            old=conn.execute('SELECT employee_uuid FROM crm_actor_links WHERE actor_uid=?',(row['actor_uid'],)).fetchone()
            if old and old[0]!=row['employee_uuid']:raise CRMError('Вход уже связан с другой личностью.')
            conn.execute('INSERT INTO crm_actor_links VALUES(?,?,?,?) ON CONFLICT(actor_uid) DO UPDATE SET evidence=excluded.evidence,active=excluded.active',
                (row['actor_uid'],row['employee_uuid'],row['evidence'],row['active']))
        for row in plan['scopes']:
            conn.execute('INSERT INTO crm_authority_scopes VALUES(?,?,?,?) ON CONFLICT(actor_uid) DO UPDATE SET employees_json=excluded.employees_json,all_contracts=excluded.all_contracts,source_sha256=excluded.source_sha256',
                         (row['actor_uid'],canonical(sorted(row['employee_ids'])),int(row['all_contracts']),plan['source_sha256']))
        for row in plan['contracts']:
            old=conn.execute('SELECT person_uuid,creditor_id,external_id FROM crm_contracts WHERE contract_uuid=?',(row['contract_uuid'],)).fetchone()
            if old and (old['person_uuid'],old['creditor_id'],old['external_id'])!=(row['person_uuid'],row['creditor_id'],row['external_id']):
                raise CRMError('Изменяется идентичность клиента или договора.')
            conn.execute('INSERT INTO crm_people VALUES(?,?,?,?) ON CONFLICT(person_uuid) DO UPDATE SET name=excluded.name',
                (row['person_uuid'],row['client'],'verified-authority-source:'+plan['source_sha256'],at))
            values=canonical(row['fields']);provenance=canonical({'source_baseline':{'captured_at':plan['captured_at'],'source_sha256':plan['source_sha256']}})
            conn.execute('INSERT INTO crm_contracts VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(contract_uuid) DO UPDATE SET legacy_id=excluded.legacy_id,currency=excluded.currency,baseline_json=excluded.baseline_json,fields_json=excluded.fields_json,provenance_json=excluded.provenance_json',
                (row['contract_uuid'],row['person_uuid'],row['creditor_id'],row['external_id'],row['legacy_id'],row['currency'],values,values,'{}',provenance,at))
            if row.get('raw') is not None:
                conn.execute('INSERT INTO crm_authority_rows VALUES(?,?,?) ON CONFLICT(contract_uuid) DO UPDATE SET source_sha256=excluded.source_sha256,record_json=excluded.record_json',
                             (row['contract_uuid'],plan['source_sha256'],canonical(row['raw'])))
        for contract in plan['contracts']:
            if conn.execute('SELECT 1 FROM crm_assignments WHERE contract_uuid=? AND starts_at>? AND ends_at IS NULL',
                            (contract['contract_uuid'],plan['captured_at'])).fetchone():
                raise CRMError('После среза уже запланирована ответственность. Нужна отдельная сверка.')
            conn.execute('UPDATE crm_assignments SET ends_at=? WHERE contract_uuid=? AND ends_at IS NULL',
                         (plan['captured_at'],contract['contract_uuid']))
        for row in plan['assignments']:
            evidence='authority-source:'+plan['source_sha256']
            conn.execute('INSERT INTO crm_assignments VALUES(?,?,?,?,?,?,?)',
                (identifier('assignment',plan_hash+canonical(row)),row['employee_uuid'],row['contract_uuid'],row['capability'],row['starts_at'],row.get('ends_at'),evidence))
        for row in plan['events']:
            event=identifier('legacy-event','authority:'+row['legacy_source'])
            conn.execute('INSERT INTO crm_events(event_id,operation_id,request_hash,actor_uid,employee_uuid,team_id,role,kind,origin,contracts_json,payload_json,occurred_at,received_at,time_quality,projection_status,legacy_source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (event,row['operation_id'],row['source_hash'],row['actor_uid'],row['employee_uuid'],'legacy-source','legacy',row['kind'],'legacy',canonical(row['contract_ids']),canonical(row['payload']),row['occurred_at'],row['received_at'],row['time_quality'],'historical_only','authority:'+row['legacy_source']))
            conn.executemany('INSERT INTO crm_event_contracts VALUES(?,?)',[(event,key) for key in row['contract_ids']])
        conn.executemany('INSERT INTO crm_monthly_totals VALUES(?,?,?,?,?,?,?) ON CONFLICT(contract_uuid,month) DO UPDATE SET amount=excluded.amount,currency=excluded.currency,source_column=excluded.source_column,observed_at=excluded.observed_at,source_sha256=excluded.source_sha256',
            [(row['contract_uuid'],row['month'],row['amount'],row['currency'],row['source_column'],row['observed_at'],plan['source_sha256']) for row in plan['monthly']])
        receipt=dict(source_sha256=plan['source_sha256'],plan_sha256=plan_hash,before_sha256=before,captured_at=plan['captured_at'],applied_at=at,
            counts={key:len(plan[key]) for key in ('employees','actors','contracts','assignments','events','monthly','scopes')},logins_created=0,primary_changed=False)
        conn.execute('INSERT INTO crm_authority_imports VALUES(?,?,?,?,?,?)',(plan['source_sha256'],plan['captured_at'],at,plan_hash,before,canonical(receipt)))
        conn.execute("INSERT INTO crm_meta VALUES('authority_snapshot',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(canonical(receipt),))
        conn.commit();return receipt
