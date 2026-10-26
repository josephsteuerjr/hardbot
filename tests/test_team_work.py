"""Disposable HTTP/SQLite journeys for the employee board and task hand-off."""
import json
import uuid

import pytest

import accounts
import db
import staff_roster
import team_work
from test_manager_account_approval import world, call, register, login, MGR
from test_manager_password_reset import activated

BOARD = '/api/manager/employee_board'
TASKS = '/api/manager/staff_tasks'


def post(path, headers, action, **fields):
    return call('POST', path, headers, dict(action=action, operation_id=uuid.uuid4().hex, **fields))


def create(world, **fields):
    status, result = post(TASKS, world, 'create', employee='Own Employee', title='Сверить оплату',
                          description='Проверить подтверждение', due_at='2026-10-20T18:30:00+05:00', **fields)
    assert status == 200, result
    return result['id']


def detail(headers, tid, path=TASKS):
    code, result = call('GET', path + '?id=' + str(tid), headers)
    assert code == 200, result
    return result['task']


def change(headers, tid, action, path=TASKS, **fields):
    row = detail(headers, tid, path)
    return post(path, headers, action, id=tid, revision=row['revision'], **fields)


def roster():
    rows = [dict(employee=name, products=['hard'], direction=country, office='Synthetic Office')
            for name, country in [('Own Employee', 'tj'), ('Foreign Employee', 'uz')]]
    preview = staff_roster.preview(rows)
    assert staff_roster.apply(rows, preview['sha256'], by='test')['ok']


def test_board_scopes_candidates_and_discloses_no_credentials(world):
    roster()
    assert register()[0] == 201
    status, result = call('GET', BOARD, world)
    assert status == 200
    assert [x['employee'] for x in result['employees']] == ['Own Employee']
    row = result['employees'][0]
    assert row['direction'] == 'tj' and row['username'] == 'self-own'
    assert row['desktop'] == 'pending' and row['login_status'] == 'awaiting'
    assert not {'password_hash', 'token', 'fingerprint', 'gps'} & row.keys()
    assert post(BOARD, world, 'add', employee='Foreign Employee')[0] == 409
    assert post(BOARD, world, 'add', employee='Not in registry')[0] == 409
    db.set_manager(MGR, 'Synthetic Leader', [], ['Own Employee'], role='security')
    assert not call('GET', BOARD, world)[1]['can_edit']
    assert post(BOARD, world, 'remove', employee='Own Employee', account_id=None,
                confirm='remove_and_delete_profile')[0] == 403


def test_remove_deletes_profile_revokes_devices_and_preserves_facts_tasks(world):
    token, _ = activated(world)
    tid = create(world)
    row = call('GET', BOARD, world)[1]['employees'][0]
    wrong = dict(employee=row['employee'], account_id=row['account_id'] + 1, confirm='remove_and_delete_profile')
    assert post(BOARD, world, 'remove', **wrong)[0] == 409
    assert db.agent_device_check(token)[0] == 'ok'
    body = dict(action='remove', employee=row['employee'], account_id=row['account_id'],
                confirm='remove_and_delete_profile', operation_id='remove-synthetic-001')
    assert call('POST', BOARD, world, body)[0] == 200
    assert call('POST', BOARD, world, body)[1]['replayed']
    assert db.agent_device_check(token)[0] != 'ok'
    assert not call('GET', BOARD, world)[1]['employees'][0]['active']
    with db.connect() as c:
        assert c.execute('SELECT count(*) FROM accounts').fetchone()[0] == 0
        assert c.execute("SELECT responsible FROM clients WHERE contract_id='SELF-1'").fetchone()[0] == 'Own Employee'
        assert c.execute('SELECT count(*) FROM staff_tasks WHERE id=?', (tid,)).fetchone()[0] == 1
        assert c.execute('SELECT count(*) FROM account_deletions').fetchone()[0] == 1
        assert c.execute('SELECT active FROM staff_roster r JOIN employee_identities i ON i.id=r.employee_id WHERE i.display_name=?', ('Foreign Employee',)).fetchone()[0] == 1
    assert 'Own Employee' not in [x['employee'] for x in call('GET', '/api/account/staff')[1]['employees']]
    assert post(BOARD, world, 'add', employee='Own Employee')[0] == 200
    assert call('GET', BOARD, world)[1]['employees'][0]['login_status'] == 'none'
    assert db.agent_device_check(token)[0] != 'ok'


def test_remove_is_atomic_when_credential_cleanup_fails(world, monkeypatch):
    token, _ = activated(world)
    def fail(c, *args, **kwargs):
        c.execute('DELETE FROM accounts')
        return {'ok': False, 'error': 'Synthetic failure'}
    monkeypatch.setattr(accounts, 'delete_account_in_transaction', fail)
    row = call('GET', BOARD, world)[1]['employees'][0]
    assert post(BOARD, world, 'remove', employee=row['employee'], account_id=row['account_id'], confirm='remove_and_delete_profile')[0] == 409
    assert db.agent_device_check(token)[0] == 'ok'
    assert call('GET', BOARD, world)[1]['employees'][0]['active']


@pytest.mark.parametrize('with_roster', [False, True])
def test_remove_from_board_keeps_account_devices_roster_portfolio_and_tasks(world, with_roster):
    if with_roster:
        roster()
    token, _ = activated(world)
    phone = login(fp='board-preserved-phone', kind='phone')
    with db.connect() as c:
        phone_id = c.execute("SELECT id FROM account_devices WHERE fingerprint='board-preserved-phone'").fetchone()[0]
    assert accounts.manager_approve(MGR, device_id=phone_id)['active']
    tid = create(world)
    tables = ('accounts', 'account_devices', 'agent_devices', 'staff_roster', 'clients',
              'staff_tasks', 'staff_task_events', 'staff_task_steps', 'agent_bind', 'account_deletions')
    def snapshot():
        with db.connect() as c:
            return {table: [tuple(r) for r in c.execute(f'SELECT * FROM {table} ORDER BY rowid')] for table in tables}
    before = snapshot()
    body = dict(action='hide', employee='Own Employee', operation_id='hide-preserve-operation')
    assert call('POST', BOARD, world, body)[0] == 200
    assert call('POST', BOARD, world, body)[1]['replayed']
    row = call('GET', BOARD, world)[1]['employees'][0]
    assert not row['on_board'] and row['active'] and row['username'] == 'self-own'
    assert snapshot() == before
    assert db.agent_device_check(token)[0] == 'ok'
    assert db.agent_device_check(phone['token'])[0] == 'ok'
    assert call('GET', '/api/staff_tasks', {'X-App-Token': token})[1]['tasks'][0]['id'] == tid
    assert 'Own Employee' in [x['employee'] for x in call('GET', '/api/account/staff')[1]['employees']]
    assert post(BOARD, world, 'add', employee='Own Employee')[0] == 200
    assert call('GET', BOARD, world)[1]['employees'][0]['on_board']
    with db.connect() as c:
        assert [tuple(r) for r in c.execute('SELECT * FROM accounts ORDER BY rowid')] == before['accounts']
        assert [tuple(r) for r in c.execute('SELECT * FROM staff_roster ORDER BY rowid')] == before['staff_roster']
    assert db.agent_device_check(token)[0] == 'ok'
    assert db.agent_device_check(phone['token'])[0] == 'ok'


def test_hide_does_not_expand_manager_scope_or_allow_read_only_role(world):
    assert post(BOARD, world, 'hide', employee='Foreign Employee')[0] == 409
    db.set_manager(MGR, 'Synthetic Leader', [], ['Own Employee'], role='security')
    assert post(BOARD, world, 'hide', employee='Own Employee')[0] == 403


def test_both_participants_add_stages_and_notes_then_employee_submits_manager_confirms(world):
    token, _ = activated(world)
    employee = {'X-App-Token': token}
    tid = create(world)
    assert detail(world, tid)['due_at'] == '2026-10-20T13:30:00+00:00'
    assert change(world, tid, 'step', text='Проверить чек')[0] == 200
    assert change(employee, tid, 'note', '/api/staff_tasks', text='Запросил документ', author='forged')[0] == 200
    assert change(employee, tid, 'step', '/api/staff_tasks', text='Сверить сумму')[0] == 200
    assert change(employee, tid, 'submit', '/api/staff_tasks', text='Готово')[0] == 409
    for step in detail(employee, tid, '/api/staff_tasks')['steps']:
        assert change(employee, tid, 'toggle_step', '/api/staff_tasks', step_id=step['id'], done=True)[0] == 200
    assert change(world, tid, 'submit', text='Готово')[0] == 409
    assert change(employee, tid, 'submit', '/api/staff_tasks', text='Все суммы совпадают')[0] == 200
    assert change(employee, tid, 'confirm', '/api/staff_tasks')[0] == 409
    assert change(world, tid, 'return', text='')[0] == 409
    assert change(world, tid, 'return', text='Проверь ещё дату')[0] == 200
    assert change(employee, tid, 'submit', '/api/staff_tasks', text='Дата проверена')[0] == 200
    assert change(world, tid, 'confirm')[0] == 200
    final = detail(world, tid)
    assert final['status'] == 'confirmed' and not final['overdue'] and not final['can_contribute']
    assert change(world, tid, 'note', text='Поздняя заметка')[0] == 409
    events = final['events']
    assert any(e['kind'] == 'submit' and e['author'] == 'Own Employee' for e in events)
    assert any(e['kind'] == 'confirm' and e['author'] == 'Synthetic Leader' for e in events)
    assert all(e['author'] != 'forged' for e in events)


def test_optimistic_revision_and_retry_do_not_duplicate_tasks_or_notes(world):
    activated(world)
    body = dict(action='create', employee='Own Employee', title='Task', due_at='2026-11-01T00:00:00Z', operation_id='create-task-operation-001')
    tid = call('POST', TASKS, world, body)[1]['id']
    assert call('POST', TASKS, world, body)[1]['replayed']
    assert call('POST', TASKS, world, {**body, 'title': 'Different'})[0] == 409
    revision = detail(world, tid)['revision']
    note = dict(action='note', id=tid, text='Note', revision=revision, operation_id='note-operation-001')
    assert call('POST', TASKS, world, note)[0] == 200
    assert call('POST', TASKS, world, note)[1]['replayed']
    assert post(TASKS, world, 'note', id=tid, text='Stale', revision=revision)[0] == 409
    assert len(detail(world, tid)['events']) == 2


def test_foreign_employee_cannot_see_modify_or_submit_someone_elses_task(world):
    activated(world)
    tid = create(world)
    assert register('Foreign Employee', 'foreign-task')[0] == 201
    # Activate the synthetic outsider without widening this manager's scope.
    with db.connect() as c:
        c.execute("UPDATE accounts SET status='active' WHERE username='foreign-task'")
        c.execute("UPDATE account_devices SET status='active' WHERE account_id=(SELECT id FROM accounts WHERE username='foreign-task')")
    token = login('foreign-task')['token']
    outsider = {'X-App-Token': token}
    assert call('GET', '/api/staff_tasks', outsider)[1]['tasks'] == []
    assert call('GET', '/api/staff_tasks?id=' + str(tid), outsider)[0] == 409
    assert post('/api/staff_tasks', outsider, 'note', id=tid, revision=1, text='Forged')[0] == 409
    assert post(TASKS, world, 'create', employee='Foreign Employee', title='No', due_at='2026-11-01T00:00:00Z')[0] == 409
    assert call('GET', TASKS)[0] == 401
    assert call('GET', '/api/staff_tasks')[0] == 401


def test_recheck_current_scope_before_create_and_protect_task_participants(world):
    activated(world)
    tid = create(world)
    db.set_manager(MGR, 'Synthetic Leader', [], [])
    assert post(TASKS, world, 'create', employee='Own Employee', title='No', due_at='2026-11-01T00:00:00Z')[0] == 409
    # The assigning leader keeps the hand-off history after a team transfer.
    assert detail(world, tid)['can_manage']
    db.set_manager(MGR, 'Synthetic Leader', [], [], role='security')
    assert not detail(world, tid)['can_manage']
    assert change(world, tid, 'note', text='No')[0] == 403


def test_republic_uses_explicit_roster_then_city_without_guessing_unknown(world):
    with db.connect() as c:
        c.execute("UPDATE clients SET city='Ташкент' WHERE responsible='Own Employee'")
    assert call('GET', BOARD, world)[1]['employees'][0]['direction'] == 'uz'
    roster()
    assert call('GET', BOARD, world)[1]['employees'][0]['direction'] == 'tj'


def test_stale_dialog_cannot_delete_recreated_account(world):
    activated(world)
    row = call('GET', BOARD, world)[1]['employees'][0]
    assert accounts.delete_account(row['username'], 'test', expected_id=row['account_id'])['ok']
    assert register()[0] == 201
    assert post(BOARD, world, 'remove', employee=row['employee'], account_id=row['account_id'], confirm='remove_and_delete_profile')[0] == 409
    with db.connect() as c:
        assert c.execute('SELECT id FROM accounts').fetchone()[0] != row['account_id']


def test_analyst_sees_company_board_and_task_history_but_cannot_impersonate_leader(world):
    from test_manager_password_reset import analyst_headers
    activated(world)
    tid = create(world)
    headers = analyst_headers()
    assert call('GET', '/admin/employee_board.json', headers)[0] == 200
    assert {r['employee'] for r in call('GET', '/admin/employee_board.json', headers)[1]['employees']} == {'Own Employee', 'Foreign Employee'}
    row = detail(headers, tid, '/admin/staff_tasks.json')
    assert not row['can_manage'] and not row['can_contribute']
    assert not call('GET', '/admin/staff_tasks.json', headers)[1]['can_create']
    assert post('/admin/staff_tasks.json', headers, 'confirm', id=tid, revision=row['revision'])[0] == 409
    assert call('GET', '/admin/employee_board.json', world)[0] >= 400


@pytest.mark.parametrize('body', [[], None, {'action': 'create'}, {'action':'create','employee':'Own Employee','title':'Task','due_at':'2026-11-01'}])
def test_malformed_task_writes_never_mutate(world, body):
    if isinstance(body, dict):
        body['operation_id'] = uuid.uuid4().hex
    assert call('POST', TASKS, world, body)[0] >= 400
    assert call('GET', TASKS, world)[1]['tasks'] == []
