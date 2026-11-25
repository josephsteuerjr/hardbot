import accounts,db
from test_pc_first_accounts import identity,proof,PASSWORD


def test_first_desktop_login_needs_flag_and_keeps_password_and_device_gates(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'primary.db');db.init_db()
    assert accounts.create_account('Synthetic CRM Employee','crm-first',PASSWORD,telegram_id=77101,by='fixture')['ok']
    assert accounts.approve_account('crm-first','fixture')['ok']
    computer=identity()
    def login(kind='desktop'):
        return accounts.login('crm-first',PASSWORD,device=dict(kind=kind,fp=computer[2],label='Synthetic CRM'),entrance='internal',
            audience='crm-desktop',pc_proof=proof(computer,'','crm-first'))
    monkeypatch.setenv('HARD_CRM_DESKTOP','0');assert login()['action']=='blocked'
    monkeypatch.setenv('HARD_CRM_DESKTOP','1');assert login('phone')['action']=='blocked'
    result=login();assert result['action']=='ok_pending' and result['must_change_password']
    with db.connect() as c:
        device=c.execute('SELECT id,status FROM account_devices WHERE fingerprint=?',(computer[2],)).fetchone()
        assert device['status']=='pending'
    assert accounts.change_password('crm-first',PASSWORD,'Personal12345')['ok']
