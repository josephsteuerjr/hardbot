import json

import pytest

import db


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "history.db")
    db.init_db()


def client(cid, hard, soft, name="Тестовый клиент", dob="1990-01-01", address="Адрес"):
    with db.connect() as conn:
        conn.execute("INSERT INTO clients(contract_id,client,birth_date,address,responsible,responsible_soft) VALUES (?,?,?,?,?,?)",
                     (cid, name, dob, address, hard, soft))


@pytest.mark.parametrize("soft", [False, True])
def test_bulk_history_matches_single_cards_after_reassignment(database, soft):
    client("A", "H1", "S1")
    client("B", "H2", "S1")
    client("C", "H1", "S2")
    client("D", "H1", "S1", dob=None, address="Другой адрес")
    client("E", "H1", "S1", dob=None, address="Ещё адрес")
    with db.connect() as conn:
        for cid in ["A", "C", "B", "D", "E"]:
            conn.execute("INSERT INTO visits(contract_id,agent_id,created_at,payload,op_id) VALUES (?,77,'2026-10-06','{}',?)",
                         (cid, "visit-" + cid))
        for i in range(61):
            conn.execute("INSERT INTO calls(contract_id,agent_id,created_at,payload,op_id,contracts_json) VALUES (?,77,'2026-10-06','{}',?,?)",
                         ("B", "call-" + str(i), json.dumps(["A", "B"])))
        for cid, ids in [("C", '["C"]'), ("REMOVED", '["A","C"]'),
                         ("D", 'invalid'), ("E", '{"original":"E"}')]:
            conn.execute("INSERT INTO calls(contract_id,agent_id,created_at,payload,op_id,contracts_json) VALUES (?,77,'2026-10-06','{}',?,?)",
                         (cid, "call-" + cid, ids))
    grouped = db.persons_for_soft("S1") if soft else db.persons_for_employee("H1")
    history = db.portfolio_card_history(grouped, by_soft=soft)
    for person in grouped:
        rep = person["rep"]
        expected_visit = db.last_visit_row([r["contract_id"] for r in person["contracts"]])
        assert dict(history[rep]["visit"]) == dict(expected_visit)
        assert [dict(r) for r in history[rep]["calls"]] == [dict(r) for r in db.calls_for_person(rep)]
        assert len(history[rep]["calls"]) <= 50


@pytest.mark.parametrize("soft", [False, True])
def test_large_portfolio_has_bounded_history_queries(database, monkeypatch, soft):
    with db.connect() as conn:
        conn.executemany("INSERT INTO clients(contract_id,client,birth_date,responsible,responsible_soft) VALUES (?,?,'1990-01-01','H','S')",
                         [(f"C{i}", f"Клиент {i}") for i in range(850)])
    grouped = db.persons_for_soft("S") if soft else db.persons_for_employee("H")
    original = db.connect
    selects = []

    def counted():
        conn = original()
        conn.set_trace_callback(lambda sql: selects.append(sql) if sql.lstrip().upper().startswith("SELECT") else None)
        return conn

    monkeypatch.setattr(db, "connect", counted)
    history = db.portfolio_card_history(grouped, by_soft=soft)
    assert len(history) == 850
    assert all(r == {"visit": None, "calls": []} for r in history.values())
    assert len(selects) <= 10


def test_empty_portfolio_needs_no_database(monkeypatch):
    monkeypatch.setattr(db, "connect", lambda: pytest.fail("Empty history queried the database"))
    assert db.portfolio_card_history([]) == {}
