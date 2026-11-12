"""Operator-only incremental exchange with the existing business database.

No HTTP route, account creation or baseline replacement. The source journal is
append-only in CRM. Current fields use a three-way comparison against the last
observed source row; concurrent changes become issues rather than overwrites.
"""
from collections import Counter, defaultdict
from datetime import date
import hashlib
import json
from pathlib import Path
import sqlite3

from . import storage, store
from .contract import CRMError, canonical, digest, identifier, instant, now
from .registry_import import typed_values

DDL = """
CREATE TABLE IF NOT EXISTS crm_legacy_state(
 contract_uuid TEXT PRIMARY KEY REFERENCES crm_contracts,
 record_json TEXT NOT NULL,source_sha256 TEXT NOT NULL,observed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS crm_legacy_batches(
 snapshot_sha256 TEXT PRIMARY KEY,captured_at TEXT NOT NULL,receipt_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS crm_legacy_exports(
 event_id TEXT PRIMARY KEY REFERENCES crm_events(event_id),state TEXT NOT NULL,
 receipt_json TEXT NOT NULL,updated_at TEXT NOT NULL);
"""
SOURCE = 'primary-business-v1'
IDENTITY = {'contract_id', 'partner', 'client'}
RESPONSIBILITY = {'responsible': 'visit', 'responsible_soft': 'call'}


def capture(path):
    """One read-only SQLite transaction. Authentication tables are excluded."""
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA query_only=ON'); conn.execute('BEGIN')
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        result = {'source': SOURCE, 'captured_at': now()}
        for table, key in [('clients','contract_id'), ('calls','id'), ('visits','id'),
                           ('employee_identities','id'), ('employee_aliases','name')]:
            result[table] = [dict(r) for r in conn.execute('SELECT * FROM ' + table + ' ORDER BY ' + key)] if table in tables else []
        result['mirror_links'] = [dict(r) for r in conn.execute('SELECT * FROM crm_exchange_links ORDER BY table_name,row_id')] if 'crm_exchange_links' in tables else []
        return result
    finally:
        conn.close()


def validate(snapshot):
    if snapshot.get('source') != SOURCE or set(snapshot) != {'source','captured_at','clients','calls','visits','employee_identities','employee_aliases','mirror_links'}:
        raise CRMError('Не подтверждён источник обмена.')
    if not -60 <= (instant(now()) - instant(snapshot['captured_at'])).total_seconds() <= 86400:
        raise CRMError('Нужен свежий срез для обмена.')
    for name in ('clients','calls','visits','employee_identities','employee_aliases','mirror_links'):
        if not isinstance(snapshot[name],list): raise CRMError('Некорректный срез обмена.')
    if len(snapshot['clients']) > 100000 or len(snapshot['calls']) + len(snapshot['visits']) > 1000000:
        raise CRMError('Срез превышает предел обмена.')
    for name,key in [('clients','contract_id'),('calls','id'),('visits','id')]:
        ids=[str(r[key]) for r in snapshot[name]]
        if len(set(ids)) != len(ids):raise CRMError('Повтор строки источника.')
    return digest(snapshot)


def _name(value):
    return ' '.join(str(value or '').casefold().replace('ё','е').split())


def _issue(kind, key, detail):
    identity={k:v for k,v in detail.items() if k!='source_sha256'}
    return dict(issue_id=identifier('issue', canonical(['legacy-exchange',kind,key,identity])),
                kind=kind,legacy_key=key,detail_json=canonical(detail))


def _guard(conn, touched):
    """Small commit guard; full planning never holds the writer lock.

    Canonical history is immutable. Its sequence catches regular commands;
    rebuild audit and exchange batches cover maintenance. Exact touched rows
    additionally detect an unjournaled change to a field or source anchor.
    """
    proof=hashlib.sha256()
    queries=[
        "SELECT key,value FROM crm_meta WHERE key IN ('deployment_namespace','schema_version') ORDER BY key",
        'SELECT COUNT(*) AS n,MAX(sequence) AS last FROM crm_events',
        'SELECT COUNT(*) AS n,MAX(audit_id) AS last FROM crm_audit',
        'SELECT COUNT(*) AS n,MAX(captured_at) AS last FROM crm_legacy_batches',
        'SELECT contract_uuid,person_uuid,creditor_id,external_id,legacy_id FROM crm_contracts ORDER BY contract_uuid',
        'SELECT * FROM crm_employees ORDER BY employee_uuid',
        'SELECT * FROM crm_actor_links ORDER BY actor_uid',
        'SELECT * FROM crm_assignments WHERE ends_at IS NULL ORDER BY assignment_id',
    ]
    for query in queries:
        for row in conn.execute(query):proof.update(canonical(dict(row)).encode());proof.update(b'\n')
        proof.update(b'\x00')
    for table in ('crm_contracts','crm_authority_rows','crm_legacy_state'):
        proof.update(table.encode())
        for start in range(0,len(touched),300):
            batch=touched[start:start+300]
            for row in conn.execute('SELECT * FROM '+table+' WHERE contract_uuid IN ('+','.join('?' for _ in batch)+') ORDER BY contract_uuid',batch):
                proof.update(canonical(dict(row)).encode());proof.update(b'\n')
    return proof.hexdigest()


def _read(conn, snapshot, sha):
    marker=conn.execute("SELECT value FROM crm_meta WHERE key='deployment_namespace'").fetchone()
    if not marker or marker[0]!='crm-shared-pilot-v1':raise CRMError('Обмен требует подтверждённого общего контура.')
    contracts={r['contract_uuid']:dict(r) for r in conn.execute('SELECT * FROM crm_contracts WHERE legacy_id IS NOT NULL')}
    legacy={r['legacy_id']:r for r in contracts.values() if r['legacy_id']}
    states={r['contract_uuid']:dict(r) for r in conn.execute('SELECT * FROM crm_legacy_state')}
    originals={r['contract_uuid']:dict(r) for r in conn.execute('SELECT * FROM crm_authority_rows')}
    event_rows=[dict(r) for r in conn.execute('SELECT event_id,request_hash,legacy_source,actor_uid,employee_uuid FROM crm_events ORDER BY event_id')]
    events={r['legacy_source']:r for r in event_rows if r['legacy_source']}
    by_event={r['event_id']:r for r in event_rows}
    employees=[dict(r) for r in conn.execute('SELECT * FROM crm_employees ORDER BY employee_uuid')]
    actors={r['actor_uid']:r['employee_uuid'] for r in conn.execute('SELECT * FROM crm_actor_links')}
    assignments=[dict(r) for r in conn.execute('SELECT * FROM crm_assignments WHERE ends_at IS NULL ORDER BY assignment_id')]
    staff={r['staff_id']:r['employee_uuid'] for r in employees if r['staff_id']}
    names=defaultdict(set)
    for r in employees:names[_name(r['name'])].add(r['employee_uuid'])
    for r in snapshot['employee_identities']:
        eid=staff.get('authority-staff:'+str(r['id']))
        if eid:names[_name(r['display_name'])].add(eid)
    for r in snapshot['employee_aliases']:
        eid=staff.get('authority-staff:'+str(r.get('employee_id')))
        if eid:names[_name(r['name'])].add(eid)
    def person(name,uid=0):
        choices=names.get(_name(name),set())
        if uid in actors and (not choices or actors[uid] in choices):return actors[uid]
        return next(iter(choices)) if len(choices)==1 else None
    patches=[];issues=[];responsibility=[];changed_rows=[];incoming=[]
    known_ids={str(r['contract_id']) for r in snapshot['clients']}
    verified_ids=set()
    for raw in snapshot['clients']:
        cid=str(raw['contract_id']);row=legacy.get(cid)
        if not row:
            issues.append(_issue('legacy_contract_unmapped',cid,{'source_sha256':sha}));continue
        if row['external_id']!=cid or row['creditor_id']!='registry-partner:'+str(raw.get('partner') or '').strip():
            issues.append(_issue('legacy_identity_changed',cid,{'source_sha256':sha}));continue
        verified_ids.add(cid)
        key=row['contract_uuid'];prior=states.get(key) or originals.get(key)
        if not prior:
            issues.append(_issue('legacy_baseline_missing',cid,{'contract_uuid':key}));continue
        old=json.loads(prior['record_json'])
        if digest(old)==digest(raw):continue
        previous,_=typed_values(old);values,invalid=typed_values(raw)
        invalid={r['key'] for r in invalid};current=json.loads(row['fields_json']);patch={}
        for field,value in values.items():
            if value==previous.get(field) or value==current.get(field):continue
            if field in IDENTITY or field in invalid or current.get(field)!=previous.get(field):
                issues.append(_issue('legacy_field_conflict',key+':'+field,
                    dict(contract_uuid=key,field=field,before=previous.get(field),current=current.get(field),incoming=value,source_sha256=sha)))
            else:patch[field]=value
        if patch:patches.append(dict(contract_uuid=key,values=patch))
        for field,capability in RESPONSIBILITY.items():
            if old.get(field)==raw.get(field):continue
            name=raw.get(field);employee=person(name)
            empty=not name or _name(name) in {'не закреплен','нет'}
            if not employee and not empty:issues.append(_issue('legacy_assignee_unmapped',key+':'+field,dict(contract_uuid=key,name=name)))
            responsibility.append(dict(contract_uuid=key,capability=capability,employee_uuid=employee))
        changed_rows.append(dict(contract_uuid=key,raw=raw))
    for cid,row in legacy.items():
        if cid not in known_ids and row['contract_uuid'] in originals:
            issues.append(_issue('legacy_contract_missing',cid,dict(contract_uuid=row['contract_uuid'])))
    mirrored={(r['table_name'],int(r['row_id'])):r for r in snapshot['mirror_links']}
    seen=set()
    for table,kind in [('calls','call'),('visits','visit')]:
        for raw in snapshot[table]:
            source='authority:'+table+':'+str(raw['id']);row_hash=digest(raw);seen.add(source)
            mirror=mirrored.get((table,int(raw['id'])))
            if mirror:
                event=by_event.get(mirror['event_id'])
                if not event or event['request_hash']!=mirror['request_hash'] or row_hash!=mirror['row_sha256']:
                    issues.append(_issue('legacy_mirror_changed',source,dict(source_sha256=sha)))
                continue
            old=events.get(source)
            if old:
                if old['request_hash']!=row_hash:issues.append(_issue('legacy_event_changed',source,dict(source_sha256=sha,before=old['request_hash'],incoming=row_hash)))
                continue
            try:
                ids=json.loads(raw.get('contracts_json') or '[]') or [raw['contract_id']]
                if not isinstance(ids,list):raise ValueError()
            except (ValueError,TypeError):ids=[raw['contract_id']]
            links=[]
            for old_id in ids:
                row=legacy.get(str(old_id))
                if row and str(old_id) in verified_ids:links.append(row['contract_uuid'])
            links=sorted(set(links))
            if len(links)!=len(set(map(str,ids))):issues.append(_issue('legacy_event_unlinked',source,dict(source_sha256=sha)))
            uid=int(raw.get('agent_id') or 0);eid=person(raw.get('employee'),uid) or 'legacy-author:'+digest([uid,raw.get('employee')])[:32]
            occurred=snapshot['captured_at'];quality='legacy_unknown';workday=None
            try:occurred=instant(raw['created_at']).isoformat();quality='legacy_recorded'
            except (CRMError,ValueError,TypeError,KeyError):
                try:workday=date.fromisoformat(str(raw.get('created_at'))[:10]).isoformat();quality='legacy_day'
                except ValueError:pass
            try:values=json.loads(raw.get('values_json') or '{}')
            except (ValueError,TypeError):values={}
            if not isinstance(values,dict):values={}
            incoming.append(dict(legacy_source=source,request_hash=row_hash,actor_uid=uid,employee_uuid=eid,kind=kind,
                contract_ids=links,operation_id=raw.get('op_id') or source,occurred_at=occurred,time_quality=quality,
                payload=dict(values=values,legacy_raw=raw,legacy_created_at=raw.get('created_at'),legacy_employee=raw.get('employee'),legacy_workday=workday)))
    for source in events:
        if source.startswith(('authority:calls:','authority:visits:')) and source not in seen:
            issues.append(_issue('legacy_event_missing',source,dict(source_sha256=sha)))
    touched=sorted({r['contract_uuid'] for r in patches+changed_rows+responsibility} |
                   {cid for event in incoming for cid in event['contract_ids']})
    return dict(snapshot_sha256=sha,captured_at=snapshot['captured_at'],before_sha256=_guard(conn,touched),
                touched=touched,patches=patches,events=incoming,rows=changed_rows,assignments=responsibility,issues=issues)


def preview(snapshot):
    sha=validate(snapshot)
    with storage.connect(readonly=True) as conn:plan=_read(conn,snapshot,sha)
    return _review(plan)


def _review(plan):
    return dict(snapshot_sha256=plan['snapshot_sha256'],before_sha256=plan['before_sha256'],plan_sha256=digest(plan),
                captured_at=plan['captured_at'],events=dict(Counter(r['kind'] for r in plan['events'])),
                changed_contracts=len(plan['patches']),changed_fields=sum(len(r['values']) for r in plan['patches']),
                source_rows=len(plan['rows']),assignments=len(plan['assignments']),issues=dict(Counter(r['kind'] for r in plan['issues'])))


def apply(snapshot, review):
    sha=validate(snapshot);at=now()
    with storage.connect(readonly=True) as source:
        old=source.execute('SELECT receipt_json FROM crm_legacy_batches WHERE snapshot_sha256=?',(sha,)).fetchone()
        if old:return json.loads(old[0])
        plan=_read(source,snapshot,sha)
    if review is not None and _review(plan)!=review:
        raise CRMError('Общая CRM изменилась после просмотра.','review_stale',409)
    with storage.connect() as conn:
        storage.begin(conn)
        old=conn.execute('SELECT receipt_json FROM crm_legacy_batches WHERE snapshot_sha256=?',(sha,)).fetchone()
        if old:return json.loads(old[0])
        if _guard(conn,plan['touched'])!=plan['before_sha256']:
            raise CRMError('Общая CRM изменилась после просмотра.','review_stale',409)
        for event in plan['events']:
            key=identifier('legacy-event',event['legacy_source'])
            conn.execute('INSERT INTO crm_events(event_id,operation_id,request_hash,actor_uid,employee_uuid,team_id,role,kind,origin,contracts_json,payload_json,occurred_at,received_at,time_quality,projection_status,legacy_source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (key,event['operation_id'],event['request_hash'],event['actor_uid'],event['employee_uuid'],'legacy-source','legacy',event['kind'],'legacy',canonical(event['contract_ids']),canonical(event['payload']),event['occurred_at'],at,event['time_quality'],'historical_only',event['legacy_source']))
            conn.executemany('INSERT INTO crm_event_contracts VALUES(?,?)',[(key,cid) for cid in event['contract_ids']])
        for patch in plan['patches']:
            cid=patch['contract_uuid'];key=identifier('legacy-projection',sha+cid)
            conn.execute('INSERT INTO crm_events(event_id,operation_id,request_hash,actor_uid,employee_uuid,team_id,role,kind,origin,contracts_json,payload_json,occurred_at,received_at,time_quality,projection_status,legacy_source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (key,key,digest(patch),0,'system:legacy-exchange','legacy-source','system','source_sync','legacy',canonical([cid]),canonical(dict(values=patch['values'],text='Сверка прежнего приложения',source_sha256=sha)),snapshot['captured_at'],at,'server','applied','exchange:'+sha+':'+cid))
            conn.execute('INSERT INTO crm_event_contracts VALUES(?,?)',(key,cid))
            row=dict(conn.execute('SELECT * FROM crm_contracts WHERE contract_uuid=?',(cid,)).fetchone())
            store.project(conn,row,patch['values'],key,snapshot['captured_at'],'legacy')
        for row in plan['rows']:
            conn.execute('INSERT INTO crm_legacy_state VALUES(?,?,?,?) ON CONFLICT(contract_uuid) DO UPDATE SET record_json=excluded.record_json,source_sha256=excluded.source_sha256,observed_at=excluded.observed_at',
                (row['contract_uuid'],canonical(row['raw']),sha,snapshot['captured_at']))
        for row in plan['assignments']:
            cid=row['contract_uuid'];cap=row['capability']
            conn.execute("UPDATE crm_assignments SET ends_at=? WHERE contract_uuid=? AND capability=? AND ends_at IS NULL AND (source LIKE 'authority-source:%' OR source LIKE 'legacy-exchange:%')",
                (snapshot['captured_at'],cid,cap))
            if row['employee_uuid']:
                conn.execute('INSERT INTO crm_assignments VALUES(?,?,?,?,?,?,?)',
                    (identifier('assignment',sha+canonical(row)),row['employee_uuid'],cid,cap,snapshot['captured_at'],None,'legacy-exchange:'+sha))
        for issue in plan['issues']:
            conn.execute('INSERT INTO crm_migration_issues VALUES(?,?,?,?,NULL,NULL) ON CONFLICT(issue_id) DO NOTHING',
                tuple(issue[k] for k in ('issue_id','kind','legacy_key','detail_json')))
        receipt=dict(_review(plan),applied_at=at,baseline_replaced=False,accounts_changed=False)
        conn.execute('INSERT INTO crm_legacy_batches VALUES(?,?,?)',(sha,snapshot['captured_at'],canonical(receipt)))
        conn.execute("INSERT INTO crm_meta VALUES('legacy_exchange_last',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(canonical(receipt),))
        return receipt
