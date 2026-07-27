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
