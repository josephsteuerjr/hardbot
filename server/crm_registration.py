"""Self-registration for the CRM entry screens, backed by the primary accounts."""
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
import re
import secrets

import accounts
import account_policy
import country_scope
import db
import staff_roster


def init_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS crm_registration_requests (
        account_id INTEGER PRIMARY KEY, staff_id TEXT NOT NULL, country TEXT NOT NULL,
        city TEXT NOT NULL, products TEXT NOT NULL, projected_at TEXT, device_id INTEGER)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS crm_registration_invites (
        code_hash TEXT PRIMARY KEY, employee_key TEXT NOT NULL, expires_at TEXT NOT NULL,
        used_at TEXT)''')


def _registry(conn, *, readonly=True):
    if os.environ.get('HARD_REGISTRY_DATABASE_URL_FILE'):
        return db.registry_connect(readonly=readonly)
    return nullcontext(conn)


def _aliases(conn):
    result = {}
    for row in conn.execute('SELECT name,employee_id FROM employee_aliases'):
        result.setdefault(db._name_key(row['name']), set()).add(row['employee_id'])
    return result


def candidates(conn, registry):
    primary_aliases = _aliases(conn)
    source_aliases = primary_aliases if registry is conn else _aliases(registry)
    assigned = {}
    for row in registry.execute("SELECT responsible employee,'hard' product FROM clients WHERE TRIM(COALESCE(responsible,''))<>'' "
                                "UNION SELECT responsible_soft,'soft' FROM clients WHERE TRIM(COALESCE(responsible_soft,''))<>''"):
        key = db._name_key(row['employee'])
        ids = source_aliases.get(key,set())
        if len(ids) <= 1:
            for index in {key,*ids}:
                assigned.setdefault(index,set()).add(row['product'])
    used = accounts._privileged_names(conn) | {db._name_key(r[0]) for r in conn.execute('SELECT employee FROM accounts')}
    used_ids = {identity for key in used for identity in primary_aliases.get(key,set()) | source_aliases.get(key,set())}
    result = []
    for row in staff_roster.candidates(conn, registry=registry):
        name = row['employee']
        key = db._name_key(name)
        identifiers = source_aliases.get(key,set())
        if key in used or identifiers & used_ids or len(identifiers)>1 or (registry is not conn and not identifiers):
            continue
        products = {product for index in {key,*identifiers} for product in assigned.get(index,())}
        if not products:
            continue
        result.append(dict(employee=name,office=row['office'],products=sorted(products),staff_id=next(iter(identifiers),'')))
    return result


def _regions(conn):
    cities = {spec['code']: set() for spec in country_scope.COUNTRIES}
    for row in staff_roster.rows(conn):
        for city in json.loads(row['offices']) or [row['office']]:
            country = row['direction'] or country_scope.scope_country([city])
            if country in cities and city:
                cities[country].add(city)
    for row in conn.execute('SELECT DISTINCT city FROM employee_city'):
        city = row['city']
        country = country_scope.scope_country([city])
        if country and city:
            cities[country].add(city)
    return [dict(code=spec['code'],name=spec['name'],cities=sorted(cities[spec['code']],key=str.casefold))
            for spec in country_scope.COUNTRIES]


def options():
    with db.connect() as conn:
        return dict(ok=True,republics=_regions(conn),employee_confirmation_paused=account_policy.device_confirmation_paused(conn))


def issue_employee_code(employee, *, hours=24):
    """Issue a personal registration code to the operator for private delivery."""
    if type(hours) is not int or not 1 <= hours <= 72:
        raise ValueError('Срок действия кода регистрации — от 1 до 72 часов.')
    key = db._name_key(employee)
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        with _registry(conn) as registry:
            if not any(db._name_key(row['employee'])==key for row in candidates(conn,registry)):
                raise ValueError('Сотрудник отсутствует в списке регистрации.')
        at = datetime.now(timezone.utc)
        code = secrets.token_urlsafe(24)
        conn.execute('DELETE FROM crm_registration_invites WHERE employee_key=? OR expires_at<=?',(key,at.isoformat()))
        conn.execute('INSERT INTO crm_registration_invites VALUES(?,?,?,NULL)',
                     (_code_hash(code),key,(at+timedelta(hours=hours)).isoformat()))
    return code


def _code_hash(code):
    return sha256(code.encode()).hexdigest()


def submit(body, ip=''):
    role = body.get('role')
    name = ' '.join(str(body.get('employee') or '').split())
    username = accounts.normalize_username(body.get('username'))
    password = body.get('password')
    if role not in ('employee','manager','analyst') or not isinstance(password,str) or len(password)>512:
        raise ValueError('Проверьте роль и пароль.')
    if role!='employee' and (not name or any(not (c.isalpha() or c in " -’'") for c in name) or len(name.split()) < 2):
        raise ValueError('Укажите фамилию и имя.')
    country, city, staff_id, products = '', '', '', []
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if role=='employee':
            with _registry(conn) as registry:
                roster=candidates(conn,registry)
            row=next((row for row in roster if db._name_key(row['employee'])==db._name_key(name)),None)
            if row is None:
                raise ValueError('Выберите сотрудника из списка действующих закреплений.')
            name, staff_id, products = row['employee'],row['staff_id'],row['products']
            code = str(body.get('code') or '').strip()
            paused = account_policy.device_confirmation_paused(conn)
            can_activate = paused
            if can_activate:
                if not re.fullmatch(r'[A-Za-z0-9_-]{32}',code) or not conn.execute(
                        'SELECT 1 FROM crm_registration_invites WHERE code_hash=? AND employee_key=? AND expires_at>? AND used_at IS NULL',
                        (_code_hash(code),db._name_key(name),datetime.now(timezone.utc).isoformat())).fetchone():
                    raise ValueError('Код регистрации не подходит выбранному сотруднику или срок действия истёк.')
            device = body.get('device') if body.get('audience')=='crm-mobile' else None
            proof = body.get('pc_proof') if body.get('audience')=='crm-desktop' else None
            result=accounts.create_account(name,username,password,self_service=True,registration_pc=proof,
                registration_device=device,registration_ip=ip,_connection=conn,
                _registration_roster=[{**row,'products':json.dumps(products)}])
        else:
            can_activate=False
            if db._name_key(name) in accounts._privileged_names(conn):
                raise ValueError('Для этого ФИО уже есть служебный вход. Восстановите его доступ.')
            if role=='manager':
                country, city = body.get('country'), body.get('city')
                region = next((row for row in _regions(conn) if row['code']==country), None)
                if region is None or not isinstance(city,str) or city and city not in region['cities']:
                    raise ValueError('Выберите республику и город или всю республику.')
            if body.get('audience')=='crm-mobile':
                phone=body.get('device')
                fingerprint=phone.get('fp') if isinstance(phone,dict) and phone.get('kind')=='phone' else None
                if not isinstance(fingerprint,str) or not 8 <= len(fingerprint) <= 128:
                    raise ValueError('Приложение не сообщило идентификатор телефона.')
                device=('phone',fingerprint,str(phone.get('label') or 'Телефон')[:120],'public',None)
            else:
                import pc_registration
                pc=pc_registration.consume(conn,body.get('pc_proof'),name,username,password)
                if not pc:
                    raise ValueError('Не удалось подтвердить ключ установки. Повторите регистрацию.')
                device=('desktop',pc['fingerprint'],pc['label'],'internal',pc['public_key'])
            result=accounts.create_account(name,username,password,office=city,role=role,
                by='crm-registration',registration_ip=ip,_connection=conn)
        if not result.get('ok'):
            raise ValueError(result['error'])
        account_id = conn.execute('SELECT id FROM accounts WHERE username=?',(username,)).fetchone()[0]
        device_id=None
        if role in ('manager','analyst'):
            conn.execute('UPDATE accounts SET must_change_password=0 WHERE id=?',(account_id,))
            kind,fingerprint,label,entrance,public_key=device
            device_id=conn.execute('INSERT INTO account_devices(account_id,kind,fingerprint,label,status,entrance,first_seen,public_key) '
                "VALUES(?,?,?,?,'pending',?,?,?)",(account_id,kind,fingerprint,label,entrance,accounts._now(),public_key)).lastrowid
        conn.execute('INSERT INTO crm_registration_requests(account_id,staff_id,country,city,products,device_id) VALUES(?,?,?,?,?,?)',
                     (account_id,staff_id,country,city,json.dumps(products),device_id))
        if can_activate:
            actor='policy:employee-registration'
            conn.execute('UPDATE crm_registration_invites SET used_at=? WHERE code_hash=?',
                         (datetime.now(timezone.utc).isoformat(),_code_hash(code)))
            accounts.approve_account(username,actor,_connection=conn,ip=ip)
            device_id=conn.execute('SELECT id FROM account_devices WHERE account_id=?',(account_id,)).fetchone()[0]
            accounts.approve_device(device_id,actor,_connection=conn,ip=ip)
            accounts._approval_event(conn,account_id,device_id,0,actor,'device_policy','approved',ip,
                                     'Verified personal registration code during employee approval pause')
    return dict(ok=True,username=username,role=role,pending=not can_activate)


def approve(username, approver, ip=''):
    """Approve a privileged registration through the existing admin action."""
    username=accounts.normalize_username(username)
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT a.*,r.country,r.city,r.device_id FROM accounts a JOIN crm_registration_requests r '
                         'ON r.account_id=a.id WHERE a.username=? AND a.role IN (\'manager\',\'analyst\')',(username,)).fetchone()
        if row is None or row['password_review_pending']:
            return None
        if row['status']!='awaiting':
            return {'ok':row['status']=='active','active':row['status']=='active'}
        if row['role']=='manager':
            label=country_scope.info(row['country'])['name']
            conn.execute('UPDATE managers SET cities=?,office_cities=?,direction=? WHERE telegram_id=?',
                         (json.dumps([label,row['city']] if row['city'] else [label]),json.dumps([row['city']] if row['city'] else []),
                          '' if row['city'] else row['country'],row['telegram_id']))
        else:
            conn.execute('INSERT INTO account_admin_grants(account_id,principal,granted_by,operation_id,at_utc,next_pc_allowed) '
                         'VALUES(?,?,?,?,?,0)',(row['id'],f"crm-analyst:{row['id']}",'crm-registration',
                                               f"registration:{row['id']}",datetime.now(timezone.utc).isoformat()))
        result=accounts.approve_account(username,approver,_connection=conn,ip=ip)
        if not result.get('active'):
            raise ValueError(result.get('error') or 'Заявка не подтверждена.')
        if row['device_id'] and not accounts.approve_device(row['device_id'],approver,_connection=conn,ip=ip).get('active'):
            raise ValueError('Устройство из заявки больше недоступно.')
    db.reset_role_caches()
    return result


def project_verified_account(actor):
    """Project an approved primary profile once. Retries reuse the stable identity."""
    if not os.environ.get('HARD_REGISTRY_DATABASE_URL_FILE'):
        return
    from crm.contract import canonical, identifier, now
    from crm.roles import ROLE_CAPABILITIES

    with db.connect() as primary:
        row=primary.execute('SELECT a.id,a.employee,a.role,r.staff_id,r.country,r.city,r.products '
            'FROM accounts a JOIN crm_registration_requests r ON r.account_id=a.id '
            "WHERE a.telegram_id=? AND a.status='active' AND r.projected_at IS NULL",(actor,)).fetchone()
    if row is None:
        return
    role='employee' if row['role']=='agent' else row['role']
    capabilities=set(ROLE_CAPABILITIES[role])
    if role=='employee':
        products=json.loads(row['products'])
        if 'soft' not in products:capabilities.discard('call')
        if 'hard' not in products:capabilities.discard('visit')
    stable=f"crm-registration:{row['id']}"
    at=now()
    with db.registry_connect() as registry:
        registry.execute('SELECT crm_registry.project_registration(?,?,?,?,?,?,?,?,?,?,?,?)',
            (actor,row['id'],identifier('employee',stable),identifier('membership',stable),role,row['employee'],
             row['staff_id'],row['country'],row['city'],canonical(sorted(capabilities)),
             'Asia/Dushanbe' if row['country']=='tj' else 'Asia/Tashkent',at))
    with db.connect() as primary:
        primary.execute('UPDATE crm_registration_requests SET projected_at=? WHERE account_id=? AND projected_at IS NULL',
                        (at,row['id']))
