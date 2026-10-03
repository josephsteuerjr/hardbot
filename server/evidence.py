"""Подтверждение выезда: был ли человек у адреса, когда и как долго — по следам
телефона, а не по слову выездника.

ЗАЧЕМ. Руководство (Шемякин, 11–14.09.2026) прямо назвало риск: выезды
«понатыкивают» потом, пачкой, из офиса или из машины. До этого система знала
про выезд одну точку — где нажали «Сохранить выезд» — и одно время — когда
открыли карточку. Этого мало: карточку открывают заранее из любопытства, а
сохранить можно и за квартал от адреса. Здесь собираются ВСЕ следы одного
выезда и из них выводится вердикт.

СЛЕДЫ (samples). Каждый — точка телефона с моментом, погрешностью и признаком
связи. Откуда берутся:
  open   — точка в момент открытия карточки (оболочка v54+);
  sample — точки, пока карточка открыта (watchPosition, шаг ≥30 с или ≥25 м);
  draft  — точка, дошедшая до сервера с автосохранением черновика: у неё есть
           серверное время приёма, то есть это доказательство СВЯЗИ и места
           одновременно, и подделать его с телефона нельзя;
  photo  — точка съёмки снимка (photo_geo);
  save   — точка в момент «Сохранить выезд» (была и раньше — visit_lat/lon).

ДОПУСК — тот же, что у гео-контроля (db.geo_tolerance_m): зависит от того, до
какого уровня разобран адрес, и складывается с погрешностью точки телефона.
Адрес, известный только до города или района, НЕ СУДИМ: вердикт «проверить
нельзя» честнее числа, которое обвинит честного выездника.

ВЕРДИКТ хранится на визите (visits.evidence_level/evidence) и не
пересчитывается: точка должника после следующего геокодинга уезжает, и
честный выезд задним числом стал бы нарушением — тот же принцип, что у geo_far.

СЕРИИ (series_flags) считаются по уже сохранённым фактам дня — точкам и
времени сохранения — и поэтому вычисляются на лету: «три сохранения за десять
минут с одной точки при адресах за километр друг от друга» — признак ввода
пачкой, а не выезда. Это сигнал руководителю, а не приговор: в одном доме
живут два должника.

Модуль ЧИСТЫЙ: без базы и сети — его можно проверить отдельно.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone

# Ташкент и Душанбе — UTC+5, перевода часов нет. Сервер живёт по UTC, а
# руководителю нужно «14:02», а не «09:02».
FIELD_TZ = timezone(timedelta(hours=5))

# Погрешность точки, хуже которой судить нельзя: сетевой фикс в подъезде даёт
# 800–1500 м, и это ещё честная точка. Дальше — «где-то в районе», не улика.
ACC_USABLE_M = 1500
# Точка телефона, дошедшая до сервера НЕ позже этого после нажатия «Сохранить
# выезд», означает связь на месте. Оффлайн-очередь доносит через часы и дни.
ONLINE_SAVE_GAP_S = 120
# Признаки серии: три сохранения за десять минут; соседние точки сохранения
# ближе ста метров при адресах дальше километра; скорость между точками выше
# той, с которой ездят по городу.
SERIES_WINDOW_S = 600
SERIES_MIN_N = 3
SAME_SPOT_M = 100
SAME_SPOT_ADDR_M = 1000
TELEPORT_KMH = 150
TELEPORT_MIN_M = 2000
MAX_SAMPLES = 300

LEVELS = ("confirmed", "far", "nofix", "nogeo", "coarse")
LEVEL_TEXT = {
    "confirmed": "подтверждён на адресе",
    "far": "у адреса не был",
    "nofix": "GPS-точка слишком грубая",
    "nogeo": "координат нет",
    "coarse": "адрес неточный",
}
LEVEL_EMOJI = {"confirmed": "🛡", "far": "⚠️", "nofix": "❔", "nogeo": "📵",
               "coarse": "❔"}
# Не удалось проверить — всё, что не подтверждение и не нарушение.
UNVERIFIED = ("nofix", "nogeo", "coarse")

_PRECISION_RU = {"city": "города", "district": "района", "region": "области"}


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371000.0 * math.asin(math.sqrt(h))


def to_ms(value) -> int | None:
    """Момент → миллисекунды эпохи. Принимает число (мс или секунды) и ISO-строку.
    ISO без зоны считается серверным временем (UTC) — так пишут saved_at,
    received_at и captured_at."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or value <= 0:
            return None
        return int(value if value > 10_000_000_000 else value * 1000)
    text = str(value).strip()
    if not text:
        return None
    if text.replace(".", "", 1).isdigit():
        try:
            return to_ms(float(text))
        except ValueError:
            return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


def local_hhmm(ms) -> str | None:
    ms = to_ms(ms)
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000.0, FIELD_TZ).strftime("%H:%M")


def local_iso(ms) -> str | None:
    ms = to_ms(ms)
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000.0, FIELD_TZ).isoformat(timespec="seconds")


def normalize_samples(raw, *, kind_default: str = "sample",
                      limit: int = MAX_SAMPLES) -> list[dict]:
    """Привести следы с телефона к одному виду. Мусор молча отбрасывается:
    телефон в поле присылает NaN, нули и точность в пять километров — падать
    из-за этого посреди выезда нельзя (тот же принцип, что в add_track_points)."""
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for item in raw[:limit]:
        if not isinstance(item, dict):
            continue
        ts = to_ms(item.get("ts"))
        if ts is None:
            continue
        try:
            lat, lon = float(item.get("lat")), float(item.get("lon"))
        except (TypeError, ValueError):
            lat = lon = None
        if lat is not None and (not (-90 <= lat <= 90 and -180 <= lon <= 180)
                                or (abs(lat) < 0.01 and abs(lon) < 0.01)):
            lat = lon = None
        try:
            acc = int(float(item.get("acc") or 0))
        except (TypeError, ValueError):
            acc = 0
        acc = max(0, min(acc, 100_000))
        kind = str(item.get("k") or item.get("kind") or kind_default)[:12]
        out.append({"ts": ts, "lat": lat, "lon": lon, "acc": acc,
                    "on": _flag(item.get("on")), "vis": _flag(item.get("vis")),
                    "k": kind})
    out.sort(key=lambda s: s["ts"])
    return out


def _flag(value) -> bool | None:
    """0/1, true/false, «да»/«нет» → bool; отсутствие → None («не знаем»)."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "yes", "да", "on")


def _dist(sample: dict, addr) -> float | None:
    if addr is None or sample.get("lat") is None:
        return None
    return haversine_m(sample["lat"], sample["lon"], addr[0], addr[1])


def _pretty_m(m) -> str:
    if m is None:
        return "—"
    return f"{m / 1000:.1f} км" if m >= 1000 else f"{int(m)} м"


def build(samples: list[dict], addr: tuple[float, float] | None,
          tolerance: int | None, *, precision: str = "",
          opened_ms=None, saved_ms=None, received_ms=None,
          online_open=None, foreground_ms=None) -> dict:
    """Вердикт по одному выезду.

    samples — нормализованные следы (normalize_samples), любого вида;
    addr — точка должника; tolerance — допуск по точности адреса (None =
    адрес не судим); opened/saved — отметки телефона; received — приём
    сервером (по нему видно, была ли связь при сохранении)."""
    geo = [s for s in samples if s.get("lat") is not None]
    usable = [s for s in geo if (s.get("acc") or 0) <= ACC_USABLE_M]
    dists = [(s, _dist(s, addr)) for s in usable]
    near = [s for s, d in dists
            if d is not None and tolerance is not None
            and d <= tolerance + (s.get("acc") or 0)]
    min_dist = min((d for _s, d in dists if d is not None), default=None)
    known_online = [s for s in samples if s.get("on") is not None]
    online_share = (round(sum(1 for s in known_online if s["on"]) / len(known_online), 2)
                    if known_online else None)
    saved = to_ms(saved_ms)
    received = to_ms(received_ms)
    online_save = None
    if saved is not None and received is not None:
        online_save = (received - saved) <= ONLINE_SAVE_GAP_S * 1000
    served = sum(1 for s in samples if s.get("k") == "draft")
    photo_near = None
    photos = [s for s, d in dists if s.get("k") == "photo"]
    if photos:
        photo_near = any(s in near for s in photos)

    if addr is None or tolerance is None:
        level = "coarse"
    elif not geo:
        level = "nogeo"
    elif not usable:
        level = "nofix"
    elif near:
        level = "confirmed"
    else:
        level = "far"

    def _of(kind):
        for s, d in dists:
            if s.get("k") == kind:
                return d
        return None

    near_first = near[0]["ts"] if near else None
    near_last = near[-1]["ts"] if near else None
    # «Был у адреса, но заполнил не там»: выезд подтверждён следами, а
    # «Сохранить выезд» нажато за пределами допуска — в машине, в кафе, в
    # офисе. Руководству это отдельный вопрос, поэтому отдельный признак,
    # а не понижение вердикта: человек на адресе был.
    save_s = next((s for s, d in dists if s.get("k") == "save" and d is not None), None)
    saved_away = None
    if level == "confirmed" and save_s is not None:
        saved_away = _dist(save_s, addr) > tolerance + (save_s.get("acc") or 0)
    ev = {
        "level": level,
        "addr": addr is not None,
        "at_address": True if level == "confirmed" else False if level == "far" else None,
        "near_first_ms": near_first, "near_last_ms": near_last,
        "near_first": local_hhmm(near_first), "near_last": local_hhmm(near_last),
        "near_min": (max(0, round((near_last - near_first) / 60000)) if near else None),
        "near_n": len(near),
        "min_dist_m": None if min_dist is None else int(round(min_dist)),
        "open_dist_m": None if _of("open") is None else int(round(_of("open"))),
        "save_dist_m": None if _of("save") is None else int(round(_of("save"))),
        "saved_away": saved_away,
        "tolerance_m": tolerance,
        "precision": precision or "",
        "samples": len(samples), "geo_samples": len(geo), "usable": len(usable),
        "served": served,
        "photo_near": photo_near,
        "online_open": None if online_open is None else bool(online_open),
        "online_save": online_save,
        "online_share": online_share,
        "opened": local_hhmm(opened_ms), "saved": local_hhmm(saved),
        "card_min": (max(0, round((to_ms(saved_ms) - to_ms(opened_ms)) / 60000))
                     if to_ms(saved_ms) is not None and to_ms(opened_ms) is not None
                     and 0 <= to_ms(saved_ms) - to_ms(opened_ms) <= 12 * 3600 * 1000
                     else None),
        "foreground_min": (max(0, round(float(foreground_ms) / 60000))
                           if isinstance(foreground_ms, (int, float)) and foreground_ms >= 0
                           else None),
    }
    if not usable and geo:
        ev["worst_acc_m"] = min((s.get("acc") or 0) for s in geo)
    ev["note"] = note(ev)
    return ev


def note(ev: dict) -> str:
    """Одна человеческая строка по вердикту — для карточки и рассылки."""
    level = ev.get("level")
    tail = []
    if ev.get("photo_near"):
        tail.append("фото на месте")
    if ev.get("online_save") is True:
        tail.append("связь была")
    elif ev.get("online_save") is False:
        tail.append("сохранён без связи, доехал позже")
    if level == "confirmed":
        first, last, mins = ev.get("near_first"), ev.get("near_last"), ev.get("near_min")
        if mins and first and last and first != last:
            head = f"На адресе {first}–{last} ({mins} мин)"
        else:
            head = f"На адресе в {first}" if first else "На адресе"
        if ev.get("saved_away"):
            head += (f" · заполнен не на адресе: в {_pretty_m(ev.get('save_dist_m'))}"
                     + (f" в {ev['saved']}" if ev.get("saved") else ""))
        return " · ".join([head, *tail])
    if level == "far":
        head = f"У адреса не был: ближе {_pretty_m(ev.get('min_dist_m'))} не подходил"
        return " · ".join([head, *tail])
    if level == "nofix":
        acc = ev.get("worst_acc_m")
        return (f"Проверить не удалось: телефон выдал точку с точностью {_pretty_m(acc)}"
                if acc else "Проверить не удалось: телефон выдал слишком грубую точку")
    if level == "nogeo":
        return "Проверить не удалось: телефон не передал координаты"
    if not ev.get("addr"):
        return "Проверить не удалось: адреса нет на карте"
    prec = _PRECISION_RU.get(ev.get("precision") or "")
    if prec:
        return f"Проверить не удалось: адрес в реестре только до {prec}"
    return "Проверить не удалось: адрес известен неточно"


def line(ev: dict | None) -> str | None:
    """Строка для карточки визита руководителю: эмодзи + вердикт."""
    if not ev or not ev.get("level"):
        return None
    return f"{LEVEL_EMOJI.get(ev['level'], '❔')} {ev.get('note') or LEVEL_TEXT.get(ev['level'], '')}"


def parse(text) -> dict | None:
    """Сохранённый JSON вердикта → словарь; мусор → None."""
    if not text:
        return None
    if isinstance(text, dict):
        return text
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def dumps(ev: dict) -> str:
    return json.dumps(ev, ensure_ascii=False, separators=(",", ":"))


def series_flags(day_visits: list[dict]) -> dict:
    """Признаки серии по выездам одного человека за один день.

    Вход: [{"id", "ms" (момент сохранения), "lat", "lon" (точка сохранения),
    "alat", "alon" (адрес)}]. Выход: {id: {"batch", "same_spot", "teleport",
    "speed_kmh"}}. Флаг ставится ОБОИМ соседям пары: без второго конца
    серия из двух была бы «одним нарушителем»."""
    items = [dict(v) for v in day_visits if to_ms(v.get("ms")) is not None]
    for v in items:
        v["ms"] = to_ms(v["ms"])
    items.sort(key=lambda v: v["ms"])
    out = {v["id"]: {"batch": False, "same_spot": False, "teleport": False,
                     "speed_kmh": None} for v in items}
    # Пачка: скользящее окно по времени сохранения.
    for i, a in enumerate(items):
        window = [b for b in items[i:] if b["ms"] - a["ms"] <= SERIES_WINDOW_S * 1000]
        if len(window) >= SERIES_MIN_N:
            for b in window:
                out[b["id"]]["batch"] = True
    # Одна точка на разные адреса и телепортация — по соседним парам.
    for a, b in zip(items, items[1:]):
        if None in (a.get("lat"), a.get("lon"), b.get("lat"), b.get("lon")):
            continue
        d_pts = haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
        dt_h = (b["ms"] - a["ms"]) / 3600000.0
        if dt_h > 0:
            speed = d_pts / 1000.0 / dt_h
            out[b["id"]]["speed_kmh"] = round(speed, 1)
            if d_pts >= TELEPORT_MIN_M and speed > TELEPORT_KMH:
                out[a["id"]]["teleport"] = out[b["id"]]["teleport"] = True
        if None in (a.get("alat"), a.get("alon"), b.get("alat"), b.get("alon")):
            continue
        d_addr = haversine_m(a["alat"], a["alon"], b["alat"], b["alon"])
        if d_pts < SAME_SPOT_M and d_addr > SAME_SPOT_ADDR_M:
            out[a["id"]]["same_spot"] = out[b["id"]]["same_spot"] = True
    return out


def is_series(flags: dict | None) -> bool:
    return bool(flags and (flags.get("batch") or flags.get("same_spot")
                           or flags.get("teleport")))


def series_text(flags: dict | None) -> str:
    if not flags:
        return ""
    parts = []
    if flags.get("batch"):
        parts.append("пачкой")
    if flags.get("same_spot"):
        parts.append("с одной точки")
    if flags.get("teleport"):
        parts.append(f"скорость {int(flags.get('speed_kmh') or 0)} км/ч")
    return ", ".join(parts)
