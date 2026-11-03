"""One-time administrative provisioning from an exact approved authority roster.

There is no public provisioning endpoint. Input contains stable account/actor
IDs verified by the authority database, never a desktop name or first launch.
"""
import json

from . import storage
from .analytics import METRIC_VERSION, USEFUL_KINDS
from .contract import CRMError, KINDS, canonical, digest, identifier, instant, now
from .shared_auth import CAPABILITIES, NAMESPACE

TEAM = 'crm-pilot-managers'
COHORT = 'crm-pilot-managers-v1'


def provision(manifest):
    if not isinstance(manifest, dict) or set(manifest) != {'namespace', 'captured_at', 'source_sha256', 'managers'}:
        raise CRMError('Нужен подтверждённый состав пилота.')
    if manifest['namespace'] != NAMESPACE:
        raise CRMError('Другой контур пилота.')
    import re
    if not re.fullmatch('[a-f0-9]{64}', str(manifest['source_sha256'])):
        raise CRMError('Не подтверждён источник состава.')
    captured = instant(manifest['captured_at'])
    age = (instant(now()) - captured).total_seconds()
    if not -60 <= age <= 86400:
        raise CRMError('Нужен свежий срез действующих учётных записей.')
    managers = manifest['managers']
    if not isinstance(managers, list) or not 1 <= len(managers) <= 100:
        raise CRMError('Некорректный состав.')
    seen_uids, seen_accounts = set(), set()
    for item in managers:
        if not isinstance(item, dict) or set(item) != {'actor_uid', 'account_id', 'name', 'role', 'status'}:
            raise CRMError('Неизвестные поля участника.')
        uid, account = item['actor_uid'], item['account_id']
        if type(uid) is not int or not 0 < uid < 2**63 or type(account) is not int or account < 1:
            raise CRMError('Не подтверждены идентификаторы участника.')
        if uid in seen_uids or account in seen_accounts:
            raise CRMError('Участник состава повторён.')
        if item['status'] != 'active' or item['role'] not in {'manager', 'full'}:
            raise CRMError('В составе есть неподтверждённый руководитель.')
        if not isinstance(item['name'], str) or not 1 <= len(item['name']) <= 200:
            raise CRMError('Некорректное имя участника.')
        seen_uids.add(uid); seen_accounts.add(account)
    fingerprint, at = digest(manifest), now()
    with storage.connect() as conn:
        storage.begin(conn)
        previous = conn.execute("SELECT value FROM crm_meta WHERE key='provision_receipt'").fetchone()
        if previous:
            receipt = json.loads(previous[0])
            if receipt['manifest_sha256'] != fingerprint:
                raise CRMError('Состав уже создан по другому срезу. Нужна отдельная сверка.', 'roster_changed', 409)
            return receipt
        marker = conn.execute("SELECT value FROM crm_meta WHERE key='deployment_namespace'").fetchone()
        if marker and marker[0] != NAMESPACE:
            raise CRMError('Обнаружена база другого контура.')
        if conn.execute('SELECT COUNT(*) FROM crm_employees').fetchone()[0]:
            raise CRMError('Начальный состав требует пустого кадрового раздела.', 'roster_not_empty', 409)
        conn.execute('INSERT INTO crm_cohorts VALUES(?,?,?,?,?)',
                     (COHORT, 'Руководители — общий пилот', at, METRIC_VERSION, '{}'))
        for item in managers:
            employee = identifier('employee', 'crm-authority-account:' + str(item['account_id']))
            conn.execute('INSERT INTO crm_employees VALUES(?,?,?,?,1)',
                         (employee, 'authority-account:' + str(item['account_id']), item['name'], 'verified-authority-roster'))
            conn.execute('INSERT INTO crm_actor_links VALUES(?,?,?,1)',
                         (item['actor_uid'], employee, canonical({'manifest_sha256': fingerprint, 'account_id': item['account_id']})))
            conn.execute('INSERT INTO crm_memberships VALUES(?,?,?,?,?,?,?,NULL)',
                         (identifier('membership', employee + ':' + TEAM), employee, TEAM, 'manager',
                          canonical(sorted(CAPABILITIES)), 'Europe/Samara', at))
            conn.execute('INSERT INTO crm_cohort_members VALUES(?,?,?,NULL,?,NULL)',
                         (COHORT, employee, at, canonical(sorted(USEFUL_KINDS | {'report'}))))
        for kind in sorted(KINDS - {'correction'}):
            conn.execute('INSERT INTO crm_route_policies VALUES(?,1,?,?,?,1)',
                         (identifier('route', TEAM + ':' + kind), TEAM, kind, canonical([{'target_id': 'general:pilot'}])))
        receipt = dict(namespace=NAMESPACE, manifest_sha256=fingerprint, provisioned_at=at,
                       managers=len(managers), cohort_id=COHORT, planned_shifts_created=0,
                       work_events_created=0, production_access_changed=False)
        for key, value in [('deployment_namespace', NAMESPACE), ('provision_receipt', canonical(receipt))]:
            conn.execute('INSERT INTO crm_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))
        return receipt
