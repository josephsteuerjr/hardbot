from pathlib import Path
import shutil

import pytest

import registry_cycle.publishing as publishing
from registry_cycle.errors import PublishError
from registry_cycle.files import fingerprint
from registry_cycle.publishing import atomic_publish_files
from registry_cycle.staging import StageSet, StagedFile


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
    assert stage.master.original_path.read_bytes() == b"new-master"
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
