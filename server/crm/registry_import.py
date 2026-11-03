"""Explicit one-time import of an immutable general-registry copy.

Every source row and unknown column is retained. Creditor + external contract
identify contracts; names/phones never merge people. No fabricated work events,
employee identities, currencies or schedules are created from Excel cells.
"""
from decimal import Decimal, InvalidOperation
import hashlib
import gzip
import json
from pathlib import Path
import re

from . import schema, storage
from .contract import CRMError, canonical, digest, field_value, fields, identifier, now


def file_sha(path):
    path = Path(path)
    with (gzip.open(path, 'rb') if path.suffix == '.gz' else path.open('rb')) as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def typed_values(raw):
    import db
    result, issues = {}, []
    for definition in fields():
        key = definition['key']
        value = raw.get(key)
        if value is None or value == '':
            result[key] = value
            continue
        try:
            if definition['type'] == 'date':
                parsed = db._as_date(value)
                if parsed is None:
                    raise CRMError('Неизвестный формат даты.')
                value = parsed.date().isoformat() if hasattr(parsed, 'date') else parsed.isoformat()
            elif definition['type'] in {'money', 'integer'}:
                value = str(value).replace('\u00a0', '').replace(' ', '').replace(',', '.')
                number = Decimal(value)
                if definition['type'] == 'integer' and number == number.to_integral_value():
                    value = str(int(number))
                elif definition['type'] == 'money' and number.is_finite():
                    value = format(number, 'f')
            else:
                value = str(value)
            result[key] = field_value(key, value)
        except (CRMError, ValueError, InvalidOperation, OverflowError):
            # Preserve the raw cell; do not silently round, erase or invent data.
            # Working fields use text, including unresolved numeric values, so
            # Excel readback stays exact. Original cell types remain in the
            # immutable registry row; no precision is rounded or fabricated.
            result[key] = str(raw[key])
            issues.append({'key': key, 'expected_type': definition['type']})
    return result, issues


def load(path, metadata):
    path = Path(path)
    required = {'workbook_sha256', 'rows_sha256', 'sheet', 'row_count'}
    if set(metadata) != required or any(not re.fullmatch('[a-f0-9]{64}', str(metadata[k]))
                                      for k in ['workbook_sha256', 'rows_sha256']):
        raise CRMError('Нужен точный манифест копии реестра.')
    if type(metadata['row_count']) is not int or not 1 <= metadata['row_count'] <= 100000:
        raise CRMError('Недопустимый размер реестра.')
    if file_sha(path) != metadata['rows_sha256']:
        raise CRMError('Контрольная сумма строк реестра не совпадает.')
    source = identifier('registry-source', canonical(metadata))
    at = now()
    with storage.connect() as conn:
        storage.begin(conn)
        schema.initialize(conn)
        prior = conn.execute('SELECT receipt_json FROM crm_registry_sources WHERE source_id=?', (source,)).fetchone()
        if prior:
            return json.loads(prior[0])
        if conn.execute('SELECT COUNT(*) FROM crm_contracts').fetchone()[0] or conn.execute('SELECT COUNT(*) FROM crm_events').fetchone()[0]:
            raise CRMError('Начальная копия загружается только в пустую базу пилота.', 'import_not_empty', 409)
        conn.execute('INSERT INTO crm_registry_sources VALUES(?,?,?,?,?,?,?)',
                     (source, metadata['workbook_sha256'], metadata['rows_sha256'], metadata['sheet'], at, metadata['row_count'], '{}'))
        seen, count, untyped = set(), 0, 0
        people, contracts, originals, problems = [], [], [], []

        def flush():
            conn.executemany('INSERT INTO crm_people VALUES(?,?,?,?)', people)
            conn.executemany('INSERT INTO crm_contracts VALUES(?,?,?,?,?,?,?,?,?,?,?)', contracts)
            conn.executemany('INSERT INTO crm_registry_rows VALUES(?,?,?,?)', originals)
            conn.executemany('INSERT INTO crm_migration_issues VALUES(?,?,?,?,?,?)', problems)
            people.clear(); contracts.clear(); originals.clear(); problems.clear()

        with (gzip.open(path, 'rt', encoding='utf-8') if path.suffix == '.gz' else path.open(encoding='utf-8')) as incoming:
            for line in incoming:
                if count >= metadata['row_count']:
                    raise CRMError('В файле больше строк, чем в подтверждённом манифесте.')
                if len(line) > 8 * 1024 * 1024:
                    raise CRMError('Слишком большая строка реестра.')
                row = json.loads(line)
                if row.get('sheet') != metadata['sheet'] or type(row.get('row')) is not int or row['row'] < 1:
                    raise CRMError('Некорректный источник строки.')
                raw = row['values']
                creditor, external = str(raw.get('partner') or '').strip(), str(raw.get('contract_id') or '').strip()
                if not creditor or not external:
                    raise CRMError('В строке отсутствует кредитор или номер договора.')
                creditor_id = 'registry-partner:' + creditor
                contract = identifier('contract', canonical([creditor_id, external]))
                if contract in seen:
                    raise CRMError('Кредитор и номер договора повторяются. Нужна сверка.', 'duplicate_contract', 409)
                seen.add(contract)
                person = identifier('person', 'registry-contract:' + contract)
                values, invalid = typed_values(raw)
                body = canonical(values)
                people.append((person, str(raw.get('client') or ''), 'registry-contract-separate:' + source, at))
                contracts.append((contract, person, creditor_id, external, None, None, body, body, '{}', '{}', at))
                originals.append((contract, source, row['row'], canonical(row)))
                detail = {'source_id': source, 'source_row': row['row'], 'untyped_fields': invalid,
                          'currency': 'not_provided', 'person_links': 'not_confirmed'}
                problems.append((identifier('issue', 'registry:' + contract), 'registry_identity_and_types', contract, canonical(detail), None, None))
                count += 1; untyped += len(invalid)
                if len(people) >= 500:
                    flush()
        if people:
            flush()
        if count != metadata['row_count'] or file_sha(path) != metadata['rows_sha256']:
            raise CRMError('Состав или содержимое копии изменились во время загрузки.')
        result = dict(stage='verified', source_id=source, contracts=count, people=count, source_rows=count,
                      untyped_fields=untyped, currency_unconfirmed=count, person_links_unconfirmed=count,
                      original_workbook_sha256=metadata['workbook_sha256'], rows_sha256=metadata['rows_sha256'],
                      imported_at=at, created_work_events=0)
        conn.execute('UPDATE crm_registry_sources SET receipt_json=? WHERE source_id=?', (canonical(result), source))
        conn.execute("INSERT INTO crm_meta VALUES('reliable_collection_since',?) ON CONFLICT(key) DO NOTHING", (at,))
        return result
