from pathlib import Path
import json
import os
import shutil
from types import SimpleNamespace

import pytest

import registry_cycle.publishing as publishing
from registry_cycle.errors import PublishError, SafetyViolation
from registry_cycle.files import fingerprint
from registry_cycle.publishing import (
    assert_no_incomplete_publications,
    atomic_publish_files,
    incomplete_publish_journals,
    publication_cycle_lock,
    publish_validated,
    reconcile_incomplete_publication,
)
from registry_cycle.staging import StageSet, StagedFile
from registry_cycle.validation import ValidationReport


def _staged_file(
    root: Path,
    name: str,
    *,
    kind: str,
    entry_id: str | None,
) -> StagedFile:
    original = root / "live" / f"{name}.bin"
    source = root / "sources" / f"{name}.bin"
    staged = root / "staging" / f"{name}.bin"
    source.parent.mkdir(parents=True, exist_ok=True)
    original.parent.mkdir(parents=True, exist_ok=True)
    staged.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"old-" + name.encode())
    shutil.copy2(source, original)
    staged.write_bytes(b"new-" + name.encode())
    return StagedFile(
        kind=kind,
        entry_id=entry_id,
        original_path=original,
        source_copy=source,
        staged_path=staged,
        source_fingerprint=fingerprint(source),
        staged_fingerprint=fingerprint(staged),
    )


def _stage(tmp_path: Path, *, with_personal: bool = False) -> StageSet:
    master = _staged_file(
        tmp_path,
        "master",
        kind="master",
        entry_id=None,
    )
    personals = (
        (
            _staged_file(
                tmp_path,
                "personal",
                kind="personal",
                entry_id="p1",
            ),
        )
        if with_personal
        else ()
    )
    stage = StageSet(
        run_id="test-run",
        run_dir=tmp_path / "runs" / "test-run",
        staging_dir=tmp_path / "staging",
        plan_digest="a" * 64,
        master=master,
        personals=personals,
    )
    stage.staging_dir.mkdir(parents=True, exist_ok=True)
    (stage.staging_dir / "stage.json").write_text(
        json.dumps(stage.as_dict()),
        encoding="utf-8",
    )
    return stage


def test_atomic_publish_replaces_only_after_preflight(tmp_path):
    stage = _stage(tmp_path)

    result = atomic_publish_files(stage)

    assert result.ok
    assert result.complete
    assert stage.master.original_path.read_bytes() == b"new-master"
    assert len(result.backups) == 1
    assert Path(result.backups[0]).read_bytes() == b"old-master"
    assert (stage.run_dir / "publish.journal.jsonl").is_file()


def test_publish_rejects_run_dir_outside_workspace_runs(tmp_path):
    stage = _stage(tmp_path)
    workspace = tmp_path / "isolated-workspace"
    bundle = SimpleNamespace(
        workspace_path=lambda: workspace,
        app=SimpleNamespace(safety={}),
    )
    plan = SimpleNamespace(
        blocked=False,
        digest=stage.plan_digest,
        config_identity="",
    )
    validation = ValidationReport(
        run_id=stage.run_id,
        plan_digest=stage.plan_digest,
        checked_files=len(stage.files),
    )

    with pytest.raises(SafetyViolation, match="вне workspace/runs"):
        publish_validated(
            bundle,
            stage,
            plan,
            validation,
            confirmation_digest=stage.plan_digest,
        )

    assert not (stage.run_dir / "publish.journal.jsonl").exists()


def test_publish_rejects_config_identity_drift(tmp_path):
    stage = _stage(tmp_path)
    bundle = SimpleNamespace(
        workspace_path=lambda: tmp_path,
        app=SimpleNamespace(safety={}),
        config_identity="current",
    )
    plan = SimpleNamespace(
        blocked=False,
        digest=stage.plan_digest,
        config_identity="old",
    )
    validation = ValidationReport(
        run_id=stage.run_id,
        plan_digest=stage.plan_digest,
        checked_files=len(stage.files),
    )

    with pytest.raises(SafetyViolation, match="другой конфигурации"):
        publish_validated(
            bundle,
            stage,
            plan,
            validation,
            confirmation_digest=stage.plan_digest,
        )


def test_atomic_publish_rejects_changed_target(tmp_path):
    stage = _stage(tmp_path)
    stage.master.original_path.write_bytes(b"employee-edit")

    with pytest.raises(PublishError):
        atomic_publish_files(stage)

    assert stage.master.original_path.read_bytes() == b"employee-edit"


def test_atomic_publish_rolls_back_prior_replacements(tmp_path, monkeypatch):
    stage = _stage(tmp_path, with_personal=True)
    real_replace = publishing.os.replace

    def fail_on_master(source, destination):
        source_path = Path(source)
        destination_path = Path(destination)
        if (
            destination_path == stage.master.original_path
            and "-next" in source_path.name
        ):
            raise OSError("fault injection")
        return real_replace(source, destination)

    monkeypatch.setattr(publishing.os, "replace", fail_on_master)

    with pytest.raises(PublishError):
        atomic_publish_files(stage)

    assert stage.master.original_path.read_bytes() == b"old-master"
    assert stage.personals[0].original_path.read_bytes() == b"old-personal"


def test_publish_rechecks_live_after_backup_before_replace(
    tmp_path,
    monkeypatch,
):
    stage = _stage(tmp_path)
    real_copy = publishing.stable_copy

    def edit_during_sibling_backup(source, destination):
        result = real_copy(source, destination)
        if Path(source) == stage.master.original_path:
            stage.master.original_path.write_bytes(b"manual-third-state")
        return result

    monkeypatch.setattr(
        publishing,
        "stable_copy",
        edit_during_sibling_backup,
    )

    with pytest.raises(PublishError):
        atomic_publish_files(stage)

    assert stage.master.original_path.read_bytes() == b"manual-third-state"


def test_caught_rollback_does_not_overwrite_third_state(
    tmp_path,
    monkeypatch,
):
    stage = _stage(tmp_path, with_personal=True)
    personal = stage.personals[0]
    real_replace = publishing.os.replace
    real_copy = publishing.stable_copy

    def fail_on_master(source, destination):
        source_path = Path(source)
        destination_path = Path(destination)
        if (
            destination_path == stage.master.original_path
            and "-next" in source_path.name
        ):
            raise OSError("fault after personal publish")
        return real_replace(source, destination)

    def edit_during_rollback_copy(source, destination):
        result = real_copy(source, destination)
        if (
            Path(source) == personal.source_copy
            and "rollback" in Path(destination).name
        ):
            personal.original_path.write_bytes(b"manual-third-state")
        return result

    monkeypatch.setattr(publishing.os, "replace", fail_on_master)
    monkeypatch.setattr(
        publishing,
        "stable_copy",
        edit_during_rollback_copy,
    )

    with pytest.raises(PublishError, match="rollback не завершён"):
        atomic_publish_files(stage)

    assert personal.original_path.read_bytes() == b"manual-third-state"
    assert stage.master.original_path.read_bytes() == b"old-master"
    assert incomplete_publish_journals(tmp_path) == (
        stage.run_dir / "publish.journal.jsonl",
    )


def test_atomic_publish_blocks_locked_live_file_by_default(tmp_path):
    stage = _stage(tmp_path, with_personal=True)
    personal = stage.personals[0]
    lock = personal.original_path.with_name("~$" + personal.original_path.name)
    lock.write_bytes(b"lock")

    with pytest.raises(PublishError):
        atomic_publish_files(stage)

    assert personal.original_path.read_bytes() == b"old-personal"
    assert stage.master.original_path.read_bytes() == b"old-master"


def test_atomic_publish_creates_sidecar_only_for_locked_personal(tmp_path):
    stage = _stage(tmp_path, with_personal=True)
    personal = stage.personals[0]
    lock = personal.original_path.with_name("~$" + personal.original_path.name)
    lock.write_bytes(b"lock")

    result = atomic_publish_files(stage, busy_mode="sidecar")

    assert result.ok
    assert not result.complete
    assert personal.original_path.read_bytes() == b"old-personal"
    assert stage.master.original_path.read_bytes() == b"new-master"
    assert len(result.sidecars) == 1
    assert Path(result.sidecars[0]).read_bytes() == b"new-personal"
    assert Path(result.sidecars[0]).name.startswith(
        "Копия personal (после ночного цикла "
    )
    assert len(result.backups) == 1
    assert Path(result.backups[0]).read_bytes() == b"old-master"


def test_atomic_publish_force_close_rechecks_exact_busy_target(tmp_path):
    stage = _stage(tmp_path, with_personal=True)
    personal = stage.personals[0]
    lock = personal.original_path.with_name("~$" + personal.original_path.name)
    lock.write_bytes(b"lock")
    requested = []

    def close_exact(items):
        requested.extend(item.path for item in items)
        lock.unlink()

    result = atomic_publish_files(
        stage,
        busy_mode="force_close",
        force_close=close_exact,
    )

    assert result.complete
    assert requested == [personal.original_path]
    assert personal.original_path.read_bytes() == b"new-personal"
    assert stage.master.original_path.read_bytes() == b"new-master"


def test_publish_validated_defers_baseline_until_sidecar_is_reconciled(
    tmp_path,
    monkeypatch,
):
    stage = _stage(tmp_path, with_personal=True)
    personal = stage.personals[0]
    lock = personal.original_path.with_name("~$" + personal.original_path.name)
    lock.write_bytes(b"lock")
    active_baseline = tmp_path / "baselines" / "active.json.gz"
    active_baseline.parent.mkdir()
    active_baseline.write_bytes(b"previous-baseline")

    def fake_save_baseline(path, _baseline):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"candidate-baseline")
        return target

    monkeypatch.setattr(publishing, "read_portfolio", lambda *_args: object())
    monkeypatch.setattr(
        publishing,
        "replace",
        lambda value, **_changes: value,
    )
    monkeypatch.setattr(
        publishing,
        "build_baseline",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(publishing, "save_baseline", fake_save_baseline)
    monkeypatch.setattr(
        publishing,
        "baseline_path",
        lambda *_args: active_baseline,
    )
    bundle = SimpleNamespace(
        fields=object(),
        workspace_path=lambda: tmp_path,
        app=SimpleNamespace(
            safety={
                "busy_file_policy": "sidecar",
                "force_close": {},
            }
        ),
    )
    plan = SimpleNamespace(blocked=False, digest=stage.plan_digest)
    validation = ValidationReport(
        run_id=stage.run_id,
        plan_digest=stage.plan_digest,
        checked_files=len(stage.files),
    )

    result = publish_validated(
        bundle,
        stage,
        plan,
        validation,
        confirmation_digest=stage.plan_digest,
    )

    assert not result.complete
    assert result.baseline is None
    assert active_baseline.read_bytes() == b"previous-baseline"
    assert (stage.run_dir / "baseline.next.json.gz").read_bytes() == (
        b"candidate-baseline"
    )
    pending = json.loads(
        (stage.run_dir / "pending-sidecars.json").read_text(
            encoding="utf-8"
        )
    )
    assert pending["originals"] == [str(personal.original_path)]
    assert pending["sidecars"] == result.sidecars
    assert any(
        issue.code == "baseline_deferred_for_sidecars"
        for issue in result.issues
    )


def test_ordinary_publish_refuses_to_create_missing_active_baseline(
    tmp_path,
    monkeypatch,
):
    stage = _stage(tmp_path)
    missing = tmp_path / "baselines" / "missing.json.gz"
    monkeypatch.setattr(publishing, "baseline_path", lambda *_args: missing)
    bundle = SimpleNamespace(
        fields=object(),
        workspace_path=lambda: tmp_path,
        app=SimpleNamespace(safety={"busy_file_policy": "block", "force_close": {}}),
    )
    plan = SimpleNamespace(blocked=False, digest=stage.plan_digest)
    validation = ValidationReport(
        run_id=stage.run_id,
        plan_digest=stage.plan_digest,
        checked_files=len(stage.files),
    )

    with pytest.raises(SafetyViolation, match="promote-first-baseline"):
        publish_validated(
            bundle,
            stage,
            plan,
            validation,
            confirmation_digest=stage.plan_digest,
        )

    assert stage.master.original_path.read_bytes() == b"old-master"
    assert not stage.run_dir.exists()


def test_publish_entrypoint_refuses_concurrent_cycle_lock(tmp_path):
    stage = _stage(tmp_path)
    bundle = SimpleNamespace(
        fields=object(),
        workspace_path=lambda: tmp_path,
        app=SimpleNamespace(
            safety={"busy_file_policy": "block", "force_close": {}}
        ),
    )
    plan = SimpleNamespace(blocked=False, digest=stage.plan_digest)
    validation = ValidationReport(
        run_id=stage.run_id,
        plan_digest=stage.plan_digest,
        checked_files=len(stage.files),
    )

    with publication_cycle_lock(bundle):
        with pytest.raises(SafetyViolation, match="другой цикл"):
            publish_validated(
                bundle,
                stage,
                plan,
                validation,
                confirmation_digest=stage.plan_digest,
            )

    assert stage.master.original_path.read_bytes() == b"old-master"


def test_explicit_initial_baseline_mode_can_publish_once(tmp_path, monkeypatch):
    stage = _stage(tmp_path)
    active_baseline = tmp_path / "baselines" / "active.json.gz"

    def fake_save_baseline(path, _baseline):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"initial-baseline")
        return target

    monkeypatch.setattr(publishing, "read_portfolio", lambda *_args: object())
    monkeypatch.setattr(publishing, "replace", lambda value, **_changes: value)
    monkeypatch.setattr(
        publishing,
        "build_baseline",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(publishing, "save_baseline", fake_save_baseline)
    monkeypatch.setattr(
        publishing,
        "baseline_path",
        lambda *_args: active_baseline,
    )
    bundle = SimpleNamespace(
        fields=object(),
        workspace_path=lambda: tmp_path,
        app=SimpleNamespace(safety={"busy_file_policy": "block", "force_close": {}}),
    )
    plan = SimpleNamespace(blocked=False, digest=stage.plan_digest)
    validation = ValidationReport(
        run_id=stage.run_id,
        plan_digest=stage.plan_digest,
        checked_files=len(stage.files),
    )

    result = publish_validated(
        bundle,
        stage,
        plan,
        validation,
        confirmation_digest=stage.plan_digest,
        allow_initial_baseline=True,
    )

    assert result.complete
    assert stage.master.original_path.read_bytes() == b"new-master"
    assert active_baseline.read_bytes() == b"initial-baseline"


def _validated_publish_inputs(tmp_path, monkeypatch, stage):
    active_baseline = tmp_path / "baselines" / "active.json.gz"
    active_baseline.parent.mkdir(exist_ok=True)
    active_baseline.write_bytes(b"previous-baseline")

    def fake_save_baseline(path, _baseline):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"candidate-baseline")
        return target

    monkeypatch.setattr(publishing, "read_portfolio", lambda *_args: object())
    monkeypatch.setattr(publishing, "replace", lambda value, **_changes: value)
    monkeypatch.setattr(
        publishing,
        "build_baseline",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(publishing, "save_baseline", fake_save_baseline)
    monkeypatch.setattr(
        publishing,
        "baseline_path",
        lambda *_args: active_baseline,
    )
    bundle = SimpleNamespace(
        fields=object(),
        workspace_path=lambda: tmp_path,
        app=SimpleNamespace(
            safety={"busy_file_policy": "block", "force_close": {}}
        ),
    )
    plan = SimpleNamespace(blocked=False, digest=stage.plan_digest)
    validation = ValidationReport(
        run_id=stage.run_id,
        plan_digest=stage.plan_digest,
        checked_files=len(stage.files),
    )
    return active_baseline, bundle, plan, validation


def test_publish_does_not_overwrite_baseline_changed_during_live_publish(
    tmp_path,
    monkeypatch,
):
    stage = _stage(tmp_path)
    active_baseline, bundle, plan, validation = _validated_publish_inputs(
        tmp_path,
        monkeypatch,
        stage,
    )
    real_publish = publishing.atomic_publish_files

    def edit_baseline_after_live_publish(*args, **kwargs):
        result = real_publish(*args, **kwargs)
        active_baseline.write_bytes(b"manual-third-baseline")
        return result

    monkeypatch.setattr(
        publishing,
        "atomic_publish_files",
        edit_baseline_after_live_publish,
    )

    with pytest.raises(PublishError, match="rollback неполон"):
        publish_validated(
            bundle,
            stage,
            plan,
            validation,
            confirmation_digest=stage.plan_digest,
        )

    assert active_baseline.read_bytes() == b"manual-third-baseline"
    assert stage.master.original_path.read_bytes() == b"old-master"
    assert incomplete_publish_journals(tmp_path) == (
        stage.run_dir / "publish.journal.jsonl",
    )


def test_baseline_failure_rollback_does_not_overwrite_third_live_state(
    tmp_path,
    monkeypatch,
):
    stage = _stage(tmp_path)
    active_baseline, bundle, plan, validation = _validated_publish_inputs(
        tmp_path,
        monkeypatch,
        stage,
    )
    real_replace = publishing.os.replace
    real_copy = publishing.stable_copy

    def fail_baseline_activation(source, destination):
        if Path(destination) == active_baseline:
            raise OSError("baseline activation fault")
        return real_replace(source, destination)

    def edit_during_baseline_rollback(source, destination):
        result = real_copy(source, destination)
        if (
            Path(source) == stage.master.source_copy
            and "baseline-rollback" in Path(destination).name
        ):
            stage.master.original_path.write_bytes(b"manual-third-state")
        return result

    monkeypatch.setattr(
        publishing.os,
        "replace",
        fail_baseline_activation,
    )
    monkeypatch.setattr(
        publishing,
        "stable_copy",
        edit_during_baseline_rollback,
    )

    with pytest.raises(PublishError, match="rollback неполон"):
        publish_validated(
            bundle,
            stage,
            plan,
            validation,
            confirmation_digest=stage.plan_digest,
        )

    assert stage.master.original_path.read_bytes() == b"manual-third-state"
    assert active_baseline.read_bytes() == b"previous-baseline"
    assert incomplete_publish_journals(tmp_path) == (
        stage.run_dir / "publish.journal.jsonl",
    )


def _crashed_publish(
    tmp_path,
    monkeypatch,
    crash_after,
):
    stage = _stage(tmp_path, with_personal=True)
    active_baseline = tmp_path / "baselines" / "active.json.gz"
    active_baseline.parent.mkdir()
    active_baseline.write_bytes(b"previous-baseline")

    def fake_save_baseline(path, _baseline):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"candidate-baseline")
        return target

    monkeypatch.setattr(publishing, "read_portfolio", lambda *_args: object())
    monkeypatch.setattr(publishing, "replace", lambda value, **_changes: value)
    monkeypatch.setattr(
        publishing,
        "build_baseline",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(publishing, "save_baseline", fake_save_baseline)
    monkeypatch.setattr(
        publishing,
        "baseline_path",
        lambda *_args: active_baseline,
    )
    bundle = SimpleNamespace(
        fields=object(),
        workspace_path=lambda: tmp_path,
        app=SimpleNamespace(
            safety={"busy_file_policy": "block", "force_close": {}}
        ),
    )
    plan = SimpleNamespace(blocked=False, digest=stage.plan_digest)
    validation = ValidationReport(
        run_id=stage.run_id,
        plan_digest=stage.plan_digest,
        checked_files=len(stage.files),
    )

    real_replace = publishing.os.replace
    live_targets = {
        item.original_path.resolve()
        for item in (stage.personals[0], stage.master)
    }
    replacements = 0

    def crash_after_nth_live_replace(source, destination):
        nonlocal replacements
        result = real_replace(source, destination)
        if Path(destination).resolve() in live_targets:
            replacements += 1
            if replacements == crash_after:
                raise KeyboardInterrupt("simulated power loss")
        return result

    monkeypatch.setattr(
        publishing.os,
        "replace",
        crash_after_nth_live_replace,
    )
    with pytest.raises(KeyboardInterrupt, match="simulated power loss"):
        publish_validated(
            bundle,
            stage,
            plan,
            validation,
            confirmation_digest=stage.plan_digest,
        )

    monkeypatch.setattr(publishing.os, "replace", real_replace)
    return stage, active_baseline, bundle


@pytest.mark.parametrize(
    ("crash_after", "expected_mode", "expected_prefix"),
    [
        (1, "all-old", b"old-"),
        (2, "all-new", b"new-"),
    ],
)
def test_hard_crash_stops_next_publish_and_reconciles_deterministically(
    tmp_path,
    monkeypatch,
    crash_after,
    expected_mode,
    expected_prefix,
):
    stage, active_baseline, bundle = _crashed_publish(
        tmp_path,
        monkeypatch,
        crash_after,
    )
    assert incomplete_publish_journals(tmp_path) == (
        stage.run_dir / "publish.journal.jsonl",
    )
    with pytest.raises(SafetyViolation, match="reconcile-publish"):
        assert_no_incomplete_publications(tmp_path)

    reconciled = reconcile_incomplete_publication(
        bundle,
        stage.run_dir,
        mode="auto",
    )

    assert reconciled["mode"] == expected_mode
    assert stage.personals[0].original_path.read_bytes().startswith(
        expected_prefix
    )
    assert stage.master.original_path.read_bytes().startswith(expected_prefix)
    assert active_baseline.read_bytes() == (
        b"candidate-baseline"
        if expected_mode == "all-new"
        else b"previous-baseline"
    )
    assert incomplete_publish_journals(tmp_path) == ()


def test_reconcile_stops_if_live_changes_after_initial_classification(
    tmp_path,
    monkeypatch,
):
    stage, active_baseline, bundle = _crashed_publish(
        tmp_path,
        monkeypatch,
        2,
    )
    real_states = publishing._reconcile_live_states
    checks = 0

    def race_before_prebaseline(current_stage):
        nonlocal checks
        checks += 1
        if checks == 2:
            current_stage.master.original_path.write_bytes(b"manual-race")
        return real_states(current_stage)

    monkeypatch.setattr(
        publishing,
        "_reconcile_live_states",
        race_before_prebaseline,
    )
    with pytest.raises(SafetyViolation, match="ни source, ни staging"):
        reconcile_incomplete_publication(
            bundle,
            stage.run_dir,
            mode="auto",
        )

    assert active_baseline.read_bytes() == b"previous-baseline"
    assert incomplete_publish_journals(tmp_path)
    assert not (stage.run_dir / "reconcile.json").exists()


def test_reconcile_final_barrier_catches_race_after_baseline_activation(
    tmp_path,
    monkeypatch,
):
    stage, active_baseline, bundle = _crashed_publish(
        tmp_path,
        monkeypatch,
        2,
    )
    real_activate = publishing._activate_new_baseline

    def mutate_after_baseline(manifest, journal):
        real_activate(manifest, journal)
        stage.master.original_path.write_bytes(b"manual-after-baseline")

    monkeypatch.setattr(
        publishing,
        "_activate_new_baseline",
        mutate_after_baseline,
    )
    with pytest.raises(SafetyViolation, match="ни source, ни staging"):
        reconcile_incomplete_publication(
            bundle,
            stage.run_dir,
            mode="auto",
        )

    assert active_baseline.read_bytes() == b"candidate-baseline"
    assert incomplete_publish_journals(tmp_path)
    pending = json.loads(
        (stage.run_dir / "reconcile.pending.json").read_text(
            encoding="utf-8"
        )
    )
    assert pending["ok"] is False
    records = [
        json.loads(line)
        for line in (
            stage.run_dir / "publish.journal.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    assert not any(
        record.get("event") == "reconciled_all_new"
        for record in records
    )


def test_all_new_rechecks_baseline_immediately_before_replace(
    tmp_path,
    monkeypatch,
):
    stage, active_baseline, bundle = _crashed_publish(
        tmp_path,
        monkeypatch,
        2,
    )
    pending = stage.run_dir / "baseline.next.json.gz"
    real_copy = publishing.stable_copy

    def edit_during_candidate_copy(source, destination):
        result = real_copy(source, destination)
        if (
            Path(source) == pending
            and "reconcile-new" in Path(destination).name
        ):
            active_baseline.write_bytes(b"manual-third-baseline")
        return result

    monkeypatch.setattr(
        publishing,
        "stable_copy",
        edit_during_candidate_copy,
    )

    with pytest.raises(SafetyViolation, match="all-new replace"):
        reconcile_incomplete_publication(
            bundle,
            stage.run_dir,
            mode="all-new",
        )

    assert active_baseline.read_bytes() == b"manual-third-baseline"
    assert incomplete_publish_journals(tmp_path)
    assert not (stage.run_dir / "reconcile.json").exists()


def test_all_old_rechecks_baseline_immediately_before_replace(
    tmp_path,
    monkeypatch,
):
    stage, active_baseline, bundle = _crashed_publish(
        tmp_path,
        monkeypatch,
        1,
    )
    pending = stage.run_dir / "baseline.next.json.gz"
    shutil.copy2(pending, active_baseline)
    previous = stage.run_dir / "baseline.previous.json.gz"
    real_copy = publishing.stable_copy

    def edit_during_previous_copy(source, destination):
        result = real_copy(source, destination)
        if (
            Path(source) == previous
            and "reconcile-old" in Path(destination).name
        ):
            active_baseline.write_bytes(b"manual-third-baseline")
        return result

    monkeypatch.setattr(
        publishing,
        "stable_copy",
        edit_during_previous_copy,
    )

    with pytest.raises(SafetyViolation, match="all-old replace"):
        reconcile_incomplete_publication(
            bundle,
            stage.run_dir,
            mode="all-old",
        )

    assert active_baseline.read_bytes() == b"manual-third-baseline"
    assert stage.personals[0].original_path.read_bytes() == b"old-personal"
    assert stage.master.original_path.read_bytes() == b"old-master"
    assert incomplete_publish_journals(tmp_path)
    assert not (stage.run_dir / "reconcile.json").exists()


def test_reconcile_rechecks_sidecar_immediately_before_unlink(
    tmp_path,
    monkeypatch,
):
    stage = _stage(tmp_path, with_personal=True)
    personal = stage.personals[0]
    active_baseline = tmp_path / "baselines" / "active.json.gz"
    active_baseline.parent.mkdir()
    active_baseline.write_bytes(b"previous-baseline")
    pending = stage.run_dir / "baseline.next.json.gz"
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_bytes(b"candidate-baseline")
    publishing._prepare_publish_recovery(
        stage,
        target_baseline=active_baseline,
        pending_baseline=pending,
        allow_initial_baseline=False,
    )
    journal = publishing._Journal(
        stage.run_dir / "publish.journal.jsonl"
    )
    sidecar = personal.original_path.with_name("sidecar-personal.bin")
    shutil.copy2(personal.staged_path, sidecar)
    shutil.copy2(stage.master.staged_path, stage.master.original_path)
    journal.append(
        "publish_started",
        run_id=stage.run_id,
        plan_digest=stage.plan_digest,
        files=[item.as_dict() for item in stage.files],
    )
    journal.append(
        "sidecar_created",
        target=str(personal.original_path),
        sidecar=str(sidecar),
    )
    journal.append(
        "target_replaced",
        target=str(stage.master.original_path),
    )
    journal.append("publish_files_completed")

    real_restore = publishing._restore_old_baseline
    real_fingerprint = publishing.fingerprint
    armed = False

    def arm_race(manifest, current_journal):
        nonlocal armed
        real_restore(manifest, current_journal)
        armed = True

    def edit_after_first_unlink_check(path):
        nonlocal armed
        observed = real_fingerprint(path)
        if armed and Path(path) == sidecar:
            sidecar.write_bytes(b"manual-third-sidecar")
            armed = False
        return observed

    monkeypatch.setattr(
        publishing,
        "_restore_old_baseline",
        arm_race,
    )
    monkeypatch.setattr(
        publishing,
        "fingerprint",
        edit_after_first_unlink_check,
    )
    bundle = SimpleNamespace(
        workspace_path=lambda: tmp_path,
        app=SimpleNamespace(safety={}),
    )

    with pytest.raises(SafetyViolation, match="reconcile unlink"):
        reconcile_incomplete_publication(
            bundle,
            stage.run_dir,
            mode="all-old",
        )

    assert sidecar.read_bytes() == b"manual-third-sidecar"
    assert incomplete_publish_journals(tmp_path)
    assert not (stage.run_dir / "reconcile.json").exists()


def test_all_old_rechecks_target_immediately_before_replace(
    tmp_path,
    monkeypatch,
):
    stage, active_baseline, bundle = _crashed_publish(
        tmp_path,
        monkeypatch,
        1,
    )
    personal = stage.personals[0]
    real_copy = publishing.stable_copy

    def race_during_rollback_temp(source, destination):
        result = real_copy(source, destination)
        if (
            Path(source) == personal.source_copy
            and "reconcile-old" in Path(destination).name
        ):
            personal.original_path.write_bytes(b"manual-before-replace")
        return result

    monkeypatch.setattr(
        publishing,
        "stable_copy",
        race_during_rollback_temp,
    )
    with pytest.raises(SafetyViolation, match="ни source, ни staging"):
        reconcile_incomplete_publication(
            bundle,
            stage.run_dir,
            mode="all-old",
        )

    assert active_baseline.read_bytes() == b"previous-baseline"
    assert personal.original_path.read_bytes() == b"manual-before-replace"
    assert incomplete_publish_journals(tmp_path)


def test_legacy_complete_publish_is_not_misclassified_as_incomplete(tmp_path):
    run = tmp_path / "runs" / "legacy-green"
    run.mkdir(parents=True)
    baseline = tmp_path / "baselines" / "legacy.json.gz"
    baseline.parent.mkdir()
    baseline.write_bytes(b"active")
    journal_path = run / "publish.journal.jsonl"
    journal_path.write_text(
        "\n".join([
            json.dumps({"event": "publish_started"}),
            json.dumps({"event": "publish_files_completed"}),
        ]) + "\n",
        encoding="utf-8",
    )
    publish_path = run / "publish.json"
    publish_path.write_text(
        json.dumps({
            "complete": True,
            "finished_at": "2026-07-29T04:05:00",
            "sidecars": [],
            "baseline": str(baseline),
        }),
        encoding="utf-8",
    )

    assert incomplete_publish_journals(tmp_path) == ()

    with journal_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"event": "publish_started"}) + "\n")
    newer_ns = publish_path.stat().st_mtime_ns + 1_000_000_000
    os.utime(journal_path, ns=(newer_ns, newer_ns))

    assert incomplete_publish_journals(tmp_path) == (journal_path,)


def test_terminal_for_other_run_cannot_close_latest_publish(tmp_path):
    run = tmp_path / "runs" / "wrong-terminal"
    run.mkdir(parents=True)
    baseline = tmp_path / "baselines" / "wrong-terminal.json.gz"
    baseline.parent.mkdir()
    baseline.write_bytes(b"candidate")
    journal = run / "publish.journal.jsonl"
    journal.write_text(
        "\n".join([
            json.dumps({
                "event": "publish_started",
                "run_id": "latest-run",
                "plan_digest": "latest-plan",
            }),
            json.dumps({
                "event": "publish_committed",
                "run_id": "older-run",
                "plan_digest": "older-plan",
            }),
        ]) + "\n",
        encoding="utf-8",
    )
    (run / "publish.json").write_text(
        json.dumps({
            "run_id": "latest-run",
            "plan_digest": "latest-plan",
            "complete": True,
            "finished_at": "2026-07-29T04:05:00",
            "sidecars": [],
            "baseline": str(baseline),
        }),
        encoding="utf-8",
    )

    assert incomplete_publish_journals(tmp_path) == (journal,)


@pytest.mark.parametrize(
    "malformed_tail",
    [
        '{"event":"publish_started","run_id":"run-b"',
        "not-json",
        json.dumps([]),
        json.dumps({"run_id": "run-b", "plan_digest": "plan-b"}),
    ],
)
def test_malformed_tail_after_exact_terminal_is_always_incomplete(
    tmp_path,
    malformed_tail,
):
    """Повреждённый новый start не может скрыться за terminal старого run."""
    run = tmp_path / "runs" / "malformed-tail"
    run.mkdir(parents=True)
    journal = run / "publish.journal.jsonl"
    journal.write_text(
        "\n".join([
            json.dumps({
                "event": "publish_started",
                "run_id": "run-a",
                "plan_digest": "plan-a",
            }),
            json.dumps({
                "event": "publish_committed",
                "run_id": "run-a",
                "plan_digest": "plan-a",
            }),
            malformed_tail,
        ]) + "\n",
        encoding="utf-8",
    )

    assert incomplete_publish_journals(tmp_path) == (journal,)
    with pytest.raises(SafetyViolation, match="reconcile-publish"):
        assert_no_incomplete_publications(tmp_path)


def test_blank_tail_after_exact_terminal_is_not_malformed(tmp_path):
    run = tmp_path / "runs" / "blank-tail"
    run.mkdir(parents=True)
    journal = run / "publish.journal.jsonl"
    journal.write_text(
        "\n".join([
            json.dumps({
                "event": "publish_started",
                "run_id": "run-a",
                "plan_digest": "plan-a",
            }),
            json.dumps({
                "event": "publish_committed",
                "run_id": "run-a",
                "plan_digest": "plan-a",
            }),
            "",
            "   ",
        ]) + "\n",
        encoding="utf-8",
    )

    assert incomplete_publish_journals(tmp_path) == ()
