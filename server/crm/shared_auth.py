"""Pilot-only sessions derived from the live, existing Manager authority.

The working credential is accepted only at exchange on the pinned first-party
origin. It is sealed inside an audience-bound pilot session. No working token
is written to the CRM database, logs or renderer. Every use rechecks the live
authority, so a revoked working login cannot keep using the pilot session.
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

AUDIENCE = b'rimborso-crm-pilot-session-v1'
TTL = 30 * 60
NAMESPACE = 'crm-shared-pilot-v1'
CAPABILITIES = frozenset(['call', 'visit', 'note', 'contact', 'promise', 'task',
                         'reported_payment', 'document', 'correct', 'shift_correct',
                         'analytics', 'exchange', 'accounting'])
ENTRY_KINDS = {'manager', 'employee', 'analyst', 'analyst-device'}
ROLE_CAPABILITIES = {
    'employee': CAPABILITIES - {'shift_correct', 'analytics', 'exchange', 'accounting'},
    'courier': frozenset({'delivery', 'correct'}),
    'manager': CAPABILITIES,
    'analyst': frozenset({'note', 'contact', 'document', 'correct', 'analytics',
                          'exchange', 'accounting', 'shift_correct'}),
    'observer': frozenset({'analytics'}),
}


def fault(code='pilot_session_invalid', status=401):
    messages = {
        'pilot_session_invalid': 'Подключите CRM через действующий вход сотрудника, руководителя или аналитика.',
        'pilot_session_expired': 'Срок подключения истёк. Повторите подключение Руководителя.',
        'authority_unavailable': 'Сервер проверки входа временно недоступен. Сохранённые действия остаются в очереди.',
        'manager_required': 'Для общего пилота нужен действующий вход руководителя.',
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
    if not isinstance(me, dict) or me.get('role') not in {'manager', 'full'}:
        raise fault('manager_required', 403)
    if not isinstance(value.get('caps'), dict) or value['caps'].get('binds_edit') is not True:
        raise fault('manager_required', 403)
    if not isinstance(device, dict) or type(device.get('id')) is not int or device['id'] < 1:
        raise fault('manager_required', 403)
    return dict(uid=uid, device_id=device['id'], name=str(me.get('name') or ''), role=me['role'])


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
    return dict(uid=uid, device_id=device['id'], name=str(me.get('name') or ''), role=role)


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
        headers = {header: token, 'X-Client-Version': '3.3.0-pilot.2',
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


def principal(identity):
    with storage.connect(readonly=True) as conn:
        marker = conn.execute("SELECT value FROM crm_meta WHERE key='deployment_namespace'").fetchone()
        if not marker or marker[0] != NAMESPACE:
            raise fault('identity_unmapped', 403)
        result = access.from_uid(conn, identity['uid'], common_read=True)
        membership = conn.execute('SELECT role FROM crm_memberships WHERE employee_uuid=? '
            'AND starts_at<=? AND (ends_at IS NULL OR ends_at>?) ORDER BY starts_at DESC',
            (result.employee_uuid, now(), now())).fetchone()
        role = identity.get('role')
        if role not in {*ROLE_CAPABILITIES, 'full'}:
            raise fault('identity_unmapped', 403)
        legacy_manager = role in {'manager', 'full'} and result.team_id == 'crm-pilot-managers'
        role = 'manager' if role == 'full' else role
        if (role not in ROLE_CAPABILITIES or not membership or
                (membership['role'] != role and not legacy_manager
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
        return replace(result, role=role, capabilities=result.capabilities & ROLE_CAPABILITIES[role],
                       employee_scope=employees, all_contracts=legacy_manager, read_all_contracts=True)


class Sessions:
    def __init__(self, key, verifier, *, resolve=principal, clock=time.time, audit=None):
        if not isinstance(key, bytes) or len(key) != 32:
            raise RuntimeError('CRM requires its own 32-byte session key')
        self.cipher, self.verify, self.resolve, self.clock = AESGCM(key), verifier, resolve, clock
        self.audit = audit or audit_exchange

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
            if set(value) not in (keys, keys | {'entry'}):
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

    async def _verify(self, credential, kind='manager', device_fingerprint=''):
        if kind not in ENTRY_KINDS:
            raise fault()
        if kind == 'analyst-device' and not re.fullmatch(r'[a-f0-9]{64}', device_fingerprint):
            raise fault()
        if kind == 'manager':
            return await self.verify(credential)
        return await self.verify(credential, kind=kind, device_fingerprint=device_fingerprint)

    async def exchange(self, credential, *, kind='manager', device_fingerprint=''):
        if not isinstance(credential, str) or not re.fullmatch(r'[A-Za-z0-9_-]{20,512}', credential):
            raise fault()
        identity = await self._verify(credential, kind, device_fingerprint)
        p = await asyncio.to_thread(self.resolve, identity)
        await asyncio.to_thread(self.audit, p, identity)
        issued = int(self.clock())
        payload = dict(uid=identity['uid'], device_id=identity['device_id'], issued=issued,
                       expires=issued + TTL, id=str(uuid.uuid4()), credential=credential, audience=NAMESPACE)
        if kind != 'manager':
            payload['entry'] = dict(kind=kind, device_fingerprint=device_fingerprint)
        return dict(token=self.seal(payload), expires_at=payload['expires'],
                    employee_uuid=p.employee_uuid, name=identity['name'], role=p.role)

    async def authenticate(self, request):
        header = request.headers.get('Authorization', '')
        if not header.startswith('Bearer '):
            raise fault()
        payload = self.open(header[7:])
        identity = await self._verify(payload['credential'], **payload.get('entry', {}))
        if identity['uid'] != payload['uid'] or identity['device_id'] != payload['device_id']:
            raise fault()
        return await asyncio.to_thread(self.resolve, identity)


def audit_exchange(employee, identity):
    with storage.connect() as conn:
        store.audit(conn, employee, 'pilot_session_exchange', str(identity['device_id']),
                    {'authority': 'existing_manager', 'scope': NAMESPACE})
