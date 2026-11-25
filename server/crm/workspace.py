"""Server-derived workspace navigation; module IDs never authorize actions."""

from collections.abc import Mapping

from .contract import CRMError

SCHEMA_VERSION = 1
REVISION = 1

_CATALOG = (
    ("portfolio", "Портфель", "briefcase"),
    ("registry", "Реестр", "table"),
    ("exchange", "Обмен", "exchange"),
    ("manager-statistics", "Показатели", "chart"),
    ("statistics", "Показатели", "chart"),
    ("cabinet", "Мои показатели", "chart"),
    ("queue", "Отправки", "outbox"),
    ("field", "Маршрут", "route"),
    ("tasks", "Задачи", "check-list"),
    ("active-time", "Активное время", "clock"),
    ("team", "Команда", "users"),
    ("script", "Скрипты и задачи", "script"),
    ("shift", "Смена", "calendar"),
    ("adoption", "Внедрение", "adoption"),
    ("logistics", "Курьерские пулы", "truck"),
    ("operator", "Сервер и входы", "server"),
    ("connection", "Подключение", "plug"),
    ("help", "Справка", "help"),
)
_MODULES = {key: dict(title=title, icon=icon) for key, title, icon in _CATALOG}
_SCOPES = {
    "exchange": ["exchange"],
    "field": ["visit"],
    "adoption": ["analytics"],
    "logistics": ["logistics"],
}
_ROLE_MODULES = {
    "employee": (
        "portfolio", "registry", "cabinet", "queue", "field", "tasks",
        "active-time", "script", "connection", "help",
    ),
    "manager": (
        "portfolio", "registry", "manager-statistics", "logistics",
        "active-time", "team", "script", "exchange", "operator", "connection",
    ),
    "analyst": ("registry", "exchange", "statistics", "team", "connection"),
    "observer": (
        "portfolio", "tasks", "cabinet", "shift", "script", "queue",
        "exchange", "adoption", "connection", "help",
    ),
    "courier": (
        "portfolio", "tasks", "cabinet", "queue", "shift", "script",
        "connection", "help",
    ),
    "logistic": ("logistics", "connection"),
}
_GENERIC_MODULES = _ROLE_MODULES["observer"]
_KNOWN_ROLES = frozenset(_ROLE_MODULES) | {"owner"}
_ROLE_ORDER = {role: index for index, role in enumerate((*_ROLE_MODULES, "owner"))}


def catalog_ids():
    return tuple(key for key, _, _ in _CATALOG)


def _value(grant, key, default=None):
    if isinstance(grant, Mapping):
        return grant.get(key, default)
    return getattr(grant, key, default)


def _legacy_authorities(principal):
    return [(principal.role, principal.capabilities, principal.server_operator)]


def _grant_authorities(principal):
    if getattr(principal, "access_model", None) == 1:
        return _legacy_authorities(principal), False
    grants = getattr(principal, "grants", None)
    if grants is None:
        return _legacy_authorities(principal), False
    if isinstance(grants, Mapping) or not hasattr(grants, "__iter__"):
        grants = (grants,)
    result = []
    for grant in grants:
        role = _value(grant, "role")
        capabilities = _value(grant, "capabilities", ()) or ()
        if not isinstance(role, str) or role not in _KNOWN_ROLES:
            continue
        actor_uid = _value(grant, "actor_uid")
        if actor_uid is not None and actor_uid != getattr(principal, "actor_uid", None):
            continue
        if role == "owner" and _value(grant, "territory") != "all":
            continue
        result.append((role, frozenset(capabilities), False))
    result.sort(key=lambda item: _ROLE_ORDER[item[0]])
    return result, True


def _allowed(module_id, capabilities):
    return all(scope in capabilities for scope in _SCOPES.get(module_id, ()))


def _role_modules(role, capabilities, server_operator, *, grants_mode):
    if role not in _KNOWN_ROLES:
        return []
    modules = _ROLE_MODULES.get(role, _GENERIC_MODULES if not grants_mode else ())
    result = [
        key for key in modules
        if _allowed(key, capabilities) and (key != "operator" or server_operator)
    ]
    return result


def _node(module_id, order, children=()):
    definition = _MODULES[module_id]
    return dict(
        id=module_id,
        title=definition["title"],
        icon=definition["icon"],
        order=order,
        children=list(children),
        scopes=list(_SCOPES.get(module_id, ())),
    )


def _find(nodes, module_id):
    for node in nodes:
        if node["id"] == module_id:
            return node
        found = _find(node["children"], module_id)
        if found is not None:
            return found
    return None


def require(manifest_value, module_id):
    node = _find(manifest_value["tree"], module_id) if isinstance(module_id, str) else None
    if node is None:
        raise CRMError("Раздел рабочего места не найден.", "unknown_module", 404)
    return node


def manifest(principal, requested_module=None):
    if principal.owner:
        identifiers = list(catalog_ids())
        children = {}
    else:
        authorities, grants_mode = _grant_authorities(principal)
        identifiers = []
        children = {}
        for role, capabilities, server_operator in authorities:
            if role == "owner":
                role_ids = list(catalog_ids())
            else:
                role_ids = _role_modules(
                    role, capabilities, server_operator or principal.server_operator,
                    grants_mode=grants_mode,
                )
            for key in role_ids:
                if key not in identifiers:
                    identifiers.append(key)
            if role == "manager" and "script" in role_ids and "tasks" not in identifiers:
                children["script"] = ["tasks"]
        if not identifiers:
            identifiers = ["connection"]
    tree = []
    for index, key in enumerate(identifiers):
        nested = [
            _node(child, (index + 1) * 100 + child_index + 1)
            for child_index, child in enumerate(children.get(key, ()))
            if child not in identifiers
        ]
        tree.append(_node(key, (index + 1) * 100, nested))
    preferred = ("registry", "portfolio", "connection") if principal.role == "analyst" else (
        ("logistics", "connection") if principal.role == "logistic"
        else ("portfolio", "registry", "logistics", "connection")
    )
    default = next((key for key in preferred if key in identifiers), identifiers[0])
    result = dict(
        schema_version=SCHEMA_VERSION,
        revision=max(REVISION, int(getattr(principal, "policy_revision", 0) or 0)),
        defaultModule=default,
        tree=tree,
    )
    if requested_module is not None:
        require(result, requested_module)
    return result
