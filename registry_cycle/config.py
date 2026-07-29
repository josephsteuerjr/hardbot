"""Загрузка и строгая проверка JSON-конфигурации."""

from __future__ import annotations

import hashlib
import json
import os
import sys
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
_HARDBOT_DIR = Path(__file__).resolve().parents[1] / "hardbot"
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
class OwnerDelegation:
    country: str
    group: str
    book_owner: str
    delegate_folder: str
    enabled: bool
    reason: str


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
    backup_name_regex: str
    active_extensions: tuple[str, ...]
    ignored_directory_prefixes: tuple[str, ...]
    profile_rules: tuple[dict[str, str], ...]
    responsible_aliases: dict[str, str]
    ownership_delegations: tuple[OwnerDelegation, ...]
    scheduler: dict[str, Any]
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

    month_names = {int(key): str(value) for key, value in calendar["month_names"].items()}
    if set(month_names) != set(range(1, 13)):
        raise ConfigurationError("calendar.month_names должен содержать месяцы 1..12")

    roots: list[PortfolioRoot] = []
    for index, item in enumerate(_require(paths, "portfolio_roots", "app.json")):
        groups = tuple(
            GroupConfig(
                name=str(group["name"]),
                profile=str(group["profile"]),
                enabled=bool(group.get("enabled", True)),
                required=bool(group.get("required", True)),
            )
            for group in item.get("groups", [])
        )
        if not groups:
            raise ConfigurationError(f"paths.portfolio_roots[{index}] не содержит groups")
        roots.append(
            PortfolioRoot(
                country=str(item["country"]),
                path=str(item["path"]),
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

    return AppConfig(
        schema_version=version,
        product_name=str(product["name"]),
        environment_name=str(product["environment"]),
        master_pattern=str(paths["master_pattern"]),
        workspace=str(paths["workspace"]),
        month_names=month_names,
        portfolio_roots=tuple(roots),
        portfolio_layout=portfolio_layout,
        backup_name_regex=str(discovery["backup_name_regex"]),
        active_extensions=active_extensions,
        ignored_directory_prefixes=tuple(
            str(prefix).casefold() for prefix in discovery["ignored_directory_prefixes"]
        ),
        profile_rules=tuple(
            {
                "filename_regex": str(rule["filename_regex"]),
                "profile": str(rule["profile"]),
            }
            for rule in discovery.get("profile_rules", [])
        ),
        responsible_aliases={
            str(source): str(target)
            for source, target in (
                raw.get("responsible_aliases") or {}
            ).items()
        },
        ownership_delegations=tuple(
            OwnerDelegation(
                country=str(item["country"]),
                group=str(item["group"]),
                book_owner=str(item["book_owner"]),
                delegate_folder=str(item["delegate_folder"]),
                enabled=bool(item.get("enabled", True)),
                reason=str(item.get("reason", "")),
            )
            for item in raw.get("ownership_delegations", [])
        ),
        scheduler=dict(raw.get("scheduler") or {}),
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
    unknown_alias_targets = set(aliases) - collect_titles
    if unknown_alias_targets:
        raise ConfigurationError(
            "aliases ссылаются на поля вне collect: "
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
    unknown_rule_profiles = {
        rule["profile"]
        for rule in app.profile_rules
        if rule["profile"] not in fields.profile_tags
    }
    if unknown_rule_profiles:
        raise ConfigurationError(
            "profile_rules ссылаются на неизвестные profiles: "
            f"{sorted(unknown_rule_profiles)}"
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
