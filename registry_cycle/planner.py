"""Чистое ядро построения поклеточного плана."""

from __future__ import annotations

from collections import defaultdict
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


def _schema_titles(
    snapshot: WorkbookSnapshot,
    fields: FieldConfig,
) -> tuple[str, ...]:
    return tuple(
        _canonical_title(fields, value)
        for value in snapshot.headers
        if norm_title(value)
    )


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


def build_sync_plan(
    master: WorkbookSnapshot,
    personal_books: list[tuple[ManifestEntry, WorkbookSnapshot]],
    fields: FieldConfig,
    baseline: CycleBaseline | None = None,
) -> SyncPlan:
    """Собрать изменения мастера и ожидаемые личные портфели.

    Функция не пишет на диск, не запускает Excel и детерминирована целиком
    входными снимками.
    """
    plan = SyncPlan(master.path, master.fingerprint)
    key_col = _column_index(master, fields, fields.key_title)
    responsible_col = _column_index(master, fields, fields.responsible_title)
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

    master_rows_by_key = master.rows_by_key(fields.key_title)
    master_title_map = {
        norm_title(title): _column_index(master, fields, title)
        for title in [rule.title for rule in fields.collect]
    }
    book_responsibles: dict[str, str] = {}
    source_values: dict[tuple[str, str], list[SourceCell]] = defaultdict(list)
    invalid_source_values: list[dict[str, Any]] = []
    source_owner: dict[tuple[str, str], str] = {}
    seen_projection_targets: dict[tuple[str, str], Path] = {}

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
        if len(responsibles) != 1:
            plan.issues.append(
                Issue(
                    "error",
                    "personal_responsible_not_unique",
                    f"в личном файле должен быть ровно один ответственный: {entry.path.name}",
                    {"responsibles": list(responsibles), "path": str(entry.path)},
                )
            )
            continue
        responsible = responsibles[0]
        responsible_norm = norm_name(responsible)
        book_responsibles[entry.entry_id] = responsible

        target_key = (entry.profile, responsible_norm)
        if target_key in seen_projection_targets:
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
        else:
            seen_projection_targets[target_key] = entry.path

        if norm_name(entry.responsible_hint) != responsible_norm:
            plan.issues.append(
                Issue(
                    "warning",
                    "filename_responsible_mismatch",
                    f"имя файла не совпадает с ответственным: {entry.path.name}",
                    {"responsible": responsible, "hint": entry.responsible_hint},
                )
            )

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
                        "error",
                        "collect_column_missing",
                        f"нет собираемой колонки «{rule.title}»: {entry.path.name}",
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
            if norm_name(row_responsible) != responsible_norm:
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
                    invalid_source_values.append(
                        {
                            "path": str(entry.path),
                            "row": book.data_start + offset + 1,
                            "contract_id": contract_id,
                            "title": rule.title,
                            "value": str(value),
                            "expected_type": rule.value_type,
                        }
                    )
                    continue
                source_key = (contract_id, norm_title(rule.title))
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
                source_values[source_key].append(
                    SourceCell(
                        entry=entry,
                        responsible=responsible,
                        row=book.data_start + offset,
                        value=value,
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

    changes_by_cell: dict[tuple[int, int], Any] = {}
    missing_contracts: dict[tuple[str, str], dict[str, Any]] = {}
    initial_conflicts: list[dict[str, Any]] = []
    baseline_conflicts: list[dict[str, Any]] = []
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
            if norm_name(target_responsible) != norm_name(source.responsible):
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

        if baseline_found:
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
                if is_blank(before) and not is_blank(baseline_value):
                    conflicts.append(
                        {
                            "row": row_number + 1,
                            "kind": "master_deletion",
                            "baseline": baseline_value,
                            "master": before,
                        }
                    )
                    continue
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
                    conflicts.append(
                        {
                            "row": row_number + 1,
                            "kind": "both_changed",
                            "baseline": baseline_value,
                            "master": before,
                        }
                    )
                    continue
                desired_by_row[row_number] = (
                    after
                    if personal_changed and not master_changed
                    else before
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

    preserve_titles = {norm_title(title): title for title in fields.preserve_if_master_blank}
    for entry, book in personal_books:
        responsible = book_responsibles.get(entry.entry_id)
        if not responsible:
            continue
        expected_columns = _expected_profile_columns(master, fields, entry.profile)
        expected_titles = tuple(title for title, _index in expected_columns)
        actual_raw_titles = tuple(
            str(value or "").strip()
            for value in book.headers
            if norm_title(value)
        )
        actual_titles = _schema_titles(book, fields)
        if tuple(map(norm_title, actual_titles)) != tuple(map(norm_title, expected_titles)):
            actual_set = set(map(norm_title, actual_titles))
            expected_set = set(map(norm_title, expected_titles))
            plan.issues.append(
                Issue(
                    "error",
                    "personal_schema_drift",
                    f"схема личного файла отличается от профиля {entry.profile}: {entry.path.name}",
                    {
                        "missing": [
                            title for title in expected_titles if norm_title(title) not in actual_set
                        ],
                        "extra": [
                            title for title in actual_titles if norm_title(title) not in expected_set
                        ],
                        "order_changed": actual_set == expected_set,
                    },
                )
            )
            continue

        target_master_rows = [
            (row_number, row)
            for row_number, row in enumerate(master.rows, start=master.data_start)
            if norm_name(master.row_value(row, responsible_col)) == norm_name(responsible)
        ]
        if not target_master_rows:
            plan.issues.append(
                Issue(
                    "error",
                    "empty_personal_projection",
                    f"в общем реестре нет строк ответственного {responsible}",
                    {"path": str(entry.path), "profile": entry.profile},
                )
            )
            continue

        old_by_key = book.rows_by_key(fields.key_title)
        old_title_map = book.title_map()
        projection_rows: list[tuple[Any, ...]] = []
        preserved_cells = 0

        for master_row_number, master_row in target_master_rows:
            row_values = []
            for _title, master_column in expected_columns:
                row_values.append(
                    changes_by_cell.get(
                        (master_row_number, master_column),
                        master.row_value(master_row, master_column),
                    )
                )
            contract_id = norm_key(master.row_value(master_row, key_col))
            old_rows = old_by_key.get(contract_id, [])
            for normalized_title, display_title in preserve_titles.items():
                output_column = next(
                    (
                        index
                        for index, title in enumerate(expected_titles)
                        if norm_title(title) == normalized_title
                    ),
                    None,
                )
                old_column = old_title_map.get(normalized_title)
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
            projection_rows.append(tuple(row_values))

        plan.projections.append(
            PersonalProjection(
                entry=entry,
                responsible=responsible,
                headers=actual_raw_titles,
                rows=tuple(projection_rows),
                source_fingerprint=book.fingerprint,
                preserved_cells=preserved_cells,
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
