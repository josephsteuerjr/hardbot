from pathlib import Path
import json
import shutil
from types import SimpleNamespace

import pytest

import registry_cycle.publishing as publishing
from registry_cycle.errors import PublishError
from registry_cycle.files import fingerprint
from registry_cycle.publishing import atomic_publish_files, publish_validated
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
    return StageSet(
        run_id="test-run",
        run_dir=tmp_path / "run",
        staging_dir=tmp_path / "staging",
        plan_digest="a" * 64,
        master=master,
        personals=personals,
    )


def test_atomic_publish_replaces_only_after_preflight(tmp_path):
    stage = _stage(tmp_path)

    result = atomic_publish_files(stage)

    assert result.ok
    assert result.complete
    assert stage.master.original_path.read_bytes() == b"new-master"
    assert len(result.backups) == 1
    assert Path(result.backups[0]).read_bytes() == b"old-master"
    assert (stage.run_dir / "publish.journal.jsonl").is_file()


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
