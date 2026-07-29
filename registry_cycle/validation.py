"""Проверка staging-набора до любой публикации."""

from __future__ import annotations

import copy
import json
import secrets
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from .config import ConfigBundle
from .excel_host import (
    ExcelHost,
    sheet_by_name,
    sheet_filter_signature,
    workbook_untouched_signatures,
)
from .files import fingerprint, stable_copy
from .models import Issue, PersonalProjection, SyncPlan, WorkbookSnapshot
from .normalize import comparable_value, is_blank, json_value, norm_title
from .reader import read_portfolio
from .semantics import same_representation, values_equal_for_rule
from .staging import (
    ProgressCallback,
    StageSet,
    _excel_datetime_serial,
    _is_general_number_format,
)


@dataclass(slots=True)
class ValidationReport:
    run_id: str
    plan_digest: str
    checked_files: int = 0
    checked_master_cells: int = 0
    checked_projection_cells: int = 0
    issues: list[Issue] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "plan_digest": self.plan_digest,
            "checked_files": self.checked_files,
            "checked_master_cells": self.checked_master_cells,
            "checked_projection_cells": self.checked_projection_cells,
            "issues": [issue.as_dict() for issue in self.issues],
            "blocked": self.blocked,
        }


def _atomic_text(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _same_value(left: Any, right: Any) -> bool:
    return comparable_value(left) == comparable_value(right)


def _master_change_value_matches(
    actual: Any,
    expected: Any,
    rule,
) -> bool:
    if values_equal_for_rule(actual, expected, rule):
        return (
            rule.value_type != "date"
            or same_representation(actual, expected)
        )
    if (
        isinstance(expected, (date, datetime))
        and isinstance(actual, (int, float))
        and not isinstance(actual, bool)
        and rule.value_type in {"date", "literal"}
    ):
        expected_serial = (
            _excel_datetime_serial(expected)
            if isinstance(expected, datetime)
            else _excel_datetime_serial(
                datetime.combine(expected, datetime.min.time())
            )
        )
        return abs(float(actual) - float(expected_serial)) < 1e-9
    return False


def _snapshot_cell(
    snapshot: WorkbookSnapshot,
    absolute_row: int,
    column: int,
) -> Any:
    offset = absolute_row - snapshot.data_start
    if offset < 0 or offset >= len(snapshot.rows):
        return None
    return snapshot.row_value(snapshot.rows[offset], column)


def _validate_structure(
    source: WorkbookSnapshot,
    staged: WorkbookSnapshot,
    issues: list[Issue],
    *,
    label: str,
    expected_headers: tuple[Any, ...] | None = None,
    expected_type_tags: tuple[Any, ...] | None = None,
) -> bool:
    okay = True
    if source.sheet_names != staged.sheet_names:
        okay = False
        issues.append(
            Issue(
                "error",
                "sheet_set_changed",
                f"изменился состав или порядок листов: {label}",
                {
                    "before": list(source.sheet_names),
                    "after": list(staged.sheet_names),
                },
            )
        )
    for key, before, after in (
        ("header_row", source.header_row, staged.header_row),
        ("type_row", source.type_row, staged.type_row),
        ("data_start", source.data_start, staged.data_start),
        (
            "headers",
            expected_headers if expected_headers is not None else source.headers,
            staged.headers,
        ),
        (
            "type_tags",
            (
                expected_type_tags
                if expected_type_tags is not None
                else source.type_tags
            ),
            staged.type_tags,
        ),
    ):
        if before != after:
            okay = False
            issues.append(
                Issue(
                    "error",
                    "portfolio_structure_changed",
                    f"изменилась структура «Портфели»: {label}",
                    {"part": key},
                )
            )
    return okay


def _mapped_master_column(plan: SyncPlan, source_column: int) -> int:
    rollover = plan.month_rollover
    if (
        rollover is not None
        and rollover.insert_history_column
        and source_column >= rollover.history_column
    ):
        return source_column + 1
    return source_column


def _expected_master_structure(
    source: WorkbookSnapshot,
    plan: SyncPlan,
) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    rollover = plan.month_rollover
    if rollover is None or not rollover.insert_history_column:
        return source.headers, source.type_tags
    headers = list(source.headers)
    type_tags = list(source.type_tags)
    headers.insert(rollover.history_column, rollover.history_title)
    type_tags.insert(
        rollover.history_column,
        source.type_tags[rollover.current_column],
    )
    return tuple(headers), tuple(type_tags)


def _validate_master(
    bundle: ConfigBundle,
    stage: StageSet,
    plan: SyncPlan,
    report: ValidationReport,
) -> None:
    source = read_portfolio(stage.master.source_copy, bundle.fields)
    staged = read_portfolio(stage.master.staged_path, bundle.fields)
    expected_headers, expected_type_tags = _expected_master_structure(
        source,
        plan,
    )
    if not _validate_structure(
        source,
        staged,
        report.issues,
        label=stage.master.original_path.name,
        expected_headers=expected_headers,
        expected_type_tags=expected_type_tags,
    ):
        return
    rules = {rule.title: rule for rule in bundle.fields.collect}
    allowed = {
        (change.row, change.column): change
        for change in plan.changes
    }
    forbidden: list[dict[str, Any]] = []
    missing_expected: list[dict[str, Any]] = []
    max_rows = max(len(source.rows), len(staged.rows))
    width = len(source.headers)
    rollover = plan.month_rollover
    for offset in range(max_rows):
        absolute_row = source.data_start + offset
        for column in range(width):
            before = _snapshot_cell(source, absolute_row, column)
            staged_column = _mapped_master_column(plan, column)
            after = _snapshot_cell(staged, absolute_row, staged_column)
            report.checked_master_cells += 1
            change = allowed.get((absolute_row, column))
            expected = change.after if change is not None else before
            if rollover is not None:
                if column == rollover.current_column:
                    expected = None
                elif (
                    not rollover.insert_history_column
                    and column == rollover.history_column
                ):
                    current_before = _snapshot_cell(
                        source,
                        absolute_row,
                        rollover.current_column,
                    )
                    if not is_blank(current_before):
                        expected = current_before
            if change is None:
                if not _same_value(expected, after):
                    if len(forbidden) < 100:
                        forbidden.append(
                            {
                                "row": absolute_row + 1,
                                "column": staged_column + 1,
                                "title": source.headers[column],
                                "before": json_value(expected),
                                "after": json_value(after),
                            }
                        )
                continue
            rule = rules[change.title]
            if not _master_change_value_matches(
                after,
                change.after,
                rule,
            ):
                if len(missing_expected) < 100:
                    missing_expected.append(
                        {
                            "row": absolute_row + 1,
                            "column": staged_column + 1,
                            "title": change.title,
                            "expected": json_value(change.after),
                            "actual": json_value(after),
                        }
                    )
        if rollover is not None and rollover.insert_history_column:
            expected_history = _snapshot_cell(
                source,
                absolute_row,
                rollover.current_column,
            )
            actual_history = _snapshot_cell(
                staged,
                absolute_row,
                rollover.history_column,
            )
            report.checked_master_cells += 1
            if (
                not _same_value(expected_history, actual_history)
                and len(forbidden) < 100
            ):
                forbidden.append(
                    {
                        "row": absolute_row + 1,
                        "column": rollover.history_column + 1,
                        "title": rollover.history_title,
                        "before": json_value(expected_history),
                        "after": json_value(actual_history),
                    }
                )
    if forbidden:
        report.issues.append(
            Issue(
                "error",
                "forbidden_master_diff",
                "staging изменил ячейки мастера вне утверждённого плана",
                {"samples": forbidden},
            )
        )
    if missing_expected:
        report.issues.append(
            Issue(
                "error",
                "missing_master_changes",
                "часть утверждённых изменений не попала в staging",
                {"samples": missing_expected},
            )
        )


def _validate_projection(
    bundle: ConfigBundle,
    source_path: Path,
    staged_path: Path,
    projection: PersonalProjection,
    report: ValidationReport,
) -> None:
    source = read_portfolio(source_path, bundle.fields)
    staged = read_portfolio(staged_path, bundle.fields)
    if not _validate_structure(
        source,
        staged,
        report.issues,
        label=projection.entry.path.name,
    ):
        return
    if tuple(
        str(value or "").strip()
        for value in staged.headers
    ) != tuple(projection.headers):
        report.issues.append(
            Issue(
                "error",
                "projection_headers_changed",
                f"заголовки проекции не совпали: {projection.entry.path.name}",
            )
        )
        return

    rules = {norm_title(rule.title): rule for rule in bundle.fields.collect}
    mismatches: list[dict[str, Any]] = []
    for offset, expected_row in enumerate(projection.rows):
        if offset >= len(staged.rows):
            mismatches.append({"row": staged.data_start + offset + 1, "missing": True})
            continue
        actual_row = staged.rows[offset]
        for column, expected in enumerate(expected_row):
            actual = staged.row_value(actual_row, column)
            rule = rules.get(norm_title(projection.headers[column]))
            equal = (
                values_equal_for_rule(actual, expected, rule)
                if rule is not None
                else _same_value(actual, expected)
            )
            if (
                equal
                and rule is not None
                and rule.value_type == "date"
            ):
                equal = same_representation(actual, expected)
            report.checked_projection_cells += 1
            if not equal and len(mismatches) < 100:
                mismatches.append(
                    {
                        "row": staged.data_start + offset + 1,
                        "column": column + 1,
                        "title": projection.headers[column],
                        "expected": json_value(expected),
                        "actual": json_value(actual),
                    }
                )
    for offset, trailing in enumerate(
        staged.rows[len(projection.rows) :],
        start=len(projection.rows),
    ):
        for column, value in enumerate(trailing[: len(projection.headers)]):
            if not is_blank(value) and len(mismatches) < 100:
                mismatches.append(
                    {
                        "row": staged.data_start + offset + 1,
                        "column": column + 1,
                        "title": projection.headers[column],
                        "unexpected_trailing": json_value(value),
                    }
                )
    if mismatches:
        report.issues.append(
            Issue(
                "error",
                "projection_value_mismatch",
                f"личный staging не совпал с проекцией: {projection.entry.path.name}",
                {"samples": mismatches},
            )
        )


def _validate_untouched_sheets(
    bundle: ConfigBundle,
    stage: StageSet,
    report: ValidationReport,
    *,
    projections: dict[str, PersonalProjection],
    plan: SyncPlan,
    progress: ProgressCallback | None,
) -> None:
    total = len(stage.files)
    with ExcelHost() as host:
        rollover_reference_signatures = None
        rollover = plan.month_rollover
        if rollover is not None and rollover.insert_history_column:
            reference_path = stage.staging_dir / (
                ".validation-rollover-reference-"
                f"{secrets.token_hex(4)}{stage.master.source_copy.suffix}"
            )
            try:
                stable_copy(stage.master.source_copy, reference_path)
                with host.open_book(reference_path, read_only=False) as reference_book:
                    reference_sheet = sheet_by_name(
                        reference_book,
                        bundle.fields.sheet_name,
                    )
                    reference_sheet.range(
                        (1, rollover.history_column + 1)
                    ).api.EntireColumn.Insert()
                    reference_book.api.Save()
                with host.open_book(reference_path, read_only=True) as reference_book:
                    rollover_reference_signatures = (
                        workbook_untouched_signatures(
                            reference_book,
                            mutable_sheet=bundle.fields.sheet_name,
                        )
                    )
            finally:
                reference_path.unlink(missing_ok=True)
        for index, item in enumerate(stage.files, start=1):
            with host.open_book(item.source_copy, read_only=True) as source_book:
                source_names = tuple(source_book.sheet_names)
                source_signatures = workbook_untouched_signatures(
                    source_book,
                    mutable_sheet=bundle.fields.sheet_name,
                )
                if (
                    item.kind == "master"
                    and rollover_reference_signatures is not None
                ):
                    source_signatures = rollover_reference_signatures
                source_filter = sheet_filter_signature(
                    sheet_by_name(source_book, bundle.fields.sheet_name)
                )
            with host.open_book(item.staged_path, read_only=True) as staged_book:
                staged_names = tuple(staged_book.sheet_names)
                staged_signatures = workbook_untouched_signatures(
                    staged_book,
                    mutable_sheet=bundle.fields.sheet_name,
                )
                staged_filter = sheet_filter_signature(
                    sheet_by_name(staged_book, bundle.fields.sheet_name)
                )
                if item.kind == "master":
                    staged_sheet = sheet_by_name(
                        staged_book,
                        bundle.fields.sheet_name,
                    )
                    bad_date_formats = []
                    for change in plan.changes:
                        if not isinstance(
                            change.after,
                            (date, datetime),
                        ):
                            continue
                        number_format = staged_sheet.range(
                            (
                                change.row + 1,
                                _mapped_master_column(
                                    plan,
                                    change.column,
                                )
                                + 1,
                            )
                        ).api.NumberFormat
                        if (
                            _is_general_number_format(number_format)
                            and len(bad_date_formats) < 100
                        ):
                            bad_date_formats.append(
                                {
                                    "row": change.row + 1,
                                    "column": change.column + 1,
                                    "title": change.title,
                                    "contract_id": change.contract_id,
                                    "number_format": str(number_format),
                                }
                            )
                    if bad_date_formats:
                        report.issues.append(
                            Issue(
                                "error",
                                "typed_date_format_missing",
                                "typed-date изменения мастера остались "
                                "в General-формате",
                                {"samples": bad_date_formats},
                            )
                        )
            if source_names != staged_names:
                report.issues.append(
                    Issue(
                        "error",
                        "workbook_sheet_order_changed",
                        f"изменился набор листов: {item.original_path.name}",
                    )
                )
            if source_signatures != staged_signatures:
                report.issues.append(
                    Issue(
                        "error",
                        "untouched_sheet_changed",
                        f"изменился лист вне «Портфели»: {item.original_path.name}",
                        {
                            "before": source_signatures,
                            "after": staged_signatures,
                        },
                    )
                )
            expected_filter = copy.deepcopy(source_filter)
            rollover = plan.month_rollover
            if (
                item.kind == "master"
                and rollover is not None
                and rollover.insert_history_column
                and bool(expected_filter.get("enabled"))
            ):
                insertion_column = rollover.history_column + 1
                first_column = int(expected_filter["first_column"])
                columns = int(expected_filter["columns"])
                if (
                    first_column
                    <= insertion_column
                    <= first_column + columns - 1
                ):
                    relative_field = (
                        insertion_column - first_column + 1
                    )
                    expected_filter["columns"] = columns + 1
                    for criterion in expected_filter.get(
                        "criteria",
                        [],
                    ):
                        if int(criterion["field"]) >= relative_field:
                            criterion["field"] = int(
                                criterion["field"]
                            ) + 1
            expected_without_last = {
                key: value
                for key, value in expected_filter.items()
                if key != "last_row"
            }
            staged_without_last = {
                key: value
                for key, value in staged_filter.items()
                if key != "last_row"
            }
            expected_last = expected_filter.get("last_row")
            if item.kind == "personal" and source_filter.get("enabled"):
                assert item.entry_id is not None
                source_snapshot = read_portfolio(item.source_copy, bundle.fields)
                projection = projections[item.entry_id]
                expected_last = max(
                    int(source_filter["first_row"]),
                    source_snapshot.data_start + len(projection.rows),
                )
            if (
                expected_without_last != staged_without_last
                or staged_filter.get("last_row") != expected_last
            ):
                report.issues.append(
                    Issue(
                        "error",
                        "portfolio_filter_changed",
                        f"не восстановлена семантика AutoFilter: {item.original_path.name}",
                        {
                            "before": source_filter,
                            "after": staged_filter,
                            "expected_last_row": expected_last,
                        },
                    )
                )
            if progress is not None:
                progress(item.original_path.name, index, total)


def _write_report(stage: StageSet, report: ValidationReport) -> None:
    json_path = stage.staging_dir / "validation.json"
    text_path = stage.staging_dir / "validation.txt"
    _atomic_text(
        json_path,
        json.dumps(report.as_dict(), ensure_ascii=False, indent=2),
    )
    lines = [
        "КОНТУР · РЕЕСТРЫ — ВАЛИДАЦИЯ STAGING",
        "",
        f"Статус: {'ЗАБЛОКИРОВАНО' if report.blocked else 'ПРОЙДЕНО'}",
        f"Digest плана: {report.plan_digest}",
        f"Проверено файлов: {report.checked_files}",
        f"Проверено ячеек мастера: {report.checked_master_cells}",
        f"Проверено ячеек личных портфелей: {report.checked_projection_cells}",
        "",
    ]
    for issue in report.issues:
        lines.append(f"[{issue.severity.upper()}] {issue.code}: {issue.message}")
    _atomic_text(text_path, "\n".join(lines))


def validate_staging(
    bundle: ConfigBundle,
    stage: StageSet,
    plan: SyncPlan,
    *,
    progress: ProgressCallback | None = None,
) -> ValidationReport:
    report = ValidationReport(
        run_id=stage.run_id,
        plan_digest=stage.plan_digest,
    )
    config_identity = str(getattr(bundle, "config_identity", "") or "")
    if not (
        stage.config_identity
        == plan.config_identity
        == config_identity
    ):
        report.issues.append(
            Issue(
                "error",
                "config_identity_mismatch",
                "snapshot/plan/staging относятся к разным конфигурациям",
            )
        )
        _write_report(stage, report)
        return report
    if stage.plan_digest != plan.digest:
        report.issues.append(
            Issue(
                "error",
                "plan_digest_mismatch",
                "staging относится к другому плану",
            )
        )
        _write_report(stage, report)
        return report
    if plan.blocked:
        report.issues.append(
            Issue(
                "error",
                "plan_is_blocked",
                "заблокированный план нельзя валидировать для публикации",
            )
        )
        _write_report(stage, report)
        return report

    for item in stage.files:
        if fingerprint(item.staged_path) != item.staged_fingerprint:
            report.issues.append(
                Issue(
                    "error",
                    "staged_file_changed",
                    f"staging-файл изменился после сборки: {item.staged_path.name}",
                )
            )
    if report.blocked:
        _write_report(stage, report)
        return report

    _validate_master(bundle, stage, plan, report)
    projections = {
        projection.entry.entry_id: projection
        for projection in plan.projections
    }
    for item in stage.personals:
        assert item.entry_id is not None
        _validate_projection(
            bundle,
            item.source_copy,
            item.staged_path,
            projections[item.entry_id],
            report,
        )
    _validate_untouched_sheets(
        bundle,
        stage,
        report,
        projections=projections,
        plan=plan,
        progress=progress,
    )
    report.checked_files = len(stage.files)
    _write_report(stage, report)
    return report
