from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import registry_cycle.manual_downward as manual_downward
from registry_cycle.config import load_config_bundle
from registry_cycle.models import (
    CellChange,
    DiscoveryReport,
    FileFingerprint,
    ManifestEntry,
    PersonalProjection,
    SyncPlan,
)
from registry_cycle.snapshot import CapturedPersonal, SnapshotSet


def _fingerprint(value: str = "a") -> FileFingerprint:
    return FileFingerprint(size=1, mtime_ns=1, sha256=value * 64)


def _entry(path: Path) -> ManifestEntry:
    return ManifestEntry(
        entry_id="entry-1",
        path=path,
        country="Узбекистан",
        group="Самарканд + курьеры",
        profile="hard",
        employee_folder="Иванов Иван",
        responsible_hint="не используется",
    )


def _snapshot(tmp_path: Path, bundle) -> tuple[SnapshotSet, ManifestEntry]:
    run_dir = tmp_path / "runs" / "downward-test"
    source_dir = run_dir / "sources"
    source_dir.mkdir(parents=True)
    master = source_dir / "master.xlsb"
    personal = source_dir / "entry-1.xlsb"
    master.write_bytes(b"master")
    personal.write_bytes(b"personal")
    entry = _entry(tmp_path / "live" / "Иванов Иван.xlsb")
    return (
        SnapshotSet(
            run_id="downward-test",
            run_dir=run_dir,
            master_original=tmp_path / "live-master.xlsb",
            master_copy=master,
            master_fingerprint=_fingerprint(),
            personals=(CapturedPersonal(entry, personal, _fingerprint()),),
            config_identity=bundle.config_identity,
        ),
        entry,
    )


def _projection(entry: ManifestEntry) -> PersonalProjection:
    return PersonalProjection(
        entry=entry,
        responsible="Иванов Иван",
        headers=(
            "Номер КД / ID",
            "ответственный Сотрудник",
            "Сумма последнего платежа",
        ),
        rows=(("1", "Иванов Иван", 100),),
        source_fingerprint=_fingerprint(),
    )


def _delta(entry: ManifestEntry) -> manual_downward.ProjectionDelta:
    return manual_downward.ProjectionDelta(
        entry_id=entry.entry_id,
        path=entry.path,
        country=entry.country,
        group=entry.group,
        profile=entry.profile,
        employee="Иванов Иван",
        rows_before=0,
        rows_after=1,
        changed_cells=3,
        added_rows=1,
        removed_rows=0,
        employee_payment_cells=1,
        changed_employee_payment_cells=1,
    )


def test_downward_scope_rejects_analyst_payment_columns():
    bundle = load_config_bundle()
    entry = _entry(Path("personal.xlsb"))
    projection = replace(
        _projection(entry),
        headers=("Номер КД / ID", "Текущие платежи"),
        rows=(("1", 500),),
    )
    plan = SyncPlan(
        master_path=Path("master.xlsb"),
        master_fingerprint=_fingerprint(),
        projections=[projection],
        config_identity=bundle.config_identity,
    )

    manual_downward._enforce_downward_scope(bundle, plan)

    assert plan.blocked
    assert plan.issues[-1].code == "manual_downward_analyst_payments_exposed"


def test_downward_requires_step_one_before_any_staging(tmp_path, monkeypatch):
    bundle = load_config_bundle()
    snapshot, entry = _snapshot(tmp_path, bundle)
    plan = SyncPlan(
        master_path=snapshot.master_original,
        master_fingerprint=snapshot.master_fingerprint,
        projections=[_projection(entry)],
        config_identity=bundle.config_identity,
    )
    plan.changes.append(
        CellChange(
            target="master",
            path=snapshot.master_original,
            sheet="Портфели",
            row=3,
            column=2,
            title="Сумма последнего платежа",
            contract_id="1",
            before=None,
            after=100,
            source_path=entry.path,
            source_row=3,
        )
    )
    calls = {}
    monkeypatch.setattr(
        manual_downward,
        "discover",
        lambda _bundle: DiscoveryReport(entries=[entry]),
    )
    monkeypatch.setattr(
        manual_downward,
        "capture_sources",
        lambda *_args, **_kwargs: snapshot,
    )

    class FakeService:
        def __init__(self, _bundle):
            pass

        def prepare_snapshot(self, *args, **kwargs):
            calls.update(kwargs)
            return SimpleNamespace(plan=plan)

    monkeypatch.setattr(manual_downward, "CycleService", FakeService)
    monkeypatch.setattr(
        manual_downward,
        "_projection_delta",
        lambda *_args: _delta(entry),
    )
    monkeypatch.setattr(
        manual_downward,
        "build_staging",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("staging")),
    )

    run = manual_downward.prepare_manual_downward(bundle, run_id="downward-test")

    assert run.plan.blocked
    assert any(
        issue.code == "manual_downward_upward_required"
        for issue in run.plan.issues
    )
    assert run.stage is None
    assert calls == {
        "rollover_month": None,
        "include_projections": True,
        "baseline_override": None,
        "conflict_resolutions": (),
        "master_conflict_policy": "employee_wins",
    }


def test_downward_prepares_full_projection_as_a_separate_step(tmp_path, monkeypatch):
    bundle = load_config_bundle()
    snapshot, entry = _snapshot(tmp_path, bundle)
    plan = SyncPlan(
        master_path=snapshot.master_original,
        master_fingerprint=snapshot.master_fingerprint,
        projections=[_projection(entry)],
        config_identity=bundle.config_identity,
    )
    monkeypatch.setattr(
        manual_downward,
        "discover",
        lambda _bundle: DiscoveryReport(entries=[entry]),
    )
    monkeypatch.setattr(
        manual_downward,
        "capture_sources",
        lambda *_args, **_kwargs: snapshot,
    )
    monkeypatch.setattr(
        manual_downward,
        "CycleService",
        lambda _bundle: SimpleNamespace(
            prepare_snapshot=lambda *_args, **_kwargs: SimpleNamespace(plan=plan)
        ),
    )
    monkeypatch.setattr(
        manual_downward,
        "_projection_delta",
        lambda *_args: _delta(entry),
    )
    stage = SimpleNamespace()
    validation = SimpleNamespace(blocked=False)
    monkeypatch.setattr(
        manual_downward,
        "build_staging",
        lambda *_args, **_kwargs: stage,
    )
    monkeypatch.setattr(
        manual_downward,
        "validate_staging",
        lambda *_args, **_kwargs: validation,
    )

    run = manual_downward.prepare_manual_downward(bundle, run_id="downward-test")

    assert run.stage is stage
    assert run.validation is validation
    assert run.ready_to_publish


def test_downward_staging_excludes_captured_book_without_projection(
    tmp_path,
):
    bundle = load_config_bundle()
    snapshot, entry = _snapshot(tmp_path, bundle)
    skipped_entry = replace(
        entry,
        entry_id="empty-entry",
        path=tmp_path / "live" / "Пустой.xlsb",
    )
    skipped_copy = snapshot.run_dir / "sources" / "empty-entry.xlsb"
    skipped_copy.write_bytes(b"empty")
    snapshot = replace(
        snapshot,
        personals=(
            *snapshot.personals,
            CapturedPersonal(
                skipped_entry,
                skipped_copy,
                _fingerprint("b"),
            ),
        ),
    )
    plan = SyncPlan(
        master_path=snapshot.master_original,
        master_fingerprint=snapshot.master_fingerprint,
        projections=[_projection(entry)],
        config_identity=bundle.config_identity,
    )

    staged = manual_downward._projected_snapshot(snapshot, plan)

    assert [item.entry.entry_id for item in staged.personals] == [
        entry.entry_id
    ]


def test_downward_publish_selects_only_changed_personals(tmp_path, monkeypatch):
    bundle = load_config_bundle()
    snapshot, entry = _snapshot(tmp_path, bundle)
    plan = SyncPlan(
        master_path=snapshot.master_original,
        master_fingerprint=snapshot.master_fingerprint,
        projections=[_projection(entry)],
        config_identity=bundle.config_identity,
    )
    personal_stage = SimpleNamespace(
        entry_id=entry.entry_id,
        original_path=entry.path,
        kind="personal",
    )
    stage = SimpleNamespace(
        master=SimpleNamespace(original_path=snapshot.master_original),
        personals=(personal_stage,),
    )
    run = manual_downward.ManualDownwardRun(
        discovery=DiscoveryReport(entries=[entry]),
        snapshot=snapshot,
        plan=plan,
        deltas=(_delta(entry),),
        stage=stage,
        validation=SimpleNamespace(blocked=False),
    )
    archive = tmp_path / "archive"
    archive.mkdir()
    receipt = tmp_path / "receipts"
    receipt.mkdir()
    settings = dict(bundle.app.manual_downward)
    settings["backup"] = {
        **settings["backup"],
        "directory": str(archive),
        "receipt_directory": str(receipt),
    }
    bundle = replace(
        bundle,
        app=replace(bundle.app, manual_downward=settings),
    )
    calls = {}
    monkeypatch.setattr(
        manual_downward,
        "_backup_directories",
        lambda *_args: {str(entry.path): archive},
    )
    monkeypatch.setattr(
        manual_downward,
        "publication_cycle_lock",
        lambda _bundle: nullcontext("lock"),
    )

    def fake_publish(*_args, **kwargs):
        calls.update(kwargs)
        return SimpleNamespace(complete=True)

    monkeypatch.setattr(
        manual_downward,
        "publish_selected_personals_validated",
        fake_publish,
    )

    published = manual_downward.publish_prepared_manual_downward(
        bundle,
        run,
        confirmation_digest=plan.digest,
    )

    assert calls["selected_files"] == (personal_stage,)
    assert calls["busy_mode"] == "block"
    assert calls["confirmation_digest"] == plan.digest
    assert callable(calls["commit_callback"])
    assert published.publication.complete
