"""Country-scoped courier pools, independent of regular Soft assignments."""

from collections import Counter, defaultdict
from datetime import date, timedelta, timezone
import json
import re
import uuid

from . import access, delivery, primary_fence, storage, store
from .contract import CRMError, digest, field_value, instant, now, operation_id

COUNTRIES = {'UZ': 'Узбекистан', 'TJ': 'Таджикистан'}
MAX_ROWS = 5000
MAX_SELECTION = 300


def normalized(value):
    return ' '.join(str(value or '').casefold().replace('ё', 'е').split())


def country_code(value):
    value = normalized(value)
    if value in {'uz', 'uzb', 'уз', 'узб', 'узбекистан', 'uzbekistan'}:
        return 'UZ'
    if value in {'tj', 'tjk', 'тж', 'рт', 'тадж', 'таджикистан', 'tajikistan'}:
        return 'TJ'
    return None


def countries(conn, principal):
    if principal.role != 'logistic' or 'logistics' not in principal.capabilities:
        return []
    return [r[0] for r in conn.execute(
        "SELECT country FROM crm_logistics_staff WHERE employee_uuid=? AND work='logist' ORDER BY country",
        (principal.employee_uuid,))]


def authorize(conn, principal, country):
    if country not in countries(conn, principal):
        raise CRMError('Этот пул недоступен вашему профилю.', 'forbidden', 403)


def require_ready(conn):
    mode = conn.execute("SELECT value FROM crm_meta WHERE key='distribution_mode'").fetchone()
    if not mode or mode[0] not in {'isolated', 'cutover'}:
        raise CRMError('Распределение курьерских пулов доступно после переключения CRM.',
                       'distribution_unavailable', 403)
    if mode[0] == 'cutover' and not primary_fence.ready(conn):
        raise CRMError('Перевод договоров в CRM требует проверки.', 'primary_fence_unavailable', 503)
    return mode[0]


def roster(conn, country):
    at = now()
    staff = {'call': {}, 'delivery': {}}
    for row in conn.execute(
        "SELECT s.work,e.employee_uuid,e.name,m.role,m.capabilities_json "
        "FROM crm_logistics_staff s JOIN crm_employees e USING(employee_uuid) "
        "JOIN crm_memberships m USING(employee_uuid) WHERE s.country=? AND s.work<>'logist' "
        "AND e.active=1 AND m.starts_at<=? AND (m.ends_at IS NULL OR m.ends_at>?) "
        "ORDER BY m.starts_at DESC,e.name", (country, at, at)):
        work = row['work']
        role = 'employee' if work == 'call' else 'courier'
        if row['role'] == role and work in json.loads(row['capabilities_json']):
            staff[work].setdefault(row['employee_uuid'], row['name'])
    return staff


def text(value, limit, label):
    if not isinstance(value, str) or len(value) > limit:
        raise CRMError('Проверьте поле «' + label + '».')
    return value.strip()


def phone(value):
    value = text(value, 64, 'Телефон')
    if value in {'', '-', '—', '–'}:
        return ''
    if not re.fullmatch(r'\+?[0-9 ().-]+', value) or not 7 <= sum(ch.isdigit() for ch in value) <= 15:
        raise CRMError('Укажите один номер телефона.')
    return value


def iso_day(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise CRMError('Укажите дату в формате ГГГГ-ММ-ДД.')
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise CRMError('Проверьте дату.') from None


def source_contracts(conn, rows, mode):
    numbers = sorted({r['number'] for r in rows if r['number']})
    by_number = defaultdict(list)
    for start in range(0, len(numbers), 300):
        chunk = numbers[start:start+300]
        marks = ','.join('?' for _ in chunk)
        for row in conn.execute(
            'SELECT contract_uuid,external_id,creditor_id,legacy_id,fields_json FROM crm_contracts '
            'WHERE external_id IN (' + marks + ')', chunk):
            by_number[row['external_id']].append(dict(row))
    if mode == 'cutover':
        from contextlib import closing
        legacy = sorted({r['legacy_id'] for group in by_number.values() for r in group if r['legacy_id']})
        known = {}
        with primary_fence.connect(readonly=True) as primary, closing(primary.cursor()) as cursor:
            primary.execute('BEGIN')
            for start in range(0, len(legacy), 300):
                chunk = legacy[start:start+300]
                marks = ','.join('?' for _ in chunk)
                known.update((r['contract_id'], (r['partner'], r['country'])) for r in cursor.execute(
                    'SELECT contract_id,partner,country FROM clients WHERE contract_id IN (' + marks + ')', chunk))
        for group in by_number.values():
            for row in group:
                source = known.get(row['legacy_id'])
                row['country'] = country_code(source[1]) if source and normalized(source[0]) == normalized(
                    row['creditor_id'].removeprefix('registry-partner:')) else None
    else:
        for group in by_number.values():
            for row in group:
                row['country'] = country_code(json.loads(row['fields_json']).get('country'))
    return by_number


def import_review(conn, principal, body):
    if set(body) != {'country', 'workbook_sha256', 'rows', 'since', 'until'}:
        raise CRMError('Некорректный формат курьерского реестра.')
    country = body['country']
    authorize(conn, principal, country)
    mode = require_ready(conn)
    fingerprint = body['workbook_sha256']
    source = body['rows']
    if not isinstance(fingerprint, str) or not re.fullmatch(r'[a-f0-9]{64}', fingerprint):
        raise CRMError('Книга не прочитана полностью.')
    if not isinstance(source, list) or not 1 <= len(source) <= MAX_ROWS:
        raise CRMError('Загрузите от 1 до 5 000 строк.')
    since, until = iso_day(body['since']), iso_day(body['until'])
    if since > until:
        raise CRMError('Начало периода должно предшествовать окончанию.')
    shape = {'line', 'number', 'partner', 'courier', 'transferred_on', 'phone'}
    rows = []
    for item in source:
        if not isinstance(item, dict) or set(item) != shape or type(item['line']) is not int or item['line'] < 2:
            raise CRMError('Некорректная строка курьерского реестра.')
        rows.append(dict(line=item['line'], number=text(item['number'], 128, 'КД'),
                         partner=text(item['partner'], 200, 'Партнёр'), courier=text(item['courier'], 200, 'Курьер'),
                         transferred_on=text(item['transferred_on'], 32, 'Дата передачи'),
                         phone=text(item['phone'], 128, 'Телефон')))
    if len({r['line'] for r in rows}) != len(rows):
        raise CRMError('Номера строк повторяются.')

    selected, report = [], []
    for row in rows:
        try:
            day = iso_day(row['transferred_on'])
        except CRMError:
            report.append(dict(line=row['line'], status='review', reason='Проверьте дату передачи курьеру.'))
            continue
        if since <= day <= until:
            selected.append(row)
        else:
            report.append(dict(line=row['line'], status='outside', reason='Дата передачи вне периода.'))

    candidates = source_contracts(conn, selected, mode)
    possible_ids = [r['contract_uuid'] for group in candidates.values() for r in group]
    enabled = primary_fence.writable_contracts(conn, possible_ids)
    repeats = Counter((normalized(r['partner']), r['number']) for r in selected)
    existing = {r['contract_uuid']: dict(r) for r in conn.execute(
        'SELECT contract_uuid,revision,active FROM crm_logistics_cases WHERE country=?', (country,))}
    couriers = defaultdict(list)
    for employee, name in roster(conn, country)['delivery'].items():
        couriers[normalized(name)].append(employee)
    eligible = []
    for row in selected:
        identity = normalized(row['partner']), row['number']
        matches = [r for r in candidates[row['number']] if r['country'] == country
                   and normalized(r['creditor_id'].removeprefix('registry-partner:')) == identity[0]]
        reason = ''
        if not all(identity):
            reason = 'Укажите партнёра и номер договора.'
        elif repeats[identity] > 1:
            reason = 'Договор повторяется в выбранном пуле.'
        elif len(matches) != 1:
            reason = 'Сверьте договор и страну по общему реестру.'
        elif matches[0]['contract_uuid'] not in enabled:
            reason = 'Договор ждёт рабочего перевода в CRM.'
        if reason:
            report.append(dict(line=row['line'], status='review', reason=reason))
            continue
        cid = matches[0]['contract_uuid']
        if cid in existing:
            report.append(dict(line=row['line'], status='existing', reason='Договор уже загружен.'))
            continue
        warnings = []
        try:
            number = phone(row['phone'])
        except CRMError:
            number = ''
            warnings.append('Телефон требует сверки.')
        people = couriers[normalized(row['courier'])]
        courier = people[0] if len(people) == 1 else None
        if row['courier'] and courier is None:
            warnings.append('Выберите аккаунт курьера в таблице.')
        eligible.append(dict(contract_uuid=cid, country=country, workbook_sha256=fingerprint,
                             courier_label=row['courier'], courier_uuid=courier,
                             transferred_on=row['transferred_on'], phone=number))
        report.append(dict(line=row['line'], status='ready', reason=' '.join(warnings)))
    report.sort(key=lambda r: r['line'])
    return dict(rows=report, counts=dict(Counter(r['status'] for r in report)),
                proof=digest([principal.actor_uid, body, eligible, report])), eligible


def preview(principal, body):
    with storage.connect(readonly=True) as conn:
        return import_review(conn, principal, body)[0]


def signal(conn, principal, operation, cid, revision, before=None, after=None, at=None):
    conn.execute(
        'INSERT INTO crm_assignment_changes(actor_uid,operation_id,team_id,contract_uuid,capability,'
        'before_employee_uuid,after_employee_uuid,revision,at,source) VALUES(?,?,?,?,?,?,?,?,?,?)',
        (principal.actor_uid, operation, principal.team_id, cid, 'logistics', before, after, revision,
         at or now(), 'courier-logistics'))


def apply(principal, body):
    if set(body) != {'source', 'proof', 'operation_id'} or not isinstance(body['source'], dict):
        raise CRMError('Подтвердите предпросмотр.')
    operation = operation_id(body['operation_id'])
    with storage.connect() as conn:
        storage.begin(conn)
        p = access.refresh_write(conn, principal)
        authorize(conn, p, body['source'].get('country'))
        prior = store.replay(conn, p, operation, body)
        if prior is not None:
            return prior
        plan, eligible = import_review(conn, p, body['source'])
        if plan['proof'] != body['proof']:
            raise CRMError('Пул или права изменились. Обновите предпросмотр.', 'revision_conflict', 409)
        if not eligible:
            raise CRMError('В выбранном периоде нет новых подтверждённых договоров.')
        ids = [row['contract_uuid'] for row in eligible]
        primary_fence.require_contracts(conn, ids)
        at = now()
        columns = ('contract_uuid', 'country', 'workbook_sha256', 'courier_label', 'courier_uuid',
                   'transferred_on', 'phone')
        conn.executemany('INSERT INTO crm_logistics_cases(' + ','.join(columns) + ') VALUES(?,?,?,?,?,?,?)',
                         [tuple(row[k] for k in columns) for row in eligible])
        for row in eligible:
            signal(conn, p, operation, row['contract_uuid'], 1, after=row['courier_uuid'], at=at)
        result = dict(imported=len(eligible), counts=plan['counts'])
        store.audit(conn, p, 'logistics_import', operation,
                    dict(country=body['source']['country'], contracts=ids, workbook_sha256=body['source']['workbook_sha256']))
        store.acknowledge(conn, p, operation, body, result, at)
        return result


def case_rows(conn, principal, country, *, search='', caller='', courier='', phones='all', state='active'):
    authorize(conn, principal, country)
    search = normalized(text(search, 200, 'Поиск'))
    caller = text(caller, 100, 'Исполнитель')
    courier = text(courier, 100, 'Курьер')
    if phones not in {'all', 'with', 'without'} or state not in {'active', 'closed', 'all'}:
        raise CRMError('Проверьте фильтры.')
    clauses = ['s.country=?']
    params = [country]
    if caller:
        clauses.append('s.caller_uuid=?' if caller != 'unassigned' else 's.caller_uuid IS NULL')
        if caller != 'unassigned':
            params.append(caller)
    if courier:
        clauses.append('s.courier_uuid=?')
        params.append(courier)
    if state != 'all':
        clauses.append('s.active=?')
        params.append(int(state == 'active'))
    rows = [dict(r) for r in conn.execute(
        'SELECT s.*,c.external_id,c.creditor_id,p.name AS client,e.name AS caller,ce.name AS courier '
        'FROM crm_logistics_cases s JOIN crm_contracts c USING(contract_uuid) JOIN crm_people p USING(person_uuid) '
        'LEFT JOIN crm_employees e ON e.employee_uuid=s.caller_uuid '
        'LEFT JOIN crm_employees ce ON ce.employee_uuid=s.courier_uuid WHERE ' + ' AND '.join(clauses)
        + ' ORDER BY s.transferred_on DESC,c.external_id,s.contract_uuid', params)]
    add_delivery(conn, rows)
    for r in rows:
        r['partner'] = r.pop('creditor_id').removeprefix('registry-partner:')
        r.pop('workbook_sha256')
        r['available_phone'] = available_phone(r)
    return [r for r in rows if (phones == 'all' or bool(r['available_phone']) == (phones == 'with'))
            and (not search or search in normalized(' '.join(str(r.get(k) or '') for k in (
                'external_id', 'partner', 'client', 'available_phone', 'courier', 'courier_label', 'caller', 'call_status'))))]


def add_delivery(conn, rows):
    ids = [r['contract_uuid'] for r in rows]
    facts = {}
    for start in range(0, len(ids), 300):
        facts.update(delivery.latest(conn, ids[start:start+300]))
    for row in rows:
        fact = facts.get(row['contract_uuid'], {})
        received = fact.get('delivery_received_at')
        local_day = instant(received).astimezone(timezone(timedelta(hours=5))).date().isoformat() if received else ''
        row['delivery'] = fact if local_day >= row['transferred_on'] else {}


def available_phone(row):
    if row['phone']:
        return row['phone']
    try:
        return phone(row.get('delivery', {}).get('delivery_phone', ''))
    except CRMError:
        return ''


def read(principal, country='', offset=0, limit=100, **filters):
    offset, limit = int(offset), int(limit)
    if offset < 0 or not 1 <= limit <= 200:
        raise CRMError('Проверьте номер страницы.')
    with storage.connect(readonly=True) as conn:
        rows = case_rows(conn, principal, country, **filters)
        return dict(rows=rows[offset:offset+limit], total=len(rows),
                    staff=roster(conn, country), offset=offset, has_more=offset+limit<len(rows))


def export(principal, country='', **filters):
    with storage.connect(readonly=True) as conn:
        rows = case_rows(conn, principal, country, **filters)
        headers = ['Партнёр', 'Номер КД', 'ФИО', 'Курьер', 'Дата передачи курьеру', 'Телефон',
                   'Результат доставки', 'Комментарий курьера', 'Софт по курьерам', 'Статус дозвона',
                   'Комментарий по обзвону', 'Дата звонка', 'Статус пула']
        return dict(title='Курьерские пулы · ' + COUNTRIES[country], headers=headers, rows=[
            [r['partner'], r['external_id'], r['client'], r['courier'] or r['courier_label'],
             r['transferred_on'], r['available_phone'], r['delivery'].get('delivery_result', ''),
             r['delivery'].get('delivery_comment', ''), r['caller'] or '', r['call_status'],
             r['call_comment'], r['call_at'], 'В работе у курьеров' if r['active'] else 'Завершён']
            for r in rows])


def get_cases(conn, requested, country):
    ids = [r['contract_uuid'] for r in requested]
    if len(set(ids)) != len(ids):
        raise CRMError('Выберите договоры без повторов.')
    result = {r['contract_uuid']: dict(r) for r in conn.execute(
        'SELECT * FROM crm_logistics_cases WHERE country=? AND contract_uuid IN ('
        + ','.join('?' for _ in ids) + ')', (country, *ids))}
    if len(result) != len(ids):
        raise CRMError('Договор вне вашего пула.', 'forbidden', 403)
    for item in requested:
        row = result[item['contract_uuid']]
        if not row['active'] or row['revision'] != item['revision']:
            raise CRMError('Пул изменился. Обновите таблицу.', 'revision_conflict', 409)
    return result


def update(principal, body):
    if set(body) != {'country', 'operation_id', 'selection', 'values'}:
        raise CRMError('Некорректное изменение пула.')
    requested, values = body['selection'], body['values']
    if (not isinstance(requested, list) or not 1 <= len(requested) <= MAX_SELECTION
            or any(not isinstance(r, dict) or set(r) != {'contract_uuid', 'revision'}
                   or not isinstance(r['contract_uuid'], str) or type(r['revision']) is not int for r in requested)
            or not isinstance(values, dict) or not values
            or set(values) - {'caller_uuid', 'courier_uuid', 'phone', 'close'}
            or ('close' in values and (values != {'close': True}))
            or ('phone' in values and len(requested) != 1)):
        raise CRMError('Проверьте состав изменения.')
    operation = operation_id(body['operation_id'])
    country = body['country']
    locks = [storage.operation_lock(principal, operation), *('contract:' + r['contract_uuid'] for r in requested)]
    with storage.connect() as conn:
        storage.begin(conn, locks)
        p = access.refresh_write(conn, principal)
        authorize(conn, p, country)
        prior = store.replay(conn, p, operation, body)
        if prior is not None:
            return prior
        require_ready(conn)
        rows = get_cases(conn, requested, country)
        primary_fence.require_contracts(conn, rows)
        people = roster(conn, country)
        changes = dict(values)
        if 'phone' in changes:
            changes['phone'] = phone(changes['phone'])
        for key, work in (('caller_uuid', 'call'), ('courier_uuid', 'delivery')):
            if key in changes and changes[key] is not None:
                if not isinstance(changes[key], str) or changes[key] not in people[work]:
                    raise CRMError('Выберите активного сотрудника своего пула.', 'forbidden', 403)
        if changes.pop('close', False):
            changes.update(active=0)
        if changes.get('caller_uuid'):
            add_delivery(conn, list(rows.values()))
        at = now()
        applied = []
        for cid, row in rows.items():
            target = {**row, **changes}
            if changes.get('caller_uuid') and 'phone' not in changes and not target['phone']:
                target['phone'] = available_phone(row)
            if target['caller_uuid'] and not target['phone']:
                raise CRMError('Для обзвона укажите телефон или снимите назначение.')
            delta = {k: v for k, v in changes.items() if row[k] != v}
            if target['phone'] != row['phone']:
                delta['phone'] = target['phone']
            if not delta:
                continue
            revision = row['revision'] + 1
            conn.execute('UPDATE crm_logistics_cases SET ' + ','.join(k + '=?' for k in delta)
                         + ',revision=? WHERE contract_uuid=?', (*delta.values(), revision, cid))
            signal(conn, p, operation, cid, revision, row['caller_uuid'],
                   target['caller_uuid'] if target['active'] else None, at)
            if row['courier_uuid'] or target['courier_uuid']:
                signal(conn, p, operation, cid, revision, row['courier_uuid'],
                       target['courier_uuid'] if target['active'] else None, at)
            applied.append(dict(contract_uuid=cid, revision=revision))
            store.audit(conn, p, 'logistics_update', cid,
                        dict(country=country, fields=sorted(delta), previous_caller=row['caller_uuid'],
                             caller=target['caller_uuid'], previous_courier=row['courier_uuid'],
                             courier=target['courier_uuid'], revision=revision))
        result = dict(changes=applied)
        store.acknowledge(conn, p, operation, body, result, at)
        return result


def for_employee(conn, employee, work):
    if work not in {'call', 'delivery'}:
        return {}
    column = 'caller_uuid' if work == 'call' else 'courier_uuid'
    return {r['contract_uuid']: dict(r) for r in conn.execute(
        'SELECT c.contract_uuid,c.phone,c.revision,c.country,c.call_status FROM crm_logistics_cases c '
        'JOIN crm_logistics_staff s ON s.country=c.country AND s.employee_uuid=c.' + column
        + " WHERE c.active=1 AND s.work=? AND s.employee_uuid=? "
        "AND NOT EXISTS(SELECT 1 FROM crm_contact_withdrawals w WHERE w.contract_uuid=c.contract_uuid) "
        "AND EXISTS(SELECT 1 FROM crm_meta WHERE key='distribution_mode' AND value IN ('isolated','cutover'))",
        (work, employee))}


def staff_plan(conn, manifest):
    if not isinstance(manifest, dict) or not manifest or set(manifest) - set(COUNTRIES):
        raise ValueError('Укажите страну UZ или TJ.')
    identities, entries = {}, []
    at = now()
    for country, groups in sorted(manifest.items()):
        if not isinstance(groups, dict) or set(groups) != {'logist', 'call', 'delivery'}:
            raise ValueError('Для страны нужны списки logist, call и delivery.')
        for work, employees in sorted(groups.items()):
            if not isinstance(employees, list) or any(not isinstance(e, str) for e in employees) or len(
                    set(employees)) != len(employees):
                raise ValueError('Задайте уникальные кадровые UUID.')
            for employee in sorted(employees):
                previous = identities.get(employee)
                if previous and previous != work:
                    raise ValueError('Одна учётная запись может иметь одну роль в логистике.')
                identities[employee] = work
                member = conn.execute(
                    'SELECT m.membership_id,m.role,m.capabilities_json FROM crm_memberships m '
                    'JOIN crm_employees e USING(employee_uuid) WHERE m.employee_uuid=? AND e.active=1 '
                    'AND m.starts_at<=? AND (m.ends_at IS NULL OR m.ends_at>?) '
                    'AND EXISTS(SELECT 1 FROM crm_actor_links a WHERE a.employee_uuid=e.employee_uuid AND a.active=1) '
                    'ORDER BY m.starts_at DESC LIMIT 1', (employee, at, at)).fetchone()
                if not member:
                    raise ValueError('У сотрудника нет действующего кадрового входа CRM.')
                current = member['role']
                caps = json.loads(member['capabilities_json'])
                if work == 'call' and (current != 'employee' or 'call' not in caps):
                    raise ValueError('Для обзвона требуется действующий сотрудник с правом звонка.')
                if work in {'logist', 'delivery'}:
                    allowed = {'employee', 'manager', 'logistic'} if work == 'logist' else {'employee', 'courier'}
                    if current not in allowed:
                        raise ValueError('Выберите отдельную действующую учётную запись для этой роли.')
                entries.append(dict(country=country, work=work, employee_uuid=employee,
                                    membership_id=member['membership_id'], current_role=current, capabilities=caps))
    previous_scope = []
    for country in sorted(manifest):
        previous_scope.extend(tuple(r[i] for i in range(3)) for r in conn.execute(
            'SELECT country,work,employee_uuid FROM crm_logistics_staff WHERE country=? ORDER BY work,employee_uuid',
            (country,)))
    return entries, digest([sorted(manifest), previous_scope, entries])


def configure_staff(conn, manifest, expected=None):
    from .contract import canonical
    entries, proof = staff_plan(conn, manifest)
    transitions = Counter()
    for row in entries:
        target = {'logist': 'logistic', 'delivery': 'courier'}.get(row['work'])
        if target and target != row['current_role']:
            transitions[row['current_role'] + ' → ' + target] += 1
    if expected is not None:
        if expected != proof:
            raise ValueError('Кадровый состав изменился. Повторите предпросмотр.')
        for country in manifest:
            conn.execute('DELETE FROM crm_logistics_staff WHERE country=?', (country,))
        for row in entries:
            conn.execute('INSERT INTO crm_logistics_staff VALUES(?,?,?)',
                         (row['employee_uuid'], row['country'], row['work']))
            if row['work'] in {'logist', 'delivery'}:
                caps = ['logistics'] if row['work'] == 'logist' else ['correct', 'delivery', 'task']
                role = 'logistic' if row['work'] == 'logist' else 'courier'
                conn.execute('UPDATE crm_memberships SET role=?,capabilities_json=? WHERE membership_id=?',
                             (role, canonical(caps), row['membership_id']))
    return dict(countries=sorted(manifest), scope_count=len(entries), role_changes=dict(transitions),
                selection_sha256=proof, applied=expected is not None)


def record_call(principal, body):
    if set(body) != {'contract_uuid', 'revision', 'status', 'comment', 'operation_id'}:
        raise CRMError('Некорректный результат обзвона.')
    cid = text(body['contract_uuid'], 64, 'Договор')
    operation = operation_id(body['operation_id'])
    status = field_value('soft_call_status', text(body['status'], 200, 'Статус дозвона'))
    comment = text(body['comment'], 4000, 'Комментарий')
    if not status or type(body['revision']) is not int:
        raise CRMError('Укажите результат обзвона.')
    with storage.connect() as conn:
        storage.begin(conn, [storage.operation_lock(principal, operation), 'contract:' + cid])
        p = access.refresh_write(conn, principal)
        access.require(p, 'call')
        if p.role != 'employee':
            raise CRMError('Требуется вход исполнителя.', 'forbidden', 403)
        prior = store.replay(conn, p, operation, body)
        if prior is not None:
            return prior
        require_ready(conn)
        from . import withdrawals
        withdrawals.require_contactable(conn, [cid])
        row = for_employee(conn, p.employee_uuid, 'call').get(cid)
        if not row or row['revision'] != body['revision']:
            raise CRMError('Курьерское назначение изменилось.', 'scope_changed', 409)
        primary_fence.require_contracts(conn, [cid])
        at = now()
        revision = row['revision'] + 1
        call_id = str(uuid.uuid4())
        conn.execute('INSERT INTO crm_logistics_calls VALUES(?,?,?,?,?,?,?,?)',
                     (call_id, cid, p.employee_uuid, p.actor_uid, status, comment, row['phone'], at))
        conn.execute('UPDATE crm_logistics_cases SET call_status=?,call_comment=?,call_at=?,revision=? '
                     'WHERE contract_uuid=?', (status, comment, at, revision, cid))
        signal(conn, p, operation, cid, revision, p.employee_uuid, p.employee_uuid, at)
        result = dict(call_id=call_id, revision=revision, at=at)
        store.acknowledge(conn, p, operation, body, result, at)
        return result
