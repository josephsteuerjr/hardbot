"""Synthetic account fixtures using the same account engine as the primary API."""
import secrets
import db
import accounts
from .contract import CRMError,now


def seed():
    with db.connect() as conn:
        if not conn.execute("SELECT 1 FROM crm_meta WHERE key='synthetic_candidate'").fetchone():
            raise RuntimeError('Synthetic account seed refuses a non-demo database')
        if conn.execute("SELECT 1 FROM crm_meta WHERE key='synthetic_team_complete'").fetchone():return
    name='Тестовый сотрудник'
    db.bind_agent(91001,name)
    db.set_manager(91002,'Тестовый руководитель',[],[name],role='manager')
    with db.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO clients(contract_id,client,responsible) VALUES('CRM-TEAM-DEMO','Синтетическая строка состава',?)",(name,))
        result=accounts.create_account(name,'crm-demo-employee',secrets.token_urlsafe(24)+'1',telegram_id=91001,by='synthetic',_connection=conn)
        if not result['ok'] and not conn.execute("SELECT 1 FROM accounts WHERE username='crm-demo-employee'").fetchone():
            raise RuntimeError('Synthetic account initialization failed')
        aid=conn.execute("SELECT id FROM accounts WHERE username='crm-demo-employee'").fetchone()[0]
        conn.execute("INSERT INTO account_devices(account_id,kind,fingerprint,label,status,entrance,first_seen) VALUES(?,'phone','synthetic-crm-team-phone','Тестовый телефон','pending','internal',?)",(aid,now()))
        conn.execute("INSERT INTO crm_meta VALUES('synthetic_team_complete',?)",(now(),))
        conn.commit()


def request(p,method,body,query):
    if p.role!='manager' or p.actor_uid!=91002:raise CRMError('Требуется вход руководителя.','forbidden',403)
    resource=(query if method=='GET' else body).get('resource')
    payload=body.get('payload',{})
    if method=='GET' and resource=='team':return accounts.manager_team(p.actor_uid)
    if method=='POST' and resource=='approve':return accounts.manager_approve(p.actor_uid,**payload)
    if method=='POST' and resource=='reset':
        return accounts.manager_reset_password(p.actor_uid,payload.get('username'),payload.get('password'),payload.get('operation_id'))
    if method=='POST' and resource=='revoke':return accounts.manager_revoke_device(p.actor_uid,payload.get('device_id'))
    if method=='POST' and resource=='board':
        import team_work
        manager,team=accounts._manager_review_context(p.actor_uid,False)
        return team_work.board_change(payload,actor=f'manager:{p.actor_uid}',team=team,manager=manager)
    raise CRMError('Действие команды не найдено.')
