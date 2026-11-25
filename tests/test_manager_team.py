import accounts,db
from test_account_cutover import world,MANAGER,STAFF,EMP,TEST_SECRET


def test_team_device_revoke_rechecks_scope_and_is_idempotent(world):
    assert accounts.create_account(EMP,'team-staff',TEST_SECRET)['ok']
    with db.connect() as c:
        aid=c.execute("SELECT id FROM accounts WHERE username='team-staff'").fetchone()[0]
        did=c.execute("INSERT INTO account_devices(account_id,kind,fingerprint,label,status,entrance,first_seen) VALUES(?,'phone','team-test-phone','Test','pending','internal','2026-01-01')",(aid,)).lastrowid
    team=accounts.manager_team(MANAGER)
    row=next(r for r in team['employees'] if r['employee']==EMP)
    assert row['account']['account_id']==aid and row['can_revoke']
    assert not accounts.manager_revoke_device(MANAGER+10,did)['ok']
    assert accounts.manager_revoke_device(MANAGER,did)['ok']
    assert accounts.manager_revoke_device(MANAGER,did)['ok']
    with db.connect() as c:
        assert c.execute('SELECT status FROM account_devices WHERE id=?',(did,)).fetchone()[0]=='revoked'
        assert c.execute("SELECT COUNT(*) FROM account_approval_events WHERE device_id=? AND decision='revoked'",(did,)).fetchone()[0]==1
