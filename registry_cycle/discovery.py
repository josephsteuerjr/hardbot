"""Детерминированное обнаружение портфелей по конфигу, без рекурсивного glob."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .config import ConfigBundle, GroupConfig
from .models import DiscoveryReport, Issue, ManifestEntry


def _entry_id(path: Path) -> str:
    return hashlib.sha256(str(path).casefold().encode("utf-8")).hexdigest()[:16]


def _profile_for(path: Path, group: GroupConfig, bundle: ConfigBundle) -> str:
    profile = group.profile
    for rule in bundle.app.profile_rules:
        if re.search(rule["filename_regex"], path.name, flags=re.IGNORECASE):
            profile = rule["profile"]
            break
    return profile


def _ignored_directory(name: str, prefixes: tuple[str, ...]) -> bool:
    folded = name.strip().casefold()
    return any(folded.startswith(prefix) for prefix in prefixes)


def _active_files(
    folder: Path,
    extensions: tuple[str, ...],
    backup_pattern: re.Pattern[str],
) -> list[Path]:
    files: list[Path] = []
    try:
        children = list(folder.iterdir())
    except OSError:
        return files
    for path in children:
        if not path.is_file() or path.suffix.casefold() not in extensions:
            continue
        if path.name.startswith("~") or backup_pattern.search(path.name):
            continue
        files.append(path)
    return sorted(files, key=lambda value: value.name.casefold())


def _group_has_active_files(
    group: Path,
    bundle: ConfigBundle,
    backup_pattern: re.Pattern[str],
) -> bool:
    try:
        employee_folders = [path for path in group.iterdir() if path.is_dir()]
    except OSError:
        return False
    return any(
        _active_files(folder, bundle.app.active_extensions, backup_pattern)
        for folder in employee_folders
    )


def _scan_group(
    root: Path,
    country: str,
    group: GroupConfig,
    bundle: ConfigBundle,
    backup_pattern: re.Pattern[str],
    report: DiscoveryReport,
) -> None:
    group_path = root / group.name
    label = f"{country} / {group.name}"
    report.configured_groups.append(label)
    if not group_path.is_dir():
        severity = "error" if group.enabled and group.required else "warning"
        report.issues.append(
            Issue(
                severity,
                "configured_group_missing",
                f"нет настроенной группы: {label}",
                {"path": str(group_path), "enabled": group.enabled},
            )
        )
        return
    if not group.enabled:
        if _group_has_active_files(group_path, bundle, backup_pattern):
            report.issues.append(
                Issue(
                    "warning",
                    "disabled_group_has_files",
                    f"в отключённой группе есть активные файлы: {label}",
                    {"path": str(group_path)},
                )
            )
        return

    empty_folders = 0
    try:
        employee_folders = sorted(
            (
                path
                for path in group_path.iterdir()
                if path.is_dir()
                and not _ignored_directory(
                    path.name,
                    bundle.app.ignored_directory_prefixes,
                )
            ),
            key=lambda value: value.name.casefold(),
        )
    except OSError as exc:
        report.issues.append(
            Issue(
                "error",
                "group_unreadable",
                f"не прочитана группа: {label}",
                {"path": str(group_path), "error": str(exc)},
            )
        )
        return

    for employee_folder in employee_folders:
        active = _active_files(
            employee_folder,
            bundle.app.active_extensions,
            backup_pattern,
        )
        if not active:
            empty_folders += 1
            continue
        if len(active) > 1:
            severity = (
                "error"
                if bundle.app.safety.get(
                    "block_multiple_active_files_per_folder",
                    False,
                )
                else "warning"
            )
            report.issues.append(
                Issue(
                    severity,
                    "multiple_active_files",
                    f"в одной папке несколько активных реестров: {employee_folder.name}",
                    {"files": [str(path) for path in active], "profile": group.profile},
                )
            )
        for path in active:
            profile = _profile_for(path, group, bundle)
            report.entries.append(
                ManifestEntry(
                    entry_id=_entry_id(path),
                    path=path,
                    country=country,
                    group=group.name,
                    profile=profile,
                    employee_folder=employee_folder.name,
                    responsible_hint=path.stem,
                )
            )
    if empty_folders:
        report.issues.append(
            Issue(
                "info",
                "empty_employee_folders",
                f"папки без активного реестра: {label} — {empty_folders}",
                {"count": empty_folders, "path": str(group_path)},
            )
        )


def discover(bundle: ConfigBundle) -> DiscoveryReport:
    """Построить runtime-manifest и вынести любое отклонение в явный отчёт."""
    report = DiscoveryReport()
    backup_pattern = re.compile(bundle.app.backup_name_regex, re.IGNORECASE)

    for root_config in bundle.app.portfolio_roots:
        root = Path(bundle.render(root_config.path))
        if not root.is_dir():
            report.issues.append(
                Issue(
                    "error",
                    "portfolio_root_missing",
                    f"корень портфелей недоступен: {root_config.country}",
                    {"path": str(root)},
                )
            )
            continue
        configured = {group.name.casefold(): group for group in root_config.groups}
        try:
            discovered = sorted(
                (
                    path
                    for path in root.iterdir()
                    if path.is_dir()
                    and not _ignored_directory(
                        path.name,
                        bundle.app.ignored_directory_prefixes,
                    )
                ),
                key=lambda value: value.name.casefold(),
            )
        except OSError as exc:
            report.issues.append(
                Issue(
                    "error",
                    "portfolio_root_unreadable",
                    f"корень портфелей не прочитан: {root_config.country}",
                    {"path": str(root), "error": str(exc)},
                )
            )
            continue

        for group_path in discovered:
            label = f"{root_config.country} / {group_path.name}"
            report.discovered_groups.append(label)
            if group_path.name.casefold() in configured:
                continue
            if _group_has_active_files(group_path, bundle, backup_pattern):
                severity = (
                    "error"
                    if bundle.app.safety.get(
                        "block_unconfigured_groups_with_files",
                        True,
                    )
                    else "warning"
                )
                report.issues.append(
                    Issue(
                        severity,
                        "unconfigured_group_has_files",
                        f"неизвестная группа содержит активные файлы: {label}",
                        {"path": str(group_path)},
                    )
                )
            else:
                report.issues.append(
                    Issue(
                        "info",
                        "unconfigured_empty_group",
                        f"неизвестная пустая группа: {label}",
                        {"path": str(group_path)},
                    )
                )

        for group in root_config.groups:
            _scan_group(
                root,
                root_config.country,
                group,
                bundle,
                backup_pattern,
                report,
            )

    report.entries.sort(key=lambda entry: str(entry.path).casefold())
    return report
