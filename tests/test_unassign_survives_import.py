"""Снятие клиента в пульте должно пережить следующий импорт реестра.

Раньше не переживало: upsert писал responsible из файла, и клиент молча
возвращался выезднику. Карточка пульта и докстринг import_grid при этом
обещали обратное — «не затирая назначение».
"""
import db


def _grid(rows):
    grid = [[f.title for f in db.FIELDS], [f.source for f in db.FIELDS]]
    for cid, responsible in rows:
        grid.append([{"contract_id": cid, "client": f"Клиент {cid}",
                      "birth_date": "1990-01-01", "responsible": responsible,
                      "address": "Худжанд, ул. Т, 1",
                      "debt_total": "10000"}.get(f.key) for f in db.FIELDS])
    return grid


def _setup(rows):
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'UA-%'")
        conn.commit()
    db.import_grid(_grid(rows), source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)


def _resp(cid):
    with db.connect() as conn:
        r = conn.execute("SELECT responsible FROM clients WHERE contract_id=?",
                         (cid,)).fetchone()
    return r["responsible"] if r else None


def test_unassigned_case_is_not_returned_by_the_same_registry():
    _setup([("UA-1", "Иванов И.")])
    assert db.unassign_clients("Иванов И.", ["UA-1"]) == 1
    assert _resp("UA-1") == ""
    db.import_grid(_grid([("UA-1", "Иванов И.")]),
                   source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    assert _resp("UA-1") == "", "импорт вернул снятого клиента"


def test_reassignment_by_the_office_does_lift_the_unassignment():
    """Офис переназначил кейс другому — это законно и снятие отменяет."""
    _setup([("UA-2", "Иванов И.")])
    db.unassign_clients("Иванов И.", ["UA-2"])
    db.import_grid(_grid([("UA-2", "Петров П.")]),
                   source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    assert _resp("UA-2") == "Петров П."


def test_after_reassignment_the_mark_no_longer_blocks():
    """Метка обязана сняться вместе с переназначением, иначе следующий импорт
    того же файла снова обнулит уже нового ответственного."""
    _setup([("UA-3", "Иванов И.")])
    db.unassign_clients("Иванов И.", ["UA-3"])
    db.import_grid(_grid([("UA-3", "Петров П.")]),
                   source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    db.import_grid(_grid([("UA-3", "Петров П.")]),
                   source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    assert _resp("UA-3") == "Петров П."


def test_untouched_cases_are_imported_as_before():
    _setup([("UA-4", "Иванов И.")])
    db.import_grid(_grid([("UA-4", "Иванов И.")]),
                   source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    assert _resp("UA-4") == "Иванов И."


def test_orphaned_binding_is_visible():
    """Сотрудника переименовали в реестре — портфель обнулился бесшумно:
    приложение отвечает 200 и «за вами никто не закреплён», а осиротевшей
    привязки не видно ни на одном экране. Признак обязан считаться."""
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE contract_id LIKE 'OR-%'")
        conn.execute("DELETE FROM agent_bind WHERE telegram_id IN (7001, 7002)")
        conn.commit()
    grid = [[f.title for f in db.FIELDS], [f.source for f in db.FIELDS]]
    grid.append([{"contract_id": "OR-1", "client": "К", "birth_date": "1990-01-01",
                  "responsible": "Живой Сотрудник", "address": "Худжанд",
                  "debt_total": "1000"}.get(f.key) for f in db.FIELDS])
    db.import_grid(grid, source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER)
    db.set_bind(7001, "Живой Сотрудник")
    db.set_bind(7002, "Переименованный Сотрудник")
    stats = {w["telegram_id"]: w for w in db.activity_stats()}
    assert stats[7001]["orphaned"] is False
    assert stats[7002]["orphaned"] is True, "осиротевшая привязка не отмечена"
