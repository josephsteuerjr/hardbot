from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import registry_cycle.manual_upward as manual_upward
from registry_cycle.config import load_config_bundle
from registry_cycle.models import (
    CellChange,
    DiscoveryReport,
    FileFingerprint,
    ManifestEntry,
    SyncPlan,
)
from registry_cycle.snapshot import CapturedPersonal, SnapshotSet


def _fingerprint() -> FileFingerprint:
    return FileFingerprint(size=1, mtime_ns=1, sha256="a" * 64)


def _entry(path: Path) -> ManifestEntry:
    return ManifestEntry(
        entry_id="entry-1",
        path=path,
        country="Тест",
        group="Город",
        profile="hard",
        employee_folder="Иванов Иван",
        responsible_hint="Иванов Иван",
    )


def _plan(bundle, *, title: str) -> SyncPlan:
    path = Path("master.xlsb")
    plan = SyncPlan(
        master_path=path,
        master_fingerprint=_fingerprint(),
        config_identity=bundle.config_identity,
    )
    plan.changes.append(
        CellChange(
            target="master",
            path=path,
            sheet="Портфели",
            row=1,
            column=1,
            title=title,
            contract_id="KD-1",
            before=None,
            after="value",
            source_path=Path("personal.xlsb"),
            source_row=1,
        )
    )
    return plan


def test_manual_scope_blocks_analyst_payment_columns():
    bundle = load_config_bundle()
    plan = _plan(bundle, title="Текущие платежи")

    manual_upward._enforce_manual_scope(bundle, plan)

    assert plan.blocked
    assert plan.issues[-1].code == "manual_upward_forbidden_master_field"


def test_manual_scope_allows_employee_last_payment():
    bundle = load_config_bundle()
    plan = _plan(bundle, title="Сумма последнего платежа")

    manual_upward._enforce_manual_scope(bundle, plan)

    assert not plan.blocked


def test_prepare_manual_upward_disables_rollover_and_personal_staging(
    tmp_path,
    monkeypatch,
):
    bundle = load_config_bundle()
    run_dir = tmp_path / "runs" / "manual-test"
    run_dir.mkdir(parents=True)
    source = run_dir / "sources" / "entry-1.xlsb"
    source.parent.mkdir()
    source.write_bytes(b"source")
    master = run_dir / "sources" / "master.xlsb"
    master.write_bytes(b"master")
    entry = _entry(Path("personal.xlsb"))
    snapshot = SnapshotSet(
        run_id="manual-test",
        run_dir=run_dir,
        master_original=Path("master.xlsb"),
        master_copy=master,
        master_fingerprint=_fingerprint(),
        personals=(CapturedPersonal(entry, source, _fingerprint()),),
        config_identity=bundle.config_identity,
    )
    plan = _plan(bundle, title="Сумма последнего платежа")
    calls = {}

    monkeypatch.setattr(
        manual_upward,
        "discover",
        lambda _bundle: DiscoveryReport(entries=[entry]),
    )
    monkeypatch.setattr(
        manual_upward,
        "capture_sources",
        lambda *_args, **_kwargs: snapshot,
    )
    monkeypatch.setattr(
        manual_upward,
        "_write_run_context",
        lambda *_args, **_kwargs: None,
    )

    class FakeService:
        def __init__(self, _bundle):
            pass

        def prepare_snapshot(self, *args, **kwargs):
            calls["prepare"] = kwargs
            return SimpleNamespace(plan=plan)

    monkeypatch.setattr(manual_upward, "CycleService", FakeService)

    def fake_stage(_bundle, staged_snapshot, staged_plan, **_kwargs):
        calls["staged_snapshot"] = staged_snapshot
        calls["staged_plan"] = staged_plan
        return SimpleNamespace()

    monkeypatch.setattr(manual_upward, "build_staging", fake_stage)
    monkeypatch.setattr(
        manual_upward,
        "validate_staging",
        lambda *_args, **_kwargs: SimpleNamespace(blocked=False),
    )

    run = manual_upward.prepare_manual_upward(
        bundle,
        run_id="manual-test",
    )

    assert calls["prepare"] == {
        "rollover_month": None,
        "include_projections": False,
        "baseline_override": None,
        "conflict_resolutions": (),
        "master_conflict_policy": "employee_wins",
    }
    assert calls["staged_snapshot"].personals == ()
    assert calls["staged_plan"] is plan
    assert run.ready_to_publish
    assert run.baseline_label == "не участвует: личный реестр имеет приоритет"
    assert run.allow_initial_baseline is True


def test_manual_publish_uses_configured_central_backup_and_allows_first_baseline(
    tmp_path,
):
    bundle = load_config_bundle()
    backup_directory = tmp_path / "central-backups"
    backup_directory.mkdir()
    receipt_directory = tmp_path / "receipts"
    receipt_directory.mkdir()
    settings = dict(bundle.app.manual_upward)
    settings["backup"] = {
        "directory": str(backup_directory),
        "label": "до ручного переноса",
        "receipt_directory": str(receipt_directory),
    }
    bundle = replace(
        bundle,
        app=replace(bundle.app, manual_upward=settings),
    )
    run = manual_upward.ManualUpwardRun(
        discovery=DiscoveryReport(),
        snapshot=SimpleNamespace(personals=()),
        plan=SimpleNamespace(blocked=False, digest="d" * 64, changes=[]),
        stage=SimpleNamespace(
            master=SimpleNamespace(
                original_path=tmp_path / "live" / "master.xlsb"
            )
        ),
        validation=SimpleNamespace(blocked=False),
        allow_initial_baseline=True,
    )
    calls = {}

    def fake_publish(*_args, **kwargs):
        calls.update(kwargs)
        return SimpleNamespace(complete=True)

    original_lock = manual_upward.publication_cycle_lock
    original_publish = manual_upward.publish_validated
    try:
        manual_upward.publication_cycle_lock = lambda _bundle: nullcontext("lock")
        manual_upward.publish_validated = fake_publish
        published = manual_upward.publish_prepared_manual_upward(
            bundle,
            run,
            confirmation_digest="d" * 64,
        )
    finally:
        manual_upward.publication_cycle_lock = original_lock
        manual_upward.publish_validated = original_publish

    assert calls["allow_initial_baseline"] is True
    assert calls["confirmation_digest"] == "d" * 64
    assert calls["backup_directory"] == backup_directory
    assert calls["backup_label"] == "до ручного переноса"
    assert published.publication.complete
