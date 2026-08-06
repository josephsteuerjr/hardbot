"""Экспорт ограничивается явными сотрудниками и доказанными датами выезда."""

from pathlib import Path

import db


def _grid(rows):
    grid = [[field.title for field in db.FIELDS],
            [field.source for field in db.FIELDS]]
    for contract_id, employee in rows:
        values = {
            "contract_id": contract_id,
            "client": f"Клиент {contract_id}",
            "responsible": employee,
        }
        grid.append([values.get(field.key) for field in db.FIELDS])
    return grid


def _setup(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "hardbot.db")
    db.init_db()
    report = db.import_grid(
        _grid([
            ("EXP-A1", "Иванов И."),
            ("EXP-A2", "Иванов И."),
            ("EXP-B1", "Петров П."),
            ("EXP-U1", ""),
        ]),
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
    )
    assert report["blocked"] is False
    assert db.record_bot_visit("EXP-A1", {}, "2026-08-01") == 1
    assert db.record_bot_visit("EXP-A2", {}, "2026-08-03") == 1
    assert db.record_bot_visit("EXP-B1", {}, "2026-08-03") == 1


def test_no_filters_preserve_full_export_including_unassigned(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    assert {row["contract_id"] for row in db.registry_rows()} == {
        "EXP-A1", "EXP-A2", "EXP-B1", "EXP-U1"
    }


def test_employee_filter_is_case_and_space_tolerant(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    rows = db.registry_rows(employees=["  иванов   и. "])

    assert {row["contract_id"] for row in rows} == {"EXP-A1", "EXP-A2"}


def test_employee_and_visit_window_are_independent_filters(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    rows = db.registry_rows(
        employees=["Иванов И."],
        visit_from="02.08.2026",
        visit_to="03.08.2026",
    )

    assert [row["contract_id"] for row in rows] == ["EXP-A2"]


def test_visit_window_uses_bot_provenance_not_raw_visit_date(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    with db.connect() as conn:
        conn.execute(
            "UPDATE clients SET visit_date='2026-08-03' WHERE contract_id='EXP-U1'"
        )
        conn.commit()

    rows = db.registry_rows(visit_from="2026-08-03", visit_to="2026-08-03")

    assert {row["contract_id"] for row in rows} == {"EXP-A2", "EXP-B1"}


def test_invalid_or_reversed_export_window_is_rejected(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    for kwargs in (
        {"visit_from": "не дата"},
        {"visit_from": "2026-08-04", "visit_to": "2026-08-03"},
    ):
        try:
            db.registry_rows(**kwargs)
        except ValueError:
            continue
        raise AssertionError(f"невалидное окно принято: {kwargs}")
