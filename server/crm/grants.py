"""Actor-scoped grants v2 with a read-only legacy shadow projection."""

from dataclasses import dataclass, replace
import json
import uuid

from .contract import CRMError, canonical, identifier, now
from .roles import GRANT_ROLE_CAPABILITIES
from . import storage

LEGACY_MODEL = 1
GRANTS_MODEL = 2
TERRITORIES = frozenset({"self", "employees", "team", "all"})


@dataclass(frozen=True)
class Grant:
    grant_id: str
    actor_uid: int
    role: str
    territory: str
    capabilities: frozenset
    revision: int
    employee_scope: frozenset = frozenset()
    all_contracts: bool = False
    read_all_contracts: bool = False
    is_default: bool = False

    def to_dict(self):
        return dict(
            grant_id=self.grant_id,
            actor_uid=self.actor_uid,
            role=self.role,
            territory=self.territory,
            capabilities=sorted(self.capabilities),
            revision=self.revision,
            employees=sorted(self.employee_scope),
            all_contracts=self.all_contracts,
            read_all_contracts=self.read_all_contracts,
            is_default=self.is_default,
        )


def _error(message, code, status, details=None):
    return CRMError(message, code, status, details)


def _begin(conn, actor_uid):
    if storage.is_postgres(conn) or not conn.in_transaction:
        storage.begin(conn, ["grant-actor:" + str(actor_uid)])


def _access(conn, actor_uid):
    return conn.execute(
        "SELECT * FROM crm_actor_access WHERE actor_uid=?", (actor_uid,)
    ).fetchone()


def model(conn, actor_uid):
    row = _access(conn, actor_uid)
    return int(row["access_model"]) if row else LEGACY_MODEL


def load(conn, actor_uid):
    rows = list(
        conn.execute(
            "SELECT * FROM crm_grants WHERE actor_uid=? AND revoked_at IS NULL "
            "ORDER BY is_default DESC,created_at,grant_id",
            (actor_uid,),
        )
    )
    employees = {}
    if rows:
        ids = [row["grant_id"] for row in rows]
        for row in conn.execute(
            "SELECT grant_id,employee_uuid FROM crm_grant_employees WHERE grant_id IN ("
            + ",".join("?" for _ in ids)
            + ") ORDER BY employee_uuid",
            ids,
        ):
            employees.setdefault(row["grant_id"], set()).add(row["employee_uuid"])
    return tuple(
        Grant(
            grant_id=row["grant_id"],
            actor_uid=int(row["actor_uid"]),
            role=row["role"],
            territory=row["territory"],
            capabilities=frozenset(json.loads(row["capabilities_json"])),
            revision=int(row["revision"]),
            employee_scope=frozenset(employees.get(row["grant_id"], ())),
            all_contracts=bool(row["all_contracts"]),
            read_all_contracts=bool(row["read_all_contracts"]),
            is_default=bool(row["is_default"]),
        )
        for row in rows
    )


def choose(available, *, role=None, grant_id=None, allow_owner=False):
    available = tuple(available)
    roles = {role} if role else set()
    if allow_owner and role in {"manager", "full"}:
        roles.add("owner")
    if grant_id is not None:
        selected = next((grant for grant in available if grant.grant_id == grant_id), None)
        if not selected or (roles and selected.role not in roles):
            raise _error(
                "Выбранное полномочие отозвано или не принадлежит этому входу.",
                "grant_revoked",
                403,
            )
        return selected
    candidates = [grant for grant in available if not roles or grant.role in roles]
    defaults = [grant for grant in candidates if grant.is_default]
    if len(defaults) == 1:
        return defaults[0]
    if len(defaults) == 0 and len(candidates) == 1:
        return candidates[0]
    raise _error(
        "Выберите действующее полномочие для этого входа.",
        "grant_required",
        409,
        {"grant_ids": [grant.grant_id for grant in candidates],
         "grants": [grant.to_dict() for grant in candidates]},
    )


def _active_employees(conn, team_id=None):
    at = now()
    team = "AND m.team_id=?" if team_id is not None else ""
    args = (at, at, team_id) if team_id is not None else (at, at)
    return frozenset(
        row[0]
        for row in conn.execute(
            "SELECT e.employee_uuid FROM crm_employees e JOIN crm_memberships m USING(employee_uuid) "
            "WHERE e.active=1 AND m.starts_at<=? AND (m.ends_at IS NULL OR m.ends_at>?) " + team,
            args,
        )
    )


def _scope(conn, principal, grant):
    if grant.territory == "self":
        return frozenset({principal.employee_uuid})
    if grant.territory == "employees":
        return grant.employee_scope
    if grant.territory == "team":
        return _active_employees(conn, principal.team_id)
    return _active_employees(conn)


def _project(conn, legacy, grant, access_model, policy_revision, available):
    if grant.role == "owner" and grant.territory != "all":
        raise _error("Глобальное полномочие владельца имеет некорректную область.", "grant_invalid", 403)
    all_contracts = grant.all_contracts or grant.territory == "all" or grant.role == "owner"
    return replace(
        legacy,
        role=grant.role,
        capabilities=grant.capabilities | ({'access_manage'} & legacy.capabilities if grant.role == 'analyst' else set()),
        employee_scope=_scope(conn, legacy, grant),
        all_contracts=all_contracts,
        read_all_contracts=grant.read_all_contracts or all_contracts,
        grants=available,
        access_model=access_model,
        policy_revision=policy_revision,
        grant_id=grant.grant_id,
    )


def resolve(conn, legacy, *, role=None, grant_id=None, allow_owner=False, force_model=None):
    row = _access(conn, legacy.actor_uid)
    access_model = int(row["access_model"]) if row else LEGACY_MODEL
    revision = int(row["policy_revision"]) if row else 0
    available = load(conn, legacy.actor_uid)
    effective_model = access_model if force_model is None else force_model
    if effective_model == LEGACY_MODEL:
        return replace(
            legacy,
            grants=available,
            access_model=LEGACY_MODEL,
            policy_revision=revision,
            grant_id=None,
        )
    selected = choose(available, role=role, grant_id=grant_id, allow_owner=allow_owner)
    return _project(conn, legacy, selected, GRANTS_MODEL, revision, available)


def _snapshot(principal):
    return dict(
        role=principal.role,
        capabilities=sorted(principal.capabilities),
        employees=sorted(principal.employee_scope),
        all_contracts=principal.all_contracts,
        read_all_contracts=principal.read_all_contracts,
        grant_id=principal.grant_id,
    )


def shadow(conn, legacy, *, role=None, grant_id=None, allow_owner=False):
    result = dict(actor_uid=legacy.actor_uid, equivalent=False, legacy=_snapshot(legacy))
    try:
        projected = resolve(
            conn,
            legacy,
            role=role,
            grant_id=grant_id,
            allow_owner=allow_owner,
            force_model=GRANTS_MODEL,
        )
    except CRMError as exc:
        result.update(error=exc.code, status=exc.status)
        return result
    current = _snapshot(projected)
    expected = dict(result["legacy"], grant_id=current["grant_id"])
    mismatches = sorted(key for key in expected if expected[key] != current[key])
    result.update(
        equivalent=not mismatches,
        grants=current,
        mismatches=mismatches,
        access_model=projected.access_model,
        policy_revision=projected.policy_revision,
    )
    return result


def _validate(actor_uid, role, territory, capabilities, employees, all_contracts):
    if type(actor_uid) is not int or not 0 < abs(actor_uid) < 2**63:
        raise _error("Некорректный actor grant.", "grant_invalid", 400)
    if role not in GRANT_ROLE_CAPABILITIES or territory not in TERRITORIES:
        raise _error("Неизвестная роль или территория grant.", "grant_invalid", 400)
    if not isinstance(capabilities, (set, frozenset, list, tuple)):
        raise _error("Некорректные capabilities grant.", "grant_invalid", 400)
    capabilities = frozenset(capabilities)
    if not capabilities <= GRANT_ROLE_CAPABILITIES[role]:
        raise _error("Capability не входит в роль grant.", "grant_invalid", 400)
    employees = frozenset(employees or ())
    if territory == "employees" and not employees:
        raise _error("Укажите сотрудников территории grant.", "grant_invalid", 400)
    if territory != "employees" and employees:
        raise _error("Явный список сотрудников допустим только для территории employees.", "grant_invalid", 400)
    if role == "owner" and (territory != "all" or not all_contracts):
        raise _error("Owner является серверным глобальным grant.", "grant_invalid", 400)
    return capabilities, employees


def _grant_state(grant):
    return grant.to_dict() if grant else {}


def _audit(conn, actor_uid, grant_id, revision, action, before, after, changed_by_uid, at):
    conn.execute(
        "INSERT INTO crm_grant_changes VALUES(?,?,?,?,?,?,?,?,?)",
        (
            identifier("grant-change", uuid.uuid4()),
            actor_uid,
            grant_id,
            revision,
            action,
            canonical(before),
            canonical(after),
            changed_by_uid,
            at,
        ),
    )


def _ensure_access(conn, actor_uid):
    conn.execute(
        "INSERT INTO crm_actor_access VALUES(?,?,?) ON CONFLICT(actor_uid) DO NOTHING",
        (actor_uid, LEGACY_MODEL, 0),
    )
    return _access(conn, actor_uid)


def _revision(conn, actor_uid, expected_revision):
    access = _ensure_access(conn, actor_uid)
    current = int(access["policy_revision"])
    if expected_revision is not None and current != expected_revision:
        raise _error(
            "Версия полномочий изменилась. Повторите чтение перед изменением.",
            "policy_revision_conflict",
            409,
            {"policy_revision": current},
        )
    revision = current + 1
    conn.execute(
        "UPDATE crm_actor_access SET policy_revision=? WHERE actor_uid=?",
        (revision, actor_uid),
    )
    return revision


def _upsert(conn, actor_uid, *, role, territory, capabilities, employees=(), grant_id=None,
            is_default=False, all_contracts=False, read_all_contracts=False,
            expected_revision=None, changed_by_uid=None, at=None):
    capabilities, employees = _validate(
        actor_uid, role, territory, capabilities, employees, all_contracts
    )
    at = at or now()
    existing = None
    if grant_id:
        existing = conn.execute(
            "SELECT * FROM crm_grants WHERE grant_id=? AND actor_uid=? AND revoked_at IS NULL",
            (grant_id, actor_uid),
        ).fetchone()
    else:
        existing = conn.execute(
            "SELECT * FROM crm_grants WHERE actor_uid=? AND role=? AND territory=? AND revoked_at IS NULL",
            (actor_uid, role, territory),
        ).fetchone()
    if not grant_id and existing:
        grant_id = existing["grant_id"]
    grant_id = grant_id or identifier("grant", uuid.uuid4())
    before = next((grant for grant in load(conn, actor_uid) if grant.grant_id == grant_id), None)
    desired = Grant(
        grant_id=grant_id,
        actor_uid=actor_uid,
        role=role,
        territory=territory,
        capabilities=capabilities,
        revision=before.revision if before else 0,
        employee_scope=employees,
        all_contracts=bool(all_contracts),
        read_all_contracts=bool(read_all_contracts),
        is_default=bool(is_default),
    )
    if before and _grant_state(before) == _grant_state(desired):
        return before
    revision = _revision(conn, actor_uid, expected_revision)
    if is_default:
        conn.execute(
            "UPDATE crm_grants SET is_default=0 WHERE actor_uid=? AND revoked_at IS NULL",
            (actor_uid,),
        )
    values = (
        grant_id,
        actor_uid,
        role,
        territory,
        canonical(sorted(capabilities)),
        int(all_contracts),
        int(read_all_contracts),
        int(is_default),
        revision,
        at,
        at,
        None,
    )
    if before:
        conn.execute(
            "UPDATE crm_grants SET role=?,territory=?,capabilities_json=?,all_contracts=?,"
            "read_all_contracts=?,is_default=?,revision=?,updated_at=? WHERE grant_id=?",
            (role, territory, values[4], values[5], values[6], values[7], revision, at, grant_id),
        )
        conn.execute("DELETE FROM crm_grant_employees WHERE grant_id=?", (grant_id,))
    else:
        conn.execute("INSERT INTO crm_grants VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", values)
    conn.executemany(
        "INSERT INTO crm_grant_employees VALUES(?,?)",
        [(grant_id, employee) for employee in sorted(employees)],
    )
    after = replace(desired, revision=revision)
    _audit(
        conn,
        actor_uid,
        grant_id,
        revision,
        "grant_updated" if before else "grant_created",
        _grant_state(before),
        _grant_state(after),
        changed_by_uid,
        at,
    )
    return after


def upsert(conn, actor_uid, **kwargs):
    _begin(conn, actor_uid)
    return _upsert(conn, actor_uid, **kwargs)


def backfill(conn, legacy, *, role=None, allow_owner=False, activate=False,
             expected_revision=None, changed_by_uid=None):
    _begin(conn, legacy.actor_uid)
    access = _ensure_access(conn, legacy.actor_uid)
    if expected_revision is not None and int(access["policy_revision"]) != expected_revision:
        raise _error(
            "Версия полномочий изменилась. Повторите чтение перед изменением.",
            "policy_revision_conflict",
            409,
            {"policy_revision": int(access["policy_revision"])},
        )
    scope = frozenset(legacy.employee_scope)
    if legacy.owner:
        territory = "all"
    elif scope == frozenset({legacy.employee_uuid}):
        territory = "self"
    elif scope == _active_employees(conn, legacy.team_id):
        territory = "team"
    elif legacy.all_contracts and scope == _active_employees(conn):
        territory = "all"
    else:
        territory = "employees"
    grant_id = identifier("grant-backfill", legacy.actor_uid)
    previous = conn.execute(
        "SELECT revoked_at FROM crm_grants WHERE grant_id=?", (grant_id,)
    ).fetchone()
    if previous and previous["revoked_at"]:
        grant_id = identifier("grant-backfill", str(legacy.actor_uid) + ":" + str(uuid.uuid4()))
    _upsert(
        conn,
        legacy.actor_uid,
        grant_id=grant_id,
        role=legacy.role,
        territory=territory,
        capabilities=legacy.capabilities,
        employees=scope if territory == "employees" else (),
        all_contracts=legacy.all_contracts or legacy.owner,
        read_all_contracts=legacy.read_all_contracts,
        is_default=True,
        changed_by_uid=changed_by_uid,
    )
    if activate and int(access["access_model"]) != GRANTS_MODEL:
        revision = _revision(conn, legacy.actor_uid, None)
        conn.execute(
            "UPDATE crm_actor_access SET access_model=? WHERE actor_uid=?",
            (GRANTS_MODEL, legacy.actor_uid),
        )
        _audit(
            conn,
            legacy.actor_uid,
            None,
            revision,
            "access_model_activated",
            {"access_model": LEGACY_MODEL},
            {"access_model": GRANTS_MODEL},
            changed_by_uid,
            now(),
        )
    return shadow(conn, legacy, role=role, allow_owner=allow_owner)


def set_model(conn, actor_uid, access_model, *, expected_revision=None, changed_by_uid=None):
    if access_model not in {LEGACY_MODEL, GRANTS_MODEL}:
        raise _error("Неизвестная модель доступа.", "grant_invalid", 400)
    _begin(conn, actor_uid)
    access = _ensure_access(conn, actor_uid)
    if int(access["access_model"]) == access_model:
        return int(access["policy_revision"])
    revision = _revision(conn, actor_uid, expected_revision)
    conn.execute(
        "UPDATE crm_actor_access SET access_model=? WHERE actor_uid=?",
        (access_model, actor_uid),
    )
    _audit(
        conn,
        actor_uid,
        None,
        revision,
        "access_model_changed",
        {"access_model": int(access["access_model"])},
        {"access_model": access_model},
        changed_by_uid,
        now(),
    )
    return revision


def revoke(conn, actor_uid, grant_id, *, role=None, expected_revision=None, changed_by_uid=None):
    _begin(conn, actor_uid)
    row = conn.execute(
        "SELECT * FROM crm_grants WHERE grant_id=? AND actor_uid=? AND revoked_at IS NULL",
        (grant_id, actor_uid),
    ).fetchone()
    if not row or (role is not None and row["role"] != role):
        raise _error("Действующий grant не найден.", "grant_revoked", 403)
    before = next(grant for grant in load(conn, actor_uid) if grant.grant_id == grant_id)
    revision = _revision(conn, actor_uid, expected_revision)
    at = now()
    conn.execute(
        "UPDATE crm_grants SET is_default=0,revision=?,revoked_at=?,updated_at=? WHERE grant_id=?",
        (revision, at, at, grant_id),
    )
    _audit(
        conn,
        actor_uid,
        grant_id,
        revision,
        "role_revoked" if role is not None else "grant_revoked",
        _grant_state(before),
        dict(_grant_state(before), revoked_at=at, revision=revision, is_default=False),
        changed_by_uid,
        at,
    )
    return revision
