"""Экспорт ограничивается владельцами обоих профилей и доказанными фактами."""

from pathlib import Path

import db
import pytest


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


def _soft_setup(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    with db.connect() as conn:
        conn.execute("UPDATE clients SET responsible_soft='Софт С.' WHERE contract_id='EXP-U1'")
        conn.execute("UPDATE clients SET responsible_soft='Другой С.' WHERE contract_id='EXP-A1'")
        conn.commit()
    values = {'soft_call_status': 'Нет контакта', 'soft_comm_date': '2026-08-10',
              'soft_comment': 'Синтетический результат'}
    db.save_call_result('EXP-U1', 101, 'Софт С.', values, '',
                        op_id='export-soft-only', on_date='2026-08-03')
    db.save_call_result('EXP-A1', 102, 'Другой С.', values, '',
                        op_id='export-dual-profile', on_date='2026-08-03')


def test_pure_soft_employee_is_exported_without_hard_owner(tmp_path, monkeypatch):
    _soft_setup(tmp_path, monkeypatch)
    rows = db.registry_rows(employees=['  СОФТ   С. '])
    assert [r['contract_id'] for r in rows] == ['EXP-U1']
    assert rows[0]['responsible_soft'] == 'Софт С.'
    assert rows[0]['soft_call_status'] == 'Нет контакта'
    assert rows[0]['soft_comment'] == 'Синтетический результат'


@pytest.mark.parametrize('employees,expected', [
    (None, {'EXP-A1', 'EXP-A2', 'EXP-B1', 'EXP-U1'}),
    (['Софт С.'], {'EXP-U1'}),
    (['Другой С.'], {'EXP-A1'}),
    (['Иванов И.'], {'EXP-A1', 'EXP-A2'}),
    (['Софт С.', 'Иванов И.'], {'EXP-A1', 'EXP-A2', 'EXP-U1'}),
])
def test_period_includes_visit_or_call_fact(tmp_path, monkeypatch, employees, expected):
    _soft_setup(tmp_path, monkeypatch)
    rows = db.registry_rows(employees=employees, visit_from='2026-08-03', visit_to='2026-08-03')
    assert {r['contract_id'] for r in rows} == expected


def test_callback_date_and_imported_soft_fields_do_not_prove_a_call(tmp_path, monkeypatch):
    _soft_setup(tmp_path, monkeypatch)
    with db.connect() as conn:
        conn.execute("UPDATE clients SET soft_comm_date='2026-08-10', "
                     "soft_call_status='Нет контакта' WHERE contract_id='EXP-A2'")
        conn.commit()
    assert db.registry_rows(visit_from='2026-08-10', visit_to='2026-08-10') == []


def test_invalid_call_snapshot_does_not_admit_a_row_into_period(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    with db.connect() as conn:
        conn.execute("UPDATE clients SET bot_call_at='2026-08-03T12:00:00', "
                     "bot_call_data='{}' WHERE contract_id='EXP-U1'")
        conn.commit()
    assert {r['contract_id'] for r in db.registry_rows(visit_from='2026-08-03')} == {
        'EXP-A2', 'EXP-B1'}
