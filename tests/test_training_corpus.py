"""Корпус для обучения скоринга: общий реестр в БД, переживающий сброс портфелей.

Зачем отдельная сущность. Портфель выездника — это раздача на выезд, и он сбрасывается
между циклами. Обучать модель на нём нельзя: каждый сброс обнулял бы всю историю.
Поэтому общий реестр грузится отдельной операцией в `training_rows`, и сброс его
не трогает.

Второе требование, столь же важное: в корпусе НЕТ персональных данных. Обучению
нужны признаки и результат, а не имя с адресом — значит «весь реестр в базе»
не создаёт нового хранилища ПД.
"""
import db
import limited_test_reset as ltr
import train_scoring


SNAP = "тест-снимок"


def _row(cid, **kw):
    base = {"contract_id": cid, "debt": 30_000, "product_type": "CONSUMER",
            "partner": "ЗК", "country": "Таджикистан", "assign_date": "2026-06-01",
            "kd_date": "2023-01-01", "promise_date": "", "result": "В работе",
            "contact_with": "Должник", "who": "По адресу проживает клиент",
            "location": "Клиент на родине", "visits": 0, "paid_months": 0,
            "target": 0.0}
    base.update(kw)
    base["contract_key"] = db.contract_key(base.pop("contract_id"))
    return base


def _clean():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM training_rows WHERE snapshot = ?", (SNAP,))
        conn.commit()


# --- загрузка -----------------------------------------------------------------
def test_rows_are_stored():
    _clean()
    n = db.add_training_rows(SNAP, [_row("A-1", target=12_000), _row("A-2")])
    assert n == 2
    rows = db.training_rows(SNAP)
    assert len(rows) == 2
    assert {r["target"] for r in rows} == {12_000.0, 0.0}


def test_reload_of_same_snapshot_adds_nothing():
    """Загрузку реестра прерывают и повторяют — задваивать строки нельзя."""
    _clean()
    batch = [_row("B-1"), _row("B-2")]
    assert db.add_training_rows(SNAP, batch) == 2
    assert db.add_training_rows(SNAP, batch) == 0
    assert len(db.training_rows(SNAP)) == 2


def test_same_contract_in_two_snapshots_is_two_rows():
    """Разные снимки — разные наблюдения одного договора во времени."""
    _clean()
    with db.connect() as conn:
        conn.execute("DELETE FROM training_rows WHERE snapshot = ?", (SNAP + "-2",))
        conn.commit()
    db.add_training_rows(SNAP, [_row("C-1", target=0)])
    db.add_training_rows(SNAP + "-2", [_row("C-1", target=5_000)])
    assert len(db.training_rows(SNAP)) == 1
    assert db.training_rows(SNAP + "-2")[0]["target"] == 5_000


def test_snapshot_summary():
    _clean()
    db.add_training_rows(SNAP, [_row("D-1", target=1_000), _row("D-2"), _row("D-3", target=500)])
    s = {x["snapshot"]: x for x in db.training_snapshots()}[SNAP]
    assert s["n"] == 3 and s["paid"] == 2 and s["collected"] == 1_500


# --- отсутствие ПД ------------------------------------------------------------
def test_corpus_has_no_personal_columns():
    """Ни имени, ни телефона, ни адреса, ни даты рождения — по построению."""
    db.init_db()
    with db.connect() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(training_rows)")}
    forbidden = {"client", "phone", "phone_main", "address", "address_customer",
                 "birth_date", "extra_address", "contract_id", "responsible"}
    assert not (cols & forbidden), f"в корпус просочились ПД: {cols & forbidden}"


def test_contract_number_is_hashed_not_stored():
    _clean()
    db.add_training_rows(SNAP, [_row("СЕКРЕТНЫЙ-НОМЕР-123")])
    stored = db.training_rows(SNAP)[0]["contract_key"]
    assert "СЕКРЕТНЫЙ" not in stored
    assert stored == db.contract_key("СЕКРЕТНЫЙ-НОМЕР-123")   # но воспроизводим


# --- переживает сброс портфелей ------------------------------------------------
def test_training_corpus_survives_portfolio_reset():
    assert "training_rows" in ltr.RETAINED_TABLES
    assert "training_rows" not in ltr.RESET_TABLES
    assert "training_rows" not in ltr.DELETE_ORDER


def test_tracks_survive_portfolio_reset():
    """Решение владельца: треки переживают сброс — иначе каждый цикл теряет ровно
    те данные, ради которых треки и заводились."""
    assert "track_points" in ltr.RETAINED_TABLES
    assert "track_points" not in ltr.DELETE_ORDER


def test_all_tables_are_declared():
    """Fail-closed контракт: любая новая таблица обязана быть объявлена."""
    db.init_db()
    with db.connect() as conn:
        real = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'")}
    assert real <= ltr.EXPECTED_TABLES, f"не объявлены: {real - ltr.EXPECTED_TABLES}"


# --- обучение из БД -------------------------------------------------------------
def test_train_reads_corpus_back_in_trainable_shape():
    _clean()
    db.add_training_rows(SNAP, [_row(f"E-{i}", target=10_000 if i % 5 == 0 else 0)
                                for i in range(200)])
    rows = train_scoring.load_from_db(SNAP)
    assert len(rows) == 200
    assert all("_target" in r and "debt" in r for r in rows)
    model = train_scoring.fit(rows)
    assert model["rows"] == 200
    assert 0 < model["base_p"] < 1 and model["base_a"] > 0
    assert "lifts" in model and model["calibration"] > 0
