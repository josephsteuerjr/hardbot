"""Server-derived principal and current object/field scopes."""

from dataclasses import dataclass, replace
import json

from .contract import CRMError, now
from . import withdrawals


@dataclass(frozen=True)
class Principal:
    actor_uid: int
    employee_uuid: str
    team_id: str
    role: str
    capabilities: frozenset
    employee_scope: frozenset
    timezone: str = "Europe/Samara"
    all_contracts: bool = False
    read_all_contracts: bool = False
    server_operator: bool = False
    grants: tuple = ()
    access_model: int = 1
    policy_revision: int = 0
    grant_id: str | None = None

    @property
    def owner(self):
        return self.role == "owner"


def require(principal, capability):
    if not principal.owner and capability not in principal.capabilities:
        raise CRMError("Недостаточно прав для действия.", "forbidden", 403)


def refresh_write(conn, principal):
    """Recheck the persistent identity/membership inside the write transaction.

    Never broadens the authenticated gate's capabilities. The caller already
    holds storage.begin()'s shared maintenance barrier; identity/assignment
    maintenance takes its exclusive side. Plain reads preserve the app role's
    SELECT-only access to identity tables (row locking would require UPDATE).
    """
    if principal.owner and principal.access_model != 2:return principal
    link=conn.execute('SELECT l.employee_uuid FROM crm_actor_links l JOIN crm_employees e USING(employee_uuid) '
                      'WHERE l.actor_uid=? AND l.active=1 AND e.active=1',(principal.actor_uid,)).fetchone()
    at=now()
    member=conn.execute('SELECT team_id,capabilities_json FROM crm_memberships WHERE employee_uuid=? '
        'AND starts_at<=? AND (ends_at IS NULL OR ends_at>?) ORDER BY starts_at DESC',
        (principal.employee_uuid,at,at)).fetchone()
    if not link or link[0]!=principal.employee_uuid or not member or member['team_id']!=principal.team_id:
        raise CRMError('Изменился действующий вход или кадровый доступ.','scope_changed',403)
    if principal.access_model == 2:
        from . import grants
        legacy = replace(principal, grants=(), access_model=1, policy_revision=0, grant_id=None)
        return grants.resolve(conn, legacy, role=principal.role, grant_id=principal.grant_id,
                              allow_owner=principal.owner)
    return replace(principal,capabilities=principal.capabilities & frozenset(json.loads(member['capabilities_json'])))


def scoped_contracts(conn, principal, *, include_tasks=True):
    if principal.owner or principal.all_contracts:
        return {r[0] for r in conn.execute("SELECT contract_uuid FROM crm_contracts")}
    if not principal.employee_scope:
        return set()
    at = now()
    result = set()
    for employee in principal.employee_scope:
        result.update(
            r[0]
            for r in conn.execute(
                "SELECT contract_uuid FROM crm_effective_assignments WHERE employee_uuid=? AND starts_at<=? "
                "AND (ends_at IS NULL OR ends_at>?)",
                (employee, at, at),
            )
        )
    if include_tasks:
        from . import tasks
        result.update(tasks.grants(conn, principal))
    return result


def reads_all(principal):
    return principal.owner or principal.all_contracts or principal.read_all_contracts


def readable_contracts(conn, principal):
    if reads_all(principal):
        return {r[0] for r in conn.execute("SELECT contract_uuid FROM crm_contracts")}
    result = scoped_contracts(conn, principal)
    if principal.role == 'employee' and 'call' in principal.capabilities:
        from . import logistics
        result.update(logistics.for_employee(conn, principal.employee_uuid, 'call'))
    if principal.role == 'manager' and 'assign' in principal.capabilities:
        result.update(r[0] for r in conn.execute('SELECT DISTINCT mc.contract_uuid FROM crm_managed_contracts mc '
            'JOIN crm_distribution_managers dm ON dm.team_id=mc.team_id AND dm.capability=mc.capability WHERE dm.employee_uuid=?',
            (principal.employee_uuid,)))
    if principal.role in {'employee', 'courier'}:
        result.difference_update(withdrawals.blocked(conn, result))
    return result


def writable_actions(conn, principal, contract_id, *, scope=None, direct_scope=None, delegated=None, enabled=None):
    scope = scoped_contracts(conn, principal) if scope is None else scope
    if contract_id not in scope:
        return []
    from .primary_fence import writable_contracts
    if contract_id not in (writable_contracts(conn,[contract_id]) if enabled is None else enabled):
        return []
    capabilities = set(principal.capabilities)
    if not principal.owner and not principal.all_contracts:
        from . import tasks
        if direct_scope is None:direct_scope=scoped_contracts(conn, principal, include_tasks=False)
        if delegated is None:delegated=tasks.grants(conn, principal)
        if contract_id not in direct_scope:
            capabilities &= delegated.get(contract_id, set())
    if principal.owner:
        capabilities.update({'call', 'visit', 'note', 'contact', 'promise', 'task',
                             'reported_payment', 'document', 'accounting', 'correct'})
    if principal.role == 'employee':
        at = now()
        assigned = {r[0] for r in conn.execute(
            'SELECT capability FROM crm_effective_assignments WHERE employee_uuid=? AND contract_uuid=? '
            'AND starts_at<=? AND (ends_at IS NULL OR ends_at>?)',
            (principal.employee_uuid, contract_id, at, at))}
        from . import tasks
        if delegated is None:delegated=tasks.grants(conn, principal)
        capabilities -= {'call', 'visit'} - assigned - delegated.get(contract_id, set())
    if capabilities & (withdrawals.CONTACT_ACTIONS | {'task'}) and withdrawals.blocked(conn, [contract_id]):
        capabilities -= withdrawals.CONTACT_ACTIONS | {'task'}
    return sorted(capabilities & {'call', 'visit', 'note', 'contact', 'promise', 'task',
                                  'reported_payment', 'document', 'accounting', 'correct', 'delivery'})


def contracts(conn, principal, requested, *, capability=None):
    if (
        not isinstance(requested, list)
        or not requested
        or len(requested) > 100
        or not all(isinstance(key, str) for key in requested)
        or len(set(requested)) != len(requested)
    ):
        raise CRMError("Выберите от 1 до 100 договоров без повторов.")
    if principal.owner or principal.all_contracts or (capability is None and principal.read_all_contracts):
        allowed = {r[0] for r in conn.execute(
            "SELECT contract_uuid FROM crm_contracts WHERE contract_uuid IN ("
            + ",".join("?" for _ in requested) + ")", requested)}
    else:
        allowed = readable_contracts(conn, principal) if capability is None else scoped_contracts(conn, principal)
    if not set(requested) <= allowed:
        raise CRMError(
            "Договор недоступен или назначение изменилось.", "scope_changed", 403
        )
    if capability:
        require(principal, capability)
        if capability in withdrawals.CONTACT_ACTIONS:
            withdrawals.require_contactable(conn, requested)
        from .primary_fence import require_contracts
        require_contracts(conn, requested)
        if not principal.owner and not principal.all_contracts:
            from . import tasks
            direct_scope = scoped_contracts(conn, principal, include_tasks=False)
            delegated = tasks.grants(conn, principal)
            if any(key not in direct_scope and capability not in delegated.get(key, set()) for key in requested):
                raise CRMError('Поручение не даёт права на это действие.', 'forbidden', 403)
        if principal.role == "employee":
            at = now()
            for identifier in requested:
                permission = conn.execute(
                    "SELECT 1 FROM crm_effective_assignments WHERE employee_uuid=? "
                    "AND contract_uuid=? AND capability=? AND starts_at<=? AND (ends_at IS NULL OR ends_at>?)",
                    (principal.employee_uuid, identifier, capability, at, at),
                ).fetchone()
                from . import tasks
                delegated = tasks.grants(conn, principal).get(identifier, set())
                if not permission and capability in {"call", "visit"} and capability not in delegated:
                    raise CRMError(
                        "Изменилось полномочие по договору.", "scope_changed", 403
                    )
    result = [
        dict(
            conn.execute(
                "SELECT * FROM crm_contracts WHERE contract_uuid=?", (key,)
            ).fetchone()
        )
        for key in requested
    ]
    return result


def employee(conn, principal, identifier, capability=None):
    if not principal.owner and identifier not in principal.employee_scope:
        raise CRMError("Сотрудник вне доступной команды.", "forbidden", 403)
    if capability:
        require(principal, capability)
    row = conn.execute(
        "SELECT * FROM crm_employees WHERE employee_uuid=?", (identifier,)
    ).fetchone()
    if not row:
        raise CRMError("Сотрудник не найден.", "not_found", 404)
    return dict(row)


def from_uid(conn, uid, *, owner=False, manager_names=None, manager_write=False,
             common_read=False, use_grants=True, grant_id=None):
    link = conn.execute(
        "SELECT l.*,e.active FROM crm_actor_links l JOIN crm_employees e USING(employee_uuid) "
        "WHERE actor_uid=? AND l.active=1 AND e.active=1",
        (uid,),
    ).fetchone()
    if not link:
        raise CRMError(
            "Для входа не подтверждено кадровое соответствие CRM.",
            "identity_unmapped",
            403,
        )
    at = now()
    membership = conn.execute(
        "SELECT * FROM crm_memberships WHERE employee_uuid=? AND starts_at<=? "
        "AND (ends_at IS NULL OR ends_at>?) ORDER BY starts_at DESC",
        (link["employee_uuid"], at, at),
    ).fetchone()
    if not membership:
        raise CRMError(
            "Не задана действующая команда сотрудника.", "identity_unmapped", 403
        )
    caps = set(json.loads(membership["capabilities_json"]))
    scope = {link["employee_uuid"]}
    role = "owner" if owner else "employee"
    if manager_names is not None and not owner:
        role = "manager" if manager_write else "observer"
        # Names arrive only from the existing authenticated manager gate and
        # are mapped to explicit roster identities. No name-based merging.
        # Existing name scope is only an additional restriction. Canonical
        # membership also must match; homonyms never cross teams.
        scope.update(
            r["employee_uuid"]
            for r in conn.execute(
                """SELECT e.employee_uuid,e.name FROM crm_employees e
            JOIN crm_memberships m USING(employee_uuid) WHERE e.active=1 AND m.team_id=? AND m.starts_at<=?
            AND (m.ends_at IS NULL OR m.ends_at>?)""",
                (membership["team_id"], at, at),
            )
            if r["name"] in manager_names
        )
        caps &= {
            "call",
            "visit",
            "note",
            "contact",
            "promise",
            "task",
            "reported_payment",
            "document",
            "correct",
            "shift_correct",
            "analytics",
            "exchange",
            "accounting",
            "assign",
        }
        if not manager_write:
            caps = {"analytics"}
    legacy = Principal(
        int(uid),
        link["employee_uuid"],
        membership["team_id"],
        role,
        frozenset(caps),
        frozenset(scope),
        membership["timezone"],
        read_all_contracts=common_read,
    )
    if use_grants:
        from . import grants
        return grants.resolve(conn, legacy, role=role, grant_id=grant_id)
    return legacy
