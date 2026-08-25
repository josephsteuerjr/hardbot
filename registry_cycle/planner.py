"""Чистое ядро построения поклеточного плана."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .baseline import CycleBaseline
from .config import FieldConfig, FieldRule
from .models import (
    CellChange,
    Issue,
    ManifestEntry,
    PersonalProjection,
    SyncPlan,
    WorkbookSnapshot,
)
from .normalize import (
    comparable_value,
    is_blank,
    norm_key,
    norm_name,
    norm_title,
    parse_date,
)
from .ownership import (
    canonical_responsible_name,
    resolve_book_responsible,
    workbook_data_is_empty,
)
from .rollover import build_month_rollover_plan
from .semantics import (
    comparable_for_rule,
    is_text_superset,
    values_equal_for_rule,
    write_value_for_rule,
)


@dataclass(frozen=True, slots=True)
class SourceCell:
    entry: ManifestEntry
    responsible: str
    row: int
    value: Any
    handoff: bool = False


@dataclass(frozen=True, slots=True)
class ConflictResolution:
    """One explicit operator decision for a contested master cell.

    The expected values make a decision single-use in practice: if either
    side changes before the next preparation, the rule stops applying and the
    operator sees a new conflict instead of silently reusing an old choice.
    """

    contract_id: str
    title: str
    master_value: Any
    employee_value: Any
    winner: str
    reason: str = ""


def _rules_for_profile(fields: FieldConfig, profile: str) -> tuple[FieldRule, ...]:
    return tuple(rule for rule in fields.collect if profile in rule.profiles)


def _title_variants(fields: FieldConfig, title: str) -> set[str]:
    return {
        norm_title(title),
        *(norm_title(alias) for alias in fields.aliases.get(title, ())),
    }


def _canonical_title(fields: FieldConfig, value: Any) -> str:
    normalized = norm_title(value)
    for canonical, aliases in fields.aliases.items():
        if normalized in {norm_title(alias) for alias in aliases}:
            return canonical
    return str(value or "").strip()


def _column_index(
    snapshot: WorkbookSnapshot,
    fields: FieldConfig,
    title: str,
) -> int | None:
    variants = _title_variants(fields, title)
    found = [
        index
        for index, value in enumerate(snapshot.headers)
        if norm_title(value) in variants
    ]
    return found[0] if len(found) == 1 else None


def _schema_key(fields: FieldConfig, value: Any) -> str:
    """Canonical title identity used only for column-to-column mapping."""
    return norm_title(_canonical_title(fields, value))


def _expected_profile_columns(
    master: WorkbookSnapshot,
    fields: FieldConfig,
    profile: str,
) -> tuple[tuple[str, int], ...]:
    tags = set(fields.profile_tags[profile])
    selected = []
    for index, title in enumerate(master.headers):
        tag = str(master.type_tags[index] if index < len(master.type_tags) else "").strip().casefold()
        if norm_title(title) and tag in tags:
            selected.append((str(title).strip(), index))
    return tuple(selected)


def _distinct_nonblank(
    values: list[Any],
    rule: FieldRule | None = None,
) -> dict[tuple[str, Any], Any]:
    out: dict[tuple[str, Any], Any] = {}
    for value in values:
        if not is_blank(value):
            comparable = (
                comparable_for_rule(value, rule)
                if rule is not None
                else comparable_value(value)
            )
            out.setdefault(comparable, value)
    return out


def _is_initial_master_value(value: Any, rule: FieldRule) -> bool:
    return any(
        values_equal_for_rule(value, allowed, rule)
        for allowed in rule.initial_master_values
    )


def _conflict_resolution_map(
    resolutions: tuple[ConflictResolution, ...],
) -> dict[tuple[str, str], ConflictResolution]:
    """Validate and index explicit per-cell conflict decisions."""
    indexed: dict[tuple[str, str], ConflictResolution] = {}
    for resolution in resolutions:
        key = (
            norm_key(resolution.contract_id),
            norm_title(resolution.title),
        )
        if not all(key):
            raise ValueError("разрешение конфликта должно содержать КД и поле")
        if resolution.winner not in {"employee", "master"}:
            raise ValueError(
                "winner разрешения конфликта должен быть 'employee' или 'master'"
            )
        if key in indexed:
            raise ValueError(
                "для одного КД и поля нельзя задать два разрешения конфликта"
            )
        indexed[key] = resolution
    return indexed


def build_sync_plan(
    master: WorkbookSnapshot,
    personal_books: list[tuple[ManifestEntry, WorkbookSnapshot]],
    fields: FieldConfig,
    baseline: CycleBaseline | None = None,
    multi_owner_policy: str = "block",
    responsible_aliases: dict[str, str] | None = None,
    rollover_month: str | None = None,
    config_identity: str = "",
    include_projections: bool = True,
    conflict_resolutions: tuple[ConflictResolution, ...] = (),
    master_conflict_policy: str = "configured",
) -> SyncPlan:
    """Собрать изменения мастера и ожидаемые личные портфели.

    Функция не пишет на диск, не запускает Excel и детерминирована целиком
    входными снимками.
    """
    if multi_owner_policy not in {"block", "master_responsible"}:
        raise ValueError(
            "multi_owner_policy должен быть 'block' или 'master_responsible'"
        )
    if master_conflict_policy not in {"configured", "employee_wins"}:
        raise ValueError(
            "master_conflict_policy должен быть 'configured' или 'employee_wins'"
        )
    owner_norm = lambda value: canonical_responsible_name(  # noqa: E731
        value,
        responsible_aliases,
    )
    resolution_by_key = _conflict_resolution_map(conflict_resolutions)
    seen_resolution_keys: set[tuple[str, str]] = set()
    resolved_conflicts: list[dict[str, Any]] = []
    stale_resolutions: list[dict[str, Any]] = []
    plan = SyncPlan(
        master.path,
        master.fingerprint,
        config_identity=config_identity,
    )
    rollover, rollover_issues = build_month_rollover_plan(
        master,
        fields,
        rollover_month,
    )
    plan.month_rollover = rollover
    plan.issues.extend(rollover_issues)
    key_col = _column_index(master, fields, fields.key_title)
    responsible_col = _column_index(master, fields, fields.responsible_title)
    previous_responsible_col = (
        _column_index(
            master,
            fields,
            fields.previous_responsible_title,
        )
        if fields.previous_responsible_title
        else None
    )
    if key_col is None or responsible_col is None:
        plan.issues.append(
            Issue(
                "error",
                "master_schema_missing_key",
                "в общем реестре нет ключа или ответственного",
                {"path": str(master.path)},
            )
        )
        return plan
    if (
        fields.previous_responsible_title
        and previous_responsible_col is None
    ):
        plan.issues.append(
            Issue(
                "error",
                "master_schema_missing_previous_responsible",
                "в общем реестре нет колонки предыдущего ответственного",
                {
                    "path": str(master.path),
                    "title": fields.previous_responsible_title,
                },
            )
        )
        return plan

    master_rows_by_key = master.rows_by_key(fields.key_title)
    master_title_map = {
        norm_title(title): _column_index(master, fields, title)
        for title in [rule.title for rule in fields.collect]
    }
    book_responsibles: dict[str, str] = {}
    source_values: dict[tuple[str, str], list[SourceCell]] = defaultdict(list)
    handoff_values: dict[tuple[str, str], list[SourceCell]] = defaultdict(list)
    invalid_source_values: list[dict[str, Any]] = []
    source_owner: dict[tuple[str, str], str] = {}
    seen_projection_targets: dict[tuple[str, str], Path] = {}
    seen_responsible_profiles: dict[str, tuple[str, Path]] = {}
    case_sources: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    read_only_sources: dict[tuple[str, str], dict[str, Any]] = {}
    removed_sources: dict[tuple[str, str], dict[str, Any]] = {}
    orphan_sources: dict[tuple[str, str], dict[str, Any]] = {}
    unassigned_master_sources: dict[tuple[str, str], dict[str, Any]] = {}
    ambiguous_master_cases: dict[str, dict[str, Any]] = {}
    tolerated_source_values: list[dict[str, Any]] = []

    for entry, book in personal_books:
        if entry.profile not in fields.profile_tags:
            plan.issues.append(
                Issue(
                    "error",
                    "unknown_profile",
                    f"неизвестный профиль {entry.profile}",
                    {"path": str(entry.path)},
                )
            )
            continue
        responsibles = book.unique_responsibles(fields.responsible_title)
        responsible = resolve_book_responsible(
            book,
            fields.responsible_title,
        )
        if responsible is None:
            if not responsibles and workbook_data_is_empty(book):
                plan.issues.append(
                    Issue(
                        "info",
                        "empty_personal_without_responsible_skipped",
                        f"пустой личный файл пропущен: {entry.path.name}",
                        {"path": str(entry.path)},
                    )
                )
                continue
            plan.issues.append(
                Issue(
                    "error",
                    "personal_responsible_not_unique",
                    f"в личном файле должен быть ровно один ответственный: {entry.path.name}",
                    {"responsibles": list(responsibles), "path": str(entry.path)},
                )
            )
            continue
        responsible_norm = owner_norm(responsible)

        previous_profile = seen_responsible_profiles.get(responsible_norm)
        if (
            previous_profile is not None
            and previous_profile[0] != entry.profile
        ):
            plan.issues.append(
                Issue(
                    "error",
                    "responsible_in_multiple_active_profiles",
                    "один ответственный найден в нескольких активных профилях",
                    {
                        "responsible": responsible,
                        "responsible_normalized": responsible_norm,
                        "first_profile": previous_profile[0],
                        "first_path": str(previous_profile[1]),
                        "second_profile": entry.profile,
                        "second_path": str(entry.path),
                    },
                )
            )
            continue
        seen_responsible_profiles.setdefault(
            responsible_norm,
            (entry.profile, entry.path),
        )

        target_key = (entry.profile, responsible_norm)
        if target_key in seen_projection_targets:
            if include_projections:
                plan.issues.append(
                    Issue(
                        "error",
                        "duplicate_projection_target",
                        f"два файла претендуют на один портфель: {responsible}",
                        {
                            "first": str(seen_projection_targets[target_key]),
                            "second": str(entry.path),
                            "profile": entry.profile,
                        },
                    )
                )
                continue
        else:
            seen_projection_targets[target_key] = entry.path
        book_responsibles[entry.entry_id] = responsible

        personal_key_col = _column_index(book, fields, fields.key_title)
        personal_responsible_col = _column_index(
            book,
            fields,
            fields.responsible_title,
        )
        if personal_key_col is None or personal_responsible_col is None:
            plan.issues.append(
                Issue(
                    "error",
                    "personal_schema_missing_key",
                    f"в личном файле нет ключа или ответственного: {entry.path.name}",
                    {"path": str(entry.path)},
                )
            )
            continue

        rules = _rules_for_profile(fields, entry.profile)
        rule_columns: list[tuple[FieldRule, int]] = []
        for rule in rules:
            personal_column = _column_index(book, fields, rule.title)
            if personal_column is None:
                plan.issues.append(
                    Issue(
                        "warning",
                        "collect_column_missing",
                        f"колонка «{rule.title}» отсутствует и пропущена: {entry.path.name}",
                        {"profile": entry.profile, "path": str(entry.path)},
                    )
                )
                continue
            if master_title_map.get(norm_title(rule.title)) is None:
                plan.issues.append(
                    Issue(
                        "error",
                        "master_collect_column_missing",
                        f"в общем реестре нет собираемой колонки «{rule.title}»",
                        {"path": str(master.path)},
                    )
                )
                continue
            rule_columns.append((rule, personal_column))

        for offset, row in enumerate(book.rows):
            contract_id = norm_key(book.row_value(row, personal_key_col))
            if not contract_id:
                continue
            row_responsible = book.row_value(row, personal_responsible_col)
            if owner_norm(row_responsible) != responsible_norm:
                plan.issues.append(
                    Issue(
                        "error",
                        "foreign_row_in_personal_file",
                        f"чужой или пустой ответственный в {entry.path.name}",
                        {
                            "contract_id": contract_id,
                            "row": book.data_start + offset + 1,
                            "row_responsible": row_responsible,
                            "expected": responsible,
                        },
                    )
                )
                continue
            owner_case = case_sources[contract_id].setdefault(
                responsible_norm,
                {
                    "responsible": responsible,
                    "paths": set(),
                },
            )
            owner_case["paths"].add(str(entry.path))

            source_is_handoff = False
            source_target_responsible = responsible
            if multi_owner_policy == "master_responsible":
                target_rows = master_rows_by_key.get(contract_id, [])
                if not target_rows:
                    orphan_sources.setdefault(
                        (contract_id, str(entry.path)),
                        {
                            "contract_id": contract_id,
                            "responsible": responsible,
                            "path": str(entry.path),
                        },
                    )
                    continue
                master_owners: dict[str, str] = {}
                previous_owners: dict[str, str] = {}
                blank_owner = False
                for _master_row_number, master_row in target_rows:
                    raw_owner = master.row_value(master_row, responsible_col)
                    normalized_owner = owner_norm(raw_owner)
                    if normalized_owner:
                        master_owners.setdefault(
                            normalized_owner,
                            str(raw_owner).strip(),
                        )
                    else:
                        blank_owner = True
                    if previous_responsible_col is not None:
                        raw_previous = master.row_value(
                            master_row,
                            previous_responsible_col,
                        )
                        normalized_previous = owner_norm(raw_previous)
                        if normalized_previous:
                            previous_owners.setdefault(
                                normalized_previous,
                                str(raw_previous).strip(),
                            )
                removal_proved = set(previous_owners) == {
                    responsible_norm
                }
                if not master_owners and blank_owner:
                    target = (
                        removed_sources
                        if removal_proved
                        else unassigned_master_sources
                    )
                    target.setdefault(
                        (contract_id, str(entry.path)),
                        {
                            "contract_id": contract_id,
                            "copy_owner": responsible,
                            "path": str(entry.path),
                            "reason": (
                                "unassigned_to_previous_responsible"
                                if removal_proved
                                else "unassigned_without_removal_proof"
                            ),
                            "previous_responsibles": list(
                                previous_owners.values()
                            ),
                            "master_rows": [
                                row_number + 1
                                for row_number, _master_row in target_rows
                            ],
                        },
                    )
                    continue
                if len(master_owners) != 1 or blank_owner:
                    ambiguous_master_cases.setdefault(
                        contract_id,
                        {
                            "contract_id": contract_id,
                            "master_owners": list(master_owners.values()),
                            "has_blank_owner": blank_owner,
                            "master_rows": [
                                row_number + 1
                                for row_number, _master_row in target_rows
                            ],
                        },
                    )
                    continue
                canonical_owner = next(iter(master_owners))
                if canonical_owner != responsible_norm:
                    target = (
                        removed_sources
                        if removal_proved
                        else read_only_sources
                    )
                    target.setdefault(
                        (contract_id, str(entry.path)),
                        {
                            "contract_id": contract_id,
                            "copy_owner": responsible,
                            "master_owner": master_owners[canonical_owner],
                            "path": str(entry.path),
                            "reason": (
                                "reassigned_from_previous_responsible"
                                if removal_proved
                                else "noncanonical_without_removal_proof"
                            ),
                            "previous_responsibles": list(
                                previous_owners.values()
                            ),
                        },
                    )
                    if removal_proved:
                        if include_projections:
                            source_is_handoff = True
                            source_target_responsible = master_owners[
                                canonical_owner
                            ]
                        else:
                            # Manual upward transfer accepts values only from
                            # the current master owner.  ``пред отв`` remains
                            # useful to remove/hand off rows during the
                            # separate downward projection, but cannot make a
                            # former owner's values eligible for collection.
                            continue
                    else:
                        continue
            for rule, column in rule_columns:
                value = book.row_value(row, column)
                if is_blank(value):
                    continue
                invalid = (
                    rule.value_type == "date"
                    and parse_date(value) is None
                ) or (
                    rule.value_type == "number"
                    and comparable_for_rule(value, rule)[0] != "number"
                )
                if invalid:
                    record = {
                        "path": str(entry.path),
                        "row": book.data_start + offset + 1,
                        "contract_id": contract_id,
                        "title": rule.title,
                        "value": str(value),
                        "expected_type": rule.value_type,
                    }
                    if rule.invalid == "preserve":
                        tolerated_source_values.append(record)
                    else:
                        invalid_source_values.append(record)
                        continue
                source_key = (contract_id, norm_title(rule.title))
                source_cell = SourceCell(
                    entry=entry,
                    responsible=source_target_responsible,
                    row=book.data_start + offset,
                    value=value,
                    handoff=source_is_handoff,
                )
                if source_is_handoff:
                    handoff_values[source_key].append(source_cell)
                    continue
                previous_owner = source_owner.setdefault(source_key, responsible_norm)
                if previous_owner != responsible_norm:
                    plan.issues.append(
                        Issue(
                            "error",
                            "cross_owner_source_conflict",
                            f"один КД и поле пришли от разных ответственных: {contract_id}",
                            {"title": rule.title, "path": str(entry.path)},
                        )
                    )
                    continue
                source_values[source_key].append(source_cell)

    handoff_projection_values: dict[tuple[str, str], Any] = {}
    handoff_conflicts: list[dict[str, Any]] = []
    handoff_used = 0
    handoff_shadowed = 0
    rules_by_normalized_title = {
        norm_title(rule.title): rule
        for rule in fields.collect
    }
    for source_key, sources in sorted(handoff_values.items()):
        contract_id, normalized_title = source_key
        rule = rules_by_normalized_title[normalized_title]
        distinct = _distinct_nonblank(
            [source.value for source in sources],
            rule,
        )
        if len(distinct) > 1:
            handoff_conflicts.append(
                {
                    "contract_id": contract_id,
                    "title": rule.title,
                    "values": [str(value) for value in distinct.values()],
                    "sources": [
                        str(source.entry.path)
                        for source in sources
                    ],
                }
            )
            continue
        if not distinct:
            continue
        selected = next(iter(distinct.values()))
        handoff_projection_values[source_key] = selected
        if source_values.get(source_key):
            handoff_shadowed += 1
            continue
        handoff_used += 1
    if handoff_conflicts:
        plan.issues.append(
            Issue(
                "error",
                "reassignment_handoff_conflict",
                "у доказанных прежних владельцев различаются передаваемые "
                "жёлтые значения",
                {
                    "count": len(handoff_conflicts),
                    "samples": handoff_conflicts[:100],
                },
            )
        )
    if handoff_used or handoff_shadowed:
        plan.issues.append(
            Issue(
                "info",
                "employee_owned_values_handed_off",
                "жёлтые данные доказанных прежних владельцев включены "
                "в перенос новому владельцу",
                {
                    "available_for_projection": handoff_used,
                    "kept_current_owner_value": handoff_shadowed,
                },
            )
        )

    if invalid_source_values:
        invalid_by_field: dict[str, int] = defaultdict(int)
        for item in invalid_source_values:
            invalid_by_field[item["title"]] += 1
        plan.issues.append(
            Issue(
                "error",
                "invalid_source_value_type",
                "в личных файлах есть значения неправильного типа: "
                f"{len(invalid_source_values)}",
                {
                    "count": len(invalid_source_values),
                    "by_field": dict(sorted(invalid_by_field.items())),
                    "samples": invalid_source_values[:50],
                },
            )
        )
    if tolerated_source_values:
        tolerated_by_field: dict[str, int] = defaultdict(int)
        for item in tolerated_source_values:
            tolerated_by_field[item["title"]] += 1
        plan.issues.append(
            Issue(
                "warning",
                "noncanonical_source_value_preserved",
                "исторические значения не совпадают с типом колонки, "
                "но будут перенесены буквально",
                {
                    "count": len(tolerated_source_values),
                    "by_field": dict(
                        sorted(tolerated_by_field.items())
                    ),
                    "samples": tolerated_source_values[:100],
                },
            )
        )

    duplicate_cases = {
        contract_id: owners
        for contract_id, owners in case_sources.items()
        if len(owners) > 1
    }
    if duplicate_cases:
        samples = []
        for contract_id, owners in sorted(duplicate_cases.items())[:100]:
            samples.append(
                {
                    "contract_id": contract_id,
                    "owners": [
                        {
                            "responsible": item["responsible"],
                            "paths": sorted(item["paths"]),
                        }
                        for item in owners.values()
                    ],
                }
            )
        plan.issues.append(
            Issue(
                "warning",
                "case_in_multiple_personal_books",
                "одни КД найдены в портфелях нескольких сотрудников; "
                "канон определяется ответственным в общем реестре",
                {
                    "count": len(duplicate_cases),
                    "policy": multi_owner_policy,
                    "samples": samples,
                },
            )
        )
    if ambiguous_master_cases:
        plan.issues.append(
            Issue(
                "error",
                "master_case_owner_ambiguous",
                "в общем реестре один КД имеет разных или пустых ответственных",
                {
                    "count": len(ambiguous_master_cases),
                    "samples": list(ambiguous_master_cases.values())[:100],
                },
            )
        )
    if read_only_sources:
        plan.issues.append(
            Issue(
                "warning",
                "noncanonical_personal_rows_read_only",
                "строки у прежних владельцев сохранены без сбора и удаления",
                {
                    "count": len(read_only_sources),
                    "policy": multi_owner_policy,
                    "samples": list(read_only_sources.values())[:100],
                },
            )
        )
    if removed_sources:
        plan.issues.append(
            Issue(
                "info",
                "personal_rows_removed_by_previous_responsible",
                "строки снятых или переданных КД будут удалены из книги "
                "доказанного предыдущего ответственного",
                {
                    "count": len(removed_sources),
                    "policy": multi_owner_policy,
                    "samples": list(removed_sources.values())[:100],
                },
            )
        )
    if orphan_sources:
        plan.issues.append(
            Issue(
                "warning",
                "personal_rows_missing_in_master_read_only",
                "строки без КД в общем реестре сохранены только в личных книгах",
                {
                    "count": len(orphan_sources),
                    "policy": multi_owner_policy,
                    "samples": list(orphan_sources.values())[:100],
                },
            )
        )
    if unassigned_master_sources:
        plan.issues.append(
            Issue(
                "warning",
                "unassigned_master_rows_read_only",
                "КД с пустым ответственным в общем сохранены в личных "
                "книгах без сбора и автоназначения",
                {
                    "count": len(unassigned_master_sources),
                    "policy": multi_owner_policy,
                    "samples": list(
                        unassigned_master_sources.values()
                    )[:100],
                },
            )
        )

    changes_by_cell: dict[tuple[int, int], Any] = {}
    missing_contracts: dict[tuple[str, str], dict[str, Any]] = {}
    initial_conflicts: list[dict[str, Any]] = []
    baseline_conflicts: list[dict[str, Any]] = []
    employee_authority_overrides: list[dict[str, Any]] = []
    for (contract_id, normalized_title), sources in sorted(source_values.items()):
        title = next(
            rule.title
            for rule in fields.collect
            if norm_title(rule.title) == normalized_title
        )
        rule = next(
            rule
            for rule in fields.collect
            if norm_title(rule.title) == normalized_title
        )
        distinct = _distinct_nonblank([source.value for source in sources], rule)
        if len(distinct) > 1:
            plan.issues.append(
                Issue(
                    "error",
                    "source_value_conflict",
                    f"разные значения одного поля для КД {contract_id}",
                    {
                        "title": title,
                        "values": [str(value) for value in distinct.values()],
                        "sources": [str(source.entry.path) for source in sources],
                    },
                )
            )
            continue
        source = sources[0]
        target_rows = master_rows_by_key.get(contract_id)
        if not target_rows:
            missing = missing_contracts.setdefault(
                (contract_id, str(source.entry.path)),
                {
                    "contract_id": contract_id,
                    "source": str(source.entry.path),
                    "titles": [],
                },
            )
            missing["titles"].append(title)
            continue
        mismatched = []
        for row_number, row in target_rows:
            target_responsible = master.row_value(row, responsible_col)
            if owner_norm(target_responsible) != owner_norm(source.responsible):
                mismatched.append(
                    {
                        "row": row_number + 1,
                        "responsible": target_responsible,
                    }
                )
        if mismatched:
            plan.issues.append(
                Issue(
                    "error",
                    "responsible_mismatch",
                    f"ответственный в мастере не совпал для КД {contract_id}",
                    {
                        "source_responsible": source.responsible,
                        "master_rows": mismatched,
                        "source": str(source.entry.path),
                    },
                )
            )
            continue

        target_column = master_title_map[normalized_title]
        assert target_column is not None
        after = write_value_for_rule(next(iter(distinct.values())), rule)
        baseline_found, baseline_value = (
            baseline.lookup(contract_id, title)
            if baseline is not None
            else (False, None)
        )
        desired_by_row: dict[int, Any] = {}
        conflicts: list[dict[str, Any]] = []
        field_resolutions: list[dict[str, Any]] = []

        def configured_resolution(
            before: Any,
            *,
            row_number: int,
            kind: str,
            baseline_value: Any = None,
        ) -> tuple[bool, Any]:
            resolution_key = (contract_id, normalized_title)
            resolution = resolution_by_key.get(resolution_key)
            if resolution is None:
                return False, None
            seen_resolution_keys.add(resolution_key)
            if not (
                values_equal_for_rule(before, resolution.master_value, rule)
                and values_equal_for_rule(
                    after,
                    resolution.employee_value,
                    rule,
                )
            ):
                stale_resolutions.append(
                    {
                        "contract_id": contract_id,
                        "title": title,
                        "row": row_number + 1,
                        "kind": kind,
                        "expected_master": resolution.master_value,
                        "actual_master": before,
                        "expected_employee": resolution.employee_value,
                        "actual_employee": after,
                        "source": str(source.entry.path),
                    }
                )
                return False, None
            desired = after if resolution.winner == "employee" else before
            field_resolutions.append(
                {
                    "contract_id": contract_id,
                    "title": title,
                    "row": row_number + 1,
                    "kind": kind,
                    "winner": resolution.winner,
                    "master": before,
                    "employee": after,
                    "reason": resolution.reason,
                    "source": str(source.entry.path),
                    "baseline": baseline_value,
                }
            )
            return True, desired

        if master_conflict_policy == "employee_wins":
            personal_changed = (
                baseline_found
                and not values_equal_for_rule(after, baseline_value, rule)
            )
            for row_number, row in target_rows:
                before = master.row_value(row, target_column)
                desired_by_row[row_number] = after
                if values_equal_for_rule(before, after, rule):
                    continue
                override_kind: str | None = None
                if baseline_found:
                    master_changed = not values_equal_for_rule(
                        before,
                        baseline_value,
                        rule,
                    )
                    if master_changed:
                        override_kind = (
                            "both_changed"
                            if personal_changed
                            else "master_only"
                        )
                elif (
                    not is_blank(before)
                    and not _is_initial_master_value(before, rule)
                    and not (
                        rule.master_conflict == "accept_superset"
                        and is_text_superset(after, before)
                    )
                ):
                    override_kind = "no_baseline"
                if override_kind is not None:
                    employee_authority_overrides.append(
                        {
                            "contract_id": contract_id,
                            "title": title,
                            "row": row_number + 1,
                            "kind": override_kind,
                            "baseline": (
                                baseline_value if baseline_found else None
                            ),
                            "master": before,
                            "employee": after,
                            "source": str(source.entry.path),
                            "policy": "employee_wins",
                        }
                    )
        elif baseline_found:
            personal_changed = not values_equal_for_rule(
                after,
                baseline_value,
                rule,
            )
            for row_number, row in target_rows:
                before = master.row_value(row, target_column)
                master_changed = not values_equal_for_rule(
                    before,
                    baseline_value,
                    rule,
                )
                if (
                    personal_changed
                    and master_changed
                    and not values_equal_for_rule(before, after, rule)
                ):
                    if (
                        rule.master_conflict == "accept_superset"
                        and is_text_superset(after, before)
                    ):
                        desired_by_row[row_number] = after
                        continue
                    if (
                        rule.master_conflict == "accept_superset"
                        and is_text_superset(before, after)
                    ):
                        desired_by_row[row_number] = before
                        continue
                    resolved, desired = configured_resolution(
                        before,
                        row_number=row_number,
                        kind="both_changed",
                        baseline_value=baseline_value,
                    )
                    if resolved:
                        desired_by_row[row_number] = desired
                        continue
                    conflicts.append(
                        {
                            "row": row_number + 1,
                            "kind": "both_changed",
                            "baseline": baseline_value,
                            "master": before,
                        }
                    )
                    continue
                desired_by_row[row_number] = after
                if (
                    master_changed
                    and not personal_changed
                    and not values_equal_for_rule(before, after, rule)
                ):
                    employee_authority_overrides.append(
                        {
                            "contract_id": contract_id,
                            "title": title,
                            "row": row_number + 1,
                            "master": before,
                            "employee": after,
                            "source": str(source.entry.path),
                        }
                    )
            if conflicts:
                baseline_conflicts.append(
                    {
                        "contract_id": contract_id,
                        "title": title,
                        "source": str(source.entry.path),
                        "source_value": after,
                        "conflicts": conflicts,
                    }
                )
                continue
        else:
            for row_number, row in target_rows:
                before = master.row_value(row, target_column)
                if (
                    is_blank(before)
                    or _is_initial_master_value(before, rule)
                    or values_equal_for_rule(before, after, rule)
                    or (
                        rule.master_conflict == "accept_superset"
                        and is_text_superset(after, before)
                    )
                ):
                    desired_by_row[row_number] = after
                    continue
                if (
                    rule.master_conflict == "accept_superset"
                    and is_text_superset(before, after)
                ):
                    desired_by_row[row_number] = before
                    continue
                resolved, desired = configured_resolution(
                    before,
                    row_number=row_number,
                    kind="no_baseline",
                )
                if resolved:
                    desired_by_row[row_number] = desired
                    continue
                conflicts.append(
                    {
                        "row": row_number + 1,
                        "before": before,
                    }
                )
            if conflicts:
                initial_conflicts.append(
                    {
                        "contract_id": contract_id,
                        "title": title,
                        "source": str(source.entry.path),
                        "source_value": after,
                        "master": conflicts,
                    }
                )
                continue

        resolved_conflicts.extend(field_resolutions)

        for row_number, row in target_rows:
            before = master.row_value(row, target_column)
            desired = desired_by_row[row_number]
            if values_equal_for_rule(before, desired, rule):
                continue
            changes_by_cell[(row_number, target_column)] = desired
            plan.changes.append(
                CellChange(
                    target="master",
                    path=master.path,
                    sheet=master.sheet,
                    row=row_number,
                    column=target_column,
                    title=title,
                    contract_id=contract_id,
                    before=before,
                    after=desired,
                    source_path=source.entry.path,
                    source_row=source.row,
                )
            )

    if missing_contracts:
        samples = list(missing_contracts.values())
        plan.issues.append(
            Issue(
                "error",
                "contracts_missing_in_master",
                f"КД из личных файлов отсутствуют в общем реестре: {len(samples)}",
                {"count": len(samples), "contracts": samples},
            )
        )
    if initial_conflicts:
        by_field: dict[str, int] = defaultdict(int)
        for conflict in initial_conflicts:
            by_field[conflict["title"]] += 1
        plan.issues.append(
            Issue(
                "error",
                "initial_master_conflicts",
                "мастер и личные файлы содержат разные непустые значения без baseline",
                {
                    "count": len(initial_conflicts),
                    "by_field": dict(sorted(by_field.items())),
                    "samples": initial_conflicts[:50],
                },
            )
        )
    if baseline_conflicts:
        by_field: dict[str, int] = defaultdict(int)
        by_kind: dict[str, int] = defaultdict(int)
        for conflict in baseline_conflicts:
            by_field[conflict["title"]] += 1
            for item in conflict["conflicts"]:
                by_kind[item["kind"]] += 1
        plan.issues.append(
            Issue(
                "error",
                "baseline_merge_conflicts",
                "мастер и личные файлы изменили одни данные после baseline",
                {
                    "count": len(baseline_conflicts),
                    "by_field": dict(sorted(by_field.items())),
                    "by_kind": dict(sorted(by_kind.items())),
                    "samples": baseline_conflicts[:50],
                },
            )
        )
    if resolved_conflicts:
        plan.issues.append(
            Issue(
                "info",
                "configured_conflict_resolutions_applied",
                "конфликты разрешены явными решениями из конфигурации",
                {
                    "count": len(resolved_conflicts),
                    "resolutions": resolved_conflicts[:100],
                },
            )
        )
    if stale_resolutions:
        plan.issues.append(
            Issue(
                "error",
                "configured_conflict_resolution_stale",
                "настроенное решение не совпало с текущими значениями; "
                "нужно новое подтверждение",
                {
                    "count": len(stale_resolutions),
                    "resolutions": stale_resolutions[:100],
                },
            )
        )
    unused_resolution_keys = sorted(
        set(resolution_by_key) - seen_resolution_keys
    )
    if unused_resolution_keys:
        plan.issues.append(
            Issue(
                "warning",
                "configured_conflict_resolution_unused",
                "настроенное решение не потребовалось в этом запуске",
                {
                    "count": len(unused_resolution_keys),
                    "resolutions": [
                        {
                            "contract_id": contract_id,
                            "title": title,
                        }
                        for contract_id, title in unused_resolution_keys
                    ],
                },
            )
        )
    if employee_authority_overrides:
        plan.issues.append(
            Issue(
                "warning",
                "employee_owned_master_edits_overridden",
                "при расхождении с общим реестром применён приоритет "
                "личного реестра",
                {
                    "count": len(employee_authority_overrides),
                    "samples": employee_authority_overrides[:100],
                },
            )
        )

    if not include_projections:
        plan.changes.sort(
            key=lambda change: (
                str(change.path).casefold(),
                change.row,
                change.column,
            )
        )
        return plan

    preserve_titles = {
        norm_title(title): title
        for title in fields.preserve_if_master_blank
    }
    employee_rules = {
        norm_title(rule.title): rule
        for rule in fields.collect
    }
    rollover_clear_titles = {
        norm_title(title)
        for title in (
            plan.month_rollover.employee_clear_titles
            if plan.month_rollover is not None
            else ()
        )
    }
    for entry, book in personal_books:
        responsible = book_responsibles.get(entry.entry_id)
        if not responsible:
            continue
        target_master_rows = [
            (row_number, row)
            for row_number, row in enumerate(
                master.rows,
                start=master.data_start,
            )
            if owner_norm(master.row_value(row, responsible_col))
            == owner_norm(responsible)
        ]
        expected_columns = _expected_profile_columns(master, fields, entry.profile)
        expected_by_key: dict[str, tuple[str, int]] = {}
        duplicate_expected: list[str] = []
        for title, master_column in expected_columns:
            key = _schema_key(fields, title)
            if key in expected_by_key:
                duplicate_expected.append(title)
            else:
                expected_by_key[key] = (title, master_column)
        if duplicate_expected:
            plan.issues.append(
                Issue(
                    "error",
                    "master_profile_schema_duplicate_title",
                    f"в профиле {entry.profile} общего реестра неоднозначные заголовки",
                    {
                        "path": str(master.path),
                        "titles": duplicate_expected,
                    },
                )
            )
            continue

        blank_header_columns = [
            index + 1
            for index, value in enumerate(book.headers)
            if not norm_title(value)
        ]
        if blank_header_columns:
            plan.issues.append(
                Issue(
                    "warning",
                    "personal_unnamed_columns_preserved",
                    f"безымянные столбцы будут сохранены только в этом личном файле: {entry.path.name}",
                    {
                        "path": str(entry.path),
                        "columns": blank_header_columns,
                    },
                )
            )

        actual_columns: list[tuple[str, int, str]] = []
        actual_by_key: dict[str, tuple[str, int]] = {}
        duplicate_actual: list[str] = []
        for personal_column, value in enumerate(book.headers):
            # Keep the existing header byte-for-byte from Calamine. Matching
            # ignores cosmetic whitespace, but a downward run must not rewrite
            # the employee's established header text as a side effect.
            raw_title = str(value or "")
            key = _schema_key(fields, raw_title)
            if not key:
                actual_columns.append((raw_title, personal_column, key))
                continue
            if key in actual_by_key:
                duplicate_actual.append(raw_title)
            else:
                actual_by_key[key] = (raw_title, personal_column)
            actual_columns.append((raw_title, personal_column, key))
        if duplicate_actual:
            plan.issues.append(
                Issue(
                    "error",
                    "personal_schema_duplicate_alias",
                    f"в личном файле два заголовка означают одно поле: {entry.path.name}",
                    {
                        "path": str(entry.path),
                        "titles": duplicate_actual,
                    },
                )
            )
            continue

        extra_titles = [
            raw_title
            for raw_title, _personal_column, key in actual_columns
            if key and key not in expected_by_key
        ]
        if extra_titles:
            missing_titles = [
                title
                for key, (title, _master_column) in expected_by_key.items()
                if key not in actual_by_key
            ]
            plan.issues.append(
                Issue(
                    "error",
                    "personal_schema_drift",
                    f"в личном файле есть столбцы вне профиля {entry.profile}: {entry.path.name}",
                    {
                        "missing": missing_titles,
                        "extra": extra_titles,
                        "order_changed": False,
                    },
                )
            )
            continue

        # Existing columns stay exactly where the employee has them. Missing
        # profile columns are appended in master order. Folder and file names,
        # as well as cosmetic header aliases, never participate in the mapping.
        output_columns: list[tuple[str, int | None, int | None]] = [
            (
                raw_title,
                expected_by_key[key][1] if key else None,
                personal_column,
            )
            for raw_title, personal_column, key in actual_columns
        ]
        added_columns: list[str] = []
        for title, master_column in expected_columns:
            key = _schema_key(fields, title)
            if key in actual_by_key or not target_master_rows:
                continue
            output_columns.append((title, master_column, None))
            added_columns.append(title)
        output_titles = tuple(title for title, _master, _old in output_columns)
        output_type_tags = tuple(
            (
                book.type_tags[personal_column]
                if personal_column is not None
                and personal_column < len(book.type_tags)
                else master.type_tags[master_column]
                if master_column is not None
                and master_column < len(master.type_tags)
                else None
            )
            for _title, master_column, personal_column in output_columns
        )
        if added_columns:
            plan.issues.append(
                Issue(
                    "info",
                    "personal_schema_columns_will_be_added",
                    f"в личный файл будут добавлены столбцы из общего реестра: {entry.path.name}",
                    {
                        "path": str(entry.path),
                        "titles": added_columns,
                    },
                )
            )
        projection_key_col = _column_index(
            book,
            fields,
            fields.key_title,
        )
        if projection_key_col is None:
            plan.issues.append(
                Issue(
                    "error",
                    "personal_schema_missing_key",
                    f"в личном файле нет ключа: {entry.path.name}",
                    {"path": str(entry.path)},
                )
            )
            continue

        if (
            not target_master_rows
            and not workbook_data_is_empty(book)
            and multi_owner_policy != "master_responsible"
        ):
            plan.issues.append(
                Issue(
                    "error",
                    "empty_personal_projection",
                    f"в общем реестре нет строк ответственного {responsible}",
                    {"path": str(entry.path), "profile": entry.profile},
                )
            )
            continue
        if not target_master_rows and workbook_data_is_empty(book):
            plan.issues.append(
                Issue(
                    "info",
                    "empty_personal_projection",
                    f"новый пустой портфель пока не имеет строк: {responsible}",
                    {"path": str(entry.path), "profile": entry.profile},
                )
            )
        elif not target_master_rows:
            plan.issues.append(
                Issue(
                    "warning",
                    "read_only_personal_projection",
                    f"у {responsible} нет текущих строк в общем; "
                    "личные строки сохранены",
                    {"path": str(entry.path), "profile": entry.profile},
                )
            )

        old_by_key = book.rows_by_key(fields.key_title)
        projection_rows: list[tuple[Any, ...]] = []
        preserved_cells = 0
        employee_owned_preserved_cells = 0
        handoff_cells = 0
        rollover_cleared_cells = 0
        retained_rows = 0
        removed_rows = 0
        employee_output_columns: list[
            tuple[str, FieldRule, int, int | None]
        ] = []
        for normalized_title, rule in employee_rules.items():
            output_column = next(
                (
                    index
                    for index, title in enumerate(output_titles)
                    if norm_title(title)
                    in _title_variants(fields, rule.title)
                ),
                None,
            )
            if output_column is None:
                continue
            employee_output_columns.append(
                (
                    normalized_title,
                    rule,
                    output_column,
                    _column_index(book, fields, rule.title),
                )
            )
        rollover_output_columns = {
            output_column
            for normalized_title, _rule, output_column, _old_column
            in employee_output_columns
            if normalized_title in rollover_clear_titles
        }
        personal_only_output_columns = tuple(
            (output_column, old_column)
            for output_column, (
                _title,
                master_column,
                old_column,
            ) in enumerate(output_columns)
            if master_column is None and old_column is not None
        )
        responsible_output_column = next(
            (
                index
                for index, title in enumerate(output_titles)
                if norm_title(title)
                in _title_variants(fields, fields.responsible_title)
            ),
            None,
        )
        if responsible_output_column is None:
            plan.issues.append(
                Issue(
                    "error",
                    "projection_responsible_column_missing",
                    f"в профиле нет ответственного: {entry.path.name}",
                    {"profile": entry.profile},
                )
            )
            continue

        for master_row_number, master_row in target_master_rows:
            row_values = []
            for _title, master_column, _old_column in output_columns:
                row_values.append(
                    (
                        changes_by_cell.get(
                            (master_row_number, master_column),
                            master.row_value(master_row, master_column),
                        )
                        if master_column is not None
                        else None
                    )
                )
            row_values[responsible_output_column] = responsible
            contract_id = norm_key(master.row_value(master_row, key_col))
            old_rows = old_by_key.get(contract_id, [])
            for (
                normalized_title,
                rule,
                output_column,
                old_column,
            ) in employee_output_columns:
                handoff_key = (contract_id, normalized_title)
                handoff_value = handoff_projection_values.get(handoff_key)
                if not old_rows:
                    if (
                        handoff_key in handoff_projection_values
                        and not is_blank(row_values[output_column])
                    ):
                        handoff_cells += 1
                    continue
                if old_column is None:
                    continue
                old_values = [
                    book.row_value(old_row, old_column)
                    for _old_row_number, old_row in old_rows
                ]
                distinct = _distinct_nonblank(old_values, rule)
                if len(distinct) > 1:
                    plan.issues.append(
                        Issue(
                            "error",
                            "employee_owned_projection_conflict",
                            "в текущем портфеле различаются жёлтые значения "
                            f"«{rule.title}» у КД {contract_id}",
                            {
                                "path": str(entry.path),
                                "values": [
                                    str(value)
                                    for value in distinct.values()
                                ],
                            },
                        )
                    )
                    continue
                if distinct:
                    row_values[output_column] = next(
                        iter(distinct.values())
                    )
                    employee_owned_preserved_cells += 1
                elif handoff_key in handoff_projection_values:
                    row_values[output_column] = handoff_value
                    handoff_cells += 1
                else:
                    # Пустота в жёлтом поле — тоже состояние сотрудника.
                    # Общий реестр не имеет права заполнить его обратно вниз.
                    row_values[output_column] = None
            for normalized_title, display_title in preserve_titles.items():
                output_column = next(
                    (
                        index
                        for index, title in enumerate(output_titles)
                        if _schema_key(fields, title) == normalized_title
                    ),
                    None,
                )
                old_column = _column_index(book, fields, display_title)
                if output_column is None or old_column is None:
                    continue
                if not is_blank(row_values[output_column]):
                    continue
                old_values = [
                    book.row_value(old_row, old_column)
                    for _old_row_number, old_row in old_rows
                ]
                distinct = _distinct_nonblank(old_values)
                if len(distinct) > 1:
                    plan.issues.append(
                        Issue(
                            "error",
                            "passthrough_value_conflict",
                            f"разные сохранённые значения «{display_title}» у КД {contract_id}",
                            {
                                "path": str(entry.path),
                                "values": [str(value) for value in distinct.values()],
                            },
                        )
                    )
                    continue
                if distinct:
                    row_values[output_column] = next(iter(distinct.values()))
                    preserved_cells += 1
            for output_column, old_column in personal_only_output_columns:
                old_values = [
                    book.row_value(old_row, old_column)
                    for _old_row_number, old_row in old_rows
                ]
                distinct = _distinct_nonblank(old_values)
                if len(distinct) > 1:
                    plan.issues.append(
                        Issue(
                            "error",
                            "personal_only_value_conflict",
                            "разные значения безымянного личного столбца "
                            f"у КД {contract_id}",
                            {
                                "path": str(entry.path),
                                "column": output_column + 1,
                                "values": [
                                    str(value)
                                    for value in distinct.values()
                                ],
                            },
                        )
                    )
                    continue
                if distinct:
                    row_values[output_column] = next(
                        iter(distinct.values())
                    )
            for output_column in rollover_output_columns:
                if not is_blank(row_values[output_column]):
                    rollover_cleared_cells += 1
                row_values[output_column] = None
            projection_rows.append(tuple(row_values))

        if multi_owner_policy == "master_responsible":
            projected_counts = Counter(
                norm_key(master.row_value(master_row, key_col))
                for _master_row_number, master_row in target_master_rows
            )
            old_seen: Counter[str] = Counter()
            for old_row in book.rows:
                contract_id = norm_key(
                    book.row_value(old_row, projection_key_col)
                )
                if contract_id:
                    if (
                        contract_id,
                        str(entry.path),
                    ) in removed_sources:
                        removed_rows += 1
                        continue
                    old_seen[contract_id] += 1
                    if old_seen[contract_id] <= projected_counts[contract_id]:
                        continue
                elif all(is_blank(value) for value in old_row):
                    continue
                retained_values = [
                    (
                        book.row_value(old_row, old_column)
                        if old_column is not None
                        else None
                    )
                    for _title, _master_column, old_column in output_columns
                ]
                for output_column in rollover_output_columns:
                    if not is_blank(retained_values[output_column]):
                        rollover_cleared_cells += 1
                    retained_values[output_column] = None
                projection_rows.append(tuple(retained_values))
                retained_rows += 1

        plan.projections.append(
            PersonalProjection(
                entry=entry,
                responsible=responsible,
                headers=output_titles,
                rows=tuple(projection_rows),
                source_fingerprint=book.fingerprint,
                type_tags=output_type_tags,
                added_columns=tuple(added_columns),
                preserved_cells=preserved_cells,
                employee_owned_preserved_cells=(
                    employee_owned_preserved_cells
                ),
                handoff_cells=handoff_cells,
                rollover_cleared_cells=rollover_cleared_cells,
                retained_rows=retained_rows,
                removed_rows=removed_rows,
            )
        )

    plan.changes.sort(
        key=lambda change: (
            str(change.path).casefold(),
            change.row,
            change.column,
        )
    )
    plan.projections.sort(key=lambda projection: str(projection.entry.path).casefold())
    return plan
