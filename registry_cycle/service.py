"""Оркестрация discovery → стабильные копии → чистый план."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .baseline import CycleBaseline, load_baseline
from .config import ConfigBundle
from .discovery import discover
from .errors import SafetyViolation, WorkbookReadError
from .models import DiscoveryReport, Issue, SyncPlan
from .ownership import manifest_owner_matches
from .planner import build_sync_plan
from .reader import read_portfolio
from .reporting import write_plan_reports
from .snapshot import SnapshotSet, capture_sources, load_snapshot


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

    def prepare(self, *, run_id: str | None = None) -> PreparedRun:
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
        )
        return self.prepare_snapshot(snapshot, discovery_report)

    def prepare_existing(self, snapshot_path) -> PreparedRun:
        snapshot = load_snapshot(snapshot_path)
        discovery_report = DiscoveryReport(
            entries=[personal.entry for personal in snapshot.personals]
        )
        return self.prepare_snapshot(snapshot, discovery_report)

    def prepare_snapshot(
        self,
        snapshot: SnapshotSet,
        discovery_report: DiscoveryReport,
    ) -> PreparedRun:
        master_local = read_portfolio(snapshot.master_copy, self.bundle.fields)
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
            if (
                len(responsibles) == 1
                and not manifest_owner_matches(
                    self.bundle,
                    personal.entry,
                    responsibles[0],
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
                            "book_responsible": responsibles[0],
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
        )
        plan.issues[:0] = [*discovery_report.issues, *read_issues]
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
