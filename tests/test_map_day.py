"""«Карта дня»: подписанные ссылки (maplink) и сбор точек (db.day_map_points).

Ссылка = HMAC от (сотрудник, дата, expiry) на ключе из токена бота: чужая подпись,
испорченная дата или истёкший срок — не пускают. Точки: визиты с гео-контролем,
метки выездника за день, адреса обработанных должников.
"""
import time
from datetime import date
from urllib.parse import parse_qs, unquote

import db
import maplink

TODAY = date.today().isoformat()
EMP = "Картограф Тест"


def _q(qs: str) -> dict:
    return {k: v[0] for k, v in parse_qs(qs).items()}


def test_sign_verify_roundtrip():
    db.init_db()   # ключ подписи читает settings.bot_token
    qs = _q(maplink.sign(EMP, TODAY))
    assert unquote(qs["e"]) == EMP
    assert maplink.verify(unquote(qs["e"]), qs["d"], qs["x"], qs["s"])
    # подпись не переносится на другого сотрудника/дату
    assert not maplink.verify("Другой", qs["d"], qs["x"], qs["s"])
    assert not maplink.verify(unquote(qs["e"]), "2020-01-01", qs["x"], qs["s"])
    assert not maplink.verify(unquote(qs["e"]), qs["d"], qs["x"], "0" * 32)
    # истёкшая — не пускает (exp в прошлом даёт другую подпись, но и время проверяем)
    past = str(int(time.time()) - 10)
    assert not maplink.verify(EMP, TODAY, past, maplink._sig(EMP, TODAY, int(past)))
    assert not maplink.verify(EMP, TODAY, "мусор", qs["s"])


def test_day_map_points():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM clients WHERE responsible = ?", (EMP,))
        conn.execute("DELETE FROM visits WHERE contract_id LIKE 'MAP-%'")
        conn.execute("DELETE FROM markers WHERE telegram_id = 909")
        conn.execute("DELETE FROM agent_bind WHERE telegram_id = 909")
        conn.execute("INSERT OR REPLACE INTO clients (contract_id, client, responsible, "
                     "visit_date, geo_lat, geo_lon, geo_src) VALUES "
                     "('MAP-1','Адресный А.',?,?,40.5,69.5,'nominatim')", (EMP, TODAY))
        conn.execute("INSERT INTO agent_bind (telegram_id, employee) VALUES (909, ?)", (EMP,))
        conn.commit()
    db.log_visit("MAP-1", 909, "визит", op_id="map-v1", lat=40.51, lon=69.51, dist_m=140)
    db.record_bot_visit("MAP-1", {}, TODAY)
    db.add_marker(909, "case", 40.52, 69.52, label="подъезд", op_id="map-m1")

    pts = db.day_map_points(EMP, TODAY)
    assert [t["client"] for t in pts["targets"]] == ["Адресный А."]
    assert pts["visits"][0]["dist_m"] == 140 and pts["visits"][0]["client"] == "Адресный А."
    assert pts["markers"][0]["label"] == "подъезд" and pts["markers"][0]["at"]
    # чужой сотрудник — пусто
    empty = db.day_map_points("Никто Такой", TODAY)
    assert empty == {"visits": [], "markers": [], "targets": []}
