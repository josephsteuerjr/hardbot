"""«Убрать клиентов у сотрудника» (пульт): снятие чистит responsible, договор
остаётся в реестре; чужие/битые contract_id молча пропускаются."""
import db


def _seed(cid: str, emp: str, client: str = "Тестов Тест"):
    grid = [[None] * len(db.FIELDS),
            [f.title for f in db.FIELDS],
            [f.source for f in db.FIELDS],
            [{"contract_id": cid, "client": client, "responsible": emp,
              "partner": "ЗК", "debt_today": "500"}.get(f.key) for f in db.FIELDS]]
    db.import_grid(
        grid,
        source_kind=db.IMPORT_SOURCE_PUBLISHED_MASTER,
    )


def test_unassign_clears_responsible_keeps_client():
    db.init_db()
    _seed("UA-1", "Джумаев Тест")
    _seed("UA-2", "Джумаев Тест")
    assert [r["contract_id"] for r in db.clients_of_employee("Джумаев Тест")] == \
        ["UA-1", "UA-2"]
    assert db.unassign_clients("Джумаев Тест", ["UA-1"]) == 1
    assert [r["contract_id"] for r in db.clients_of_employee("Джумаев Тест")] == ["UA-2"]
    c = db.get_client("UA-1")                       # клиент жив, просто без ответственного
    assert c is not None and (c["responsible"] or "") == ""


def test_unassign_ignores_foreign_and_garbage_ids():
    db.init_db()
    _seed("UA-3", "Джумаев Тест")
    _seed("UA-4", "Другой Сотрудник")
    n = db.unassign_clients("Джумаев Тест", ["UA-4", "нет-такого", "", None])
    assert n == 0                                   # чужой договор не тронут
    assert db.get_client("UA-4")["responsible"] == "Другой Сотрудник"
    assert db.unassign_clients("", ["UA-3"]) == 0   # без сотрудника — отказ
    assert db.unassign_clients("Джумаев Тест", []) == 0
