"""activity_stats — активность работников: визиты/метки за 7/30 дней, простой.

Окна включают границу: created_at >= today-7 / today-30 (сравнение ISO-строк,
работает и для дат visits, и для datetime меток). Сортировка задокументирована
как «проблемные сверху»: сначала работники вовсе без активности, затем по
убыванию idle_days; работавшие сегодня — внизу.
"""
from datetime import date, datetime, timedelta

import db

TID_ACTIVE, TID_COURIER, TID_IDLE = 910001, 910002, 910003
EMP_ACTIVE, EMP_COURIER, EMP_IDLE = "Актв Выездной", "Актв Курьерский", "Актв Спящий"
OUR = (TID_ACTIVE, TID_COURIER, TID_IDLE)


def _seed():
    db.init_db()
    db.bind_agent(TID_ACTIVE, EMP_ACTIVE, "agent")
    db.bind_agent(TID_COURIER, EMP_COURIER, "courier")
    db.bind_agent(TID_IDLE, EMP_IDLE, "agent")
    with db.connect() as conn:
        # клиент выездника — для photos_total (matched по responsible)
        conn.execute(
            "INSERT OR IGNORE INTO clients (contract_id, client, responsible, photos_count) "
            "VALUES (?,?,?,?)", ("ACT-PH-1", "Активов Тест", EMP_ACTIVE, 3))
        # старый визит выездника: 8 дней назад — в окне 30д, вне 7д
        conn.execute(
            "INSERT INTO visits (contract_id, agent_id, created_at, payload) VALUES (?,?,?,?)",
            ("ACT-PH-1", TID_ACTIVE, (date.today() - timedelta(days=8)).isoformat(), "старый"))
        # метка курьера: 10 дней назад (datetime, как пишет add_marker)
        conn.execute(
            "INSERT INTO markers (telegram_id, kind, lat, lon, created_at) VALUES (?,?,?,?,?)",
            (TID_COURIER, "courier", 41.31, 69.24,
             (datetime.now() - timedelta(days=10)).isoformat(timespec="seconds")))
        conn.commit()
    db.log_visit("ACT-PH-1", TID_ACTIVE, "визит сегодня")   # created_at = сегодня


def test_activity_stats_counts_and_idle():
    _seed()
    ours = {w["telegram_id"]: w for w in db.activity_stats() if w["telegram_id"] in OUR}
    assert set(ours) == set(OUR)

    a = ours[TID_ACTIVE]                     # визит сегодня + визит 8 дней назад
    assert a["employee"] == EMP_ACTIVE and a["kind"] == "agent"
    assert a["visits_7d"] == 1 and a["visits_30d"] == 2
    assert a["markers_7d"] == 0 and a["markers_30d"] == 0
    assert a["last_activity"] == date.today().isoformat()
    assert a["idle_days"] == 0
    assert a["photos_total"] == 3            # matched в реестре по responsible

    c = ours[TID_COURIER]                    # только метка 10 дней назад
    assert c["kind"] == "courier"
    assert c["visits_7d"] == 0 and c["visits_30d"] == 0
    assert c["markers_7d"] == 0 and c["markers_30d"] == 1
    assert c["idle_days"] == 10
    assert c["last_activity"].startswith((date.today() - timedelta(days=10)).isoformat())
    assert c["photos_total"] is None         # в реестре не matched — данных нет

    i = ours[TID_IDLE]                       # активности не было вовсе
    assert i["visits_30d"] == 0 and i["markers_30d"] == 0
    assert i["last_activity"] is None and i["idle_days"] is None


def test_activity_stats_sorted_idle_first():
    _seed()
    order = [w["telegram_id"] for w in db.activity_stats() if w["telegram_id"] in OUR]
    # без активности → простой 10 дн → активен сегодня
    assert order == [TID_IDLE, TID_COURIER, TID_ACTIVE]
