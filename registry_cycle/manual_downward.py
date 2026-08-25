"""Manual step 2: verified master registry -> personal XLSB workbooks."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .config import ConfigBundle
from .discovery import discover
from .errors import ConfigurationError, SafetyViolation
from .excel_retry import retry_local_excel
from .models import DiscoveryReport, Issue, PersonalProjection, SyncPlan
from .normalize import comparable_value, is_blank, norm_title
from .publishing import (
    PublishResult,
    publication_cycle_lock,
    publish_selected_personals_validated,
)
from .reader import read_portfolio
from .receipts import ensure_archive_directory, write_publication_receipt
from .reporting import write_plan_reports
from .semantics import values_equal_for_rule
from .service import CycleService
from .snapshot import SnapshotSet, capture_sources
from .staging import StageSet, StagedFile, build_staging
from .validation import ValidationReport, validate_staging


@dataclass(frozen=True, slots=True)
class ProjectionDelta:
    entry_id: str
    path: Path
    country: str
    group: str
    profile: str
    employee: str
    rows_before: int
    rows_after: int
    changed_cells: int
    added_rows: int
    removed_rows: int
    employee_payment_cells: int
    changed_employee_payment_cells: int
    added_columns: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(
            self.changed_cells
            or self.rows_before != self.rows_after
            or self.added_columns
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "path": str(self.path),
            "country": self.country,
            "group": self.group,
            "profile": self.profile,
            "employee": self.employee,
            "rows_before": self.rows_before,
            "rows_after": self.rows_after,
            "changed_cells": self.changed_cells,
            "added_rows": self.added_rows,
            "removed_rows": self.removed_rows,
            "employee_payment_cells": self.employee_payment_cells,
            "changed_employee_payment_cells": (
                self.changed_employee_payment_cells
            ),
            "added_columns": list(self.added_columns),
            "changed": self.changed,
        }


@dataclass(frozen=True, slots=True)
class ManualDownwardRun:
    discovery: DiscoveryReport
    snapshot: SnapshotSet
    plan: SyncPlan
    deltas: tuple[ProjectionDelta, ...]
    stage: StageSet | None = None
    validation: ValidationReport | None = None
    publication: PublishResult | None = None

    @property
    def changed_deltas(self) -> tuple[ProjectionDelta, ...]:
        return tuple(item for item in self.deltas if item.changed)

    @property
    def ready_to_publish(self) -> bool:
        return bool(
            not self.plan.blocked
            and not self.plan.changes
            and self.changed_deltas
            and self.stage is not None
            and self.validation is not None
            and not self.validation.blocked
        )


def _blocked_discovery_message(report: DiscoveryReport) -> str:
    errors = [
        f"{item.code}: {item.message}"
        for item in report.issues
        if item.severity == "error"
    ]
    return "Источники для раздачи не прошли проверку: " + "; ".join(errors[:10])


def _trim_trailing_blank_rows(rows: tuple[tuple[Any, ...], ...]) -> list[tuple[Any, ...]]:
    trimmed = list(rows)
    while trimmed and all(is_blank(value) for value in trimmed[-1]):
        trimmed.pop()
    return trimmed


def _projection_delta(
    bundle: ConfigBundle,
    snapshot: SnapshotSet,
    projection: PersonalProjection,
) -> ProjectionDelta:
    captured = next(
        item
        for item in snapshot.personals
        if item.entry.entry_id == projection.entry.entry_id
    )
    source = read_portfolio(captured.copy_path, bundle.fields)
    before = _trim_trailing_blank_rows(source.rows)
    after = list(projection.rows)
    rules = {
        norm_title(rule.title): rule
        for rule in bundle.fields.collect
    }
    payment_titles = {
        norm_title(title)
        for title in (
            bundle.fields.month_rollover.employee_clear_titles
            if bundle.fields.month_rollover is not None
            else ()
        )
    }
    payment_columns = {
        index
        for index, title in enumerate(projection.headers)
        if norm_title(title) in payment_titles
    }
    changed_cells = 0
    changed_payment_cells = 0
    payment_cells = 0
    width = len(projection.headers)
    for row_index in range(max(len(before), len(after))):
        before_row = before[row_index] if row_index < len(before) else ()
        after_row = after[row_index] if row_index < len(after) else ()
        for column in range(width):
            left = source.row_value(before_row, column) if before_row else None
            right = after_row[column] if column < len(after_row) else None
            rule = rules.get(norm_title(projection.headers[column]))
            equal = (
                values_equal_for_rule(left, right, rule)
                if rule is not None
                else comparable_value(left) == comparable_value(right)
            )
            if not equal:
                changed_cells += 1
                if column in payment_columns:
                    changed_payment_cells += 1
            if column in payment_columns and not is_blank(right):
                payment_cells += 1
    return ProjectionDelta(
        entry_id=projection.entry.entry_id,
        path=projection.entry.path,
        country=projection.entry.country,
        group=projection.entry.group,
        profile=projection.entry.profile,
        employee=projection.responsible,
        rows_before=len(before),
        rows_after=len(after),
        changed_cells=changed_cells,
        added_rows=max(0, len(after) - len(before)),
        removed_rows=max(0, len(before) - len(after)),
        employee_payment_cells=payment_cells,
        changed_employee_payment_cells=changed_payment_cells,
        added_columns=projection.added_columns,
    )


def _enforce_downward_scope(bundle: ConfigBundle, plan: SyncPlan) -> None:
    settings = bundle.app.manual_downward
    exact = {
        norm_title(value)
        for value in settings["forbidden_personal_titles"]
    }
    prefixes = tuple(
        norm_title(value)
        for value in settings["forbidden_personal_title_prefixes"]
    )
    violations = sorted(
        {
            title
            for projection in plan.projections
            for title in projection.headers
            if norm_title(title) in exact
            or any(norm_title(title).startswith(prefix) for prefix in prefixes)
        }
    )
    if violations:
        plan.issues.append(
            Issue(
                "error",
                "manual_downward_analyst_payments_exposed",
                "аналитические платёжные колонки попали в личную проекцию",
                {"titles": violations},
            )
        )


def _require_completed_upward(bundle: ConfigBundle, plan: SyncPlan) -> None:
    if not bool(bundle.app.manual_downward["require_zero_master_changes"]):
        return
    if not plan.changes:
        return
    payment_titles = {
        norm_title(title)
        for title in (
            bundle.fields.month_rollover.employee_clear_titles
            if bundle.fields.month_rollover is not None
            else ()
        )
    }
    plan.issues.append(
        Issue(
            "error",
            "manual_downward_upward_required",
            "в личных реестрах есть изменения, которых ещё нет в общем; "
            "сначала выполни шаг 1",
            {
                "master_changes": len(plan.changes),
                "employee_payment_changes": sum(
                    norm_title(change.title) in payment_titles
                    for change in plan.changes
                ),
                "samples": [
                    {
                        "contract_id": change.contract_id,
                        "title": change.title,
                        "source": str(change.source_path),
                    }
                    for change in plan.changes[:20]
                ],
            },
        )
    )


def _write_context(snapshot: SnapshotSet) -> None:
    path = snapshot.run_dir / "manual-downward.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(
            {
                "run_id": snapshot.run_id,
                "mode": "manual_downward",
                "master": str(snapshot.master_original),
                "rollover": "disabled",
                "publication_scope": "personals_only",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_downward_report(run: ManualDownwardRun) -> Path:
    path = run.snapshot.run_dir / "downward.txt"
    changed = run.changed_deltas
    lines = [
        "ПЕРЕНОС РЕЕСТРОВ — ШАГ 2",
        "",
        f"Запуск: {run.snapshot.run_id}",
        f"Личных источников: {len(run.snapshot.personals)}",
        f"Проекций: {len(run.plan.projections)}",
        f"Файлов с изменениями: {len(changed)}",
        f"Изменённых ячеек: {sum(item.changed_cells for item in changed)}",
        f"Строк после раздачи: {sum(item.rows_after for item in run.deltas)}",
        f"Добавлено столбцов: {sum(len(item.added_columns) for item in changed)}",
        "Аналитические платежи в личных проекциях: 0",
        "",
    ]
    for item in changed:
        lines.append(
            f"{item.employee} · {item.path.name} · "
            f"строк {item.rows_before} → {item.rows_after} · "
            f"ячеек {item.changed_cells}"
            + (
                " · новые столбцы: " + ", ".join(item.added_columns)
                if item.added_columns
                else ""
            )
        )
    temporary = path.with_suffix(".txt.tmp")
    temporary.write_text("\n".join(lines), encoding="utf-8")
    temporary.replace(path)
    payload_path = path.with_suffix(".json")
    payload_temporary = payload_path.with_suffix(".json.tmp")
    payload_temporary.write_text(
        json.dumps(
            {
                "run_id": run.snapshot.run_id,
                "plan_digest": run.plan.digest,
                "blocked": run.plan.blocked,
                "deltas": [item.as_dict() for item in run.deltas],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    payload_temporary.replace(payload_path)
    return path


def _projected_snapshot(
    snapshot: SnapshotSet,
    plan: SyncPlan,
) -> SnapshotSet:
    """Keep skipped empty books out of staging and therefore out of publish."""
    projection_ids = {
        projection.entry.entry_id
        for projection in plan.projections
    }
    captured_ids = {
        item.entry.entry_id
        for item in snapshot.personals
    }
    unknown = projection_ids - captured_ids
    if unknown:
        raise SafetyViolation(
            "проекция ссылается на файлы вне снимка: "
            + ", ".join(sorted(unknown))
        )
    return replace(
        snapshot,
        personals=tuple(
            item
            for item in snapshot.personals
            if item.entry.entry_id in projection_ids
        ),
    )


def prepare_manual_downward(
    bundle: ConfigBundle,
    *,
    run_id: str | None = None,
    now: datetime | None = None,
    progress: Callable[[str], None] | None = None,
) -> ManualDownwardRun:
    """Build and validate personal projections without touching live files."""
    settings = bundle.app.manual_downward
    if not bool(settings["enabled"]):
        raise SafetyViolation("раздача вниз отключена в app.json")
    if bool(settings["automatic_publish"]) or bool(settings["automatic_rollover"]):
        raise SafetyViolation("раздача должна оставаться отдельной ручной операцией")

    def emit(message: str) -> None:
        if progress is not None:
            progress(message)

    now = now or datetime.now()
    emit("[1/6] Ищу личные реестры .xlsb…")
    discovery_report = discover(bundle)
    if discovery_report.blocked:
        raise SafetyViolation(_blocked_discovery_message(discovery_report))
    emit(f"[1/6] Найдено источников: {len(discovery_report.entries)}")

    emit("[2/6] Создаю локальный снимок общего и личных реестров…")
    snapshot_progress = None
    if progress is not None:
        def snapshot_progress(name: str, done: int, total: int) -> None:
            if done == 1 or done == total or done % 5 == 0:
                emit(f"[2/6] Скопировано {done}/{total}: {name}")
    snapshot = capture_sources(
        bundle,
        discovery_report,
        run_id=run_id,
        now=now,
        progress=snapshot_progress,
    )
    _write_context(snapshot)

    emit("[3/6] Строю личные реестры по ответственным общего реестра…")
    prepared = CycleService(bundle).prepare_snapshot(
        snapshot,
        discovery_report,
        rollover_month=None,
        include_projections=True,
        baseline_override=None,
        conflict_resolutions=(),
        master_conflict_policy=str(settings["conflict_policy"]),
    )
    plan = prepared.plan
    _enforce_downward_scope(bundle, plan)
    _require_completed_upward(bundle, plan)
    deltas = tuple(
        _projection_delta(bundle, snapshot, projection)
        for projection in plan.projections
    )
    write_plan_reports(snapshot.run_dir, plan)
    run = ManualDownwardRun(discovery_report, snapshot, plan, deltas)
    _write_downward_report(run)
    emit(
        f"[3/6] Изменятся личные файлы: {len(run.changed_deltas)}; "
        f"ошибок: {sum(issue.severity == 'error' for issue in plan.issues)}"
    )
    if plan.blocked or not run.changed_deltas:
        return run

    emit("[4/6] Собираю локальные проверочные копии .xlsb…")
    staging_progress = None
    validation_progress = None
    if progress is not None:
        staging_progress = lambda name, done, total: emit(
            f"[4/6] Готово {done}/{total}: {name}"
        )
        validation_progress = lambda name, done, total: emit(
            f"[5/6] Проверено {done}/{total}: {name}"
        )
    excel_attempts = int(bundle.app.safety["local_excel_attempts"])
    staging_snapshot = _projected_snapshot(snapshot, plan)

    def build_attempt(attempt: int) -> StageSet:
        output_dir = (
            None
            if attempt == 1
            else snapshot.run_dir / f"staging-retry-{attempt}"
        )
        return build_staging(
            bundle,
            staging_snapshot,
            plan,
            output_dir=output_dir,
            progress=staging_progress,
        )

    stage = retry_local_excel(
        build_attempt,
        attempts=excel_attempts,
        on_retry=lambda attempt, _error: emit(
            f"[4/6] Локальный Excel прервался; повтор {attempt}/{excel_attempts}…"
        ),
    )
    emit("[5/6] Сверяю staging через Calamine и Excel-проверки…")
    validation = retry_local_excel(
        lambda _attempt: validate_staging(
            bundle,
            stage,
            plan,
            progress=validation_progress,
        ),
        attempts=excel_attempts,
        on_retry=lambda attempt, _error: emit(
            f"[5/6] Excel-проверка прервалась; повтор {attempt}/{excel_attempts}…"
        ),
    )
    emit(
        "[6/6] Проверка завершена: "
        + ("есть ошибки" if validation.blocked else "можно подтверждать")
    )
    run = replace(run, stage=stage, validation=validation)
    _write_downward_report(run)
    return run


def _archive_relative_path(layout: str, delta: ProjectionDelta) -> Path:
    try:
        rendered = layout.format_map(
            {
                "country": delta.country,
                "group": delta.group,
                "employee": delta.path.parent.name,
                "profile": delta.profile,
            }
        )
    except KeyError as exc:
        raise ConfigurationError(
            f"неизвестный плейсхолдер backup.layout: {{{exc.args[0]}}}"
        ) from exc
    relative = Path(rendered)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ConfigurationError(
            f"manual_downward.backup.layout должен быть относительным: {layout}"
        )
    return relative


def _backup_directories(
    bundle: ConfigBundle,
    run: ManualDownwardRun,
    selected: tuple[StagedFile, ...],
) -> dict[str, Path]:
    settings = bundle.app.manual_downward["backup"]
    root = ensure_archive_directory(
        bundle.render(str(settings["directory"])),
        live_targets=(item.original_path for item in selected),
    )
    by_entry = {item.entry_id: item for item in run.changed_deltas}
    result: dict[str, Path] = {}
    for item in selected:
        assert item.entry_id is not None
        delta = by_entry[item.entry_id]
        directory = ensure_archive_directory(
            root / _archive_relative_path(str(settings["layout"]), delta),
            live_targets=(item.original_path,),
        )
        result[str(item.original_path)] = directory
    return result


def publish_prepared_manual_downward(
    bundle: ConfigBundle,
    run: ManualDownwardRun,
    *,
    confirmation_digest: str,
) -> ManualDownwardRun:
    """Publish only changed personal files with central backups and receipt."""
    if not run.ready_to_publish or run.stage is None or run.validation is None:
        raise SafetyViolation("раздачу нельзя опубликовать: проверка не зелёная")
    if confirmation_digest != run.plan.digest:
        raise SafetyViolation("digest подтверждения не совпал с планом")
    changed_ids = {item.entry_id for item in run.changed_deltas}
    selected = tuple(
        item
        for item in run.stage.personals
        if item.entry_id in changed_ids
    )
    if len(selected) != len(changed_ids):
        raise SafetyViolation("не все изменённые личные файлы найдены в staging")
    settings = bundle.app.manual_downward["backup"]
    backup_directories = _backup_directories(bundle, run, selected)
    receipt_directory = ensure_archive_directory(
        bundle.render(str(settings["receipt_directory"])),
        live_targets=(item.original_path for item in selected),
    )
    by_entry = {item.entry_id: item for item in run.changed_deltas}

    def commit_receipt(result: PublishResult) -> Path:
        return write_publication_receipt(
            stage=run.stage,
            result=result,
            files=selected,
            receipt_directory=receipt_directory,
            operation="раздача-вниз",
            context_by_entry={
                entry_id: {
                    "employee": delta.employee,
                    "country": delta.country,
                    "group": delta.group,
                    "profile": delta.profile,
                    "rows_before": delta.rows_before,
                    "rows_after": delta.rows_after,
                    "rows_added": delta.added_rows,
                    "rows_removed": delta.removed_rows,
                    "cells_written": delta.changed_cells,
                    "employee_payment_cells": delta.employee_payment_cells,
                    "changed_employee_payment_cells": (
                        delta.changed_employee_payment_cells
                    ),
                    "analyst_payment_cells": 0,
                }
                for entry_id, delta in by_entry.items()
            },
            summary={
                "source_files": len(run.snapshot.personals),
                "published_files": len(selected),
                "cells_written": sum(
                    item.changed_cells for item in run.changed_deltas
                ),
                "rows_after": sum(item.rows_after for item in run.deltas),
                "employee_payment_cells": sum(
                    item.employee_payment_cells for item in run.deltas
                ),
                "analyst_payment_cells": 0,
            },
        )

    with publication_cycle_lock(bundle) as cycle_lock:
        publication = publish_selected_personals_validated(
            bundle,
            run.stage,
            run.plan,
            run.validation,
            selected_files=selected,
            confirmation_digest=confirmation_digest,
            backup_directories=backup_directories,
            backup_label=str(settings["label"]),
            busy_mode=str(bundle.app.manual_downward["busy_file_policy"]),
            commit_callback=commit_receipt,
            cycle_lock=cycle_lock,
        )
    return replace(run, publication=publication)


def format_summary(run: ManualDownwardRun) -> str:
    errors = sum(issue.severity == "error" for issue in run.plan.issues)
    warnings = sum(issue.severity == "warning" for issue in run.plan.issues)
    return "\n".join(
        [
            "ПЕРЕНОС РЕЕСТРОВ — ШАГ 2",
            f"Запуск: {run.snapshot.run_id}",
            f"Источников .xlsb: {len(run.snapshot.personals)}",
            f"Файлов с изменениями: {len(run.changed_deltas)}",
            f"Изменённых ячеек: {sum(item.changed_cells for item in run.changed_deltas)}",
            f"Строк после раздачи: {sum(item.rows_after for item in run.deltas)}",
            f"Платёжных ячеек сотрудника: {sum(item.employee_payment_cells for item in run.deltas)}",
            "Аналитических платёжных ячеек: 0",
            f"Ошибок: {errors} · предупреждений: {warnings}",
            f"Digest: {run.plan.digest}",
            f"Отчёт: {run.snapshot.run_dir / 'downward.txt'}",
        ]
    )
