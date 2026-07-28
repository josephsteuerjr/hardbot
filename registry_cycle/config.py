"""Загрузка и строгая проверка JSON-конфигурации."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class FieldRule:
    title: str
    profiles: tuple[str, ...]
    empty: str = "ignore"
    conflict: str = "error"
    value_type: str = "text"
    master_conflict: str = "require_baseline"


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
    backup_name_regex: str
    active_extensions: tuple[str, ...]
    ignored_directory_prefixes: tuple[str, ...]
    profile_rules: tuple[dict[str, str], ...]
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
    if version != 1:
        raise ConfigurationError(f"неподдерживаемая schema_version={version}")
    product = _require(raw, "product", "app.json")
    paths = _require(raw, "paths", "app.json")
    discovery = _require(raw, "discovery", "app.json")
    calendar = _require(raw, "calendar", "app.json")

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

    return AppConfig(
        schema_version=version,
        product_name=str(product["name"]),
        environment_name=str(product["environment"]),
        master_pattern=str(paths["master_pattern"]),
        workspace=str(paths["workspace"]),
        month_names=month_names,
        portfolio_roots=tuple(roots),
        backup_name_regex=str(discovery["backup_name_regex"]),
        active_extensions=tuple(str(ext).casefold() for ext in discovery["active_extensions"]),
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
        safety=dict(raw.get("safety") or {}),
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
        if rule.value_type not in {"text", "date", "number"}:
            raise ConfigurationError(
                f"неизвестный value_type у {rule.title!r}: {rule.value_type}"
            )
        if rule.master_conflict not in {"require_baseline", "accept_superset"}:
            raise ConfigurationError(
                f"неизвестный master_conflict у {rule.title!r}: "
                f"{rule.master_conflict}"
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
    )


def load_config_bundle(config_dir: str | Path | None = None) -> ConfigBundle:
    config_path = (
        Path(config_dir)
        if config_dir is not None
        else Path(__file__).resolve().parent / "config"
    ).resolve()
    project_root = Path(__file__).resolve().parents[1]
    raw_app = _read_json(config_path / "app.json")
    raw_fields = _read_json(config_path / "fields.json")
    raw_theme = _read_json(config_path / "theme.json")
    app = _parse_app(raw_app)
    fields = _parse_fields(raw_fields)

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
    )
