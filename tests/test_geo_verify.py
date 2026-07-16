"""Гео-контроль визита: дистанция «точка закрытия ↔ точка должника».

visits получил visit_lat/visit_lon/dist_m (авто-миграция); dist_m считает web._save
из body.geo миниаппа. Здесь: хранение (log_visit/last_visit_geo), строка в карточке
дайджеста (📍/⚠️ по порогу geo_alert_m) и пометка в реалтайм-пуше руководителю.
"""
import asyncio
from datetime import date
from types import SimpleNamespace

import db
import digest
import web

TODAY = date.today().isoformat()
EMP = "Геоконтролев Тест"
MGR = 700003


def _add(cid, client, **f):
    row = dict(contract_id=cid, client=client, responsible=EMP,
               partner="ЗК", debt_today="100 000", visit_date=TODAY)
    row.update(f)
    with db.connect() as conn:
        keys = ", ".join(f'"{k}"' for k in row)
        qs = ", ".join("?" for _ in row)
        conn.execute(f"INSERT OR REPLACE INTO clients ({keys}) VALUES ({qs})",
                     tuple(row.values()))
        conn.commit()


def _seed():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE responsible = ?", (EMP,))
        conn.execute("DELETE FROM visits WHERE contract_id LIKE 'GV-%'")
        conn.commit()
    _add("GV-1", "Далёкий Д.", geo_lat=40.28, geo_lon=69.62, geo_src="nominatim")
    _add("GV-2", "Точный Т.", geo_lat=40.28, geo_lon=69.62, geo_src="manual")
    db.set_manager(MGR, "Гео", [], [EMP])


def test_log_and_last_visit_geo():
    _seed()
    db.log_visit("GV-1", 1, "старый", op_id="gv-old", lat=40.0, lon=69.0, dist_m=5000)
    db.log_visit("GV-1", 1, "новый", op_id="gv-new", lat=40.3, lon=69.6, dist_m=2500)
    db.log_visit("GV-2", 1, "без гео", op_id="gv-nogeo")   # NULL-ы не мешают
    gv = db.last_visit_geo(["GV-1", "GV-2"])
    assert gv["dist_m"] == 2500 and gv["payload"] == "новый"
    assert db.last_visit_geo([]) is None
    db.delete_manager(MGR)


def test_person_card_shows_distance():
    _seed()
    db.log_visit("GV-1", 1, "далеко", op_id="gv-far", lat=40.0, lon=69.0, dist_m=2500)
    db.log_visit("GV-2", 1, "рядом", op_id="gv-near", lat=40.28, lon=69.62, dist_m=70)
    mgr = db.get_manager(MGR)
    ei = digest.sorted_employees(mgr).index(EMP)
    persons = db.persons_processed(EMP, TODAY)
    ci_far = next(i for i, p in enumerate(persons) if p["client"] == "Далёкий Д.")
    ci_near = next(i for i, p in enumerate(persons) if p["client"] == "Точный Т.")
    far, _ = digest.person_card(mgr, TODAY, ei, ci_far)
    assert "⚠️ Визит закрыт в 2.5 км от адреса!" in far
    near, _ = digest.person_card(mgr, TODAY, ei, ci_near)
    assert "📍 Точка закрытия: 70 м от адреса" in near
    db.delete_manager(MGR)


class _Bot:
    def __init__(self):
        self.msgs = []

    async def send_message(self, chat_id, text):
        self.msgs.append((chat_id, text))


def _push(row, dist_m):
    bot = _Bot()
    req = SimpleNamespace(app={"bot": bot})
    asyncio.run(web._notify_managers(req, row, dist_m))
    return bot.msgs


def test_push_geo_marks():
    _seed()
    db.set_setting("visit_push", "1")
    row = db.get_client("GV-1")
    far = _push(row, 2500)
    assert far and "⚠️ Закрыт в 2.5 км от адреса!" in far[0][1]
    near = _push(row, 70)
    assert "📍 На адресе (70 м от точки)" in near[0][1]
    none = _push(row, None)
    assert "📍 На адресе" not in none[0][1] and "⚠️ Закрыт" not in none[0][1]
    db.delete_manager(MGR)
