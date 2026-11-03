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


def fault(code='pilot_session_invalid', status=401):
    messages = {
        'pilot_session_invalid': 'Подключите CRM через действующий вход Руководителя.',
        'pilot_session_expired': 'Срок подключения истёк. Повторите подключение Руководителя.',
        'authority_unavailable': 'Сервер проверки входа временно недоступен. Сохранённые действия остаются в очереди.',
        'manager_required': 'Для общего пилота нужен действующий вход руководителя.',
        'identity_protocol': 'Сервер входа ещё не поддерживает проверку личности CRM.',
        'identity_unmapped': 'Доступ этого руководителя ещё не включён в состав пилота.',
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


class Authority:
    def __init__(self, session, origin='http://hard-core-app-1:8080'):
        if origin not in {'http://hard-core-app-1:8080', 'https://hard.rimborso.ru'}:
            raise RuntimeError('Untrusted Manager authority origin')
        self.session, self.url = session, origin + '/api/manager/device/me'

    async def __call__(self, token):
        try:
            async with self.session.get(self.url, headers={'X-Pult-Token': token,
                    'X-Client-Version': '3.3.0-pilot.2', 'X-Work-Product': 'manager'},
                    timeout=aiohttp.ClientTimeout(total=8), allow_redirects=False) as response:
                if response.status in {401, 403, 410}:
                    raise fault('manager_required', 403)
                if response.status != 200 or (response.content_length or 0) > 65536:
                    raise fault('authority_unavailable', 503)
                raw = await response.content.read(65537)
                if len(raw) > 65536:
                    raise fault('authority_unavailable', 503)
                return validate_identity(json.loads(raw))
        except CRMError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError):
            raise fault('authority_unavailable', 503) from None


def principal(identity):
    with storage.connect(readonly=True) as conn:
        marker = conn.execute("SELECT value FROM crm_meta WHERE key='deployment_namespace'").fetchone()
        if not marker or marker[0] != NAMESPACE:
            raise fault('identity_unmapped', 403)
        result = access.from_uid(conn, identity['uid'], manager_names=[], manager_write=True)
        if result.team_id != 'crm-pilot-managers':
            raise fault('identity_unmapped', 403)
        # The approved test scope covers this isolated database. It never
        # changes the authority's role, team or production permissions.
        employees = frozenset(r[0] for r in conn.execute('SELECT employee_uuid FROM crm_employees WHERE active=1'))
        return replace(result, role='manager', capabilities=result.capabilities & CAPABILITIES,
                       employee_scope=employees, all_contracts=True)


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
            if set(value) != {'uid', 'device_id', 'issued', 'expires', 'id', 'credential', 'audience'}:
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

    async def exchange(self, credential):
        if not isinstance(credential, str) or not re.fullmatch(r'[A-Za-z0-9_-]{20,512}', credential):
            raise fault()
        identity = await self.verify(credential)
        p = await asyncio.to_thread(self.resolve, identity)
        await asyncio.to_thread(self.audit, p, identity)
        issued = int(self.clock())
        payload = dict(uid=identity['uid'], device_id=identity['device_id'], issued=issued,
                       expires=issued + TTL, id=str(uuid.uuid4()), credential=credential, audience=NAMESPACE)
        return dict(token=self.seal(payload), expires_at=payload['expires'],
                    employee_uuid=p.employee_uuid, name=identity['name'], role=p.role)

    async def authenticate(self, request):
        header = request.headers.get('Authorization', '')
        if not header.startswith('Bearer '):
            raise fault()
        payload = self.open(header[7:])
        identity = await self.verify(payload['credential'])
        if identity['uid'] != payload['uid'] or identity['device_id'] != payload['device_id']:
            raise fault()
        return await asyncio.to_thread(self.resolve, identity)


def audit_exchange(employee, identity):
    with storage.connect() as conn:
        store.audit(conn, employee, 'pilot_session_exchange', str(identity['device_id']),
                    {'authority': 'existing_manager', 'scope': NAMESPACE})
