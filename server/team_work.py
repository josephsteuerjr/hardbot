"""Employee board and manager assignments; credentials and business facts stay separate.

Every write is atomic, scoped again at request time, and replayable by operation_id.
Task history survives account removal and never follows a recycled login/uid.
"""
import hashlib
import json
from datetime import datetime, timezone

import accounts
import country_scope
import db
import employee_identity
import staff_roster


def init_schema(c):
    c.executescript('''
        CREATE TABLE IF NOT EXISTS team_work_operations (
          actor TEXT NOT NULL, operation_id TEXT NOT NULL, payload_sha TEXT NOT NULL,
          result TEXT NOT NULL, at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(actor,operation_id));
        CREATE TABLE IF NOT EXISTS employee_board_visibility (
          employee_id TEXT PRIMARY KEY REFERENCES employee_identities(id),
          visible INTEGER NOT NULL DEFAULT 1, actor TEXT NOT NULL,
          updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS staff_tasks (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          employee_id TEXT NOT NULL REFERENCES employee_identities(id),
          employee TEXT NOT NULL, creator_uid INTEGER NOT NULL, creator TEXT NOT NULL,
          title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', due_at TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'open', revision INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS staff_tasks_employee ON staff_tasks(employee_id,status);
        CREATE INDEX IF NOT EXISTS staff_tasks_creator ON staff_tasks(creator_uid,status);
        CREATE TABLE IF NOT EXISTS staff_task_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL REFERENCES staff_tasks(id),
          kind TEXT NOT NULL, text TEXT NOT NULL DEFAULT '', actor TEXT NOT NULL,
          author TEXT NOT NULL, at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS staff_task_steps (
          id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL REFERENCES staff_tasks(id),
          text TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0, author TEXT NOT NULL,
          created_at TEXT NOT NULL);
    ''')


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _replay(c, actor, body):
    op = body.get('operation_id')
    if not isinstance(op, str) or not 12 <= len(op) <= 100:
        raise ValueError('Не указан номер операции. Обновите экран.')
    sha = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    old = c.execute('SELECT * FROM team_work_operations WHERE actor=? AND operation_id=?', (actor, op)).fetchone()
    if old:
        if old['payload_sha'] != sha:
            raise ValueError('Этот номер операции уже использован для другого действия.')
        return op, sha, {**json.loads(old['result']), 'replayed': True}
    return op, sha, None


def _remember(c, actor, op, sha, result):
    c.execute('INSERT INTO team_work_operations(actor,operation_id,payload_sha,result) VALUES(?,?,?,?)',
              (actor, op, sha, json.dumps(result, ensure_ascii=False)))
    return result


def _aliases(c, name):
    keys = {db._name_key(name)}
    ids = {r['employee_id'] for r in c.execute('SELECT * FROM employee_aliases') if db._name_key(r['name']) in keys}
    if len(ids) > 1:
        raise ValueError('Неоднозначное ФИО. Уточните сотрудника в справочнике.')
    if ids:
        keys.update(db._name_key(r[0]) for r in c.execute('SELECT name FROM employee_aliases WHERE employee_id=?', (next(iter(ids)),)))
    for r in staff_roster.rows(c, active_only=False):
        names = {db._name_key(r['employee']), *(db._name_key(x) for x in json.loads(r['product_names']).values())}
        if keys & names:
            keys.update(names)
    return keys


def _registry(c):
    """The roster is authoritative; clients supply candidates absent from it."""
    rows = staff_roster.rows(c, active_only=False)
    country_by_manager = country_scope.employee_countries([
        {'cities': json.loads(r['cities'] or '[]') + json.loads(r['office_cities'] or '[]'),
         'employees': json.loads(r['employees'] or '[]')} for r in c.execute('SELECT * FROM managers')])
    covered = set()
    for r in rows:
        r['products'] = json.loads(r['products'])
        r['offices'] = json.loads(r['offices']) or [r['office']]
        if not r['direction']:
            r['direction'] = country_scope.scope_country(r['offices']) or country_by_manager.get(r['employee'], '')
        covered.update(_aliases(c, r['employee']))
    by_name = {}
    for r in c.execute("SELECT responsible employee, country, city, 'hard' product FROM clients WHERE responsible<>'' "
                       "UNION SELECT responsible_soft employee, country, city, 'soft' product FROM clients WHERE responsible_soft<>''"):
        key = db._name_key(r['employee'])
        if key in covered:
            continue
        item = by_name.setdefault(key, dict(employee=r['employee'], employee_id='', office='', offices=[],
                                           direction='', products=[], active=1, manager_uid=None))
        if r['product'] not in item['products']:
            item['products'].append(r['product'])
        country = str(r['country'] or '').casefold()
        direction = country if country in ('tj', 'uz') else country_scope.scope_country([country]) or country_scope.scope_country([r['city'] or ''])
        if direction:
            item.setdefault('_countries', set()).add(direction)
    has_roster = bool(rows)
    for item in by_name.values():
        countries = item.pop('_countries', set())
        item['direction'] = next(iter(countries)) if len(countries) == 1 else country_by_manager.get(item['employee'], '') if not countries else ''
        # Before migration, show the existing registry team. Afterwards add explicitly.
        item['active'] = int(not has_roster)
    return rows + list(by_name.values())


def _covered(row, team, manager):
    if team is None:
        return True
    if row.get('manager_uid') is not None and row['manager_uid'] != manager['telegram_id']:
        return False
    names = {row['employee'], *json.loads(row.get('product_names') or '{}').values()}
    if {db._name_key(x) for x in names} & {db._name_key(x) for x in team}:
        return True
    cities = {x.casefold() for x in manager.get('office_cities') or []}
    return bool(cities & {x.casefold() for x in row['offices']} or
                manager.get('direction') and manager['direction'] in ('all', row['direction']))


def board(*, team=None, manager=None, writable=True):
    with db.connect() as c:
        privileged = accounts._privileged_names(c)
        hidden = {r[0] for r in c.execute('SELECT employee_id FROM employee_board_visibility WHERE visible=0')}
        accs = [dict(x) for x in c.execute("SELECT id,employee,username,telegram_id,role,status,must_change_password,password_review_pending FROM accounts WHERE role='agent'")]
        devices = [dict(x) for x in c.execute("SELECT account_id,kind,status,last_seen FROM account_devices WHERE status<>'revoked'")]
        out = []
        for r in _registry(c):
            if not _covered(r, team, manager):
                continue
            keys = _aliases(c, r['employee'])
            matches = [a for a in accs if db._name_key(a['employee']) in keys]
            a = matches[0] if len(matches) == 1 else None
            if db._name_key(r['employee']) in privileged and not (a and accounts.separate_staff_account(c, a, keys)):
                continue
            eid = r['employee_id'] or _identity(c, r['employee'])
            devs = [d for d in devices if a and d['account_id'] == a['id']]
            def device_status(kind):
                ds = [d for d in devs if d['kind'] == kind]
                return 'pending' if any(d['status'] == 'pending' for d in ds) else 'active' if any(d['status'] == 'active' for d in ds) else 'none'
            out.append({k: r[k] for k in ('employee', 'employee_id', 'office', 'direction', 'products', 'active')} | {
                'employee_id': eid, 'on_board': bool(r['active'] and eid not in hidden),
                'account_id': a['id'] if a else None, 'username': a['username'] if a else '',
                'login_status': 'ambiguous' if len(matches) > 1 else a['status'] if a else 'none',
                'password_review_pending': bool(a and a['password_review_pending']),
                'must_change_password': bool(a and a['must_change_password']),
                'desktop': device_status('desktop'), 'phone': device_status('phone'),
                'last_seen': max((d['last_seen'] or '' for d in devs), default=''),
                'can_remove': writable, 'can_delete_profile': writable and len(matches) <= 1})
    return {'ok': True, 'employees': sorted(out, key=lambda x: (x['direction'], x['employee'])), 'can_edit': writable}


def board_change(body, *, actor, team=None, manager=None):
    if body.get('action') not in ('add', 'hide', 'remove') or not isinstance(body.get('employee'), str):
        raise ValueError('Выберите сотрудника и действие.')
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        op, sha, replay = _replay(c, actor, body)
        if replay:
            return replay
        row = next((r for r in _registry(c) if r['employee'] == body['employee'] and _covered(r, team, manager)), None)
        if not row or db._name_key(row['employee']) in accounts._privileged_names(c):
            raise ValueError('Сотрудник не найден в доступном списке реестра.')
        keys = _aliases(c, row['employee'])
        matches = [r for r in c.execute('SELECT * FROM accounts') if db._name_key(r['employee']) in keys]
        if body['action'] == 'remove':
            if body.get('confirm') != 'remove_and_delete_profile':
                raise ValueError('Подтвердите: «Убрать и удалить профиль».')
            if len(matches) > 1 or any(r['role'] != 'agent' for r in matches):
                raise ValueError('Неоднозначный или служебный профиль. Обратитесь к администратору.')
            current_id = matches[0]['id'] if matches else None
            if (body.get('account_id') != current_id or 'account_id' not in body or
                    body['account_id'] is not None and type(body['account_id']) is not int):
                raise ValueError('Профиль изменился. Обновите доску и повторите выбор.')
            if matches:
                result = accounts.delete_account_in_transaction(c, matches[0]['username'], actor, expected_id=current_id)
                if not result.get('ok'):
                    raise ValueError(result['error'])
        employee_identity.ensure(c, [row['employee']])
        eid = c.execute('SELECT employee_id FROM employee_aliases WHERE name=?', (row['employee'],)).fetchone()[0]
        # Board visibility never changes account eligibility or active roster membership.
        # Only deletion, or reactivating a previously inactive employee, changes the roster.
        if body['action'] == 'remove' or body['action'] == 'add' and not row['active']:
            if not c.execute('SELECT 1 FROM staff_roster LIMIT 1').fetchone():
                for r in _registry(c):
                    employee_identity.ensure(c, [r['employee']])
                    ident = c.execute('SELECT employee_id FROM employee_aliases WHERE name=?', (r['employee'],)).fetchone()[0]
                    c.execute('INSERT OR IGNORE INTO staff_roster(employee_id,office,direction,products,source_sha256) VALUES(?,?,?,?,?)',
                              (ident, r['office'], r['direction'], json.dumps(r['products']), 'initial-registry-board'))
            c.execute('INSERT INTO staff_roster(employee_id,office,direction,products,active,source_sha256) VALUES(?,?,?,?,?,?) '
                      'ON CONFLICT(employee_id) DO UPDATE SET active=excluded.active,updated_at=CURRENT_TIMESTAMP',
                      (eid, row['office'], row['direction'], json.dumps(row['products']), int(body['action'] == 'add'), 'employee-board'))
        c.execute('INSERT INTO employee_board_visibility(employee_id,visible,actor,updated_at) VALUES(?,?,?,?) '
                  'ON CONFLICT(employee_id) DO UPDATE SET visible=excluded.visible,actor=excluded.actor,updated_at=excluded.updated_at',
                  (eid, int(body['action'] == 'add'), actor, _now()))
        result = _remember(c, actor, op, sha, {'ok': True, 'employee': row['employee'], 'action': body['action']})
        c.commit()
    return result


def _text(body, key, maximum, *, required=True):
    value = body.get(key, '')
    if not isinstance(value, str) or len(value.strip()) > maximum or (required and not value.strip()):
        raise ValueError({'title': 'Укажите название (до 240 символов).', 'text': 'Укажите текст (до 4000 символов).',
                          'description': 'Описание: до 8000 символов.'}.get(key, 'Проверьте поле ' + key))
    return value.strip()


def _identity(c, employee):
    keys = _aliases(c, employee)
    ids = {r['employee_id'] for r in c.execute('SELECT * FROM employee_aliases') if db._name_key(r['name']) in keys}
    if len(ids) > 1:
        raise ValueError('Неоднозначная идентичность сотрудника.')
    return next(iter(ids), '')


def _rights(c, task, context):
    creator = context['kind'] == 'manager' and task['creator_uid'] == context['uid']
    assignee = context['kind'] == 'employee' and task['employee_id'] == _identity(c, context['name'])
    visible = creator or assignee or context['kind'] == 'analyst' or (context['kind'] == 'manager' and
               bool(_aliases(c, task['employee']) & {db._name_key(x) for x in context['team']}))
    return visible, creator and context.get('writable', True), assignee


def _task(c, row, context, *, detail=False):
    result = dict(row)
    _, creator, assignee = _rights(c, row, context)
    result['can_manage'] = creator
    result['can_work'] = assignee
    result['can_contribute'] = (creator or assignee) and row['status'] != 'confirmed'
    result['overdue'] = row['status'] != 'confirmed' and row['due_at'] < _now()
    result['steps'] = [dict(s) for s in c.execute('SELECT * FROM staff_task_steps WHERE task_id=? ORDER BY id', (row['id'],))]
    if detail:
        result['events'] = [dict(e) for e in c.execute('SELECT * FROM staff_task_events WHERE task_id=? ORDER BY id', (row['id'],))]
    return result


def task_list(context, task_id=None):
    with db.connect() as c:
        rows = c.execute('SELECT * FROM staff_tasks ORDER BY CASE status WHEN \'confirmed\' THEN 1 ELSE 0 END,due_at,id DESC').fetchall()
        rows = [r for r in rows if _rights(c, r, context)[0]]
        if task_id is not None:
            row = next((r for r in rows if r['id'] == task_id), None)
            if row is None:
                raise ValueError('Задача не найдена или доступ закрыт.')
            return {'ok': True, 'task': _task(c, row, context, detail=True)}
        return {'ok': True, 'tasks': [_task(c, r, context) for r in rows],
                'can_create': context['kind'] == 'manager' and context.get('writable', True)}


def task_change(body, context):
    actor = context['actor']
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        op, sha, replay = _replay(c, actor, body)
        if replay:
            return replay
        action = body.get('action')
        now = _now()
        if action == 'create':
            if context['kind'] != 'manager' or not context.get('writable', True):
                raise ValueError('Задачи назначает руководитель.')
            employee = body.get('employee')
            if not isinstance(employee, str):
                raise ValueError('Выберите сотрудника.')
            row = next((r for r in _registry(c) if r['employee'] == employee and r['active'] and
                        _covered(r, context['team'], context['manager'])), None)
            if not row or db._name_key(employee) in accounts._privileged_names(c):
                raise ValueError('Сотрудник не входит в действующую команду.')
            title, description = _text(body, 'title', 240), _text(body, 'description', 8000, required=False)
            try:
                date = datetime.fromisoformat(str(body.get('due_at') or '').replace('Z', '+00:00'))
                if date.tzinfo is None:
                    raise ValueError()
                due = date.astimezone(timezone.utc).isoformat(timespec='seconds')
            except (ValueError, OverflowError):
                raise ValueError('Укажите срок с датой и временем.')
            employee_identity.ensure(c, [employee])
            eid = _identity(c, employee)
            tid = c.execute('INSERT INTO staff_tasks(employee_id,employee,creator_uid,creator,title,description,due_at,created_at,updated_at) '
                            'VALUES(?,?,?,?,?,?,?,?,?)', (eid, employee, context['uid'], context['name'], title, description, due, now, now)).lastrowid
            text = title
        else:
            tid = body.get('id')
            if type(tid) is not int:
                raise ValueError('Выберите задачу.')
            row = c.execute('SELECT * FROM staff_tasks WHERE id=?', (tid,)).fetchone()
            if row is None:
                raise ValueError('Задача не найдена или доступ закрыт.')
            visible, creator, assignee = _rights(c, row, context)
            if not visible or not (creator or assignee):
                raise ValueError('Изменять задачу могут её руководитель и исполнитель.')
            if type(body.get('revision')) is not int or body['revision'] != row['revision']:
                raise ValueError('Задача изменилась. Обновите её перед сохранением.')
            if row['status'] == 'confirmed':
                raise ValueError('Задача уже подтверждена.')
            text = ''
            if action in ('note', 'step'):
                text = _text(body, 'text', 4000)
                if action == 'step':
                    if row['status'] == 'submitted':
                        raise ValueError('Сначала верните задачу в работу.')
                    c.execute('INSERT INTO staff_task_steps(task_id,text,author,created_at) VALUES(?,?,?,?)', (tid, text, context['name'], now))
            elif action == 'toggle_step':
                if row['status'] == 'submitted' or type(body.get('step_id')) is not int or type(body.get('done')) is not bool:
                    raise ValueError('Проверьте этап и статус задачи.')
                step = c.execute('SELECT * FROM staff_task_steps WHERE id=? AND task_id=?', (body['step_id'], tid)).fetchone()
                if not step:
                    raise ValueError('Этап не найден.')
                c.execute('UPDATE staff_task_steps SET done=? WHERE id=?', (int(body['done']), step['id']))
                text = ('Выполнен: ' if body['done'] else 'В работе: ') + step['text']
            elif action == 'submit':
                if not assignee or row['status'] != 'open':
                    raise ValueError('Результат сдаёт исполнитель задачи в работе.')
                text = _text(body, 'text', 4000)
                if c.execute('SELECT 1 FROM staff_task_steps WHERE task_id=? AND done=0', (tid,)).fetchone():
                    raise ValueError('Завершите все этапы перед сдачей результата.')
                c.execute("UPDATE staff_tasks SET status='submitted' WHERE id=?", (tid,))
            elif action in ('confirm', 'return'):
                if not creator or row['status'] != 'submitted':
                    raise ValueError('Руководитель подтверждает или возвращает сданную задачу.')
                text = _text(body, 'text', 4000, required=action == 'return')
                c.execute('UPDATE staff_tasks SET status=? WHERE id=?', ('confirmed' if action == 'confirm' else 'open', tid))
            else:
                raise ValueError('Неизвестное действие задачи.')
            c.execute('UPDATE staff_tasks SET revision=revision+1,updated_at=? WHERE id=?', (now, tid))
        c.execute('INSERT INTO staff_task_events(task_id,kind,text,actor,author,at) VALUES(?,?,?,?,?,?)',
                  (tid, action, text, actor, context['name'], now))
        result = _remember(c, actor, op, sha, {'ok': True, 'id': tid})
        c.commit()
    return result
