"""Durable, explicitly scoped CRM call/visit delivery to existing readers.

SQLite receipt + journal row + exact field projection commit together. Losing
the PostgreSQL acknowledgement is recoverable without a second business fact.
This module does not authenticate users or expand a command's contract set.
"""
from datetime import datetime, timezone
from contextlib import ExitStack
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from . import storage
from .contract import CRMError, canonical, digest, instant, now
from .registry_import import typed_values

DDL = """
CREATE TABLE IF NOT EXISTS crm_exchange_receipts(
 event_id TEXT PRIMARY KEY,request_hash TEXT NOT NULL,receipt_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS crm_exchange_links(
 table_name TEXT NOT NULL,row_id INTEGER NOT NULL,event_id TEXT NOT NULL,
 request_hash TEXT NOT NULL,row_sha256 TEXT NOT NULL,
 PRIMARY KEY(table_name,row_id),UNIQUE(event_id));
"""


def _initialize(conn):
    # executescript would end the caller's transaction.
    for statement in DDL.split(';'):
        if statement.strip():conn.execute(statement)


def _call_snapshot(conn, row, values, happened, day):
    import db
    previous=db.bot_call_snapshot(row) or {}
    snapshot={**previous,**{k:v for k,v in values.items() if k in db.SOFT_SNAPSHOT_KEYS}}
    raw=json.loads(row['bot_call_data'] or '{}')
    snapshot['_field_at']={**raw.get('_field_at',{}),**{k:happened for k in values if k in db.SOFT_SNAPSHOT_KEYS}}
    snapshot['_field_day']={**raw.get('_field_day',{}),**{k:day for k in values if k in db.SOFT_SNAPSHOT_KEYS}}
    snapshot.update(_event_day=day,_communication_semantics='event_day')
    log=db._call_log_add(db._call_value_log(row),snapshot)
    conn.execute('UPDATE clients SET bot_call_at=?,bot_call_data=?,call_office_data=NULL,call_value_log=?,app_call_edited_at=?,updated_at=? WHERE contract_id=?',
        (happened,canonical(snapshot),canonical(log),happened,day,row['contract_id']))


def deliver(path, employees, *, limit=100, after_sqlite_commit=None):
    """Only current, already-authorized applied work of this explicit cohort."""
    if len(employees)>100:raise CRMError('Не задана точная группа обмена.')
    if not 1<=limit<=500:raise CRMError('Некорректный размер обмена.')
    with storage.connect(readonly=True) as conn:
        events=[r['event_id'] for r in conn.execute("SELECT e.event_id,e.kind,e.payload_json FROM crm_events e LEFT JOIN crm_legacy_exports x USING(event_id) "
            "WHERE e.kind IN ('call','visit','correction','registry_edit','manual_edit','contact') AND e.origin!='legacy' AND e.projection_status='applied' "
            "AND x.event_id IS NULL AND ("+('e.employee_uuid IN ('+','.join('?' for _ in employees)+') OR ' if employees else '')+
            "(EXISTS(SELECT 1 FROM crm_event_contracts ec JOIN crm_primary_fences f USING(contract_uuid) WHERE ec.event_id=e.event_id) "
            "AND NOT EXISTS(SELECT 1 FROM crm_event_contracts ec LEFT JOIN crm_primary_fences f USING(contract_uuid) WHERE ec.event_id=e.event_id AND f.contract_uuid IS NULL))) "
            "ORDER BY e.sequence LIMIT ?",(*sorted(employees),limit))]
    results=[]
    for eid in events:
        with storage.connect() as conn:
            storage.begin(conn)
            prior=conn.execute('SELECT * FROM crm_legacy_exports WHERE event_id=?',(eid,)).fetchone()
            if prior:continue
            event=dict(conn.execute('SELECT * FROM crm_events WHERE event_id=?',(eid,)).fetchone())
            payload=json.loads(event['payload_json']);ids=json.loads(event['contracts_json']);rows=[];expected={};conflicts=[]
            if event['kind']=='correction' and payload.get('target_kind') not in {'call','visit'}:
                conn.execute('INSERT INTO crm_legacy_exports VALUES(?,?,?,?)',(eid,'not_applicable',canonical({'event_id':eid,'state':'not_applicable'}),now()))
                continue
            for cid in ids:
                row=dict(conn.execute('SELECT * FROM crm_contracts WHERE contract_uuid=?',(cid,)).fetchone());rows.append(row)
                state=conn.execute('SELECT record_json FROM crm_legacy_state WHERE contract_uuid=?',(cid,)).fetchone()
                if not state:state=conn.execute('SELECT record_json FROM crm_authority_rows WHERE contract_uuid=?',(cid,)).fetchone()
                if not row['legacy_id'] or not state:conflicts.append(dict(contract_uuid=cid,reason='source_unmapped'));continue
                expected[cid]=json.loads(state[0])
                for issue in conn.execute("SELECT detail_json FROM crm_migration_issues WHERE kind='legacy_field_conflict' AND resolved_at IS NULL AND legacy_key LIKE ?",(cid+':%',)):
                    if json.loads(issue[0]).get('field') in payload['values']:
                        conflicts.append(dict(contract_uuid=cid,reason='unresolved_source_conflict'))
            employee=conn.execute('SELECT name FROM crm_employees WHERE employee_uuid=?',(event['employee_uuid'],)).fetchone()
            membership=conn.execute('SELECT timezone FROM crm_memberships WHERE employee_uuid=? AND starts_at<=? AND (ends_at IS NULL OR ends_at>?) ORDER BY starts_at DESC',
                (event['employee_uuid'],event['received_at'],event['received_at'])).fetchone()
            if not employee or not membership:conflicts.append(dict(reason='author_unmapped'))
            if conflicts:
                receipt=dict(event_id=eid,state='needs_review',conflicts=conflicts)
            else:
                receipt=_sqlite(path,event,payload,rows,expected,employee[0],membership[0])
                if after_sqlite_commit:after_sqlite_commit(receipt)
            conn.execute('INSERT INTO crm_legacy_exports VALUES(?,?,?,?)',(eid,receipt['state'],canonical(receipt),now()))
            results.append(receipt)
    return results


def _sqlite(path, event, payload, rows, expected, employee, timezone_name):
    primary=sqlite3.connect(Path(path),timeout=3)
    primary.row_factory=sqlite3.Row
    permits=ExitStack()
    try:
        primary.execute('BEGIN IMMEDIATE');_initialize(primary)
        prior=primary.execute('SELECT * FROM crm_exchange_receipts WHERE event_id=?',(event['event_id'],)).fetchone()
        if prior:
            if prior['request_hash']!=event['request_hash']:raise CRMError('Квитанция другого события.','mirror_conflict',409)
            primary.commit();return json.loads(prior['receipt_json'])
        active=[];conflicts=[]
        for row in rows:
            raw=primary.execute('SELECT * FROM clients WHERE contract_id=?',(row['legacy_id'],)).fetchone()
            if not raw or row['creditor_id']!='registry-partner:'+str(raw['partner'] or '').strip():
                conflicts.append(dict(contract_uuid=row['contract_uuid'],reason='identity_changed'));continue
            provenance=json.loads(row['provenance_json']);owned={k:v for k,v in payload['values'].items() if provenance.get('_fields',{}).get(k,{}).get('event_id')==event['event_id']}
            before,_=typed_values(expected[row['contract_uuid']]);current,_=typed_values(dict(raw))
            for key,value in owned.items():
                if current.get(key) not in (before.get(key),value):conflicts.append(dict(contract_uuid=row['contract_uuid'],field=key,reason='source_changed'))
            active.append((raw,owned))
        if conflicts:
            primary.rollback();return dict(event_id=event['event_id'],state='needs_review',conflicts=conflicts)
        from .primary_fence import projection
        permits.enter_context(projection(primary))
        day=instant(event['occurred_at']).astimezone(ZoneInfo(timezone_name)).date().isoformat()
        # Existing journals use naive UTC saved_at and a separate local workday.
        happened=instant(event['occurred_at']).astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec='milliseconds')
        received=instant(event['received_at']).astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec='milliseconds')
        target=payload.get('target_kind') if event['kind']=='correction' else event['kind']
        table='calls' if target=='call' else 'visits' if target=='visit' else None
        journal_fact=event['kind'] in {'call','visit'}
        rawids=[r['legacy_id'] for r in rows];op='crm:'+event['event_id']
        if table and primary.execute('SELECT 1 FROM '+table+' WHERE op_id=?',(op,)).fetchone():raise CRMError('Событие есть без квитанции обмена.','mirror_conflict',409)
        columns={'contract_id':rawids[0],'agent_id':event['actor_uid'],'created_at':day,'payload':'CRM '+event['event_id'],
            'op_id':op,'received_at':received,'saved_at':happened,'employee':employee,
            'values_json':canonical(payload['values']),'contracts_json':canonical(rawids)}
        available={r[1] for r in primary.execute('PRAGMA table_info('+table+')')} if table else set()
        columns={k:v for k,v in columns.items() if k in available}
        if table=='visits' and 'request_hash' in available:columns['request_hash']=event['request_hash']
        cursor=primary.execute('INSERT INTO '+table+'('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',tuple(columns.values())) if journal_fact else None
        for raw,values in active:
            if not values:continue
            available={r[1] for r in primary.execute('PRAGMA table_info(clients)')}
            if not set(values)<=available:raise CRMError('Поле отсутствует в прежнем приложении.')
            primary.execute('UPDATE clients SET '+','.join('"'+k+'"=?' for k in values)+' WHERE contract_id=?',(*values.values(),raw['contract_id']))
            if table=='calls':_call_snapshot(primary,raw,values,happened,day)
            elif table=='visits':
                import db
                previous=db.bot_visit_snapshot(raw) or {}
                db.record_bot_visit(raw['contract_id'],{**previous,**values},day,happened,_conn=primary,_contracts=[raw])
                primary.execute('UPDATE clients SET app_visit_edited_at=? WHERE contract_id=?',(happened,raw['contract_id']))
        if journal_fact:
            journal=dict(primary.execute('SELECT * FROM '+table+' WHERE id=?',(cursor.lastrowid,)).fetchone())
            primary.execute('INSERT INTO crm_exchange_links VALUES(?,?,?,?,?)',(table,journal['id'],event['event_id'],event['request_hash'],digest(journal)))
        receipt=dict(event_id=event['event_id'],state='delivered',table=table if journal_fact else None,row_id=cursor.lastrowid if cursor else None,contract_ids=rawids,
                     projected_fields={str(raw['contract_id']):sorted(values) for raw,values in active},verified_at=now())
        primary.execute('INSERT INTO crm_exchange_receipts VALUES(?,?,?)',(event['event_id'],event['request_hash'],canonical(receipt)))
        permits.close();primary.commit();return receipt
    except BaseException:
        primary.rollback();raise
    finally:
        permits.close();primary.close()
