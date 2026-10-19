import db
import control
import digest_data


def test_access_window_filters_utc_events_by_office_day(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "time.db")
    db.init_db()
    with db.connect() as conn:
        for stamp in ("2026-10-15T18:59:59", "2026-10-15T19:00:00",
                      "2026-10-16T18:59:59", "2026-10-16T19:00:00"):
            conn.execute("INSERT INTO audit(ts,actor,action,target) VALUES(?,0,'mgr_app_link','S1')", (stamp,))
        conn.commit()
    rows = control.access_log({"from": "2026-10-16", "to": "2026-10-16"}, ["S1"])["events"]
    assert {r["ts"] for r in rows} == {"2026-10-15T19:00:00", "2026-10-16T18:59:59"}
    assert {r["day"] for r in rows} == {"2026-10-16"}


def test_received_utc_timestamp_is_compared_with_office_visit_day():
    assert digest_data._late_days("2026-10-16", "2026-10-16T20:30:00") == 1
    assert digest_data._late_days("2026-10-16", "2026-10-15T23:30:00") == 0
