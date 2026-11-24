"""Resolve report authors against the same staff, offices and regions as the primary team."""
from collections import defaultdict
import json
import os

from . import primary_fence
from .contract import CRMError


def visit_norms(conn):
    settings = dict(conn.execute(
        "SELECT key,value FROM settings WHERE key IN ('visit_plan_per_day','visit_plan_by_employee')"
    ).fetchall())
    def bounded(raw, limit):
        try:return int(raw) if 0 < int(raw) <= limit else None
        except (ValueError,TypeError):return None
    default = bounded(settings.get('visit_plan_per_day'),1000) or 10
    try:personal = json.loads(settings.get('visit_plan_by_employee') or '{}')
    except ValueError:personal = {}
    if not isinstance(personal,dict):personal = {}
    return default,{name:norm for name,raw in personal.items() if (norm:=bounded(raw,100))}


def staff(principal, rows, *, shared):
    if not os.environ.get('HARD_CRM_PRIMARY_DB'):
        # A standalone synthetic world has no regional authority.
        if shared:
            raise CRMError('Недоступен справочник команды.', 'unavailable', 503)
        return {r['employee_uuid']: dict(name=r['name'], country='', offices=[]) for r in rows}

    import country_scope
    import db
    import team_entry
    import team_work
    with primary_fence.connect(readonly=True) as conn:
        conn.execute('BEGIN')
        manager_row = conn.execute('SELECT * FROM managers WHERE telegram_id=?', (principal.actor_uid,)).fetchone()
        if not (principal.owner or principal.role == 'analyst') and not manager_row:
            raise CRMError('Не найдена действующая область руководителя.', 'forbidden', 403)
        manager = db._manager_dict(manager_row) if manager_row else {}
        families = {r['employee_id']: r for r in team_entry.families(conn)}
        accounts = defaultdict(set)
        for r in conn.execute("SELECT employee,office FROM accounts WHERE role='agent' AND office<>''"):
            accounts[db._name_key(r['employee'])].add(r['office'])
        registry = team_work._registry(conn)
        default_norm, norms = visit_norms(conn)
    by_name = defaultdict(set)
    people = {}
    for item in registry:
        key = item['employee_id'] or 'portfolio:' + db._name_key(item['employee'])
        family = families.get(key)
        names = family['keys'] if family else {db._name_key(item['employee'])}
        offices = [o for o in item['offices'] if o]
        if not offices:
            known = {o for n in names for o in accounts[n]}
            if len(known) == 1:
                offices = sorted(known)
        country = item['direction'] or country_scope.scope_country(offices) or ''
        item = {**item, 'direction': country, 'offices': offices}
        permitted = principal.owner or (principal.role == 'analyst' and not manager_row) or team_work._covered(item, manager['employees'], manager)
        if manager.get('direction') in ('uz', 'tj') and country != manager['direction'] and not principal.owner:
            permitted = False
        # Restrict a city manager to their own offices for multi-office staff.
        if permitted and not principal.owner and not manager.get('direction') and manager.get('office_cities'):
            visible = {o.casefold() for o in manager['office_cities']}
            if not names.intersection(db._name_key(v) for v in manager['employees']):
                offices = [o for o in offices if o.casefold() in visible]
        norm = norms.get(item['employee'])
        people[key] = dict(name=item['employee'],country=country,offices=offices,
                           visit_norm=(norm or default_norm) if 'hard' in item['products'] else None,
                           personal_norm=norm) if permitted and (not family or item['active']) else None
        for name in names:
            by_name[name].add(key)

    result = {}
    for row in rows:
        key = str(row['staff_id'] or '').removeprefix('authority-staff:')
        if key not in people:
            matches = by_name[db._name_key(row['name'])]
            if len(matches) != 1:
                continue
            key = next(iter(matches))
        if people[key] is not None:
            result[row['employee_uuid']] = {**people[key], 'group': key}
    return result
