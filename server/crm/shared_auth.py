"""Pilot-only sessions derived from the live, existing Manager authority.

The working credential is accepted only at exchange on the pinned first-party
origin. It is sealed inside an audience-bound pilot session. Background work
stores a separately sealed credential; plaintext never enters DB/logs/renderer.
A fresh exchange, a context check or a sensitive action checks the live
authority. Ordinary reads can reuse a successful confirmation for 15 seconds
and for at most 30 further seconds while the authority is unavailable.
"""
import asyncio
import base64
from dataclasses import replace
import hashlib
import json
import os
import re
import time
import uuid

import aiohttp
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from . import access, storage, store
from .contract import CRMError, canonical, now
from .roles import CAPABILITIES, ROLE_CAPABILITIES

AUDIENCE = b'rimborso-crm-pilot-session-v1'
TTL = 30 * 60
AUTHORITY_CACHE_SECONDS = 15
AUTHORITY_GRACE_SECONDS = 30
AUTHORITY_CACHE_LIMIT = 2048
NAMESPACE = 'crm-shared-pilot-v1'
ENTRY_KINDS = {'manager', 'employee', 'analyst', 'analyst-device'}
BOUND_ACTIONS = frozenset({'command', 'receipt', 'registry-edit', 'active-time'})


def fault(code='pilot_session_invalid', status=401):
    messages = {
        'pilot_session_invalid': 'Подключите CRM через действующий вход сотрудника, руководителя или аналитика.',
        'pilot_session_expired': 'Срок подключения истёк. Повторите подключение Руководителя.',
        'authority_unavailable': 'Сервер проверки входа временно недоступен. Сохранённые действия остаются в очереди.',
        'manager_required': 'Этот вход не даёт доступа к CRM. Проверьте действующий вход и назначенную роль.',
        'identity_protocol': 'Сервер входа ещё не поддерживает проверку личности CRM.',
        'identity_unmapped': 'Для этого входа ещё не подтверждено соответствие сотруднику CRM.',
    }
    return CRMError(messages.get(code, messages['pilot_session_invalid']), code, status)


def validate_identity(value):
    if not isinstance(value, dict) or value.get('ok') is not True:
        raise fault()
    subject, me, device = value.get('subject'), value.get('me'), value.get('device')
    if not isinstance(subject, dict) or subject.get('type') != 'manager':
        raise fault('identity_protocol', 503)
    uid = subject.get('actor_uid')
    if type(uid) is not int or not 0 < uid < 2**63:
        raise fault('identity_protocol', 503)
    if not isinstance(me, dict) or me.get('role') not in {'manager', 'full','security'}:
        raise fault('manager_required', 403)
    if not isinstance(value.get('caps'), dict) or (me['role']!='security' and value['caps'].get('binds_edit') is not True):
        raise fault('manager_required', 403)
    if not isinstance(device, dict) or type(device.get('id')) is not int or device['id'] < 1:
        raise fault('manager_required', 403)
    team = value.get('crm_team')
    if team is not None and (not isinstance(team, list) or not all(isinstance(name, str) for name in team)):
        raise fault('identity_protocol', 503)
    manager_team = {} if me.get('isOwner') is True or me['role'] == 'security' else {'manager_team': team}
    return dict(uid=uid, device_id=device['id'], name=str(me.get('name') or ''), **manager_team,
                role='observer' if me['role']=='security' else me['role'],
                server_operator=me['role'] != 'security' and value.get('server_operator') is True,
                account_bound=device.get('account_bound') is True)


def validate_entry_identity(value, kind):
    if kind not in {'employee', 'analyst', 'analyst-device'}:
        raise fault()
    allowed = {'employee', 'courier'} if kind == 'employee' else {'analyst'}
    if not isinstance(value, dict) or value.get('ok') is not True:
        raise fault()
    subject, me, device = value.get('subject'), value.get('me'), value.get('device')
    if not all(isinstance(part, dict) for part in (subject, me, device)):
        raise fault('identity_protocol', 503)
    uid, role = subject.get('actor_uid'), subject.get('type')
    if (type(uid) is not int or not 0 < abs(uid) < 2**63
            or (uid < 0 and kind != 'analyst-device') or role not in allowed
            or me.get('role') != role or type(device.get('id')) is not int or device['id'] < 1):
        raise fault('identity_protocol', 503)
    return dict(uid=uid, device_id=device['id'], name=str(me.get('name') or ''), role=role,
                account_bound=device.get('account_bound') is True)


class Authority:
    def __init__(self, session, origin='http://hard-core-app-1:8080'):
        if origin not in {'http://hard-core-app-1:8080', 'https://hard.rimborso.ru'}:
            raise RuntimeError('Untrusted Manager authority origin')
        self.session, self.url = session, origin + '/api/manager/device/me'
        self.identity_url = origin + '/api/crm-identity'

    async def __call__(self, token, *, kind='manager', device_fingerprint=''):
        if kind not in ENTRY_KINDS:
            raise fault()
        header = 'X-App-Token' if kind == 'employee' else 'X-Device-Token' if kind == 'analyst-device' else 'X-Pult-Token'
        headers = {header: token, 'X-Client-Version': '3.3.0-pilot.4',
                   'X-Work-Product': 'crm', 'X-Client-Runtime': 'crm-identity-bridge'}
        if kind == 'analyst':
            headers['X-CRM-Entry'] = 'analyst'
        if kind == 'analyst-device':
            if not re.fullmatch(r'[a-f0-9]{64}', device_fingerprint):
                raise fault()
            headers['X-Device-Id'] = device_fingerprint
        try:
            async with self.session.get(self.url if kind == 'manager' else self.identity_url, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=8), allow_redirects=False) as response:
                if response.status in {401, 403, 410}:
                    raise fault('manager_required', 403)
                if response.status != 200 or (response.content_length or 0) > 65536:
                    raise fault('authority_unavailable', 503)
                raw = await response.content.read(65537)
                if len(raw) > 65536:
                    raise fault('authority_unavailable', 503)
                value = json.loads(raw)
                return validate_identity(value) if kind == 'manager' else validate_entry_identity(value, kind)
        except CRMError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError):
            raise fault('authority_unavailable', 503) from None


def _primary_manager_scope(conn, identity, employee_uuid):
    if 'manager_team' not in identity:
        return None
    names = identity['manager_team']
    if names is None:
        return frozenset({employee_uuid})
    from db import _name_key
    wanted = {_name_key(name) for name in names} - {''}
    matched = {}
    at = now()
    for row in conn.execute('SELECT DISTINCT e.employee_uuid,e.name FROM crm_employees e '
                            'JOIN crm_memberships m USING(employee_uuid) WHERE e.active=1 AND m.starts_at<=? '
                            'AND (m.ends_at IS NULL OR m.ends_at>?)', (at, at)):
        key = _name_key(row['name'])
        if key in wanted:
            matched.setdefault(key, set()).add(row['employee_uuid'])
    # An ambiguous spelling is not authority to add either employee.
    return frozenset().union(*(ids for ids in matched.values() if len(ids) == 1), {employee_uuid})


def _legacy_principal(conn, identity):
    marker = conn.execute("SELECT value FROM crm_meta WHERE key='deployment_namespace'").fetchone()
    if not marker or marker[0] != NAMESPACE:
        raise fault('identity_unmapped', 403)
    result = access.from_uid(conn, identity['uid'], common_read=True, use_grants=False)
    membership = conn.execute('SELECT role,capabilities_json FROM crm_memberships WHERE employee_uuid=? '
        'AND starts_at<=? AND (ends_at IS NULL OR ends_at>?) ORDER BY starts_at DESC',
        (result.employee_uuid, now(), now())).fetchone()
    authority_role = identity.get('role')
    role = authority_role
    if role not in {*ROLE_CAPABILITIES, 'full'}:
        raise fault('identity_unmapped', 403)
    if role == 'employee' and membership and membership['role'] == 'courier':
        role = 'courier'
    if role in {'employee', 'manager', 'full'} and membership and membership['role'] == 'logistic':
        role = 'logistic'
    # An Analyst entry has already passed the authority's admin gate. For
    # an existing pilot manager identity it is a capability reduction.
    legacy_manager = (role in {'manager', 'full','analyst'} and result.team_id == 'crm-pilot-managers'
                      and membership and membership['role']=='manager')
    role = 'manager' if role == 'full' else role
    lower_entry=membership and membership['role']=='manager' and role in {'employee','courier','observer','analyst'}
    from . import grants
    if (role not in ROLE_CAPABILITIES or not membership or
            (grants.model(conn, identity['uid']) == grants.LEGACY_MODEL
             and membership['role'] != role and not legacy_manager
             and not lower_entry
             and not (role == 'courier' and membership['role'] == 'employee'))):
        raise fault('identity_unmapped', 403)
    # The approved test scope covers this isolated database. It never
    # changes the authority's role, team or production permissions.
    employees = result.employee_scope
    if role in {'manager', 'analyst', 'observer'}:
        employees = frozenset(r[0] for r in conn.execute(
            'SELECT e.employee_uuid FROM crm_employees e JOIN crm_memberships m USING(employee_uuid) '
            'WHERE e.active=1 AND m.starts_at<=? AND (m.ends_at IS NULL OR m.ends_at>?) '
            'AND (m.team_id=? OR ?=1)', (now(), now(), result.team_id, int(legacy_manager))))
    all_contracts=legacy_manager
    policy=conn.execute('SELECT * FROM crm_authority_scopes WHERE actor_uid=?',(identity['uid'],)).fetchone()
    if policy and role in {'manager','analyst','observer'}:
        employees=frozenset(json.loads(policy['employees_json'])) | {result.employee_uuid}
        all_contracts=bool(policy['all_contracts']) and role in {'manager','analyst'}
    logistics_caps = set(json.loads(membership['capabilities_json'])) & {'logistics'} if membership and membership['role'] in {'manager', 'owner'} else set()
    access_caps = {'access_manage'} if membership and membership['role'] == 'analyst' and 'access_manage' in json.loads(membership['capabilities_json']) else set()
    registered_manager = role == 'manager' and conn.execute(
        "SELECT 1 FROM crm_employees WHERE employee_uuid=? AND source='crm-registration'",(result.employee_uuid,)).fetchone()
    if registered_manager:
        employees = access.registered_manager_staff(conn, result)
        all_contracts = False
    primary_scope = _primary_manager_scope(conn, identity, result.employee_uuid)
    if primary_scope is not None and role == 'manager':
        employees = employees & primary_scope if policy or registered_manager else primary_scope
        all_contracts = False
    legacy = replace(result, role=role, capabilities=(result.capabilities & ROLE_CAPABILITIES[role]) | logistics_caps | access_caps,
                     employee_scope=employees, all_contracts=all_contracts,
                     read_all_contracts=role != 'logistic' and not registered_manager,
                     server_operator=role == 'manager' and identity.get('server_operator') is True,
                     account_bound=identity.get('account_bound') is True,
                     registered_manager=bool(registered_manager), primary_manager_scope=primary_scope,
                     primary_team_verified=identity.get('manager_team') is not None)
    return legacy, role, authority_role in {'manager', 'full'}


def principal(identity, *, grant_id=None):
    from . import grants
    with storage.connect(readonly=True) as conn:
        legacy, role, allow_owner = _legacy_principal(conn, identity)
        return grants.resolve(conn, legacy, role=role, grant_id=grant_id, allow_owner=allow_owner,
                              account_bound=identity.get('account_bound') is True)


def backfill(identity, *, activate=False, expected_revision=None):
    from . import grants
    with storage.connect() as conn:
        legacy, role, allow_owner = _legacy_principal(conn, identity)
        return grants.backfill(conn, legacy, role=role, allow_owner=allow_owner,
                               activate=activate, expected_revision=expected_revision,
                               changed_by_uid=identity['uid'])


def shadow(identity):
    from . import grants
    with storage.connect(readonly=True) as conn:
        legacy, role, allow_owner = _legacy_principal(conn, identity)
        return grants.shadow(conn, legacy, role=role, allow_owner=allow_owner)


class Sessions:
    def __init__(self, key, verifier, *, resolve=principal, clock=time.time, audit=None, background_store=None):
        if not isinstance(key, bytes) or len(key) != 32:
            raise RuntimeError('CRM requires its own 32-byte session key')
        self.cipher, self.verify, self.resolve, self.clock = AESGCM(key), verifier, resolve, clock
        self.audit = audit or audit_exchange
        self.background_store = background_store
        self._authority_cache = {}
        self._authority_metrics = dict(exchange=0, refresh=0, hit=0, grace=0, denied=0)

    def authority_metrics(self):
        return dict(self._authority_metrics, entries=len(self._authority_cache),
                    ttl_seconds=AUTHORITY_CACHE_SECONDS, grace_seconds=AUTHORITY_GRACE_SECONDS)

    def background_seal(self, payload):
        nonce=os.urandom(12)
        return base64.urlsafe_b64encode(nonce+self.cipher.encrypt(nonce,canonical(payload).encode(),AUDIENCE+b':background')).decode()

    async def _resolve(self, identity, grant_id=None):
        kwargs = {'grant_id': grant_id} if grant_id is not None else {}
        return await asyncio.to_thread(self.resolve, identity, **kwargs)

    async def background_principal(self, sealed):
        try:
            raw=base64.urlsafe_b64decode(sealed)
            payload=json.loads(self.cipher.decrypt(raw[:12],raw[12:],AUDIENCE+b':background'))
        except Exception:raise fault() from None
        identity=await self._verify(payload['credential'],allow_cache=False,**payload.get('entry',{}))
        if identity['uid']!=payload['uid'] or identity['device_id']!=payload['device_id']:raise fault()
        return await self._resolve(identity, payload.get('grant_id'))

    def seal(self, payload):
        nonce = os.urandom(12)
        sealed = self.cipher.encrypt(nonce, canonical(payload).encode(), AUDIENCE)
        return 'crm1.' + base64.urlsafe_b64encode(nonce + sealed).decode().rstrip('=')

    def open(self, token):
        try:
            if not isinstance(token, str) or not re.fullmatch(r'crm1\.[A-Za-z0-9_-]{100,4096}', token):
                raise ValueError()
            raw = base64.urlsafe_b64decode(token[5:] + '=' * (-len(token[5:]) % 4))
            value = json.loads(self.cipher.decrypt(raw[:12], raw[12:], AUDIENCE))
            keys = {'uid', 'device_id', 'issued', 'expires', 'id', 'credential', 'audience'}
            if not keys <= set(value) <= keys | {'entry', 'grant_id'}:
                raise ValueError()
            if 'grant_id' in value and (not isinstance(value['grant_id'], str)
                                        or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value['grant_id'])):
                raise ValueError()
            if 'entry' in value:
                entry = value['entry']
                if (not isinstance(entry, dict) or set(entry) != {'kind', 'device_fingerprint'}
                        or entry['kind'] not in ENTRY_KINDS or not isinstance(entry['device_fingerprint'], str)
                        or (entry['kind'] == 'analyst-device' and not re.fullmatch(r'[a-f0-9]{64}', entry['device_fingerprint']))):
                    raise ValueError()
            at = self.clock()
            if value['audience'] != NAMESPACE or type(value['issued']) is not int or type(value['expires']) is not int:
                raise ValueError()
            if not 0 < value['expires'] - value['issued'] <= TTL or value['issued'] > at + 5:
                raise ValueError()
            if at >= value['expires']:
                raise fault('pilot_session_expired')
            return value
        except CRMError:
            raise
        except Exception:
            raise fault() from None

    async def _verify(self, credential, kind='manager', device_fingerprint='', allow_cache=True):
        if kind not in ENTRY_KINDS:
            raise fault()
        if kind == 'analyst-device' and not re.fullmatch(r'[a-f0-9]{64}', device_fingerprint):
            raise fault()
        key = (credential, kind, device_fingerprint)
        at = self.clock()
        cached = self._authority_cache.get(key)
        age = at - cached[1] if cached else None
        if allow_cache and cached and 0 <= age < AUTHORITY_CACHE_SECONDS:
            self._authority_metrics['hit'] += 1
            return cached[0]
        if cached and (age < 0 or age >= AUTHORITY_CACHE_SECONDS + AUTHORITY_GRACE_SECONDS):
            self._authority_cache.pop(key, None)
            cached = None
        try:
            identity = (await self.verify(credential) if kind == 'manager' else
                        await self.verify(credential, kind=kind, device_fingerprint=device_fingerprint))
        except CRMError as exc:
            if allow_cache and cached and 0 <= age < AUTHORITY_CACHE_SECONDS + AUTHORITY_GRACE_SECONDS and exc.code == 'authority_unavailable':
                self._authority_metrics['grace'] += 1
                return cached[0]
            if exc.status in {401, 403, 410}:
                self._authority_cache.pop(key, None)
                self._authority_metrics['denied'] += 1
            raise
        self._authority_metrics['refresh'] += 1
        self._authority_cache[key] = (identity, at)
        if len(self._authority_cache) > AUTHORITY_CACHE_LIMIT:
            oldest = min(self._authority_cache, key=lambda item: self._authority_cache[item][1])
            self._authority_cache.pop(oldest, None)
        return identity

    async def exchange(self, credential, *, kind='manager', device_fingerprint='', grant_id=None):
        if not isinstance(credential, str) or not re.fullmatch(r'[A-Za-z0-9_-]{20,512}', credential):
            raise fault()
        if grant_id is not None and (not isinstance(grant_id, str)
                                     or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', grant_id)):
            raise fault()
        identity = await self._verify(credential, kind, device_fingerprint, allow_cache=False)
        self._authority_metrics['exchange'] += 1
        p = await self._resolve(identity, grant_id)
        await asyncio.to_thread(self.audit, p, identity)
        issued = int(self.clock())
        payload = dict(uid=identity['uid'], device_id=identity['device_id'], issued=issued,
                       expires=issued + TTL, id=str(uuid.uuid4()), credential=credential, audience=NAMESPACE)
        if kind != 'manager':
            payload['entry'] = dict(kind=kind, device_fingerprint=device_fingerprint)
        if p.grant_id is not None:
            payload['grant_id'] = p.grant_id
        if self.background_store and p.role in {'manager','owner','analyst'}:
            await asyncio.to_thread(self.background_store,p.actor_uid,self.background_seal(payload),p.grant_id)
        return dict(token=self.seal(payload), expires_at=payload['expires'],
                    employee_uuid=p.employee_uuid, actor_uid=p.actor_uid, name=identity['name'], role=p.role,
                    access_model=p.access_model, policy_revision=p.policy_revision,
                    grant_id=p.grant_id, grants=[grant.to_dict() for grant in p.grants])

    async def authenticate(self, request):
        header = request.headers.get('Authorization', '')
        if not header.startswith('Bearer '):
            raise fault()
        payload = self.open(header[7:])
        action = request.path.rsplit('/', 1)[-1]
        cached_read = request.method == 'GET' and action not in {
            'context', 'operator-status', 'grant-admin', 'grant-catalog',
        }
        identity = await self._verify(payload['credential'], allow_cache=cached_read, **payload.get('entry', {}))
        if identity['uid'] != payload['uid'] or identity['device_id'] != payload['device_id']:
            raise fault()
        principal = await self._resolve(identity, payload.get('grant_id'))
        binding = request.headers.get('X-CRM-Work-Authority')
        if binding is not None:
            expected = f"{principal.actor_uid}/{principal.access_model}/{principal.grant_id or ''}"
            if request.method != 'POST' or action not in BOUND_ACTIONS or binding != expected:
                raise CRMError('Изменились полномочия. Исходная операция сохранена.', 'grant_changed', 409)
        if binding and action == 'command' and principal.access_model == 2 and request.headers.get('X-CRM-Recover-Legacy') == '1':
            # A migration can check an exact earlier acknowledgement. This does
            # not authorize a new command or expose the old result payload.
            principal = replace(principal, recover_legacy_receipt=True)
        return principal


def audit_exchange(employee, identity):
    with storage.connect() as conn:
        store.audit(conn, employee, 'pilot_session_exchange', str(identity['device_id']),
                    {'authority': 'existing_manager', 'scope': NAMESPACE})
