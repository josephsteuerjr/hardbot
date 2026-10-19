"""Doctor must recognize a real usable owner, not a legacy Telegram setting."""
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize('scenario,expected', [
    ('ready', 'ready'), ('no_pc', 'needs_pc'), ('phone_only', 'needs_pc'),
    ('pending_pc', 'needs_pc'), ('disabled', 'missing'),
    ('untrusted_grant', 'missing'), ('new_session', 'ready'),
    ('no_owner', 'missing'), ('legacy_schema', 'legacy'),
])
def test_owner_diagnostic_uses_authority_and_live_signed_pc(tmp_path, scenario, expected):
    path = tmp_path / 'authority.db'
    c = sqlite3.connect(path)
    c.execute('CREATE TABLE settings(key TEXT, value TEXT)')
    if scenario != 'legacy_schema':
        c.execute("INSERT INTO settings VALUES('account_cutover_activated_at_utc','enabled')")
        c.executescript('''
            CREATE TABLE accounts(id INTEGER,role TEXT,status TEXT,must_change_password INTEGER);
            CREATE TABLE account_admin_grants(account_id INTEGER,granted_by TEXT,principal TEXT);
            CREATE TABLE account_devices(id INTEGER,account_id INTEGER,kind TEXT,status TEXT,token_table TEXT,public_key TEXT);
            CREATE TABLE manager_devices(id INTEGER,account_id INTEGER,revoked_at TEXT);
            CREATE TABLE account_token_sessions(device_id INTEGER,token_table TEXT,token_id INTEGER);
        ''')
        if scenario != 'no_owner':
            c.execute('INSERT INTO accounts VALUES(1,?,?,0)', ('analyst', 'disabled' if scenario == 'disabled' else 'active'))
            c.execute('INSERT INTO account_admin_grants VALUES(1,?,?)',
                      ('http' if scenario == 'untrusted_grant' else 'ssh-root', 'test-owner'))
            if scenario != 'no_pc':
                c.execute('INSERT INTO account_devices VALUES(10,1,?,?,?,?)',
                          ('phone' if scenario == 'phone_only' else 'desktop',
                           'pending' if scenario == 'pending_pc' else 'active', 'manager_devices', 'test-public-key'))
                c.execute('INSERT INTO manager_devices VALUES(20,1,?)', ('revoked' if scenario == 'new_session' else None,))
                c.execute("INSERT INTO account_token_sessions VALUES(10,'manager_devices',20)")
                if scenario == 'new_session':
                    c.execute('INSERT INTO manager_devices VALUES(21,1,NULL)')
                    c.execute("INSERT INTO account_token_sessions VALUES(10,'manager_devices',21)")
    c.commit()
    schema_before = c.execute('SELECT sql FROM sqlite_master ORDER BY name').fetchall()
    c.close()
    shell = (Path(__file__).resolve().parents[1] / 'deploy/units/core/doctor.sh').read_text('utf-8')
    program = shell.split("<<'PY' 2>/dev/null\n", 1)[1].split('\nPY\n', 1)[0]
    program = program.replace("'file:/data/hardbot.db?mode=ro'", repr('file:' + path.as_posix() + '?mode=ro'))
    run = subprocess.run([sys.executable, '-B', '-c', program], capture_output=True, text=True, check=True)
    assert run.stdout.strip() == expected
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT sql FROM sqlite_master ORDER BY name').fetchall() == schema_before
