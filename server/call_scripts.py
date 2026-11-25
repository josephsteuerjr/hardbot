"""Team call guidance. Drafts never reach employees; scope is checked live."""
import hashlib
import json
import re
from datetime import datetime, timezone
import db
import digest_data
import staff_roster


class ScriptError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def init_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS team_call_scripts (
        manager_uid INTEGER PRIMARY KEY, revision INTEGER NOT NULL,
        draft_json TEXT NOT NULL, published_json TEXT,
        published_revision INTEGER, updated_at TEXT NOT NULL, published_at TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS team_call_script_operations (
        manager_uid INTEGER NOT NULL, operation_id TEXT NOT NULL,
        request_sha256 TEXT NOT NULL, result_json TEXT NOT NULL, at TEXT NOT NULL,
        PRIMARY KEY(manager_uid,operation_id))''')


def _context(uid):
    from config import ADMIN_ID
    if type(uid) is not int or uid <= 0:
        raise ScriptError('Руководитель не назначен.', 403)
    mgr = digest_data.manager_scope(uid, is_owner=uid == ADMIN_ID)
    if not mgr or not db.manager_caps(mgr.get('role'), is_owner=uid == ADMIN_ID)['binds_edit']:
        raise ScriptError('Скрипт редактирует руководитель своей команды.', 403)
    return mgr, {db._name_key(n) for n in digest_data.sorted_employees(mgr)}


def _members(conn, uid, team):
    return [dict(employee_id=r['employee_id'], employee=r['employee'])
            for r in staff_roster.rows(conn)
            if 'soft' in json.loads(r['products'])
            and r['manager_uid'] == uid
            and any(db._name_key(n) in team for n in [r['employee'], *json.loads(r['product_names']).values()])]


def _state(conn, uid, mgr, team):
    row = conn.execute('SELECT * FROM team_call_scripts WHERE manager_uid=?', (uid,)).fetchone()
    return dict(ok=True, manager_uid=uid, revision=row['revision'] if row else 0,
                draft=json.loads(row['draft_json']) if row else None,
                published=json.loads(row['published_json']) if row and row['published_json'] else None,
                published_revision=row['published_revision'] if row else None,
                published_at=row['published_at'] if row else None,
                author=mgr.get('name') or '', team=_members(conn, uid, team))


def manager_state(uid):
    with db.connect() as conn:
        mgr, team = _context(uid)
        return _state(conn, uid, mgr, team)


def _content(raw):
    if not isinstance(raw, dict) or set(raw) != {'title', 'steps'}:
        raise ScriptError('Укажите название и шаги скрипта.')
    title, steps = raw['title'], raw['steps']
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 100:
        raise ScriptError('Название: от 1 до 100 символов.')
    if not isinstance(steps, list) or not 1 <= len(steps) <= 12:
        raise ScriptError('Добавьте от 1 до 12 шагов.')
    result = []
    for step in steps:
        if (not isinstance(step, dict) or set(step) != {'title', 'text'}
                or not isinstance(step['title'], str) or not isinstance(step['text'], str)
                or not 1 <= len(step['title'].strip()) <= 80
                or not 1 <= len(step['text'].strip()) <= 6000):
            raise ScriptError('У каждого шага нужны заголовок до 80 и текст до 6000 символов.')
        result.append(dict(title=step['title'].strip(), text=step['text'].strip()))
    value = dict(title=title.strip(), steps=result)
    if len(json.dumps(value, ensure_ascii=False)) > 24000:
        raise ScriptError('Скрипт: не более 24 000 символов.')
    return value


def mutate(uid, body):
    if not isinstance(body, dict) or set(body) - {'action', 'base_revision', 'operation_id', 'content'}:
        raise ScriptError('Некорректный запрос.')
    action, revision, operation = body.get('action'), body.get('base_revision'), body.get('operation_id')
    if action not in ('save', 'publish', 'withdraw') or type(revision) is not int or revision < 0:
        raise ScriptError('Обновите скрипт перед изменением.')
    if not isinstance(operation, str) or not re.fullmatch(r'[A-Za-z0-9_-]{12,100}', operation):
        raise ScriptError('operation_id')
    content = _content(body.get('content')) if action == 'save' else None
    if action != 'save' and 'content' in body:
        raise ScriptError('Публикуется только сохранённый черновик.')
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        mgr, team = _context(uid)
        old = conn.execute('SELECT * FROM team_call_script_operations WHERE manager_uid=? AND operation_id=?', (uid, operation)).fetchone()
        if old:
            if old['request_sha256'] != digest:
                raise ScriptError('Этот operation_id уже использован с другим содержимым.', 409)
            return {**_state(conn, uid, mgr, team), 'replayed': True}
        row = conn.execute('SELECT * FROM team_call_scripts WHERE manager_uid=?', (uid,)).fetchone()
        if (row['revision'] if row else 0) != revision:
            raise ScriptError('Скрипт изменён в другом окне. Скопируйте свои правки и загрузите актуальный.', 409)
        if action != 'save' and not row:
            raise ScriptError('Сначала сохраните черновик.')
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        if action == 'save':
            conn.execute('INSERT INTO team_call_scripts(manager_uid,revision,draft_json,updated_at) VALUES(?,?,?,?) '
                'ON CONFLICT(manager_uid) DO UPDATE SET revision=excluded.revision,draft_json=excluded.draft_json,updated_at=excluded.updated_at',
                (uid, revision + 1, json.dumps(content, ensure_ascii=False), now))
        elif action == 'publish':
            if not _members(conn, uid, team):
                raise ScriptError('В команде нет активных сотрудников Софта с указанным руководителем.')
            conn.execute('UPDATE team_call_scripts SET published_json=draft_json,published_revision=?,published_at=?,revision=?,updated_at=? WHERE manager_uid=?',
                (revision + 1, now, revision + 1, now, uid))
        else:
            conn.execute('UPDATE team_call_scripts SET published_json=NULL,published_revision=NULL,published_at=NULL,revision=?,updated_at=? WHERE manager_uid=?',
                (revision + 1, now, uid))
        result = _state(conn, uid, mgr, team)
        conn.execute('INSERT INTO team_call_script_operations VALUES(?,?,?,?,?)',
            (uid, operation, digest, json.dumps(dict(action=action, revision=result['revision'])), now))
        conn.commit()
    db.audit_access(uid, 'call_script_' + action, str(uid), f'revision={result["revision"]}; operation={operation}')
    return result


def employee_script(employee):
    # Use the current roster's explicit leader. A broad regional scope never
    # overrides the employee's own leader or exposes somebody else's draft.
    key = db._name_key(employee)
    with db.connect() as conn:
        matches = [r for r in staff_roster.rows(conn, active_only=False)
                   if key in {db._name_key(n) for n in [r['employee'], *json.loads(r['product_names']).values()]}]
        if len(matches) != 1 or not matches[0]['active'] or 'soft' not in json.loads(matches[0]['products']):
            return dict(ok=True, script=None)
        row = matches[0]
        try:
            mgr, team = _context(row['manager_uid'])
        except ScriptError:
            return dict(ok=True, script=None)
        if row['employee_id'] not in {r['employee_id'] for r in _members(conn, row['manager_uid'], team)}:
            return dict(ok=True, script=None)
        script = conn.execute('SELECT published_json,published_revision,published_at FROM team_call_scripts WHERE manager_uid=?', (row['manager_uid'],)).fetchone()
        if not script or not script['published_json']:
            return dict(ok=True, script=None)
        return dict(ok=True, script=json.loads(script['published_json']),
                    revision=script['published_revision'], published_at=script['published_at'], author=mgr['name'])
