"""Полный unattended-проход с lock, журналом и owner-only уведомлением."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .config import ConfigBundle
from .cycle_lock import CycleLock
from .errors import SafetyViolation
from .file_access import busy_files
from .force_close import DelegatedForceCloser
from .notifications import NotificationResult, OwnerNotifier
from .publishing import PublishResult, publish_validated
from .service import CycleService, PreparedRun
from .staging import StageSet, build_staging
from .validation import ValidationReport, validate_staging

RunnerProgress = Callable[[str, int, int], None]


@dataclass(frozen=True, slots=True)
class CycleOutcome:
    status: str
    prepared: PreparedRun
    stage: StageSet | None = None
    validation: ValidationReport | None = None
    publication: PublishResult | None = None
    notification: NotificationResult | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "run_id": self.prepared.snapshot.run_id,
            "run_dir": str(self.prepared.snapshot.run_dir),
            "plan_digest": self.prepared.plan.digest,
            "plan_blocked": self.prepared.plan.blocked,
            "stage": self.stage.as_dict() if self.stage else None,
            "validation": self.validation.as_dict() if self.validation else None,
            "publication": (
                self.publication.as_dict()
                if self.publication
                else None
            ),
            "notification": (
                {
                    "sent": self.notification.sent,
                    "skipped": self.notification.skipped,
                    "detail": self.notification.detail,
                }
                if self.notification
                else None
            ),
        }


def _atomic_outcome(run_dir: Path, outcome: CycleOutcome) -> None:
    path = run_dir / "outcome.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(outcome.as_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _summary(outcome: CycleOutcome) -> str:
    plan = outcome.prepared.plan
    errors = sum(issue.severity == "error" for issue in plan.issues)
    lines = [
        f"Контур · Реестры — {outcome.status}",
        f"Запуск: {outcome.prepared.snapshot.run_id}",
        f"Изменений мастера: {len(plan.changes)}",
        f"Портфелей: {len(plan.projections)}",
        f"Ошибок плана: {errors}",
    ]
    if plan.month_rollover is not None:
        lines.append(
            "Закрытие месяца: "
            f"{plan.month_rollover.closing_month}, "
            f"оплат {len(plan.month_rollover.archive_cells)}"
        )
    if outcome.validation is not None:
        lines.append(
            "Validation: "
            + ("ошибка" if outcome.validation.blocked else "пройдено")
        )
    if outcome.publication is not None:
        lines.append(f"Опубликовано файлов: {len(outcome.publication.published)}")
        lines.append(f"Sidecar-файлов: {len(outcome.publication.sidecars)}")
    return "\n".join(lines)


def execute_cycle(
    bundle: ConfigBundle,
    *,
    publish: bool,
    scheduled: bool = False,
    run_id: str | None = None,
    progress: RunnerProgress | None = None,
    busy_mode: str | None = None,
    rollover_month: str | None = None,
) -> CycleOutcome:
    if scheduled and not bool(bundle.app.scheduler.get("enabled", False)):
        raise SafetyViolation("запуск из планировщика отключён в config")
    lock_path = bundle.workspace_path() / "cycle.lock"
    stale = int(bundle.app.safety.get("lock_stale_seconds", 21600))
    notifier = OwnerNotifier(bundle)
    prepared: PreparedRun | None = None
    selected_busy_mode = (
        busy_mode
        if busy_mode is not None
        else str(bundle.app.safety.get("busy_file_policy", "block"))
    )
    try:
        with CycleLock(lock_path, stale_seconds=stale):
            service = CycleService(bundle)
            if publish and selected_busy_mode == "force_close":
                discovery = service.discover()
                if discovery.blocked:
                    reasons = "; ".join(
                        issue.message
                        for issue in discovery.issues
                        if issue.severity == "error"
                    )
                    raise SafetyViolation(
                        f"manifest не прошёл проверку до force_close: {reasons}"
                    )
                targets = (
                    bundle.master_path(),
                    *(entry.path for entry in discovery.entries),
                )
                occupied = busy_files(tuple(targets))
                if occupied:
                    DelegatedForceCloser(bundle).close(
                        occupied,
                        audit_dir=(
                            bundle.workspace_path()
                            / "force-close"
                            / "pre-snapshot"
                        ),
                    )
            # При force_close snapshot принципиально снимается уже после
            # освобождения файлов.
            prepared = service.prepare(
                run_id=run_id,
                rollover_month=rollover_month,
            )
            if prepared.plan.blocked:
                outcome = CycleOutcome("ПЛАН ЗАБЛОКИРОВАН", prepared)
                notification = notifier.send(_summary(outcome))
                outcome = CycleOutcome(
                    outcome.status,
                    prepared,
                    notification=notification,
                )
                _atomic_outcome(prepared.snapshot.run_dir, outcome)
                return outcome

            stage = build_staging(
                bundle,
                prepared.snapshot,
                prepared.plan,
                progress=progress,
            )
            validation = validate_staging(
                bundle,
                stage,
                prepared.plan,
                progress=progress,
            )
            if validation.blocked:
                outcome = CycleOutcome(
                    "VALIDATION ЗАБЛОКИРОВАН",
                    prepared,
                    stage,
                    validation,
                )
                notification = notifier.send(_summary(outcome))
                outcome = CycleOutcome(
                    outcome.status,
                    prepared,
                    stage,
                    validation,
                    notification=notification,
                )
                _atomic_outcome(prepared.snapshot.run_dir, outcome)
                return outcome

            publication = (
                publish_validated(
                    bundle,
                    stage,
                    prepared.plan,
                    validation,
                    confirmation_digest=prepared.plan.digest,
                    busy_mode=selected_busy_mode,
                )
                if publish
                else None
            )
            status = (
                "ОПУБЛИКОВАНО"
                if publication and publication.complete
                else (
                    "ОПУБЛИКОВАНО С SIDECAR"
                    if publication
                    else "DRY-RUN ПРОЙДЕН"
                )
            )
            outcome = CycleOutcome(
                status,
                prepared,
                stage,
                validation,
                publication,
            )
            notification = notifier.send(_summary(outcome))
            outcome = CycleOutcome(
                status,
                prepared,
                stage,
                validation,
                publication,
                notification,
            )
            _atomic_outcome(prepared.snapshot.run_dir, outcome)
            return outcome
    except Exception as exc:
        notifier.send(
            "Контур · Реестры — СБОЙ\n"
            f"{datetime.now():%d.%m.%Y %H:%M}\n"
            f"{type(exc).__name__}: {exc}"
        )
        raise
