"""Полный импорт снимает только отсутствующие строки и сохраняет их историю."""

from pathlib import Path

import db


def _grid(rows):
    grid = [[f.title for f in db.FIELDS], [f.source for f in db.FIELDS]]
    for cid, responsible in rows:
        values = {
            "contract_id": cid,
            "client": f"Клиент {cid}",
            "birth_date": "1990-01-01",
            "responsible": responsible,
            "address": "Худжанд, ул. Тестовая, 1",
            "debt_total": "10000",
        }
        grid.append([values.get(f.key) for f in db.FIELDS])
    return grid


def _isolated_db(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "hardbot.db")
    db.init_db()


def _seed(rows) -> None:
    report = db.import_grid(
        _grid(rows), source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER
    )
    assert report["blocked"] is False


def test_full_import_unassigns_missing_contract_but_keeps_row_and_visits(
    tmp_path, monkeypatch
):
    _isolated_db(tmp_path, monkeypatch)
    _seed([("AUTO-KEEP", "Иванов И."), ("AUTO-GONE", "Петров П.")])
    assert db.log_visit("AUTO-GONE", 7001, '{"result":"visited"}') is True

    report = db.import_grid(
        _grid([("AUTO-KEEP", "Иванов И.")]),
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
        full_registry=True,
        authoritative_employees=["Иванов И.", "Петров П."],
    )

    assert report["auto_unassigned"] == 1
    removed = db.get_client("AUTO-GONE")
    assert removed is not None
    assert removed["responsible"] == ""
    assert removed["unassigned_from"] == "Петров П."
    assert removed["unassigned_reason"] == db.UNASSIGNED_REASON_ABSENT_FROM_REGISTRY
    assert removed["debt_total"] == "10000"
    with db.connect() as conn:
        visits = conn.execute(
            "SELECT COUNT(*) FROM visits WHERE contract_id='AUTO-GONE'"
        ).fetchone()[0]
    assert visits == 1


def test_partial_import_never_unassigns_missing_contract(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    _seed([("PART-KEEP", "Иванов И."), ("PART-STILL", "Петров П.")])

    report = db.import_grid(
        _grid([("PART-KEEP", "Иванов И.")]),
        source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
        full_registry=False,
    )

    assert report["auto_unassigned"] == 0
    assert db.get_client("PART-STILL")["responsible"] == "Петров П."


def test_automatically_unassigned_contract_returns_with_the_same_owner(
    tmp_path, monkeypatch
):
    _isolated_db(tmp_path, monkeypatch)
    _seed([("RETURN-KEEP", "Иванов И."), ("RETURN-ME", "Петров П.")])
    db.import_grid(
        _grid([("RETURN-KEEP", "Иванов И.")]),
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
        full_registry=True,
        authoritative_employees=["Иванов И.", "Петров П."],
    )
    assert db.get_client("RETURN-ME")["responsible"] == ""

    db.import_grid(
        _grid([("RETURN-ME", "Петров П.")]),
        source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
    )

    returned = db.get_client("RETURN-ME")
    assert returned["responsible"] == "Петров П."
    assert returned["unassigned_from"] == ""
    assert returned["unassigned_reason"] == ""


def test_manual_unassignment_still_blocks_same_owner_and_allows_another(
    tmp_path, monkeypatch
):
    _isolated_db(tmp_path, monkeypatch)
    _seed([("MANUAL-1", "Иванов И.")])
    assert db.unassign_clients("Иванов И.", ["MANUAL-1"]) == 1
    manual = db.get_client("MANUAL-1")
    assert manual["unassigned_reason"] == db.UNASSIGNED_REASON_MANUAL

    db.import_grid(
        _grid([("MANUAL-1", "Иванов И.")]),
        source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
    )
    assert db.get_client("MANUAL-1")["responsible"] == ""

    db.import_grid(
        _grid([("MANUAL-1", "Сидоров С.")]),
        source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
    )
    reassigned = db.get_client("MANUAL-1")
    assert reassigned["responsible"] == "Сидоров С."
    assert reassigned["unassigned_from"] == ""
    assert reassigned["unassigned_reason"] == ""


def test_preview_reports_unassignment_and_rolls_back_every_write(
    tmp_path, monkeypatch
):
    _isolated_db(tmp_path, monkeypatch)
    _seed([("PREVIEW-KEEP", "Иванов И."), ("PREVIEW-GONE", "Петров П.")])
    version_before = db.data_version()

    preview = db.import_grid(
        _grid([("PREVIEW-KEEP", "Иванов И.")]),
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
        full_registry=True,
        authoritative_employees=["Иванов И.", "Петров П."],
        preview_only=True,
    )

    assert preview["preview_only"] is True
    assert preview["auto_unassigned"] == 1
    assert preview["auto_unassigned_by_employee"] == {"Петров П.": 1}
    assert len(preview["auto_unassigned_digest"]) == 64
    assert db.get_client("PREVIEW-GONE")["responsible"] == "Петров П."
    assert db.data_version() == version_before

    applied = db.import_grid(
        _grid([("PREVIEW-KEEP", "Иванов И.")]),
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
        full_registry=True,
        authoritative_employees=["Иванов И.", "Петров П."],
    )
    assert applied["auto_unassigned"] == 1
    assert db.get_client("PREVIEW-GONE")["responsible"] == ""


def test_old_unassignment_marker_is_migrated_as_manual(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    _seed([("LEGACY-MANUAL", "Иванов И.")])
    with db.connect() as conn:
        conn.execute(
            "UPDATE clients SET responsible='', unassigned_from='Иванов И.', "
            "unassigned_reason=NULL WHERE contract_id='LEGACY-MANUAL'"
        )
        conn.commit()

    db.init_db()

    row = db.get_client("LEGACY-MANUAL")
    assert row["unassigned_reason"] == db.UNASSIGNED_REASON_MANUAL


def test_apply_stops_if_unassignment_count_changed_after_preview(
    tmp_path, monkeypatch
):
    _isolated_db(tmp_path, monkeypatch)
    _seed([("STALE-KEEP", "Иванов И."), ("STALE-GONE", "Петров П.")])
    preview = db.import_grid(
        _grid([("STALE-KEEP", "Иванов И.")]),
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
        full_registry=True,
        authoritative_employees=["Иванов И.", "Петров П."],
        preview_only=True,
    )
    assert preview["auto_unassigned"] == 1

    # Между preview и apply договор уже сняли вручную: актуальное число теперь 0.
    assert db.unassign_clients("Петров П.", ["STALE-GONE"]) == 1
    report = db.import_grid(
        _grid([("STALE-KEEP", "Иванов И.")]),
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
        full_registry=True,
        authoritative_employees=["Иванов И.", "Петров П."],
        expected_auto_unassigned=1,
        expected_auto_unassigned_digest=preview["auto_unassigned_digest"],
    )

    assert report["blocked"] is True
    assert report["preview_stale"] is True
    assert report["auto_unassigned"] == 0
    stale = db.get_client("STALE-GONE")
    assert stale["unassigned_reason"] == db.UNASSIGNED_REASON_MANUAL


def test_full_slice_never_touches_an_unselected_employee(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    _seed([
        ("A-KEEP", "Иванов И."),
        ("A-GONE", "Иванов И."),
        ("B-STAY", "Петров П."),
    ])

    report = db.import_grid(
        _grid([("A-KEEP", "Иванов И.")]),
        source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
        full_registry=True,
        authoritative_employees=["иванов  и."],
    )

    assert report["blocked"] is False
    assert report["auto_unassigned"] == 1
    assert db.get_client("A-GONE")["responsible"] == ""
    assert db.get_client("B-STAY")["responsible"] == "Петров П."


def test_selected_employee_with_zero_rows_loses_assignments_not_rows(
    tmp_path, monkeypatch
):
    _isolated_db(tmp_path, monkeypatch)
    _seed([("ZERO-1", "Петров П."), ("ZERO-2", "Петров П.")])

    report = db.import_grid(
        _grid([]),
        source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
        full_registry=True,
        authoritative_employees=["Петров П."],
    )

    assert report["auto_unassigned"] == 2
    assert report["inserted"] == 0
    assert report["updated"] == 0
    assert db.get_client("ZERO-1") is not None
    assert db.get_client("ZERO-2") is not None
    assert db.get_client("ZERO-1")["responsible"] == ""
    assert db.get_client("ZERO-2")["responsible"] == ""


def test_full_slice_requires_explicit_employee_scope(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    _seed([("SCOPE-1", "Иванов И.")])

    report = db.import_grid(
        _grid([]),
        source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
        full_registry=True,
    )

    assert report["blocked"] is True
    assert "authoritative_employees" in report["error"]
    assert db.get_client("SCOPE-1")["responsible"] == "Иванов И."


def test_effect_digest_catches_same_count_different_contract(
    tmp_path, monkeypatch
):
    _isolated_db(tmp_path, monkeypatch)
    _seed([("DIGEST-KEEP", "Иванов И."), ("DIGEST-OLD", "Иванов И.")])
    preview = db.import_grid(
        _grid([("DIGEST-KEEP", "Иванов И.")]),
        source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
        full_registry=True,
        authoritative_employees=["Иванов И."],
        preview_only=True,
    )
    assert preview["auto_unassigned"] == 1

    assert db.unassign_clients("Иванов И.", ["DIGEST-OLD"]) == 1
    _seed([("DIGEST-NEW", "Иванов И.")])
    report = db.import_grid(
        _grid([("DIGEST-KEEP", "Иванов И.")]),
        source_kind=db.IMPORT_SOURCE_TEST_SUBSET,
        full_registry=True,
        authoritative_employees=["Иванов И."],
        expected_auto_unassigned=1,
        expected_auto_unassigned_digest=preview["auto_unassigned_digest"],
    )

    assert report["auto_unassigned"] == 1
    assert report["blocked"] is True
    assert report["preview_stale"] is True
    assert db.get_client("DIGEST-NEW")["responsible"] == "Иванов И."
