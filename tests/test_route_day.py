"""Черновик маршрута дня (/api/route_day): жадная цепочка «ближайший следующий».

Кандидаты — невизитированные кейсы с гео-точкой; визитированные и без точки
не попадают. От стартовой точки выездника цепочка идёт по ближайшим.
"""
import asyncio
import json
from types import SimpleNamespace

import db
import web

EMP = "Маршрутов Тест"


def _add(cid, client, **f):
    row = dict(contract_id=cid, client=client, responsible=EMP,
               partner="ЗК", debt_today="300 000")
    row.update(f)
    with db.connect() as conn:
        keys = ", ".join(f'"{k}"' for k in row)
        qs = ", ".join("?" for _ in row)
        conn.execute(f"INSERT OR REPLACE INTO clients ({keys}) VALUES ({qs})",
                     tuple(row.values()))
        conn.commit()
    if row.get("visit_date"):
        db.record_bot_visit(cid, row, row["visit_date"])


def test_route_day_chain(monkeypatch):
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE responsible = ?", (EMP,))
        conn.commit()
    _add("R-1", "Ближний Б.", geo_lat=40.01, geo_lon=69.01, geo_src="manual")
    _add("R-2", "Дальний Д.", geo_lat=41.0, geo_lon=70.0, geo_src="manual")
    _add("R-3", "Средний С.", geo_lat=40.1, geo_lon=69.1, geo_src="manual")
    _add("R-4", "Визитированный В.", geo_lat=40.0, geo_lon=69.0,
         geo_src="manual", visit_date="2026-01-01")
    _add("R-5", "Безгео Б.")

    monkeypatch.setattr(web, "_uid", lambda r: 424242)
    monkeypatch.setattr(web, "_employee", lambda uid: EMP)
    req = SimpleNamespace(query={"lat": "40.0", "lon": "69.0"})
    resp = asyncio.run(web._route_day(req))
    j = json.loads(resp.text)

    names = [o["client"] for o in j["order"]]
    assert names == ["Ближний Б.", "Средний С.", "Дальний Д."]   # по близости
    assert "Визитированный В." not in names and "Безгео Б." not in names
    assert j["order"][0]["km"] is not None and j["km"] > 0

    # без стартовой точки — старт с самого приоритетного, без km первой ноги
    req2 = SimpleNamespace(query={})
    j2 = json.loads(asyncio.run(web._route_day(req2)).text)
    assert len(j2["order"]) == 3 and j2["order"][0]["km"] is None
