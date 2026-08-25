"""Manual one-way transfer: employee portfolios -> master registry only.

This is the operational entry point. It deliberately does not distribute the
master back into portfolios, does not schedule anything and never starts a
month rollover. A run always creates a local snapshot, plan, master-only
staging and validation before an explicit publication confirmation.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Callable

from .config import ConfigBundle, load_config_bundle
from .discovery import discover
from .errors import RegistryCycleError, SafetyViolation
from .excel_retry import retry_local_excel
from .models import DiscoveryReport, Issue, SyncPlan
from .normalize import norm_title
from .publishing import PublishResult, publication_cycle_lock, publish_validated
from .receipts import ensure_archive_directory, write_publication_receipt
from .reporting import write_plan_reports
from .service import CycleService
from .snapshot import SnapshotSet, capture_sources
from .staging import StageSet, build_staging
from .validation import ValidationReport, validate_staging


@dataclass(frozen=True, slots=True)
class ManualUpwardRun:
    """All local artifacts of one manual upward transfer."""

    discovery: DiscoveryReport
    snapshot: SnapshotSet
    plan: SyncPlan
    stage: StageSet | None = None
    validation: ValidationReport | None = None
    publication: PublishResult | None = None
    baseline_label: str = "нет"
    allow_initial_baseline: bool = False

    @property
    def ready_to_publish(self) -> bool:
        return bool(
            not self.plan.blocked
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
    return "Источники для ручного переноса не прошли проверку: " + "; ".join(
        errors[:10]
    )


def _enforce_manual_scope(bundle: ConfigBundle, plan: SyncPlan) -> None:
    """Keep analyst-owned payment columns outside this one-way transfer."""
    settings = bundle.app.manual_upward
    exact_titles = {
        norm_title(value)
        for value in settings["forbidden_master_titles"]
    }
    prefixes = tuple(
        norm_title(value)
        for value in settings["forbidden_master_title_prefixes"]
    )
    violations = [
        change
        for change in plan.changes
        if (
            norm_title(change.title) in exact_titles
            or any(norm_title(change.title).startswith(prefix) for prefix in prefixes)
        )
    ]
    if violations:
        plan.issues.append(
            Issue(
                "error",
                "manual_upward_forbidden_master_field",
                "ручной перенос наверх попытался изменить поле аналитических платежей",
                {
                    "titles": sorted({change.title for change in violations}),
                    "count": len(violations),
                },
            )
        )


def _write_run_context(
    snapshot: SnapshotSet,
    *,
    baseline_context: dict[str, object],
) -> None:
    path = snapshot.run_dir / "manual-upward.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(
            {
                "run_id": snapshot.run_id,
                "mode": "manual_upward",
                "master": str(snapshot.master_original),
                "baseline": baseline_context,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def _manual_backup_destination(
    bundle: ConfigBundle,
    stage: StageSet,
) -> tuple[Path, str]:
    """Return the configured central archive, never the master directory."""
    settings = bundle.app.manual_upward["backup"]
    directory = ensure_archive_directory(
        bundle.render(str(settings["directory"])),
        live_targets=(stage.master.original_path,),
    )
    label = str(settings["label"])
    return directory, label


def _manual_receipt_destination(
    bundle: ConfigBundle,
    stage: StageSet,
) -> Path:
    settings = bundle.app.manual_upward["backup"]
    return ensure_archive_directory(
        bundle.render(str(settings["receipt_directory"])),
        live_targets=(stage.master.original_path,),
    )


def prepare_manual_upward(
    bundle: ConfigBundle,
    *,
    run_id: str | None = None,
    now: datetime | None = None,
    progress: Callable[[str], None] | None = None,
) -> ManualUpwardRun:
    """Make and validate master-only staging without touching live workbooks."""
    settings = bundle.app.manual_upward
    if not bool(settings["enabled"]):
        raise SafetyViolation("ручной перенос наверх отключён в app.json")
    if bool(settings["automatic_publish"]) or bool(settings["automatic_rollover"]):
        raise SafetyViolation(
            "ручной перенос наверх должен оставаться полностью ручным"
        )

    def emit(message: str) -> None:
        if progress is not None:
            progress(message)

    now = now or datetime.now()
    emit("[1/5] Ищу личные портфели .xlsb…")
    discovery_report = discover(bundle)
    if discovery_report.blocked:
        raise SafetyViolation(_blocked_discovery_message(discovery_report))
    emit(f"[1/5] Найдено источников: {len(discovery_report.entries)}")

    emit("[2/5] Создаю локальный снимок общего и личных реестров…")
    snapshot_progress = None
    if progress is not None:
        def snapshot_progress(name: str, done: int, total: int) -> None:
            if done == 1 or done == total or done % 5 == 0:
                emit(f"[2/5] Скопировано {done}/{total}: {name}")
    snapshot = capture_sources(
        bundle,
        discovery_report,
        run_id=run_id,
        now=now,
        progress=snapshot_progress,
    )
    baseline_label = "не участвует: личный реестр имеет приоритет"
    baseline_context = {
        "mode": "not_used",
        "conflict_policy": settings["conflict_policy"],
    }
    allow_initial_baseline = snapshot.baseline_copy is None
    _write_run_context(snapshot, baseline_context=baseline_context)
    emit("[3/5] Сверяю изменения по КД и ответственным…")
    service = CycleService(bundle)
    prepared = service.prepare_snapshot(
        snapshot,
        discovery_report,
        rollover_month=None,
        include_projections=False,
        baseline_override=None,
        conflict_resolutions=(),
        master_conflict_policy=str(settings["conflict_policy"]),
    )
    plan = prepared.plan
    _enforce_manual_scope(bundle, plan)
    write_plan_reports(snapshot.run_dir, plan)
    emit(
        f"[3/5] План готов: изменений {len(plan.changes)}, "
        f"ошибок {sum(issue.severity == 'error' for issue in plan.issues)}"
    )
    if plan.blocked:
        return ManualUpwardRun(
            discovery_report,
            snapshot,
            plan,
            baseline_label=baseline_label,
            allow_initial_baseline=allow_initial_baseline,
        )

    # ``build_staging`` can reuse its verification/publishing machinery.  A
    # master-only view guarantees that no personal file is ever staged or
    # published by this entry point.
    master_only_snapshot = replace(snapshot, personals=())
    emit("[4/5] Собираю проверочную копию общего реестра…")
    staging_progress = None
    validation_progress = None
    if progress is not None:
        staging_progress = lambda name, done, total: emit(
            f"[4/5] Готово {done}/{total}: {name}"
        )
        validation_progress = lambda name, done, total: emit(
            f"[5/5] Проверено {done}/{total}: {name}"
        )
    excel_attempts = int(bundle.app.safety["local_excel_attempts"])

    def build_attempt(attempt: int) -> StageSet:
        output_dir = (
            None
            if attempt == 1
            else snapshot.run_dir / f"staging-retry-{attempt}"
        )
        return build_staging(
            bundle,
            master_only_snapshot,
            plan,
            output_dir=output_dir,
            progress=staging_progress,
        )

    stage = retry_local_excel(
        build_attempt,
        attempts=excel_attempts,
        on_retry=lambda attempt, _error: emit(
            f"[4/5] Локальный Excel прервался; повтор {attempt}/{excel_attempts}…"
        ),
    )
    emit("[5/5] Проверяю staging перед публикацией…")
    validation = retry_local_excel(
        lambda _attempt: validate_staging(
            bundle,
            stage,
            plan,
            progress=validation_progress,
        ),
        attempts=excel_attempts,
        on_retry=lambda attempt, _error: emit(
            f"[5/5] Excel-проверка прервалась; повтор {attempt}/{excel_attempts}…"
        ),
    )
    emit(
        "[5/5] Проверка завершена: "
        + ("есть ошибки" if validation.blocked else "всё готово")
    )
    return ManualUpwardRun(
        discovery_report,
        snapshot,
        plan,
        stage,
        validation,
        baseline_label=baseline_label,
        allow_initial_baseline=allow_initial_baseline,
    )


def publish_prepared_manual_upward(
    bundle: ConfigBundle,
    run: ManualUpwardRun,
    *,
    confirmation_digest: str,
) -> ManualUpwardRun:
    """Publish with the configured manual busy-file policy and exact digest."""
    if not run.ready_to_publish or run.stage is None or run.validation is None:
        raise SafetyViolation(
            "ручной перенос нельзя опубликовать: план или validation не зелёные"
        )
    if confirmation_digest != run.plan.digest:
        raise SafetyViolation("digest подтверждения не совпал с планом")
    backup_directory, backup_label = _manual_backup_destination(bundle, run.stage)
    receipt_directory = _manual_receipt_destination(bundle, run.stage)
    employee_payment_changes, protected_changes = _scope_summary(
        bundle,
        run.plan,
    )

    def commit_receipt(result: PublishResult) -> Path:
        return write_publication_receipt(
            stage=run.stage,
            result=result,
            files=run.stage.files,
            receipt_directory=receipt_directory,
            operation="перенос-наверх",
            context_by_entry={
                "master": {
                    "cells_written": len(run.plan.changes),
                    "employee_payment_cells": employee_payment_changes,
                    "analyst_payment_cells": protected_changes,
                }
            },
            summary={
                "source_files": len(run.snapshot.personals),
                "master_cells_written": len(run.plan.changes),
                "employee_payment_cells": employee_payment_changes,
                "analyst_payment_cells": protected_changes,
            },
        )

    with publication_cycle_lock(bundle) as cycle_lock:
        publication = publish_validated(
            bundle,
            run.stage,
            run.plan,
            run.validation,
            confirmation_digest=confirmation_digest,
            busy_mode=str(bundle.app.manual_upward["busy_file_policy"]),
            allow_initial_baseline=run.allow_initial_baseline,
            backup_directory=backup_directory,
            backup_label=backup_label,
            commit_callback=commit_receipt,
            cycle_lock=cycle_lock,
        )
    return replace(run, publication=publication)


def _scope_summary(bundle: ConfigBundle, plan: SyncPlan) -> tuple[int, int]:
    settings = bundle.app.manual_upward
    protected = {
        norm_title(value)
        for value in settings["forbidden_master_titles"]
    }
    protected_changes = sum(
        norm_title(change.title) in protected
        for change in plan.changes
    )
    payment_titles = set()
    if bundle.fields.month_rollover is not None:
        payment_titles = {
            norm_title(title)
            for title in bundle.fields.month_rollover.employee_clear_titles
        }
    employee_payment_changes = sum(
        norm_title(change.title) in payment_titles
        for change in plan.changes
    )
    return employee_payment_changes, protected_changes


def format_summary(bundle: ConfigBundle, run: ManualUpwardRun) -> str:
    """Short operator-facing digest; detailed evidence remains in ``run_dir``."""
    counts = Counter(issue.severity for issue in run.plan.issues)
    payment_changes, protected_changes = _scope_summary(bundle, run.plan)
    lines = [
        "РУЧНОЙ ПЕРЕНОС НАВЕРХ",
        f"Запуск: {run.snapshot.run_id}",
        f"Baseline: {run.baseline_label}",
        f"Источников .xlsb: {len(run.snapshot.personals)}",
        f"Изменений общего реестра: {len(run.plan.changes)}",
        f"Из них последний платёж сотрудника: {payment_changes}",
        f"Аналитические платежи в плане: {protected_changes}",
        f"Ошибок: {counts['error']} · предупреждений: {counts['warning']}",
        f"Digest: {run.plan.digest}",
        f"Отчёт: {run.snapshot.run_dir / 'plan.txt'}",
    ]
    if run.validation is not None:
        lines.append(
            "Validation: "
            + ("ЗАБЛОКИРОВАНА" if run.validation.blocked else "ПРОЙДЕНА")
        )
    if run.allow_initial_baseline:
        lines.append("Baseline текущего месяца: будет создан при публикации")
    if run.publication is not None:
        lines.append(
            "Публикация: "
            + ("ГОТОВО" if run.publication.complete else "НЕ ЗАВЕРШЕНА")
        )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="manual-upward",
        description="Ручной перенос портфелей сотрудников в общий реестр",
    )
    parser.add_argument("--config-dir", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument(
        "--prepare",
        action="store_true",
        help="только создать и проверить staging; ничего не публиковать",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="опубликовать зелёный staging; требует --confirm-digest",
    )
    parser.add_argument("--confirm-digest")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.prepare and args.publish:
        raise SystemExit("--prepare и --publish нельзя использовать вместе")
    if args.publish and not args.confirm_digest:
        raise SystemExit("для --publish нужен --confirm-digest")

    try:
        bundle = load_config_bundle(args.config_dir)
        run = prepare_manual_upward(
            bundle,
            run_id=args.run_id,
            progress=lambda message: print(message, flush=True),
        )
        print(format_summary(bundle, run))
        if not run.ready_to_publish:
            return 2
        if args.prepare:
            return 0

        if args.publish:
            run = publish_prepared_manual_upward(
                bundle,
                run,
                confirmation_digest=args.confirm_digest,
            )
            print(format_summary(bundle, run))
            return 0 if run.publication and run.publication.complete else 2

        phrase = str(bundle.app.manual_upward["confirmation_phrase"])
        answer = input(
            "\nПроверь отчёт. Для публикации введи "
            f"{phrase}; иначе нажми Enter: "
        ).strip()
        if answer != phrase:
            print("Не опубликовано. Staging и отчёт сохранены локально.")
            return 0
        print("Публикую общий реестр. Не закрывай это окно…", flush=True)
        run = publish_prepared_manual_upward(
            bundle,
            run,
            confirmation_digest=run.plan.digest,
        )
        print(format_summary(bundle, run))
        return 0 if run.publication and run.publication.complete else 2
    except RegistryCycleError as exc:
        print(f"СТОП: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
