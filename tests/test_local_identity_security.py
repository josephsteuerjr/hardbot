"""Synthetic identity allocation must never inherit another person's role."""
from concurrent.futures import ThreadPoolExecutor
import pytest
import db

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "identity.db")
    db.init_db()
    db.reset_role_caches()

def test_employee_allocator_accounts_for_manager_without_user():
    manager = 910_000_000_003
    db.set_manager(manager, "Synthetic leader", [], ["Synthetic staff"])
    uid, created = db.local_identity_for("Synthetic staff")
    assert created and uid > manager
    assert db.get_manager(uid) is None
    assert db.get_binding(manager) is None
    assert db.get_binding(uid) == "Synthetic staff"

def test_concurrent_same_employee_has_one_identity():
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: db.local_identity_for("Same staff"), range(12)))
    assert len({uid for uid, _ in results}) == 1
    assert sum(created for _, created in results) == 1

def test_concurrent_different_employees_never_share_identity():
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda n: db.local_identity_for(f"Staff {n}"), range(12)))
    assert len({uid for uid, _ in results}) == 12

def test_new_local_manager_cannot_take_employee_identity():
    uid, _ = db.local_identity_for("Synthetic staff")
    with pytest.raises(ValueError, match="ID"):
        db.set_manager(uid, "Different leader", [], ["Synthetic staff"])
    assert db.get_manager(uid) is None
    assert db.get_binding(uid) == "Synthetic staff"

def test_next_manager_id_reserves_employee_and_device_ids():
    db.set_manager(910_000_000_000, "Leader", [], [])
    uid, _ = db.local_identity_for("Staff")
    assert db.next_local_manager_id() > uid

def test_real_telegram_dual_role_remains_allowed():
    db.set_bind(123456, "Staff")
    db.set_manager(123456, "Staff leader", [], ["Staff"])
    assert db.get_manager(123456) and db.get_binding(123456) == "Staff"


def test_deleted_local_account_id_is_never_reused_for_pending_device_queue():
    old, _ = db.local_identity_for("Previous staff")
    with db.connect() as conn:
        conn.execute("DELETE FROM agent_bind WHERE telegram_id=?", (old,))
        conn.execute("DELETE FROM users WHERE telegram_id=?", (old,))
    new, _ = db.local_identity_for("Different staff")
    assert new > old


def test_deleted_manager_id_cannot_be_reassigned_to_another_person():
    old = 910_000_000_003
    db.set_manager(old, "Previous leader", [], [])
    db.delete_manager(old)
    assert db.next_local_manager_id() > old
    with pytest.raises(ValueError, match="ID"):
        db.set_manager(old, "Different leader", [], [])


def test_manually_bound_local_identity_also_survives_account_deletion():
    old = 920_000_000_000
    db.set_bind(old, "Previous staff")
    with db.connect() as conn:
        conn.execute("DELETE FROM agent_bind WHERE telegram_id=?", (old,))
    new, _ = db.local_identity_for("Different staff")
    assert new > old
