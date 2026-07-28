"""Непубликуемая репетиция первого baseline на зафиксированных копиях."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from .baseline import baseline_path, build_baseline, load_baseline, save_baseline
from .config import ConfigBundle
from .errors import SafetyViolation
from .files import fingerprint
from .models import DiscoveryReport
from .publishing import PublishResult, publish_validated
from .reader import read_portfolio
from .service import CycleService, PreparedRun
from .snapshot import SnapshotSet, load_snapshot
from .staging import StageSet, build_staging, load_stage
from .validation import ValidationReport, validate_staging


@dataclass(frozen=True, slots=True)
class RehearsalOutcome:
    run_dir: Path
    prepared: PreparedRun
    stage: StageSet | None = None
    validation: ValidationReport | None = None

    @property
    def blocked(self) -> bool:
        return self.prepared.plan.blocked or bool(
            self.validation and self.validation.blocked
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "candidate_first_baseline_rehearsal",
            "publishable": False,
            "run_dir": str(self.run_dir),
            "plan": self.prepared.plan.as_dict(),
            "stage": self.stage.as_dict() if self.stage else None,
            "validation": (
                self.validation.as_dict()
                if self.validation
                else None
            ),
            "blocked": self.blocked,
        }


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def execute_first_baseline_rehearsal(
    bundle: ConfigBundle,
    snapshot_path: str | Path,
    *,
    now: datetime | None = None,
    rollover_month: str | None = None,
) -> RehearsalOutcome:
    """Проверить предполагаемый первый цикл, не создавая active baseline."""
    source = load_snapshot(snapshot_path)
    now = now or datetime.now()
    run_dir = (
        source.run_dir
        / "rehearsals"
        / f"candidate-{now:%Y%m%d-%H%M%S-%f}"
    )
    run_dir.mkdir(parents=True, exist_ok=False)

    local_master = read_portfolio(source.master_copy, bundle.fields)
    master = replace(
        local_master,
        path=source.master_original,
        fingerprint=source.master_fingerprint,
    )
    candidate = build_baseline(
        master,
        bundle.fields,
        plan_digest=(
            "candidate-first-baseline:"
            + source.master_fingerprint.sha256
        ),
        created_at=now,
    )
    candidate_path = save_baseline(
        run_dir / "candidate-baseline.json.gz",
        candidate,
    )
    rehearsal_snapshot = SnapshotSet(
        run_id=f"{source.run_id}-candidate",
        run_dir=run_dir,
        master_original=source.master_original,
        master_copy=source.master_copy,
        master_fingerprint=source.master_fingerprint,
        personals=source.personals,
        baseline_copy=candidate_path,
        baseline_fingerprint=fingerprint(candidate_path),
    )
    discovery = DiscoveryReport(
        entries=[item.entry for item in source.personals]
    )
    prepared = CycleService(bundle).prepare_snapshot(
        rehearsal_snapshot,
        discovery,
        rollover_month=rollover_month,
    )
    if prepared.plan.blocked:
        outcome = RehearsalOutcome(run_dir, prepared)
        _atomic_json(run_dir / "rehearsal.json", outcome.as_dict())
        return outcome

    try:
        stage = build_staging(
            bundle,
            rehearsal_snapshot,
            prepared.plan,
            output_dir=run_dir / "staging",
        )
        validation = validate_staging(
            bundle,
            stage,
            prepared.plan,
        )
    except Exception as exc:
        _atomic_json(
            run_dir / "failure.json",
            {
                "kind": "candidate_first_baseline_rehearsal",
                "publishable": False,
                "phase": (
                    "validation"
                    if "stage" in locals()
                    else "staging"
                ),
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        raise SafetyViolation(
            f"непубликуемая репетиция остановлена на фазе "
            f"{'validation' if 'stage' in locals() else 'staging'}: {exc}"
        ) from exc
    outcome = RehearsalOutcome(
        run_dir,
        prepared,
        stage,
        validation,
    )
    _atomic_json(run_dir / "rehearsal.json", outcome.as_dict())
    return outcome


def promote_first_baseline_rehearsal(
    bundle: ConfigBundle,
    snapshot_path: str | Path,
    stage_path: str | Path,
    candidate_baseline_path: str | Path,
    *,
    confirmation_digest: str,
    rollover_month: str | None = None,
    busy_mode: str | None = None,
) -> PublishResult:
    """Явно опубликовать зелёную одноразовую first-baseline репетицию."""
    source = load_snapshot(snapshot_path)
    if source.baseline_copy is not None:
        raise SafetyViolation(
            "snapshot уже содержит baseline; одноразовое продвижение запрещено"
        )
    active_baseline = baseline_path(bundle, source.master_original)
    if active_baseline.exists():
        raise SafetyViolation(
            "активный baseline уже существует; используй обычный цикл"
        )
    candidate_path = Path(candidate_baseline_path)
    candidate = load_baseline(
        candidate_path,
        expected_master_path=source.master_original,
    )
    expected_candidate_digest = (
        "candidate-first-baseline:"
        + source.master_fingerprint.sha256
    )
    if candidate.plan_digest != expected_candidate_digest:
        raise SafetyViolation(
            "candidate baseline относится к другому снимку общего реестра"
        )

    stage = load_stage(stage_path)
    if stage.master.source_copy.resolve() != source.master_copy.resolve():
        raise SafetyViolation(
            "staging и snapshot используют разные копии общего реестра"
        )
    source_personals = {
        item.entry.entry_id: item.copy_path.resolve()
        for item in source.personals
    }
    staged_personals = {
        item.entry_id: item.source_copy.resolve()
        for item in stage.personals
    }
    if source_personals != staged_personals:
        raise SafetyViolation(
            "staging и snapshot используют разные личные копии"
        )

    promotion_snapshot = SnapshotSet(
        run_id=stage.run_id,
        run_dir=stage.run_dir,
        master_original=source.master_original,
        master_copy=source.master_copy,
        master_fingerprint=source.master_fingerprint,
        personals=source.personals,
        baseline_copy=candidate_path,
        baseline_fingerprint=fingerprint(candidate_path),
    )
    discovery = DiscoveryReport(
        entries=[item.entry for item in source.personals]
    )
    prepared = CycleService(bundle).prepare_snapshot(
        promotion_snapshot,
        discovery,
        rollover_month=rollover_month,
    )
    if prepared.plan.blocked:
        raise SafetyViolation(
            "план first-baseline продвижения заблокирован"
        )
    if prepared.plan.digest != confirmation_digest:
        raise SafetyViolation(
            "digest подтверждения не совпал с пересобранным планом"
        )
    validation = validate_staging(
        bundle,
        stage,
        prepared.plan,
    )
    if validation.blocked:
        raise SafetyViolation(
            "повторная validation first-baseline staging не пройдена"
        )
    return publish_validated(
        bundle,
        stage,
        prepared.plan,
        validation,
        confirmation_digest=confirmation_digest,
        busy_mode=busy_mode,
    )
