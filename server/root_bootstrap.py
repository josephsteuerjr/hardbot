"""Root-only analyst provisioning with a stable real operator principal.

The configured Telegram owner is not presumed to be the current operator.
Old and new sessions of one person retain the SAME approval principal.
"""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import sys

import accounts
import db


def init_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS account_admin_grants (
        account_id INTEGER PRIMARY KEY, principal TEXT NOT NULL UNIQUE,
        granted_by TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE, at_utc TEXT NOT NULL,
        next_pc_allowed INTEGER NOT NULL DEFAULT 1)''')
    cols={r[1] for r in conn.execute('PRAGMA table_info(account_admin_grants)')}
    if 'next_pc_allowed' not in cols:
        conn.execute('ALTER TABLE account_admin_grants ADD COLUMN next_pc_allowed INTEGER NOT NULL DEFAULT 1')
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_account_root_primary ON account_admin_grants(granted_by) WHERE granted_by='ssh-root'")
    conn.execute('''CREATE TABLE IF NOT EXISTS owner_bootstrap_challenges (
        operation_id TEXT PRIMARY KEY, nonce_hash TEXT NOT NULL, expires_at TEXT NOT NULL)''')


def challenge(operation_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{12,100}',operation_id):raise ValueError('operation_id')
    nonce=secrets.token_urlsafe(32)
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        now=dt.datetime.now(dt.timezone.utc).isoformat()
        c.execute('DELETE FROM owner_bootstrap_challenges WHERE expires_at<?',(now,))
        if c.execute('SELECT count(*) FROM owner_bootstrap_challenges').fetchone()[0]>=1200:
            raise ValueError('Слишком много запросов. Повторите через пять минут.')
        c.execute('INSERT INTO owner_bootstrap_challenges VALUES(?,?,?) ON CONFLICT(operation_id) DO UPDATE SET '
            'nonce_hash=excluded.nonce_hash,expires_at=excluded.expires_at',
            (operation_id,db.app_token_key(nonce),(dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=5)).isoformat()))
        c.commit()
    return {'ok':True,'nonce':nonce}


def _operator(conn,username,legacy_token,legacy_device_id):
    if legacy_token:
        # A retired credential proves historical principal continuity only. The
        # actual authority is the verified root session; this grants no legacy API access.
        row=conn.execute("SELECT account_approver FROM admin_devices WHERE token_hash=? AND device_id=? "
            "AND role='operator'",(db._device_token_hash(legacy_token),legacy_device_id)).fetchone()
        if row and row['account_approver']:return row['account_approver']
    existing=conn.execute("SELECT 1 FROM admin_devices WHERE role='operator' "
        "AND TRIM(COALESCE(account_approver,''))<>'' LIMIT 1").fetchone()
    if existing:
        raise RuntimeError('Перенесите настройки своего прежнего Аналитика: личность администратора должна сохраниться в журнале, а не стать вторым подтверждающим.')
    return 'root-owner'


def provision(username,secret,operation_id,nonce,*,legacy_token='',legacy_device_id='',employee='',recovery=False):
    if not hasattr(os,'geteuid') or os.geteuid()!=0:
        raise PermissionError('Первичный профиль создаётся только из подтверждённой root-сессии сервера.')
    username=accounts.normalize_username(username)
    if not re.fullmatch(r'[A-Za-z0-9_-]{12,100}',operation_id):raise ValueError('operation_id')
    if not accounts.USERNAME_RE.fullmatch(username) or not accounts._valid_password(secret):
        return {'ok':False,'error':'Проверьте логин и пароль: от 8 символов, с буквой и цифрой.'}
    with db.connect() as c:
        prior=c.execute('SELECT account_id FROM account_admin_grants WHERE operation_id=?',(operation_id,)).fetchone()
        if prior:
            acc=c.execute('SELECT username,status FROM accounts WHERE id=?',(prior[0],)).fetchone()
            return {'ok':True,'already_applied':True,**dict(acc),'operation_id':operation_id}
        existing=c.execute('SELECT a.*,g.principal,g.granted_by FROM accounts a LEFT JOIN account_admin_grants g '
            'ON g.account_id=a.id WHERE a.username=?',(username,)).fetchone()
        if existing and not recovery:raise RuntimeError('Профиль уже существует. Войдите или выберите восстановление доступа; пароль не менялся.')
        if not recovery and c.execute("SELECT 1 FROM account_admin_grants WHERE granted_by='ssh-root'").fetchone():
            raise RuntimeError('Первичный профиль Аналитика уже существует. Используйте вход или восстановление, не создавайте второго администратора.')
        if recovery:
            if not existing or existing['role']!='analyst' or existing['granted_by']!='ssh-root':
                raise RuntimeError('Восстановление root-доступом доступно только первичному профилю Аналитика.')
            principal=existing['principal']
        else:
            principal=_operator(c,username,legacy_token,legacy_device_id)
            if c.execute('SELECT 1 FROM account_admin_grants WHERE principal=?',(principal,)).fetchone():
                raise RuntimeError('У этого администратора уже есть личный профиль. Используйте его, не создавайте вторую личность.')
        request=c.execute('SELECT nonce_hash,expires_at FROM owner_bootstrap_challenges WHERE operation_id=?',(operation_id,)).fetchone()
        now=dt.datetime.now(dt.timezone.utc).isoformat()
        if not request or request['expires_at']<=now or not secrets.compare_digest(request['nonce_hash'],db.app_token_key(nonce)):
            raise RuntimeError('SSH сервер не соответствует выбранному HTTPS-серверу или запрос истёк. Профиль не менялся.')
    backup_dir=Path(db.DB_PATH).parent/'backups';backup_dir.mkdir(mode=0o700,parents=True,exist_ok=True)
    backup=backup_dir/('owner-bootstrap-'+operation_id+'-'+secrets.token_hex(4)+'.db')
    log_path=backup_dir/'owner-bootstrap-events.jsonl'
    def record(stage,**details):
        fd=os.open(log_path,os.O_APPEND|os.O_CREAT|os.O_WRONLY,0o600)
        with os.fdopen(fd,'a',encoding='utf-8') as f:
            f.write(json.dumps({'at_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'operation_id':operation_id,
                'stage':stage,**details},ensure_ascii=False)+'\n');f.flush();os.fsync(f.fileno())
    record('intent',action='recover own analyst' if recovery else 'create own analyst',username=username,
           principal=principal,backup=str(backup),preview='root authority verified; identity and nonce checked')
    backup.touch(mode=0o600,exist_ok=False)
    with sqlite3.connect(db.DB_PATH) as src,sqlite3.connect(backup) as dst:
        src.backup(dst);assert dst.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    digest=hashlib.sha256()
    with backup.open('rb') as f:
        for chunk in iter(lambda:f.read(1048576),b''):digest.update(chunk)
    record('backup_completed',backup=str(backup),sha256=digest.hexdigest(),integrity_check='ok')
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        request=c.execute('SELECT nonce_hash,expires_at FROM owner_bootstrap_challenges WHERE operation_id=?',(operation_id,)).fetchone()
        if not request or request['expires_at']<=dt.datetime.now(dt.timezone.utc).isoformat() or not secrets.compare_digest(request['nonce_hash'],db.app_token_key(nonce)):
            raise RuntimeError('Запрос изменился или истёк. Профиль не менялся.')
        if recovery:
            acc=c.execute("SELECT a.* FROM accounts a JOIN account_admin_grants g ON g.account_id=a.id "
                "WHERE a.username=? AND a.role='analyst' AND g.principal=? AND g.granted_by='ssh-root'",(username,principal)).fetchone()
            if not acc:raise RuntimeError('Профиль изменился после проверки. Повторите предпросмотр.')
            accounts._assert_uid_owned(c,acc)
            accounts._revoke_account_tokens(c,acc['id'],'ssh-root:recovery')
            c.execute('UPDATE accounts SET password_hash=?,failed_attempts=0,locked_until=NULL WHERE id=?',(accounts.hash_password(secret),acc['id']))
            c.execute('UPDATE account_admin_grants SET operation_id=?,at_utc=?,next_pc_allowed=1 WHERE account_id=?',(operation_id,now,acc['id']))
        else:
            # Internal analyst identity; existing managers and their scopes stay intact.
            uid=accounts._synthetic_uid(c);label=str(employee or principal).strip()[:120]
            c.execute("INSERT INTO managers(telegram_id,name,cities,employees,direction,role,created_at) VALUES(?,?,'','','','analyst',?)",(uid,label,now))
            result=accounts.create_account(label,username,secret,role='analyst',by='ssh-root-bootstrap',telegram_id=uid,_connection=c)
            if not result.get('ok'):
                c.rollback();return result
            acc=c.execute('SELECT * FROM accounts WHERE username=?',(username,)).fetchone()
            c.execute('INSERT INTO account_admin_grants(account_id,principal,granted_by,operation_id,at_utc,next_pc_allowed) VALUES(?,?,?,?,?,1)',(acc['id'],principal,'ssh-root',operation_id,now))
        c.execute("UPDATE accounts SET status='active',activated_by='ssh-root',must_change_password=0, activated_at=?,updated_at=? WHERE id=?",(now,now,acc['id']))
        accounts._approval_event(c,acc['id'],None,int(acc['telegram_id']),'ssh-root:'+principal,
            'owner_recovery' if recovery else 'owner_bootstrap','approved',detail='one real root authority; operation_id='+operation_id)
        c.execute('DELETE FROM owner_bootstrap_challenges WHERE operation_id=?',(operation_id,));c.commit()
    db.audit_access(int(acc['telegram_id']),'owner_root_recovery' if recovery else 'owner_root_bootstrap',username,'operation_id='+operation_id)
    result={'ok':True,'username':username,'operation_id':operation_id,'backup':str(backup),'backup_sha256':digest.hexdigest(),
        'status':'active','must_change_password':False,'principal':principal,'recovery':bool(recovery)}
    record('result',result=result);return result


if __name__=='__main__':
    try:
        body=json.load(sys.stdin)
        print(json.dumps(provision(str(body.get('username') or ''),str(body.get('password') or ''),str(body.get('operation_id') or ''),
            str(body.get('nonce') or ''),legacy_token=str(body.get('legacy_token') or ''),legacy_device_id=str(body.get('legacy_device_id') or ''),
            employee=str(body.get('employee') or ''),recovery=body.get('recovery') is True),ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False));raise SystemExit(1)
