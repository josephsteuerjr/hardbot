"""An owner reset permits the next attested leader PC and phone, once each."""
import pytest
import accounts
import db
from test_manager_account_approval import world, MGR
from test_manager_password_reset import owner
from test_pc_first_accounts import identity, proof


def enter(password, pc=None, kind='desktop'):
    pc = pc or identity()
    return accounts.login('synthetic-leader', password,
        device={'kind':kind,'fp':pc[2],'label':'Synthetic device'}, entrance='internal',
        audience='manager', pc_proof=proof(pc,'','synthetic-leader',password) if kind=='desktop' else None)


def leader(role="manager"):
    db.set_manager(MGR,'Synthetic Leader',[],['Own Employee'],role=role)
    assert accounts.create_account('Synthetic Leader','synthetic-leader','Original123',
        role=role,telegram_id=MGR)['ok']
    assert accounts.approve_account('synthetic-leader','initial-owner')['active']
    assert accounts.change_password('synthetic-leader','Original123','Personal123')['ok']
    assert enter('Personal123')['action']=='ok'


@pytest.mark.parametrize('actor',['root','cli'])
@pytest.mark.parametrize('role',['manager','security','full'])
def test_owner_reset_allows_next_pc_and_phone_without_another_approval(world,monkeypatch,actor,role):
    monkeypatch.setenv('ACCOUNT_CUTOVER_AT_UTC','2000-01-01T00:00:00Z')
    leader(role)
    by=owner() if actor=='root' else 'cli'
    assert accounts.set_password('synthetic-leader','Temporary123',by=by)['ok']
    pc=identity()
    # Wrong password and unsigned installation cannot consume permission.
    assert enter('Incorrect123',pc)['action']=='bad'
    assert accounts.login('synthetic-leader','Temporary123',device={'fp':pc[2]},
        entrance='internal',audience='manager')['action']=='blocked'
    login=enter('Temporary123',pc)
    assert login['action']=='ok' and login['must_change_password']
    with db.connect() as c:
        dev=c.execute('SELECT id FROM manager_devices WHERE token_hash=?',(db.app_token_key(login['token']),)).fetchone()[0]
    assert accounts.device_gate_by_token('manager_devices',dev)[0]=='password'
    assert accounts.change_password('synthetic-leader','Temporary123','NewPersonal123')['ok']
    assert accounts.device_gate_by_token('manager_devices',dev)[0]=='ok'
    assert enter('NewPersonal123',pc)['action']=='ok'
    assert enter('NewPersonal123')['action']=='ok_pending'
    assert enter('NewPersonal123',kind='phone')['action']=='ok'
    assert enter('NewPersonal123',kind='phone')['action']=='ok_pending'
    with db.connect() as c:
        grants=c.execute('SELECT consumed_at,device_id FROM account_reset_device_grants').fetchall()
    assert len(grants)==2 and all(r[0] and r[1] for r in grants)


def test_untrusted_reset_and_disabled_account_do_not_get_reset_permissions(world,monkeypatch):
    monkeypatch.setenv('ACCOUNT_CUTOVER_AT_UTC','2000-01-01T00:00:00Z')
    leader()
    principal=owner()
    assert accounts.set_password('synthetic-leader','Temporary123',by=principal)['ok']
    assert accounts.set_password('synthetic-leader','OtherTemporary123',by='principal:forged')['ok']
    assert enter('OtherTemporary123')['action']=='ok_pending'
    with db.connect() as c:
        assert not c.execute('SELECT 1 FROM account_reset_device_grants').fetchone()
    assert accounts.set_status('synthetic-leader','disabled',principal)['ok']
    assert accounts.set_password('synthetic-leader','Disabled123',by=principal)['ok']
    assert enter('Disabled123')['action']=='blocked'
    with db.connect() as c:
        assert not c.execute('SELECT 1 FROM account_reset_device_grants').fetchone()
