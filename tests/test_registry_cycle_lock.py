import json
import os
import threading
import time

import pytest

from registry_cycle.cycle_lock import CycleLock
from registry_cycle.errors import SafetyViolation


def test_cycle_lock_is_exclusive_and_removed_after_exit(tmp_path):
    path = tmp_path / "cycle.lock"
    with CycleLock(path, stale_seconds=3600):
        assert path.is_file()
        with pytest.raises(SafetyViolation):
            with CycleLock(path, stale_seconds=3600):
                pass
    assert not path.exists()


def test_stale_lock_is_not_removed_when_publish_guard_blocks(tmp_path):
    path = tmp_path / "cycle.lock"
    path.write_text("crashed publish", encoding="utf-8")
    old = time.time() - 7200
    os.utime(path, (old, old))

    def incomplete_publish():
        raise SafetyViolation("незавершённая публикация")

    with pytest.raises(SafetyViolation, match="незавершённая"):
        with CycleLock(
            path,
            stale_seconds=3600,
            before_stale_remove=incomplete_publish,
        ):
            pass

    assert path.read_text(encoding="utf-8") == "crashed publish"


def test_two_stale_reclaimers_cannot_claim_same_owner(tmp_path):
    path = tmp_path / "cycle.lock"
    original = b'{"owner_token":"crashed-owner"}\n'
    path.write_bytes(original)
    old = time.time() - 7200
    os.utime(path, (old, old))

    hook_entered = threading.Event()
    release_hook = threading.Event()
    acquired = threading.Event()
    release_owner = threading.Event()
    failures: list[BaseException] = []

    def slow_guard():
        hook_entered.set()
        assert release_hook.wait(5)

    def first_reclaimer():
        try:
            with CycleLock(
                path,
                stale_seconds=3600,
                before_stale_remove=slow_guard,
            ):
                acquired.set()
                assert release_owner.wait(5)
        except BaseException as exc:  # pragma: no cover - surfaced below
            failures.append(exc)

    worker = threading.Thread(target=first_reclaimer)
    worker.start()
    assert hook_entered.wait(5)

    with pytest.raises(SafetyViolation, match="меняет cycle lock"):
        with CycleLock(path, stale_seconds=3600):
            pass

    release_hook.set()
    assert acquired.wait(5)
    tombstones = tuple(tmp_path.glob("cycle.lock.stale.*"))
    assert len(tombstones) == 1
    assert tombstones[0].read_bytes() == original
    release_owner.set()
    worker.join(5)
    assert not worker.is_alive()
    assert failures == []
    assert not path.exists()


def test_old_owner_cannot_delete_replacement_lock(tmp_path):
    path = tmp_path / "cycle.lock"
    old_owner = CycleLock(path, stale_seconds=3600)
    old_owner.__enter__()
    replacement = {
        "owner_token": "new-owner-token",
        "pid": 999,
        "started": "2026-07-29T12:00:00",
    }
    path.write_text(
        json.dumps(replacement) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(SafetyViolation, match="другому owner"):
        old_owner.__exit__(None, None, None)

    assert json.loads(path.read_text(encoding="utf-8")) == replacement
