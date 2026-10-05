"""Загрузка и строгая проверка JSON-конфигурации."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import ConfigurationError
from .normalize import norm_title


SUPPORTED_PORTFOLIO_LAYOUT = "root/group/employee/*.xlsb"

# Registry-cycle owns personal↔master execution, while this machine contract
# owns the shared workbook boundary.  Loading it read-only here catches a
# sheet/header/mapping drift before discovery or Excel is touched.
_HARDBOT_DIR = Path(__file__).resolve().parents[1] / "server"
if str(_HARDBOT_DIR) not in sys.path:
    sys.path.insert(0, str(_HARDBOT_DIR))
import exchange_contract as _exchange_contract  # noqa: E402
import fields as _hardbot_fields  # noqa: E402

if Path(_exchange_contract.__file__).resolve() != (
    _HARDBOT_DIR / "exchange_contract.py"
).resolve():
    raise RuntimeError("exchange_contract imported outside the hardbot checkout")
if Path(_hardbot_fields.__file__).resolve() != (
    _HARDBOT_DIR / "fields.py"
).resolve():
    raise RuntimeError("fields imported outside the hardbot checkout")


@dataclass(frozen=True, slots=True)
class FieldRule:
    title: str
    profiles: tuple[str, ...]
    empty: str = "ignore"
    conflict: str = "error"
    value_type: str = "text"
    master_conflict: str = "require_baseline"
    invalid: str = "error"
    comparison_type: str | None = None
    initial_master_values: tuple[Any, ...] = ()


@dataclass(frozen=True, slots=True)
class MonthRolloverConfig:
    enabled: bool
    trigger: str
    current_payments_title: str
    history_title_template: str
    employee_clear_titles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FieldConfig:
    sheet_name: str
    statistics_sheet: str
    status_sheet: str
    key_title: str
    responsible_title: str
    header_search_rows: int
    type_row_offset: int
    profile_tags: dict[str, tuple[str, ...]]
    collect: tuple[FieldRule, ...]
    preserve_if_master_blank: tuple[str, ...]
    aliases: dict[str, tuple[str, ...]]
    previous_responsible_title: str | None = None
    employee_header_fill_rgb: tuple[str, ...] = ("#FFFF00",)
    strict_header_color_validation: bool = False
    month_rollover: MonthRolloverConfig | None = None
    soft_responsible_title: str = "ответственный Сотрудник СОФТ"

    def title_variants(self, title: str) -> set[str]:
        """Нормализованные написания колонки: каноническое + все алиасы.

        Офис переименовывает колонки общего реестра, не спрашивая никого (в
        сентябре 2026 «ответственный Сотрудник» стал «…ХАРД»). Искать колонку
        по одному буквальному заголовку — значит останавливать перенос личных
        реестров на каждом таком переименовании.
        """
        return {
            norm_title(title),
            *(norm_title(alias) for alias in self.aliases.get(title, ())),
        }


@dataclass(frozen=True, slots=True)
class GroupConfig:
    name: str
    profile: str
    enabled: bool
    required: bool


@dataclass(frozen=True, slots=True)
class PortfolioRoot:
    country: str
    path: str
    groups: tuple[GroupConfig, ...]


@dataclass(frozen=True, slots=True)
class AppConfig:
    schema_version: int
    product_name: str
    environment_name: str
    master_pattern: str
    workspace: str
    month_names: dict[int, str]
    portfolio_roots: tuple[PortfolioRoot, ...]
    portfolio_layout: str
    personal_filename_policy: str
    group_discovery_mode: str
    profile_detection_mode: str
    discovery_audit_mode: str
    unsupported_workbook_policy: str
    backup_name_prefixes: tuple[str, ...]
    active_extensions: tuple[str, ...]
    ignored_directory_prefixes: tuple[str, ...]
    responsible_aliases: dict[str, str]
    scheduler: dict[str, Any]
    manual_upward: dict[str, Any]
    manual_downward: dict[str, Any]
    manual_ui: dict[str, Any]
    notifications: dict[str, Any]
    safety: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ConfigBundle:
    app: AppConfig
    fields: FieldConfig
    theme: dict[str, Any]
    config_dir: Path
    project_root: Path
    config_identity: str = ""
    config_payloads: tuple[tuple[str, str], ...] = ()

    def render(self, value: str, at: datetime | None = None) -> str:
        at = at or datetime.now()
        month_name = self.app.month_names.get(at.month)
        if not month_name:
            raise ConfigurationError(f"нет названия месяца {at.month} в app.json")
        tokens = {
            "project_root": str(self.project_root),
            "config_dir": str(self.config_dir),
            "month_ru": month_name,
            "month": f"{at.month:02d}",
            "year": str(at.year),
            "date": at.strftime("%Y%m%d"),
        }
        expanded = os.path.expandvars(os.path.expanduser(value))
        try:
            return expanded.format_map(tokens)
        except KeyError as exc:
            raise ConfigurationError(f"неизвестный плейсхолдер {{{exc.args[0]}}}: {value}") from exc

    def master_path(self, at: datetime | None = None) -> Path:
        return Path(self.render(self.app.master_pattern, at))

    def workspace_path(self, at: datetime | None = None) -> Path:
        return Path(self.render(self.app.workspace, at))

    def pinned_config(self, name: str) -> dict[str, Any]:
        """Return a fresh object from the exact bytes bound by config_identity."""

        payload = dict(self.config_payloads).get(name)
        if payload is None:
            raise ConfigurationError(
                f"конфигурация {name} не закреплена в ConfigBundle"
            )
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise ConfigurationError(f"конфигурация {name} должна быть object")
        return decoded


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"нет конфигурации: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"битый JSON {path}: строка {exc.lineno}, {exc.msg}") from exc


def _require(mapping: dict[str, Any], key: str, where: str) -> Any:
    if key not in mapping:
        raise ConfigurationError(f"в {where} нет обязательного ключа {key!r}")
    return mapping[key]


def _text_values(
    mapping: Any,
    where: str,
    keys: tuple[str, ...],
) -> dict[str, str]:
    """Read operator-facing copy without silently accepting blank UI text."""
    if not isinstance(mapping, dict):
        raise ConfigurationError(f"{where} должен быть объектом")
    normalized = dict(mapping)
    for key in keys:
        value = str(_require(mapping, key, where)).strip()
        if not value:
            raise ConfigurationError(f"{where}.{key} не должен быть пустым")
        normalized[key] = value
    return normalized


def _parse_manual_ui(raw: Any) -> dict[str, Any]:
    """Validate the single two-step operator experience from app.json."""
    if not isinstance(raw, dict):
        raise ConfigurationError("manual_ui должен быть объектом")
    ui = dict(raw)
    ui["window"] = _text_values(
        raw.get("window"),
        "manual_ui.window",
        ("title", "subtitle"),
    )
    ui["actions"] = _text_values(
        raw.get("actions"),
        "manual_ui.actions",
        (
            "check_upward",
            "check_downward",
            "retry_upward",
            "retry_downward",
            "publish_upward",
            "publish_downward",
            "rules",
            "open_report",
            "details",
            "details_hide",
            "save",
            "cancel",
            "add_root",
            "browse",
            "remove",
            "close",
        ),
    )
    ui["sections"] = _text_values(
        raw.get("sections"),
        "manual_ui.sections",
        (
            "upward",
            "downward",
            "status",
            "summary",
            "changes",
            "issues",
            "details",
            "rules",
            "folders",
        ),
    )
    ui["copy"] = _text_values(
        raw.get("copy"),
        "manual_ui.copy",
        (
            "upward",
            "downward",
            "rules",
            "rules_saved",
            "no_upward_changes",
            "no_downward_changes",
            "payment_scope",
            "technical",
            "blocked",
            "upward_published",
            "downward_published",
        ),
    )
    ui["states"] = _text_values(
        raw.get("states"),
        "manual_ui.states",
        (
            "idle",
            "checking",
            "ready",
            "no_changes",
            "blocked",
            "publishing",
            "published",
            "failed",
        ),
    )
    ui["templates"] = _text_values(
        raw.get("templates"),
        "manual_ui.templates",
        (
            "source_count",
            "change_count",
            "portfolio_change_count",
            "row_count",
            "employee_payment_count",
            "issue_count",
            "master",
            "baseline",
            "progress",
            "run",
            "digest",
            "report",
            "receipt",
            "published_files",
            "issue_group",
        ),
    )
    raw_limits = raw.get("limits")
    if not isinstance(raw_limits, dict):
        raise ConfigurationError("manual_ui.limits должен быть объектом")
    ui["limits"] = {}
    for key in ("change_preview", "issue_preview", "value_preview_chars"):
        try:
            value = int(_require(raw_limits, key, "manual_ui.limits"))
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(
                f"manual_ui.limits.{key} должен быть целым числом"
            ) from exc
        if value < 1:
            raise ConfigurationError(
                f"manual_ui.limits.{key} должен быть >= 1"
            )
        ui["limits"][key] = value
    raw_issue_display = raw.get("issue_display")
    if not isinstance(raw_issue_display, dict):
        raise ConfigurationError("manual_ui.issue_display должен быть объектом")
    severities = tuple(
        str(value).strip().casefold()
        for value in raw_issue_display.get("severities", [])
    )
    if not severities or not set(severities) <= {"error", "warning", "info"}:
        raise ConfigurationError(
            "manual_ui.issue_display.severities содержит неизвестный уровень"
        )
    hidden_codes_raw = raw_issue_display.get("hidden_codes", [])
    if not isinstance(hidden_codes_raw, list) or not all(
        str(value).strip() for value in hidden_codes_raw
    ):
        raise ConfigurationError(
            "manual_ui.issue_display.hidden_codes должен быть списком строк"
        )
    ui["issue_display"] = {
        "severities": severities,
        "hidden_codes": tuple(str(value).strip() for value in hidden_codes_raw),
        "group_repeated": bool(raw_issue_display.get("group_repeated", True)),
    }
    confirmations = raw.get("confirmations")
    if not isinstance(confirmations, dict):
        raise ConfigurationError("manual_ui.confirmations должен быть объектом")
    ui["confirmations"] = {
        name: _text_values(
            confirmations.get(name),
            f"manual_ui.confirmations.{name}",
            (
                "title",
                "body",
                "input",
                "invalid",
                "cancelled",
                "success_title",
            ),
        )
        for name in ("upward", "downward")
    }
    ui["rules"] = _text_values(
        raw.get("rules"),
        "manual_ui.rules",
        (
            "country",
            "root_path",
            "root_hint",
            "empty_root",
        ),
    )
    return ui


def _parse_manual_archive(raw: Any, where: str, *, layout: bool) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ConfigurationError(f"{where} должен быть объектом")
    keys = ["directory", "label", "receipt_directory"]
    if layout:
        keys.append("layout")
    parsed = _text_values(raw, where, tuple(keys))
    return parsed


def _parse_manual_operation(raw: Any, where: str, *, layout: bool) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ConfigurationError(f"{where} должен быть объектом")
    operation = dict(raw)
    if not bool(operation.get("enabled", False)):
        raise ConfigurationError(f"{where}.enabled должен быть true")
    if bool(operation.get("automatic_publish", True)):
        raise ConfigurationError(f"{where}.automatic_publish должен быть false")
    if bool(operation.get("automatic_rollover", True)):
        raise ConfigurationError(f"{where}.automatic_rollover должен быть false")
    busy_policy = str(_require(operation, "busy_file_policy", where)).strip()
    if busy_policy != "block":
        raise ConfigurationError(
            f"{where}.busy_file_policy должен быть 'block': соседние копии "
            "и принудительное закрытие в ручном приложении запрещены"
        )
    phrase = str(_require(operation, "confirmation_phrase", where)).strip()
    if not phrase:
        raise ConfigurationError(f"{where}.confirmation_phrase не должен быть пустым")
    conflict_policy = str(
        _require(operation, "conflict_policy", where)
    ).strip().casefold()
    if conflict_policy != "employee_wins":
        raise ConfigurationError(
            f"{where}.conflict_policy должен быть 'employee_wins'"
        )
    operation["busy_file_policy"] = busy_policy
    operation["confirmation_phrase"] = phrase
    operation["conflict_policy"] = conflict_policy
    operation["backup"] = _parse_manual_archive(
        _require(operation, "backup", where),
        f"{where}.backup",
        layout=layout,
    )
    return operation


def _parse_app(raw: dict[str, Any]) -> AppConfig:
    version = int(_require(raw, "schema_version", "app.json"))
    if version != 2:
        raise ConfigurationError(f"неподдерживаемая schema_version={version}")
    product = _require(raw, "product", "app.json")
    paths = _require(raw, "paths", "app.json")
    discovery = _require(raw, "discovery", "app.json")
    calendar = _require(raw, "calendar", "app.json")
    portfolio_layout = str(
        _require(discovery, "portfolio_layout", "discovery")
    ).strip()
    if portfolio_layout != SUPPORTED_PORTFOLIO_LAYOUT:
        raise ConfigurationError(
            "discovery.portfolio_layout должен быть "
            f"{SUPPORTED_PORTFOLIO_LAYOUT!r}, получено {portfolio_layout!r}"
        )
    personal_filename_policy = str(discovery.get('personal_filename_policy', 'employee_name')).strip()
    if personal_filename_policy != 'employee_name':
        raise ConfigurationError('discovery.personal_filename_policy должен быть employee_name')
    group_discovery_mode = str(
        discovery.get("group_mode", "configured")
    ).strip().casefold()
    if group_discovery_mode not in {"configured", "all_except_ignored"}:
        raise ConfigurationError(
            "discovery.group_mode должен быть 'configured' или "
            "'all_except_ignored'"
        )
    profile_detection_mode = str(
        discovery.get("profile_detection", "configured")
    ).strip().casefold()
    if profile_detection_mode not in {"configured", "workbook_columns"}:
        raise ConfigurationError(
            "discovery.profile_detection должен быть 'configured' или "
            "'workbook_columns'"
        )
    if (
        group_discovery_mode == "all_except_ignored"
        and profile_detection_mode != "workbook_columns"
    ):
        raise ConfigurationError(
            "автоматический поиск групп требует "
            "discovery.profile_detection='workbook_columns'"
        )
    active_extensions = tuple(
        str(ext).casefold()
        for ext in _require(
            discovery,
            "active_extensions",
            "discovery",
        )
    )
    if set(active_extensions) != {".xlsb"}:
        raise ConfigurationError(
            "для discovery.portfolio_layout "
            f"{SUPPORTED_PORTFOLIO_LAYOUT!r} active_extensions должен "
            "содержать только '.xlsb'"
        )
    ignored_prefixes_raw = _require(
        discovery,
        "ignored_directory_prefixes",
        "discovery",
    )
    if not isinstance(ignored_prefixes_raw, list) or not all(
        str(prefix).strip() for prefix in ignored_prefixes_raw
    ):
        raise ConfigurationError(
            "discovery.ignored_directory_prefixes должен быть списком непустых строк"
        )
    backup_prefixes_raw = _require(
        discovery,
        "backup_name_prefixes",
        "discovery",
    )
    if not isinstance(backup_prefixes_raw, list) or not all(
        str(prefix).strip() for prefix in backup_prefixes_raw
    ):
        raise ConfigurationError(
            "discovery.backup_name_prefixes должен быть списком непустых строк"
        )
    discovery_audit_mode = str(
        _require(discovery, "audit_mode", "discovery")
    ).strip().casefold()
    if discovery_audit_mode not in {"direct", "deep"}:
        raise ConfigurationError(
            "discovery.audit_mode должен быть 'direct' или 'deep'"
        )
    unsupported_workbook_policy = str(
        _require(discovery, "unsupported_workbook_policy", "discovery")
    ).strip().casefold()
    if unsupported_workbook_policy not in {"error", "warning", "ignore"}:
        raise ConfigurationError(
            "discovery.unsupported_workbook_policy должен быть 'error' "
            "'warning' или 'ignore'"
        )

    month_names = {int(key): str(value) for key, value in calendar["month_names"].items()}
    if set(month_names) != set(range(1, 13)):
        raise ConfigurationError("calendar.month_names должен содержать месяцы 1..12")

    roots: list[PortfolioRoot] = []
    for index, item in enumerate(_require(paths, "portfolio_roots", "app.json")):
        if not isinstance(item, dict):
            raise ConfigurationError(f"paths.portfolio_roots[{index}] должен быть объектом")
        country = str(_require(item, "country", f"paths.portfolio_roots[{index}]")).strip()
        root_path = str(_require(item, "path", f"paths.portfolio_roots[{index}]")).strip()
        if not country or not root_path:
            raise ConfigurationError(
                f"paths.portfolio_roots[{index}] должен содержать страну и путь"
            )
        group_names: set[str] = set()
        parsed_groups: list[GroupConfig] = []
        for group_index, group in enumerate(item.get("groups", [])):
            where = f"paths.portfolio_roots[{index}].groups[{group_index}]"
            if not isinstance(group, dict):
                raise ConfigurationError(f"{where} должен быть объектом")
            name = str(_require(group, "name", where)).strip()
            profile = str(_require(group, "profile", where)).strip()
            if not name or not profile:
                raise ConfigurationError(
                    f"{where} должен содержать непустые name и profile"
                )
            normalized_name = name.casefold()
            if normalized_name in group_names:
                raise ConfigurationError(
                    f"paths.portfolio_roots[{index}] содержит повторяющуюся группу {name!r}"
                )
            group_names.add(normalized_name)
            parsed_groups.append(
                GroupConfig(
                    name=name,
                    profile=profile,
                    enabled=bool(group.get("enabled", True)),
                    required=bool(group.get("required", True)),
                )
            )
        groups = tuple(parsed_groups)
        if group_discovery_mode == "configured" and not groups:
            raise ConfigurationError(f"paths.portfolio_roots[{index}] не содержит groups")
        if group_discovery_mode == "all_except_ignored" and groups:
            raise ConfigurationError(
                f"paths.portfolio_roots[{index}].groups не используются при "
                "discovery.group_mode='all_except_ignored'"
            )
        roots.append(
            PortfolioRoot(
                country=country,
                path=root_path,
                groups=groups,
            )
        )

    safety = dict(raw.get("safety") or {})
    if not bool(safety.get("block_unconfigured_groups_with_files", True)):
        raise ConfigurationError(
            "safety.block_unconfigured_groups_with_files должен быть true "
            "для fail-closed portfolio_layout"
        )
    multi_owner_policy = str(
        safety.get("multi_owner_case_policy", "block")
    )
    if multi_owner_policy not in {"block", "master_responsible"}:
        raise ConfigurationError(
            "safety.multi_owner_case_policy должен быть "
            "'block' или 'master_responsible'"
        )
    busy_file_policy = str(safety.get("busy_file_policy", "block"))
    if busy_file_policy not in {"block", "sidecar", "force_close"}:
        raise ConfigurationError(
            "safety.busy_file_policy должен быть "
            "'block', 'sidecar' или 'force_close'"
        )
    force_close = safety.get("force_close") or {}
    if not isinstance(force_close, dict):
        raise ConfigurationError("safety.force_close должен быть объектом")
    unsaved_policy = str(
        force_close.get("unsaved_changes_policy", "block")
    )
    if unsaved_policy not in {"block", "save_then_close", "discard"}:
        raise ConfigurationError(
            "safety.force_close.unsaved_changes_policy должен быть "
            "'block', 'save_then_close' или 'discard'"
        )
    try:
        local_excel_attempts = int(safety.get("local_excel_attempts", 2))
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(
            "safety.local_excel_attempts должен быть целым числом"
        ) from exc
    if not 1 <= local_excel_attempts <= 3:
        raise ConfigurationError(
            "safety.local_excel_attempts должен быть от 1 до 3"
        )
    safety["local_excel_attempts"] = local_excel_attempts

    manual_upward = _parse_manual_operation(
        _require(raw, "manual_upward", "app.json"),
        "manual_upward",
        layout=False,
    )
    forbidden_titles = _require(
        manual_upward,
        "forbidden_master_titles",
        "manual_upward",
    )
    forbidden_prefixes = _require(
        manual_upward,
        "forbidden_master_title_prefixes",
        "manual_upward",
    )
    if not isinstance(forbidden_titles, list) or not all(
        str(value).strip() for value in forbidden_titles
    ):
        raise ConfigurationError(
            "manual_upward.forbidden_master_titles должен быть непустым списком"
        )
    if not isinstance(forbidden_prefixes, list) or not all(
        str(value).strip() for value in forbidden_prefixes
    ):
        raise ConfigurationError(
            "manual_upward.forbidden_master_title_prefixes должен быть "
            "непустым списком"
        )
    manual_upward["forbidden_master_titles"] = [
        str(value).strip() for value in forbidden_titles
    ]
    manual_upward["forbidden_master_title_prefixes"] = [
        str(value).strip() for value in forbidden_prefixes
    ]
    manual_downward = _parse_manual_operation(
        _require(raw, "manual_downward", "app.json"),
        "manual_downward",
        layout=True,
    )
    for key in (
        "forbidden_personal_titles",
        "forbidden_personal_title_prefixes",
    ):
        values = _require(manual_downward, key, "manual_downward")
        if not isinstance(values, list) or not all(
            str(value).strip() for value in values
        ):
            raise ConfigurationError(
                f"manual_downward.{key} должен быть непустым списком"
            )
        manual_downward[key] = [str(value).strip() for value in values]
    if not bool(manual_downward.get("require_zero_master_changes", False)):
        raise ConfigurationError(
            "manual_downward.require_zero_master_changes должен быть true"
        )
    manual_ui = _parse_manual_ui(_require(raw, "manual_ui", "app.json"))

    return AppConfig(
        schema_version=version,
        product_name=str(product["name"]),
        environment_name=str(product["environment"]),
        master_pattern=str(paths["master_pattern"]),
        workspace=str(paths["workspace"]),
        month_names=month_names,
        portfolio_roots=tuple(roots),
        portfolio_layout=portfolio_layout,
        personal_filename_policy=personal_filename_policy,
        group_discovery_mode=group_discovery_mode,
        profile_detection_mode=profile_detection_mode,
        discovery_audit_mode=discovery_audit_mode,
        unsupported_workbook_policy=unsupported_workbook_policy,
        backup_name_prefixes=tuple(
            str(prefix).strip().casefold()
            for prefix in backup_prefixes_raw
        ),
        active_extensions=active_extensions,
        ignored_directory_prefixes=tuple(
            str(prefix).strip().casefold()
            for prefix in ignored_prefixes_raw
        ),
        responsible_aliases={
            str(source): str(target)
            for source, target in (
                raw.get("responsible_aliases") or {}
            ).items()
        },
        scheduler=dict(raw.get("scheduler") or {}),
        manual_upward=manual_upward,
        manual_downward=manual_downward,
        manual_ui=manual_ui,
        notifications=dict(raw.get("notifications") or {}),
        safety=safety,
    )


def _parse_fields(raw: dict[str, Any]) -> FieldConfig:
    schema = _require(raw, "schema", "fields.json")
    profiles_raw = _require(raw, "profiles", "fields.json")
    profile_tags = {
        str(name): tuple(str(tag).strip().casefold() for tag in profile["include_type_tags"])
        for name, profile in profiles_raw.items()
    }
    def initial_master_values(item: dict[str, Any]) -> tuple[Any, ...]:
        values = item.get("initial_master_values", [])
        if not isinstance(values, list):
            raise ConfigurationError(
                "initial_master_values в fields.collect должен быть списком"
            )
        return tuple(values)

    collect = tuple(
        FieldRule(
            title=str(item["title"]),
            profiles=tuple(str(value) for value in item["profiles"]),
            empty=str(item.get("empty", "ignore")),
            conflict=str(item.get("conflict", "error")),
            value_type=str(item.get("value_type", "text")),
            master_conflict=str(
                item.get("master_conflict", "require_baseline")
            ),
            invalid=str(item.get("invalid", "error")),
            comparison_type=(
                str(item["comparison_type"])
                if item.get("comparison_type") is not None
                else None
            ),
            initial_master_values=initial_master_values(item),
        )
        for item in _require(raw, "collect", "fields.json")
    )
    known_profiles = set(profile_tags)
    for rule in collect:
        unknown = set(rule.profiles) - known_profiles
        if unknown:
            raise ConfigurationError(
                f"поле {rule.title!r} ссылается на неизвестные profiles: {sorted(unknown)}"
            )
        if rule.empty != "ignore":
            raise ConfigurationError(
                f"пока поддерживается только empty='ignore': {rule.title!r}"
            )
        if rule.conflict != "error":
            raise ConfigurationError(
                f"пока поддерживается только conflict='error': {rule.title!r}"
            )
        if rule.value_type not in {"text", "date", "number", "literal"}:
            raise ConfigurationError(
                f"неизвестный value_type у {rule.title!r}: {rule.value_type}"
            )
        comparison_type = rule.comparison_type or rule.value_type
        if comparison_type not in {"text", "date", "number", "literal"}:
            raise ConfigurationError(
                f"неизвестный comparison_type у {rule.title!r}: "
                f"{comparison_type}"
            )
        if rule.master_conflict not in {"require_baseline", "accept_superset"}:
            raise ConfigurationError(
                f"неизвестный master_conflict у {rule.title!r}: "
                f"{rule.master_conflict}"
            )
        if rule.invalid not in {"error", "preserve"}:
            raise ConfigurationError(
                f"неизвестная политика invalid у {rule.title!r}: "
                f"{rule.invalid}"
            )

    titles = [rule.title.casefold() for rule in collect]
    if len(titles) != len(set(titles)):
        raise ConfigurationError("в fields.collect есть дубли заголовков")
    aliases = {
        str(canonical): tuple(str(alias) for alias in values)
        for canonical, values in (raw.get("aliases") or {}).items()
    }
    collect_titles = {rule.title for rule in collect}
    # Колонки границы (ключ, ответственный, прежний ответственный) в collect не
    # входят — цикл их не собирает вверх, а только читает. Но переименовать их
    # офис может ровно так же, поэтому алиас для них законен. Как и для любой
    # другой колонки реестра: вниз, к сотруднику, цикл переносит весь профиль,
    # и сопоставление колонок master ↔ personal идёт по тем же заголовкам.
    boundary_titles = {
        str(schema.get("key_title") or ""),
        str(schema.get("responsible_title") or ""),
        str(schema.get("previous_responsible_title") or ""),
        "ответственный Сотрудник СОФТ",
    } - {""}
    registry_titles = {field.title for field in _hardbot_fields.FIELDS}
    unknown_alias_targets = (
        set(aliases) - collect_titles - boundary_titles - registry_titles)
    if unknown_alias_targets:
        raise ConfigurationError(
            "aliases ссылаются на поля вне collect и границы: "
            f"{sorted(unknown_alias_targets)}"
        )

    directions = raw.get("directions") or {}
    if not isinstance(directions, dict):
        raise ConfigurationError("directions в fields.json должен быть объектом")
    raw_fill_rgb = directions.get(
        "employee_header_fill_rgb",
        ["#FFFF00"],
    )
    if not isinstance(raw_fill_rgb, list) or not raw_fill_rgb:
        raise ConfigurationError(
            "directions.employee_header_fill_rgb должен быть непустым списком"
        )
    employee_header_fill_rgb = tuple(
        str(value).strip().upper()
        for value in raw_fill_rgb
    )
    if any(
        len(value) != 7
        or not value.startswith("#")
        or any(character not in "0123456789ABCDEF" for character in value[1:])
        for value in employee_header_fill_rgb
    ):
        raise ConfigurationError(
            "directions.employee_header_fill_rgb должен содержать цвета #RRGGBB"
        )
    supported_direction_keys = {
        "employee_header_fill_rgb",
        "strict_header_color_validation",
        "employee_to_master",
        "master_to_employee",
        "preserve_employee_values_for_existing_owner",
        "transfer_employee_values_on_reassignment",
    }
    unknown_direction_keys = set(directions) - supported_direction_keys
    if unknown_direction_keys:
        raise ConfigurationError(
            "неизвестные ключи fields.directions: "
            f"{sorted(unknown_direction_keys)}"
        )
    expected_direction_values: dict[str, str | bool] = {
        "employee_to_master": "collect",
        "master_to_employee": "profile_columns_except_collect",
        "preserve_employee_values_for_existing_owner": True,
        "transfer_employee_values_on_reassignment": True,
    }
    for key, expected in expected_direction_values.items():
        actual = directions.get(key, expected)
        if type(actual) is not type(expected) or actual != expected:
            raise ConfigurationError(
                f"directions.{key} пока поддерживает только {expected!r}"
            )

    raw_rollover = raw.get("month_rollover")
    month_rollover: MonthRolloverConfig | None = None
    if raw_rollover is not None:
        if not isinstance(raw_rollover, dict):
            raise ConfigurationError(
                "month_rollover в fields.json должен быть объектом"
            )
        trigger = str(
            raw_rollover.get("trigger", "last_calendar_day")
        )
        if trigger != "last_calendar_day":
            raise ConfigurationError(
                "month_rollover.trigger пока поддерживает только "
                "'last_calendar_day'"
            )
        clear_titles = tuple(
            str(value)
            for value in raw_rollover.get("employee_clear_titles", [])
        )
        normalized_collect = {title.casefold() for title in collect_titles}
        unknown_clear_titles = [
            title
            for title in clear_titles
            if title.casefold() not in normalized_collect
        ]
        if unknown_clear_titles:
            raise ConfigurationError(
                "month_rollover.employee_clear_titles ссылается на поля "
                f"вне collect: {unknown_clear_titles}"
            )
        history_template = str(
            raw_rollover.get(
                "history_title_template",
                "Оплаты за {month:02d}.{year2:02d}",
            )
        )
        try:
            history_template.format(month=7, year=2026, year2=26)
        except (KeyError, ValueError) as exc:
            raise ConfigurationError(
                "некорректный month_rollover.history_title_template"
            ) from exc
        month_rollover = MonthRolloverConfig(
            enabled=bool(raw_rollover.get("enabled", False)),
            trigger=trigger,
            current_payments_title=str(
                _require(
                    raw_rollover,
                    "current_payments_title",
                    "month_rollover",
                )
            ),
            history_title_template=history_template,
            employee_clear_titles=clear_titles,
        )

    return FieldConfig(
        sheet_name=str(schema["portfolio_sheet"]),
        statistics_sheet=str(schema["statistics_sheet"]),
        status_sheet=str(schema["status_sheet"]),
        key_title=str(schema["key_title"]),
        responsible_title=str(schema["responsible_title"]),
        header_search_rows=int(schema.get("header_search_rows", 10)),
        type_row_offset=int(schema.get("type_row_offset", 1)),
        profile_tags=profile_tags,
        collect=collect,
        preserve_if_master_blank=tuple(
            str(value) for value in raw.get("preserve_if_master_blank", [])
        ),
        aliases=aliases,
        previous_responsible_title=(
            str(schema["previous_responsible_title"])
            if schema.get("previous_responsible_title")
            else None
        ),
        employee_header_fill_rgb=employee_header_fill_rgb,
        strict_header_color_validation=bool(
            directions.get("strict_header_color_validation", False)
        ),
        month_rollover=month_rollover,
    )


def load_config_bundle(config_dir: str | Path | None = None) -> ConfigBundle:
    config_path = (
        Path(config_dir)
        if config_dir is not None
        else Path(__file__).resolve().parent / "config"
    ).resolve()
    project_root = Path(__file__).resolve().parents[1]
    config_names = {
        path.name
        for path in config_path.glob("*.json")
    } | {"app.json", "fields.json", "theme.json"}
    raw_configs = {
        name: _read_json(config_path / name)
        for name in sorted(config_names)
    }
    raw_app = raw_configs["app.json"]
    raw_fields = raw_configs["fields.json"]
    raw_theme = raw_configs["theme.json"]
    config_identity = hashlib.sha256(
        json.dumps(
            raw_configs,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    config_payloads = tuple(
        (
            name,
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        for name, payload in sorted(raw_configs.items())
    )
    app = _parse_app(raw_app)
    fields = _parse_fields(raw_fields)

    boundary_mismatches = []
    if fields.sheet_name != _exchange_contract.WORKBOOK_SHEET_NAME:
        boundary_mismatches.append("portfolio_sheet")
    if fields.header_search_rows != _exchange_contract.WORKBOOK_HEADER_SEARCH_ROWS:
        boundary_mismatches.append("header_search_rows")
    if fields.key_title != _exchange_contract.WORKBOOK_CONTRACT_TITLE:
        boundary_mismatches.append("key_title")
    if fields.responsible_title != _exchange_contract.WORKBOOK_RESPONSIBLE_TITLE:
        boundary_mismatches.append("responsible_title")
    if boundary_mismatches:
        raise ConfigurationError(
            "fields.json расходится с data_exchange_contract: "
            + ", ".join(boundary_mismatches)
        )

    collect_titles = {norm_title(rule.title) for rule in fields.collect}
    boundary_keys = {
        target
        for _source, target, _merge in (
            _exchange_contract.ENRICHMENT_WRITE_RULES
        )
    } | set(_exchange_contract.OFFICE_CALL_FIELDS)
    unknown_boundary_keys = boundary_keys - set(_hardbot_fields.BY_KEY)
    if unknown_boundary_keys:
        raise ConfigurationError(
            "data_exchange_contract ссылается на неизвестные hardbot fields: "
            + ", ".join(sorted(unknown_boundary_keys))
        )
    missing_collect_titles = sorted(
        _hardbot_fields.BY_KEY[key].title
        for key in boundary_keys
        if norm_title(_hardbot_fields.BY_KEY[key].title) not in collect_titles
    )
    if missing_collect_titles:
        raise ConfigurationError(
            "registry collect не содержит поля bot/Excel boundary: "
            + ", ".join(missing_collect_titles)
        )
    for key, aliases in _exchange_contract.WORKBOOK_HEADER_ALIASES.items():
        canonical_title = _hardbot_fields.BY_KEY[key].title
        configured = set(fields.aliases.get(canonical_title, ()))
        missing_aliases = set(aliases) - configured
        if missing_aliases:
            raise ConfigurationError(
                f"registry aliases для {canonical_title!r} расходятся с "
                "data_exchange_contract: "
                + ", ".join(sorted(missing_aliases))
            )

    unknown_group_profiles = {
        group.profile
        for root in app.portfolio_roots
        for group in root.groups
        if group.profile not in fields.profile_tags
    }
    if unknown_group_profiles:
        raise ConfigurationError(
            f"группы ссылаются на неизвестные profiles: {sorted(unknown_group_profiles)}"
        )
    return ConfigBundle(
        app=app,
        fields=fields,
        theme=raw_theme,
        config_dir=config_path,
        project_root=project_root,
        config_identity=config_identity,
        config_payloads=config_payloads,
    )


def save_app_config(
    config_dir: str | Path,
    payload: dict[str, Any],
) -> ConfigBundle:
    """Validate a complete app.json candidate, then replace it atomically.

    The rules editor is intentionally constrained to this function: it never
    writes a live registry and it never leaves half of app.json on disk if a
    new folder/name condition is invalid.
    """
    if not isinstance(payload, dict):
        raise ConfigurationError("новый app.json должен быть объектом")
    directory = Path(config_dir).resolve()
    target = directory / "app.json"
    if not target.is_file():
        raise ConfigurationError(f"нет конфигурации: {target}")
    try:
        serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"app.json нельзя сохранить: {exc}") from exc

    # Validate the candidate together with the exact neighbouring fields/theme
    # files first.  A temporary sibling directory makes this work on Windows
    # as well, without ever swapping an invalid config into place.
    with tempfile.TemporaryDirectory(
        prefix="registry-cycle-config-",
        dir=directory.parent,
    ) as temporary_directory:
        temporary_config = Path(temporary_directory)
        for source in directory.glob("*.json"):
            shutil.copy2(source, temporary_config / source.name)
        (temporary_config / "app.json").write_text(
            serialized,
            encoding="utf-8",
        )
        load_config_bundle(temporary_config)

    replacement = target.with_suffix(".json.tmp")
    replacement.write_text(serialized, encoding="utf-8")
    os.replace(replacement, target)
    return load_config_bundle(directory)
