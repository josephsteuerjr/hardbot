"""Employee registry: explicit field grants, atomic edits and honest history.

No exchange/accounting grant is manufactured. Registry corrections share the
canonical projection and revisions, but are never contact/work facts.
"""
import json
import re
import uuid

from fields import BY_KEY, DROPDOWNS
from . import access, storage, store
from .contract import (CRMError, CALL_KEYS, VISIT_KEYS, CONTACT_KEYS, canonical,
                       digest, fields, group, now, operation_id, field_value)

# Overlapping extra_address belongs to the existing visit revision group.
WRITE = {**dict.fromkeys(CONTACT_KEYS, 'contact'),
         **dict.fromkeys(VISIT_KEYS, 'visit'), **dict.fromkeys(CALL_KEYS, 'call')}
SENSITIVE = re.compile(r'token|password|secret|credential|authorization|bearer|api.?key|access.?key|парол|секрет|токен', re.I)


def definitions():
    priority={'contract_id':0,'partner':1,'client':2}
    ordered=sorted(enumerate(fields()),key=lambda item:priority.get(item[1]['key'],item[0]+3))
    return [dict(f, section=BY_KEY[f['key']].block if f['key'] in BY_KEY else 'Передача конверта',
                 write_capability=WRITE.get(f['key']),
                 options=DROPDOWNS.get(f['key'], []),
                 readonly=f['key'] not in WRITE,sortable=True)
            for _,f in ordered]


def writable(conn, principal, row, **scopes):
    actions = set(access.writable_actions(conn, principal, row['contract_uuid'], **scopes))
    # Contact capability alone must not turn common visibility into ownership.
    work = actions & {'call', 'visit'}
    return [key for key, capability in WRITE.items()
            if capability in actions and (capability != 'contact' or work)]


def source_details(conn, row):
    """Business-only DTO. Never expose arbitrary raw source/authentication JSON."""
    originals = []
    for table in ('crm_registry_rows', 'crm_authority_rows'):
        item = conn.execute('SELECT record_json FROM ' + table + ' WHERE contract_uuid=?',
                            (row['contract_uuid'],)).fetchone()
        if item:
            raw = json.loads(item[0])
            originals.append(raw)
    present = set()
    extras = []
    seen = set()
    for raw in originals:
        values = raw.get('values', raw)
        if isinstance(values, dict):
            present.update(key for key in values if key in BY_KEY)
        for extra in raw.get('extra_columns', []):
            if not isinstance(extra, dict):
                continue
            title, value = extra.get('title'), extra.get('value')
            if (not isinstance(title, str) or not title.strip() or len(title) > 300
                    or SENSITIVE.search(title) or isinstance(value, (dict, list))):
                continue
            column=extra.get('column')
            identity = (column if isinstance(column,(str,int)) else None, title)
            if identity not in seen:
                seen.add(identity)
                extras.append(dict(key='extra_'+digest(identity)[:24],title=title,value=value,present=True))
    for total in conn.execute('SELECT month,amount,currency,source_column FROM crm_monthly_totals WHERE contract_uuid=? ORDER BY month',
                              (row['contract_uuid'],)):
        title=total['source_column'] or (total['month']+' · Поступления по реестру')
        if not SENSITIVE.search(title) and not any(extra['title']==title for extra in extras):
            extras.append(dict(key='monthly_'+total['month'],title=title,value=total['amount'],currency=total['currency'],present=True))
    current = json.loads(row['fields_json'])
    # A native change makes a field available even if absent in the initial row.
    provenance = json.loads(row['provenance_json'])
    present.update(provenance.get('_fields', {}))
    if not originals:
        present.update(current)
    return dict(present=sorted(present), extras=extras, has_source=bool(originals))


def read(principal, *, search='', view='mine', sort='contract_id', direction='asc', offset=0, limit=50, ids=None):
    if (view not in {'mine', 'all'} or direction not in {'asc', 'desc'}
            or sort not in {f['key'] for f in definitions()}):
        raise CRMError('Неизвестный фильтр реестра.')
    try:
        offset, limit = int(offset), int(limit)
    except (TypeError, ValueError):
        raise CRMError('Некорректная страница реестра.')
    if not 0 <= offset <= 1000000 or not 1 <= limit <= 100 or not isinstance(search, str) or len(search) > 300:
        raise CRMError('Некорректные параметры реестра.')
    selected=ids.split(',') if isinstance(ids,str) else None
    if ids is not None and (not selected or len(selected)>100 or not all(re.fullmatch(r'[a-f0-9-]{36}',key) for key in selected)):
        raise CRMError('Некорректные внутренние ID строк.')
    with storage.connect(readonly=True) as conn:
        from . import tasks
        direct=access.scoped_contracts(conn,principal,include_tasks=False)
        delegated=tasks.grants(conn,principal)
        write_scope=direct | set(delegated)
        conditions, args = [], []
        allowed = access.scoped_contracts(conn, principal) if view == 'mine' else (
            None if access.reads_all(principal) else access.readable_contracts(conn, principal))
        if allowed is not None:
            conditions.append('c.contract_uuid IN (' + ','.join('?' for _ in allowed) + ')' if allowed else '1=0')
            args.extend(sorted(allowed))
        if selected is not None:
            conditions.append('c.contract_uuid IN ('+','.join('?' for _ in selected)+')');args.extend(selected)
        if search:
            haystack = "lower(p.name || ' ' || c.external_id || ' ' || c.creditor_id || ' ' || c.fields_json)"
            conditions.append(('strpos(' + haystack + ',lower(?))>0') if storage.is_postgres(conn)
                              else 'instr(' + haystack + ',lower(?))>0')
            args.append(search)
        base = ' FROM crm_contracts c JOIN crm_people p USING(person_uuid) WHERE ' + (' AND '.join(conditions) or '1=1')
        total = conn.execute('SELECT COUNT(*)' + base, args).fetchone()[0]
        # sort is strictly a schema key. No caller-provided SQL identifiers.
        expression = {'contract_id': 'c.external_id', 'client': 'p.name', 'partner': 'c.creditor_id'}.get(sort)
        if expression is None:
            expression = ("(c.fields_json::jsonb ->> '" + sort + "')" if storage.is_postgres(conn)
                          else "json_extract(c.fields_json,'$." + sort + "')")
            # Source values can intentionally retain invalid numeric text.
            # Text ordering stays deterministic without risky implicit casts.
        rows = conn.execute('SELECT c.*,p.name AS client_name' + base + ' ORDER BY ' + expression
                            + ' ' + direction + ',c.contract_uuid LIMIT ? OFFSET ?', (*args, limit, offset)).fetchall()
        output = [];columns=definitions();extra_keys=set()
        for raw in rows:
            row = dict(raw)
            from .assignments import effective_fields
            values=effective_fields(conn,row['contract_uuid'],json.loads(row['fields_json']))
            for extra in source_details(conn,row)['extras']:
                key=extra['key'];value=extra['value']
                values[key]=str(value)+' '+extra['currency'] if value is not None and extra.get('currency') else value
                if key not in extra_keys:
                    extra_keys.add(key)
                    columns.append(dict(key=key,title=extra['title'],section='Прочие сведения источника',group='source_extra',
                        type='text',source='source_row',write_capability=None,readonly=True,options=[],nullable=True,sortable=False))
            output.append(dict(contract_uuid=row['contract_uuid'], external_id=row['external_id'],
                creditor_id=row['creditor_id'], currency=row['currency'], client_name=row['client_name'],
                fields=values, revisions=json.loads(row['revisions_json']),
                writable_fields=writable(conn, principal, row,scope=write_scope,direct_scope=direct,delegated=delegated)))
        return dict(schema_version=1, columns=columns, rows=output, total=total, offset=offset,
                    has_more=offset+len(output)<total)


def edit(principal, body):
    if not isinstance(body, dict) or set(body) != {'schema_version', 'operation_id', 'rows'} or body['schema_version'] != 1:
        raise CRMError('Неизвестный формат правки реестра.')
    operation = operation_id(body['operation_id'])
    patches = body['rows']
    if not isinstance(patches, list) or not 1 <= len(patches) <= 100:
        raise CRMError('Правка содержит от 1 до 100 строк.')
    normalized, ids, cells = [], set(), 0
    for patch in patches:
        if not isinstance(patch, dict) or set(patch) != {'contract_uuid', 'values', 'clear', 'base_revisions'}:
            raise CRMError('Неизвестные параметры строки.')
        cid, values, clear, revisions = (patch[k] for k in ('contract_uuid', 'values', 'clear', 'base_revisions'))
        if not isinstance(cid, str) or cid in ids or not isinstance(values, dict) or not values:
            raise CRMError('Повторная или пустая строка правки.')
        if (not isinstance(clear, list) or not all(isinstance(k, str) for k in clear)
                or len(set(clear)) != len(clear) or not set(clear) <= set(values)
                or any(values[k] is not None for k in clear)):
            raise CRMError('Очистка должна явно передавать пустое значение.')
        if set(values) - WRITE.keys():
            raise CRMError('Поле доступно только для чтения.', 'forbidden', 403)
        groups = {group(key) for key in values}
        if (not isinstance(revisions, dict) or set(revisions) != groups
                or any(type(v) is not int or v < 0 for v in revisions.values())):
            raise CRMError('Укажите версии изменяемых групп.')
        normalized.append(dict(patch, values={key: field_value(key, value, clear=key in clear)
                                              for key, value in values.items()}))
        ids.add(cid)
        cells += len(values)
    if cells > 1000:
        raise CRMError('В одном пакете допускается до 1000 ячеек.')
    at = now()
    with storage.connect() as conn:
        store.begin(conn, [storage.operation_lock(principal, operation), *('contract:' + cid for cid in ids)])
        principal=access.refresh_write(conn,principal)
        previous = store.replay(conn, principal, operation, body)
        if previous is not None:
            return previous
        rows = {r['contract_uuid']: r for r in access.contracts(conn, principal, sorted(ids))}
        for patch in normalized:
            permitted = set(writable(conn, principal, rows[patch['contract_uuid']]))
            if not set(patch['values']) <= permitted:
                raise CRMError('Изменилось назначение или право поля.', 'scope_changed', 403)
        conflicts = []
        for patch in normalized:
            actual = json.loads(rows[patch['contract_uuid']]['revisions_json'])
            for key, expected in patch['base_revisions'].items():
                if actual.get(key, 0) != expected:
                    conflicts.append(dict(contract_uuid=patch['contract_uuid'], group=key, revision=actual.get(key, 0)))
        if conflicts:
            raise CRMError('Реестр изменился. Введённые значения сохранены для сверки.', 'revision_conflict', 409, conflicts)
        events, confirmed = [], []
        for patch in normalized:
            row = rows[patch['contract_uuid']]
            before = json.loads(row['fields_json'])
            values = patch['values']
            event = str(uuid.uuid4())
            payload = dict(values=values, clear=patch['clear'],
                           before={key: dict(present=key in before, value=before.get(key)) for key in values})
            conn.execute('''INSERT INTO crm_events(event_id,operation_id,request_hash,actor_uid,employee_uuid,
                team_id,role,kind,origin,contracts_json,payload_json,occurred_at,received_at,time_quality,projection_status)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (event, operation, digest(body), principal.actor_uid, principal.employee_uuid, principal.team_id,
                 principal.role, 'registry_edit', 'app', canonical([row['contract_uuid']]), canonical(payload), at, at, 'server', 'applied'))
            conn.execute('INSERT INTO crm_event_contracts VALUES(?,?)', (event, row['contract_uuid']))
            store.project(conn, row, values, event, at, 'app')
            events.append(event)
            updated = conn.execute('SELECT fields_json,revisions_json FROM crm_contracts WHERE contract_uuid=?', (row['contract_uuid'],)).fetchone()
            confirmed.append(dict(contract_uuid=row['contract_uuid'], values=values, revisions=json.loads(updated['revisions_json'])))
        # No outbox work facts or delivery routes: this is a registry correction.
        store.audit(conn, principal, 'registry_edit', operation, dict(rows=len(normalized), cells=cells, event_ids=events))
        result = dict(ok=True, status='accepted', operation_id=operation, event_ids=events,
                      projection_status='applied', received_at=at, rows=confirmed)
        store.acknowledge(conn, principal, operation, body, result, at)
        conn.commit()
        return result
