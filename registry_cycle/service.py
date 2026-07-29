"""Оркестрация discovery → стабильные копии → чистый план."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from .baseline import CycleBaseline, load_baseline
from .config import ConfigBundle
from .directions import validate_master_header_directions
from .discovery import discover
from .errors import SafetyViolation, WorkbookReadError
from .models import DiscoveryReport, Issue, SyncPlan
from .ownership import manifest_owner_matches, resolve_book_responsible
from .planner import build_sync_plan
from .reader import read_portfolio
from .reporting import write_plan_reports
from .rollover import resolve_rollover_month
from .snapshot import (
    SnapshotSet,
    assert_snapshot_config_identity,
    assert_snapshot_run_dir,
    capture_sources,
    load_snapshot,
)


@dataclass(frozen=True, slots=True)
class PreparedRun:
    discovery: DiscoveryReport
    snapshot: SnapshotSet
    plan: SyncPlan
    baseline: CycleBaseline | None = None


class CycleService:
    def __init__(self, bundle: ConfigBundle):
        self.bundle = bundle

    def discover(self) -> DiscoveryReport:
        return discover(self.bundle)

    def prepare(
        self,
        *,
        run_id: str | None = None,
        rollover_month: str | None = None,
        now: datetime | None = None,
    ) -> PreparedRun:
        now = now or datetime.now()
        resolved_rollover = resolve_rollover_month(
            self.bundle.fields,
            explicit=rollover_month,
            at=now,
            automatic=True,
        )
        discovery_report = self.discover()
        if discovery_report.blocked:
            reasons = "; ".join(
                issue.message
                for issue in discovery_report.issues
                if issue.severity == "error"
            )
            raise SafetyViolation(f"manifest не прошёл проверку: {reasons}")

        snapshot = capture_sources(
            self.bundle,
            discovery_report,
            run_id=run_id,
            now=now,
        )
        return self.prepare_snapshot(
            snapshot,
            discovery_report,
            rollover_month=resolved_rollover,
        )

    def prepare_existing(
        self,
        snapshot_path,
        *,
        rollover_month: str | None = None,
    ) -> PreparedRun:
        snapshot = load_snapshot(snapshot_path)
        discovery_report = DiscoveryReport(
            entries=[personal.entry for personal in snapshot.personals]
        )
        return self.prepare_snapshot(
            snapshot,
            discovery_report,
            rollover_month=rollover_month,
        )

    def prepare_snapshot(
        self,
        snapshot: SnapshotSet,
        discovery_report: DiscoveryReport,
        *,
        rollover_month: str | None = None,
    ) -> PreparedRun:
        assert_snapshot_config_identity(self.bundle, snapshot)
        assert_snapshot_run_dir(self.bundle, snapshot)
        master_local = read_portfolio(snapshot.master_copy, self.bundle.fields)
        direction_issues = validate_master_header_directions(
            snapshot.master_copy,
            master_local,
            self.bundle.fields,
        )
        master = replace(
            master_local,
            path=snapshot.master_original,
            fingerprint=snapshot.master_fingerprint,
        )
        baseline = (
            load_baseline(
                snapshot.baseline_copy,
                expected_master_path=snapshot.master_original,
            )
            if snapshot.baseline_copy is not None
            else None
        )

        books = []
        read_issues: list[Issue] = []
        for personal in snapshot.personals:
            try:
                local_book = read_portfolio(personal.copy_path, self.bundle.fields)
            except WorkbookReadError as exc:
                read_issues.append(
                    Issue(
                        "error",
                        "personal_read_failed",
                        f"не прочитан личный файл: {personal.entry.path.name}",
                        {"path": str(personal.entry.path), "error": str(exc)},
                    )
                )
                continue
            responsibles = local_book.unique_responsibles(
                self.bundle.fields.responsible_title
            )
            responsible = resolve_book_responsible(
                personal.entry,
                local_book,
                self.bundle.fields.responsible_title,
            )
            if (
                responsible is not None
                and not manifest_owner_matches(
                    self.bundle,
                    personal.entry,
                    responsible,
                )
            ):
                read_issues.append(
                    Issue(
                        "error",
                        "manifest_owner_mismatch",
                        "ответственный внутри книги не совпал с папкой "
                        "или именем файла",
                        {
                            "path": str(personal.entry.path),
                            "employee_folder": (
                                personal.entry.employee_folder
                            ),
                            "responsible_hint": (
                                personal.entry.responsible_hint
                            ),
                            "book_responsible": responsible,
                            "responsible_source": (
                                "manifest"
                                if not responsibles
                                else "workbook"
                            ),
                        },
                    )
                )
                continue
            books.append(
                (
                    personal.entry,
                    replace(
                        local_book,
                        path=personal.entry.path,
                        fingerprint=personal.fingerprint,
                    ),
                )
            )

        plan = build_sync_plan(
            master,
            books,
            self.bundle.fields,
            baseline=baseline,
            multi_owner_policy=str(
                self.bundle.app.safety.get(
                    "multi_owner_case_policy",
                    "block",
                )
            ),
            responsible_aliases=self.bundle.app.responsible_aliases,
            rollover_month=rollover_month,
            config_identity=self.bundle.config_identity,
        )
        plan.issues[:0] = [
            *discovery_report.issues,
            *read_issues,
            *direction_issues,
        ]
        if len(plan.changes) > int(
            self.bundle.app.safety.get("max_master_cell_changes", 20000)
        ):
            plan.issues.append(
                Issue(
                    "error",
                    "master_change_limit_exceeded",
                    "план превысил защитный лимит изменений общего реестра",
                    {
                        "changes": len(plan.changes),
                        "limit": self.bundle.app.safety.get(
                            "max_master_cell_changes",
                            20000,
                        ),
                    },
                )
            )
        write_plan_reports(snapshot.run_dir, plan)
        return PreparedRun(discovery_report, snapshot, plan, baseline)
