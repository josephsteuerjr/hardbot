"""New mobile audience preserves existing role/grant/device boundaries."""
import accounts,db

def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'accounts.sqlite3');db.init_db()
    monkeypatch.setenv('HARD_CRM_MOBILE','1')

def test_mobile_audience_requires_explicit_enablement(tmp_path,monkeypatch):
    setup(tmp_path,monkeypatch)
    assert accounts.create_account('Synthetic Phone Employee','mobile-employee','initial123',telegram_id=99001,by='fixture')['ok']
    device={'kind':'phone','fp':'mobile-phone-fixture','label':'Synthetic phone'}
    monkeypatch.setenv('HARD_CRM_MOBILE','0')
    assert accounts.login('mobile-employee','initial123',device=device,entrance='public',audience='crm-mobile')['action']=='blocked'
    monkeypatch.setenv('HARD_CRM_MOBILE','1')
    result=accounts.login('mobile-employee','initial123',device=device,entrance='public',audience='crm-mobile')
    assert result['action']=='ok_pending'
    with db.connect() as conn:assert conn.execute("SELECT status FROM account_devices WHERE fingerprint=?",(device['fp'],)).fetchone()[0]=='pending'

def test_analyst_phone_requires_existing_admin_grant_and_own_approval(tmp_path,monkeypatch):
    setup(tmp_path,monkeypatch)
    name='Synthetic Mobile Analyst';uid=99002
    assert accounts.create_account(name,'mobile-analyst','initial123',role='analyst',telegram_id=uid,by='fixture')['ok']
    db.set_manager(uid,name,[],[],role='manager')
    with db.connect() as conn:
        conn.execute("UPDATE managers SET role='analyst' WHERE telegram_id=?",(uid,));conn.execute("UPDATE accounts SET status='active',must_change_password=0,password_review_pending=0 WHERE username='mobile-analyst'")
    device={'kind':'phone','fp':'mobile-analyst-phone','label':'Synthetic analyst phone'}
    assert accounts.login('mobile-analyst','initial123',device=device,entrance='public',audience='crm-mobile')['action']=='blocked'
    with db.connect() as conn:
        aid=conn.execute("SELECT id FROM accounts WHERE username='mobile-analyst'").fetchone()[0]
        conn.execute("INSERT INTO account_admin_grants(account_id,principal,granted_by,operation_id,at_utc,next_pc_allowed) VALUES(?,?,?,?,?,1)",(aid,'synthetic-mobile','fixture','mobile-grant','2026-11-16T00:00:00+00:00'))
    result=accounts.login('mobile-analyst','initial123',device=device,entrance='public',audience='crm-mobile')
    assert result['action']=='ok_pending'
    with db.connect() as conn:
        assert conn.execute('SELECT status FROM account_devices WHERE fingerprint=?',(device['fp'],)).fetchone()[0]=='pending'
        assert conn.execute('SELECT next_pc_allowed FROM account_admin_grants WHERE account_id=?',(aid,)).fetchone()[0]==1
    assert accounts.login('mobile-analyst','initial123',device=device,entrance='public',audience='mobile')['action']=='blocked'
