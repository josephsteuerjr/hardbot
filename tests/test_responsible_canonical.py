"""Опечатка регистра в реестре не должна заводить двойника сотрудника.

«михаил» вместо «Михаил» — и в системе появляется отдельный сотрудник с одним
договором, своим портфелем и своей привязкой. SQL-ный NOCASE тут бессилен: он
работает только с латиницей.
"""
import db


def _grid(rows):
    grid = [[f.title for f in db.FIELDS], [f.source for f in db.FIELDS]]
    for cid, responsible in rows:
        grid.append([{"contract_id": cid, "client": f"К {cid}",
                      "birth_date": "1990-01-01", "responsible": responsible,
                      "address": "Худжанд", "debt_total": "1000"}.get(f.key)
                     for f in db.FIELDS])
    return grid


def _setup():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'CN-%'")
        conn.commit()


def _employees():
    return {r["employee"] for r in db.employees_in_registry()}


def test_case_typo_does_not_create_a_twin():
    _setup()
    db.import_grid(_grid([("CN-1", "Михаил Петров")]),
                   source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    db.import_grid(_grid([("CN-2", "михаил петров")]),
                   source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    assert "Михаил Петров" in _employees()
    assert "михаил петров" not in _employees(), "завёлся двойник"


def test_extra_spaces_do_not_create_a_twin():
    _setup()
    db.import_grid(_grid([("CN-3", "Иванов Иван")]),
                   source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    db.import_grid(_grid([("CN-4", "  Иванов   Иван  ")]),
                   source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    assert len([e for e in _employees() if "Иванов" in e]) == 1


def test_yo_and_ye_are_the_same_person():
    _setup()
    db.import_grid(_grid([("CN-5", "Алёшин П.")]),
                   source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    db.import_grid(_grid([("CN-6", "Алешин П.")]),
                   source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    assert len([e for e in _employees() if "лешин" in e or "лёшин" in e]) == 1


def test_a_genuinely_different_person_is_still_separate():
    """Канонизация не должна склеивать разных людей."""
    _setup()
    db.import_grid(_grid([("CN-7", "Иванов Иван"), ("CN-8", "Иванов Пётр")]),
                   source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    assert len([e for e in _employees() if "Иванов" in e]) == 2
