"""Server-derived principal and current object/field scopes."""

from dataclasses import dataclass
import json

from .contract import CRMError, now


@dataclass(frozen=True)
class Principal:
    actor_uid: int
    employee_uuid: str
    team_id: str
    role: str
    capabilities: frozenset
    employee_scope: frozenset
    timezone: str = "Europe/Samara"

    @property
    def owner(self):
        return self.role == "owner"


def require(principal, capability):
    if not principal.owner and capability not in principal.capabilities:
        raise CRMError("Недостаточно прав для действия.", "forbidden", 403)


def scoped_contracts(conn, principal):
    if principal.owner:
        return {r[0] for r in conn.execute("SELECT contract_uuid FROM crm_contracts")}
    if not principal.employee_scope:
        return set()
    at = now()
    result = set()
    for employee in principal.employee_scope:
        result.update(
            r[0]
            for r in conn.execute(
                "SELECT contract_uuid FROM crm_assignments WHERE employee_uuid=? AND starts_at<=? "
                "AND (ends_at IS NULL OR ends_at>?)",
                (employee, at, at),
            )
        )
    return result


def contracts(conn, principal, requested, *, capability=None):
    if (
        not isinstance(requested, list)
        or not requested
        or len(requested) > 100
        or not all(isinstance(key, str) for key in requested)
        or len(set(requested)) != len(requested)
    ):
        raise CRMError("Выберите от 1 до 100 договоров без повторов.")
    allowed = scoped_contracts(conn, principal)
    if not set(requested) <= allowed:
        raise CRMError(
            "Договор недоступен или назначение изменилось.", "scope_changed", 403
        )
    if capability:
        require(principal, capability)
        if principal.role == "employee":
            at = now()
            for identifier in requested:
                permission = conn.execute(
                    "SELECT 1 FROM crm_assignments WHERE employee_uuid=? "
                    "AND contract_uuid=? AND capability=? AND starts_at<=? AND (ends_at IS NULL OR ends_at>?)",
                    (principal.employee_uuid, identifier, capability, at, at),
                ).fetchone()
                if not permission and capability in {"call", "visit"}:
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


def from_uid(conn, uid, *, owner=False, manager_names=None, manager_write=False):
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
        }
        if not manager_write:
            caps = {"analytics"}
    return Principal(
        int(uid),
        link["employee_uuid"],
        membership["team_id"],
        role,
        frozenset(caps),
        frozenset(scope),
        membership["timezone"],
    )
