"""Scoped team access inventory. Reads never register, activate or bind a person."""
import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

import accounts
import client_versions
import db
import staff_roster
import team_work


DESKTOP_VERSIONS = {'hard': '3.2.17', 'soft': '3.2.17', 'manager': '3.2.25'}
DOWNLOAD_BASE = 'https://github.com/josephsteuerjr/hard-desktop-releases/releases/latest/download/'


def families(conn):
    """Only declared, unambiguous aliases; never guess by name or role suffix."""
    result = []
    aliases = list(conn.execute('SELECT name,employee_id FROM employee_aliases'))
    owners = {}
    for a in aliases:
        owners.setdefault(db._name_key(a['name']), set()).add(a['employee_id'])
    for row in staff_roster.rows(conn, active_only=False):
        names = {row['employee'], *json.loads(row['product_names']).values()}
        names.update(a['name'] for a in aliases if a['employee_id'] == row['employee_id']
                     and owners[db._name_key(a['name'])] == {row['employee_id']})
        result.append({**row, 'keys': {db._name_key(n) for n in names},
                       'products': json.loads(row['products']),
                       'product_names': json.loads(row['product_names']),
                       'offices': json.loads(row['offices']) or [row['office']]})
    return result


def resources():
    origin = urlsplit(os.environ.get('MINIAPP_URL', 'https://hard.rimborso.ru'))
    url = 'https://' + origin.netloc + '/' if origin.scheme == 'https' and origin.hostname else 'https://hard.rimborso.ru/'
    return {'login_url': url, 'downloads': {p: {'version': v, 'url': DOWNLOAD_BASE + f'{p}-{v}-setup.exe'}
                                         for p, v in DESKTOP_VERSIONS.items()}}


def _state(row):
    if row.get('identity_conflict'):
        return 'identity_conflict', 'Неоднозначное сопоставление. Требуется проверка администратора.'
    if not row['active']:
        return 'inactive', 'Сотрудник отключён в справочнике. Восстановление выполняет администратор.'
    if not row.get('username'):
        return 'unregistered', 'Профиль ещё не создан. Сотрудник регистрируется по инструкции.'
    if row['status'] == 'disabled':
        return 'disabled', 'Профиль отключён. Восстановление выполняет администратор.'
    if row.get('locked_until'):
        return 'locked', 'Временная блокировка после неверного пароля.'
    if row['password_review_pending']:
        return ('password_change', 'Сотрудник должен сменить временный пароль на личный.') if row['must_change_password'] else (
            'password_review', 'Подтвердите устройство после смены пароля сотрудником.')
    if any(d['status'] == 'pending' for d in row['devices']):
        return 'pending', 'Ожидает подтверждения устройства руководителем.'
    if row['status'] != 'active':
        return 'awaiting', 'Профиль ожидает подтверждения; сотруднику нужно войти на устройстве.'
    if not row['devices']:
        return 'no_device', 'Нет действующего устройства. Сотруднику нужно войти и отправить заявку.'
    if row['must_change_password']:
        return 'password_change', 'Сотрудник должен сменить временный пароль на личный.'
    return 'ready', 'Доступ открыт.'


def snapshot(uid, manager, team, *, is_owner=False, shell_version=0):
    with db.connect() as conn:
        declared = families(conn)
        privileged=accounts._privileged_names(conn)
        team_keys={db._name_key(n) for n in team}
        account_rows = [dict(a) for a in conn.execute('SELECT * FROM accounts')]
        devices = [dict(d) for d in conn.execute("SELECT * FROM account_devices WHERE status<>'revoked' ORDER BY id DESC")]
        office_index={}
        for r in conn.execute('SELECT employee,city FROM employee_city'):
            if r['city']:office_index.setdefault(db._name_key(r['employee']),set()).add(r['city'])
        versions = {}
        for v in conn.execute('SELECT * FROM account_client_versions ORDER BY last_seen_at DESC,id DESC'):
            if v['source'] != 'compatibility':
                prior=versions.get(v['device_id'])
                known=lambda x:x['source']=='pwa' or x['source']=='desktop' and x['product'] in DESKTOP_VERSIONS
                if prior is None or not known(prior) and known(v):
                    versions[v['device_id']]={k:v[k] for k in (
                        'client_version','shell_version','product','source','last_seen_at')}
        scoped = {}
        for name in team:
            matches = [f for f in declared if db._name_key(name) in f['keys']]
            if len(matches) == 1:
                f = matches[0]
                scoped.setdefault(f['employee_id'], f)
            else:
                scoped.setdefault('name:' + db._name_key(name), dict(employee=name, employee_id='',
                    keys={db._name_key(name)}, products=[], product_names={}, office='', offices=[],
                    direction='', active=1, manager_uid=None, identity_conflict=len(matches)>1))
        # Current portfolio columns supply the product for people not yet in the roster.
        products = {}
        for r in conn.execute("SELECT responsible employee,'hard' product FROM clients WHERE TRIM(COALESCE(responsible,''))<>'' "
                              "UNION SELECT responsible_soft employee,'soft' product FROM clients WHERE TRIM(COALESCE(responsible_soft,''))<>''"):
            products.setdefault(db._name_key(r['employee']), set()).add(r['product'])
        out = []
        for key, f in scoped.items():
            matched = [a for a in account_rows if db._name_key(a['employee']) in f['keys']]
            overlap = any(f['keys'] & other['keys'] for other in declared if other.get('employee_id') != f.get('employee_id'))
            conflict = bool(f.get('identity_conflict') or overlap or len(matched)>1)
            a = matched[0] if len(matched)==1 and not conflict else None
            offices=[o for o in f['offices'] if o] or sorted({o for n in f['keys'] for o in office_index.get(n,set())})
            if not offices and a and a['office']:offices=[a['office']]
            current = [d for d in devices if a and d['account_id']==a['id']]
            reviewable = bool(a and db.manager_caps(manager.get('role'),is_owner=is_owner)['binds_edit'] and accounts._reviewable(conn, a, uid, manager,
                            team_keys,declared=declared,privileged=privileged))
            safe_devices = []
            for d in current:
                client = versions.get(d['id']);product = client.get('product') if client else ''
                reported = client_versions.protocol_version(client.get('client_version','')) if client else None
                recommended = DESKTOP_VERSIONS.get(product)
                update = bool(client and ((client['source']=='pwa' and shell_version and client['shell_version']<shell_version)
                    or (client['source']=='desktop' and recommended and reported and reported<client_versions.protocol_version(recommended))))
                safe_devices.append({k:d[k] for k in ('id','kind','label','status','first_seen','last_seen','approved_at')} | {
                    'at':d['first_seen'], 'client':client, 'update_available':update,
                    'recommended_version':str(shell_version) if client and client['source']=='pwa' else recommended,
                    'can_revoke':reviewable})
            row = dict(key=key,employee=f['employee'],employee_id=f['employee_id'],
                office=f['office'] or (offices[0] if len(offices)==1 else ''),offices=offices,direction=f['direction'],
                products=sorted(set(f['products']) | {p for n in f['keys'] for p in products.get(n,set())}),
                product_names=f['product_names'],active=bool(f['active']),identity_conflict=conflict,
                account_id=a['id'] if a else None,username=a['username'] if a else '',
                status=a['status'] if a else 'none',role=a['role'] if a else 'agent',
                must_change_password=bool(a and a['must_change_password']),
                password_review_pending=bool(a and a['password_review_pending']),
                locked_until=(a['locked_until'] or '') if a else '',devices=safe_devices,
                can_approve=reviewable and not (a['password_review_pending'] and a['must_change_password']),
                can_reset_password=reviewable and not a['password_review_pending'],
                can_request_phone=False,can_revoke=reviewable,
                action_reason='' if reviewable else 'Подтверждение и управление доступны своему руководителю; привилегированные и отключённые профили ведёт администратор.')
            if row['locked_until'] and row['locked_until'] <= accounts._now():row['locked_until']=''
            row['state'],row['state_reason']=_state(row)
            row['ready']=bool(a and a['status']=='active' and not a['must_change_password'] and not a['password_review_pending']
                              and not row['locked_until'] and any(d['status']=='active' for d in safe_devices))
            row['last_seen']=max((d['last_seen'] or '' for d in safe_devices),default='')
            for kind in ('phone','desktop'):
                d=next((d for d in safe_devices if d['kind']==kind),None)
                row[kind]=dict(label=d['label'],at=d['at'],confirmed=d['status']=='active') if d else None
            out.append(row)
    out.sort(key=lambda r:(r['state'] not in ('pending','password_review','awaiting'),db._name_key(r['employee'])))
    summary={k:sum(r['state']==k for r in out) for k in (
        'ready','unregistered','pending','password_change','password_review','awaiting','no_device','disabled','inactive','locked','identity_conflict')}
    summary.update(total=len(out),ready=sum(r['ready'] for r in out),requests=sum(d['status']=='pending' for r in out for d in r['devices']),
                   without_phone=sum(not r['phone'] for r in out),without_desktop=sum(not r['desktop'] for r in out),
                   updates=sum(d['update_available'] for r in out for d in r['devices']))
    return dict(ok=True,employees=out,entered=[r for r in out if r['username']],summary=summary,
        manager=dict(name=manager.get('name') or '',direction=manager.get('direction') or '',role=manager.get('role') or ''),
        generated_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),resources=resources())


def profile(uid, manager, team, employee, *, is_owner=False, shell_version=0):
    row=next((r for r in snapshot(uid,manager,team,is_owner=is_owner,shell_version=shell_version)['employees']
              if r['employee']==employee),None)
    if row is None:return None
    row=dict(row,history=[],past_devices=[])
    if row['account_id']:
        with db.connect() as c:
            # No coordinates, IPs, keys, fingerprints, tokens or password data.
            logins=[dict(r) for r in c.execute('SELECT kind,at,device_kind,label FROM account_logins '
                    'WHERE account_id=? ORDER BY id DESC LIMIT 20',(row['account_id'],))]
            approvals=[dict(r) for r in c.execute('SELECT action,decision,at FROM account_approval_events '
                    'WHERE account_id=? ORDER BY id DESC LIMIT 20',(row['account_id'],))]
            row['history']=sorted([dict(r,event='login') for r in logins]+[dict(r,event='approval') for r in approvals],key=lambda r:r['at'],reverse=True)[:30]
            row['past_devices']=[dict(r) for r in c.execute("SELECT id,kind,label,revoked_at FROM account_devices WHERE account_id=? AND status='revoked' ORDER BY id DESC LIMIT 10",(row['account_id'],))]
    return row


def revoke(uid, body, *, is_owner=False, ip=''):
    if not isinstance(body,dict) or type(body.get('device_id')) is not int or type(body.get('account_id')) is not int:
        raise ValueError('Выберите устройство текущего профиля.')
    if not isinstance(body.get('username'),str):raise ValueError('Укажите профиль.')
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        manager,team=accounts._manager_review_context(uid,is_owner)
        acc=c.execute('SELECT * FROM accounts WHERE id=? AND username=?',(body['account_id'],body['username'])).fetchone()
        if not accounts._reviewable(c,acc,uid,manager,team):raise PermissionError('Профиль недоступен для управления.')
        actor=f'team-entry:{uid}'
        op,sha,replay=team_work._replay(c,actor,body)
        if replay:return replay
        d=c.execute('SELECT * FROM account_devices WHERE id=? AND account_id=?',(body['device_id'],acc['id'])).fetchone()
        if not d:raise ValueError('Устройство больше не относится к этому профилю.')
        by=f'manager:{uid}:{manager.get("name") or ""}'
        accounts._revoke_slot_tokens(c,d['id'],by)
        c.execute("UPDATE account_devices SET status='revoked',revoked_at=COALESCE(revoked_at,?) WHERE id=?",(accounts._now(),d['id']))
        accounts._approval_event(c,acc['id'],d['id'],uid,by,'device','revoked',ip)
        result=team_work._remember(c,actor,op,sha,dict(ok=True,device_id=d['id'],revoked=True))
        c.commit();return result
