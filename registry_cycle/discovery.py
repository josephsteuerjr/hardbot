"""Детерминированное обнаружение портфелей по конфигу, без рекурсивного glob."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .config import ConfigBundle, GroupConfig
from .models import DiscoveryReport, Issue, ManifestEntry


_KNOWN_EXCEL_WORKBOOK_EXTENSIONS = frozenset({
    ".xls",
    ".xlsx",
    ".xlsm",
    ".xlsb",
})


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


def _is_active_file(
    path: Path,
    extensions: tuple[str, ...],
    backup_pattern: re.Pattern[str],
) -> bool:
    return bool(
        path.is_file()
        and path.suffix.casefold() in extensions
        and not _ignored_workbook_name(path, backup_pattern)
    )


def _ignored_workbook_name(
    path: Path,
    backup_pattern: re.Pattern[str],
) -> bool:
    if path.name.startswith("~") or backup_pattern.search(path.name):
        return True
    # The configured historical-backup regex may mention the supported .xlsb
    # suffix explicitly. Apply that same naming rule to anomaly formats rather
    # than turning an old .xlsx/.xlsm/.xls backup into a false active error.
    if path.suffix.casefold() in _KNOWN_EXCEL_WORKBOOK_EXTENSIONS:
        canonical_name = path.with_suffix(".xlsb").name
        return bool(backup_pattern.search(canonical_name))
    return False


def _is_unsupported_workbook(
    path: Path,
    bundle: ConfigBundle,
    backup_pattern: re.Pattern[str],
) -> bool:
    suffix = path.suffix.casefold()
    return bool(
        path.is_file()
        and suffix in _KNOWN_EXCEL_WORKBOOK_EXTENSIONS
        and suffix not in bundle.app.active_extensions
        and not _ignored_workbook_name(path, backup_pattern)
    )


def _remember_unreadable(
    unreadable: dict[Path, str] | None,
    folder: Path,
    exc: OSError,
) -> None:
    if unreadable is not None:
        unreadable.setdefault(folder, str(exc))


def _active_files(
    folder: Path,
    extensions: tuple[str, ...],
    backup_pattern: re.Pattern[str],
    unreadable: dict[Path, str] | None = None,
) -> list[Path]:
    files: list[Path] = []
    try:
        children = list(folder.iterdir())
    except OSError as exc:
        _remember_unreadable(unreadable, folder, exc)
        return files
    for path in children:
        if _is_active_file(path, extensions, backup_pattern):
            files.append(path)
    return sorted(files, key=lambda value: value.name.casefold())


def _unsupported_files(
    folder: Path,
    bundle: ConfigBundle,
    backup_pattern: re.Pattern[str],
    unreadable: dict[Path, str] | None = None,
) -> list[Path]:
    try:
        children = list(folder.iterdir())
    except OSError as exc:
        _remember_unreadable(unreadable, folder, exc)
        return []
    return sorted(
        (
            path
            for path in children
            if _is_unsupported_workbook(path, bundle, backup_pattern)
        ),
        key=lambda value: value.name.casefold(),
    )


def _all_active_files(
    folder: Path,
    bundle: ConfigBundle,
    backup_pattern: re.Pattern[str],
    unreadable: dict[Path, str] | None = None,
) -> list[Path]:
    """Audit a subtree without importing anything from unexpected depths."""
    files: list[Path] = []
    pending = [folder]
    while pending:
        current = pending.pop()
        try:
            children = list(current.iterdir())
        except OSError as exc:
            _remember_unreadable(unreadable, current, exc)
            continue
        for path in children:
            if path.is_dir():
                if not _ignored_directory(
                    path.name,
                    bundle.app.ignored_directory_prefixes,
                ):
                    pending.append(path)
                continue
            if _is_active_file(
                path,
                bundle.app.active_extensions,
                backup_pattern,
            ):
                files.append(path)
    return sorted(files, key=lambda value: str(value).casefold())


def _all_unsupported_files(
    folder: Path,
    bundle: ConfigBundle,
    backup_pattern: re.Pattern[str],
    unreadable: dict[Path, str] | None = None,
) -> list[Path]:
    files: list[Path] = []
    pending = [folder]
    while pending:
        current = pending.pop()
        try:
            children = list(current.iterdir())
        except OSError as exc:
            _remember_unreadable(unreadable, current, exc)
            continue
        for path in children:
            if path.is_dir():
                if not _ignored_directory(
                    path.name,
                    bundle.app.ignored_directory_prefixes,
                ):
                    pending.append(path)
                continue
            if _is_unsupported_workbook(path, bundle, backup_pattern):
                files.append(path)
    return sorted(files, key=lambda value: str(value).casefold())


def _nested_active_files(
    employee_folder: Path,
    bundle: ConfigBundle,
    backup_pattern: re.Pattern[str],
    unreadable: dict[Path, str] | None = None,
) -> list[Path]:
    files: list[Path] = []
    try:
        children = list(employee_folder.iterdir())
    except OSError as exc:
        _remember_unreadable(unreadable, employee_folder, exc)
        return files
    for path in children:
        if (
            path.is_dir()
            and not _ignored_directory(
                path.name,
                bundle.app.ignored_directory_prefixes,
            )
        ):
            files.extend(
                _all_active_files(
                    path,
                    bundle,
                    backup_pattern,
                    unreadable,
                )
            )
    return sorted(files, key=lambda value: str(value).casefold())


def _nested_unsupported_files(
    employee_folder: Path,
    bundle: ConfigBundle,
    backup_pattern: re.Pattern[str],
    unreadable: dict[Path, str] | None = None,
) -> list[Path]:
    files: list[Path] = []
    try:
        children = list(employee_folder.iterdir())
    except OSError as exc:
        _remember_unreadable(unreadable, employee_folder, exc)
        return files
    for path in children:
        if (
            path.is_dir()
            and not _ignored_directory(
                path.name,
                bundle.app.ignored_directory_prefixes,
            )
        ):
            files.extend(
                _all_unsupported_files(
                    path,
                    bundle,
                    backup_pattern,
                    unreadable,
                )
            )
    return sorted(files, key=lambda value: str(value).casefold())


def _report_layout_violation(
    report: DiscoveryReport,
    bundle: ConfigBundle,
    path: Path,
    *,
    reason: str,
) -> None:
    report.issues.append(
        Issue(
            "error",
            "unexpected_active_workbook_path",
            "активный реестр находится вне настроенной геометрии папок",
            {
                "path": str(path),
                "reason": reason,
                "expected_layout": bundle.app.portfolio_layout,
            },
        )
    )


def _report_unsupported_workbook(
    report: DiscoveryReport,
    bundle: ConfigBundle,
    path: Path,
) -> None:
    report.issues.append(
        Issue(
            "error",
            "unsupported_active_workbook_format",
            "активная Excel-книга имеет неподдерживаемый формат",
            {
                "path": str(path),
                "extension": path.suffix.casefold(),
                "supported_extensions": list(
                    bundle.app.active_extensions
                ),
            },
        )
    )


def _report_unreadable_directories(
    report: DiscoveryReport,
    unreadable: dict[Path, str],
    employee_folders: tuple[Path, ...] = (),
) -> None:
    employee_paths = set(employee_folders)
    for path, error in sorted(
        unreadable.items(),
        key=lambda item: str(item[0]).casefold(),
    ):
        is_employee_folder = path in employee_paths
        report.issues.append(
            Issue(
                "error",
                (
                    "employee_folder_unreadable"
                    if is_employee_folder
                    else "portfolio_subtree_unreadable"
                ),
                (
                    "не прочитана папка сотрудника"
                    if is_employee_folder
                    else "не прочитана ветка портфелей"
                ),
                {"path": str(path), "error": error},
            )
        )


def _employee_audit_incomplete(
    employee_folder: Path,
    unreadable: dict[Path, str],
) -> bool:
    return any(
        path == employee_folder or employee_folder in path.parents
        for path in unreadable
    )


def _group_has_active_files(
    group: Path,
    bundle: ConfigBundle,
    backup_pattern: re.Pattern[str],
    unreadable: dict[Path, str] | None = None,
) -> bool:
    return bool(
        _all_active_files(
            group,
            bundle,
            backup_pattern,
            unreadable,
        )
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

    try:
        group_children = list(group_path.iterdir())
        employee_folders = sorted(
            (
                path
                for path in group_children
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

    unreadable: dict[Path, str] = {}
    for path in group_children:
        if _is_active_file(
            path,
            bundle.app.active_extensions,
            backup_pattern,
        ):
            _report_layout_violation(
                report,
                bundle,
                path,
                reason="active_file_directly_in_group",
            )
        elif _is_unsupported_workbook(path, bundle, backup_pattern):
            _report_unsupported_workbook(report, bundle, path)
    for employee_folder in employee_folders:
        for path in _unsupported_files(
            employee_folder,
            bundle,
            backup_pattern,
            unreadable,
        ):
            _report_unsupported_workbook(report, bundle, path)
        for path in _nested_active_files(
            employee_folder,
            bundle,
            backup_pattern,
            unreadable,
        ):
            _report_layout_violation(
                report,
                bundle,
                path,
                reason="active_file_below_employee",
            )
        for path in _nested_unsupported_files(
            employee_folder,
            bundle,
            backup_pattern,
            unreadable,
        ):
            _report_unsupported_workbook(report, bundle, path)

    if not group.enabled:
        if _group_has_active_files(
            group_path,
            bundle,
            backup_pattern,
            unreadable,
        ):
            report.issues.append(
                Issue(
                    "warning",
                    "disabled_group_has_files",
                    f"в отключённой группе есть активные файлы: {label}",
                    {"path": str(group_path)},
                )
            )
        _report_unreadable_directories(
            report,
            unreadable,
            tuple(employee_folders),
        )
        return

    empty_folders = 0
    for employee_folder in employee_folders:
        active = _active_files(
            employee_folder,
            bundle.app.active_extensions,
            backup_pattern,
            unreadable,
        )
        if _employee_audit_incomplete(employee_folder, unreadable):
            continue
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
    _report_unreadable_directories(
        report,
        unreadable,
        tuple(employee_folders),
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
        for path in _active_files(
            root,
            bundle.app.active_extensions,
            backup_pattern,
        ):
            _report_layout_violation(
                report,
                bundle,
                path,
                reason="active_file_directly_in_root",
            )
        for path in _unsupported_files(root, bundle, backup_pattern):
            _report_unsupported_workbook(report, bundle, path)
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
            unreadable: dict[Path, str] = {}
            active_files = _all_active_files(
                group_path,
                bundle,
                backup_pattern,
                unreadable,
            )
            unsupported_files = _all_unsupported_files(
                group_path,
                bundle,
                backup_pattern,
                unreadable,
            )
            _report_unreadable_directories(report, unreadable)
            for path in unsupported_files:
                _report_unsupported_workbook(report, bundle, path)
            if active_files:
                report.issues.append(
                    Issue(
                        "error",
                        "unconfigured_group_has_files",
                        f"неизвестная группа содержит активные файлы: {label}",
                        {
                            "path": str(group_path),
                            "files": [str(path) for path in active_files],
                            "expected_layout": bundle.app.portfolio_layout,
                        },
                    )
                )
            elif not unsupported_files and not unreadable:
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
