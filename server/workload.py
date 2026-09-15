"""Нагрузка и норма выездов — по географии портфеля и фактическому темпу.

ЗАЧЕМ. Норма «10 выездов на человека» одна на всех, а портфели разные: у
одного должники в трёх кварталах, у другого — по кишлакам за 60 км
(Худжанд, 14.09.2026: «очень много удалённых территорий, близких клиентов
объехали в августе»). Одинаковая норма одному даёт лёгкий день, другому —
невыполнимый, а руководитель не может отличить лень от географии.

КАК СЧИТАЕМ НОРМУ. Рабочий день делится на выезды, каждый из которых стоит
«дорога + время у адреса»:
  • дорога — по дорожной матрице OSRM (свой сервер) для дня из SAMPLE_N
    ближайших к выезду адресов портфеля в порядке объезда VROOM; без дорожного
    графа — прямая с поправкой на дороги. Источник времени в пути один и
    переключаемый (routing_client), чтобы позже подставить онлайн-провайдера;
  • время у адреса — ИЗМЕРЕННОЕ по подтверждённым выездам этого человека
    (evidence.near_min), пока данных мало — значение офиса (visit_minutes);
  • запас 15 % на обед, заправку и звонки.
Норма = сколько таких выездов помещается в день, в пределах NORM_MIN..NORM_MAX.
Это подсказка руководителю рядом с его ручной нормой, а не замена ей.

КАК РАЗБИРАЕМ ДЕНЬ. По сохранённым фактам: первое открытие карточки и
последнее сохранение дают протяжённость дня; сумма минут у адреса — по
подтверждениям (иначе по времени на карточке); дорога — по точкам сохранения
через ту же матрицу; остальное — простой. Так «не выполнил норму» разбирается
на «далеко ездил», «долго стоял у адресов» и «два часа ничего не делал».
"""
from __future__ import annotations

import json
import logging
import statistics
from datetime import date, datetime

import db
import evidence
import gazetteer
import routing_client

WORK_MIN_DEFAULT = 480      # settings: work_minutes
VISIT_MIN_DEFAULT = 20      # settings: visit_minutes — пока нет измеренного
BUFFER = 0.85               # запас на обед, заправку, звонки
NORM_MIN, NORM_MAX = 4, 16
SAMPLE_N = 12               # адресов в расчётном дне — как у маршрута дня
FAR_KM = 30                 # «дальний» адрес от офиса
ROAD_FACTOR = 1.3           # прямая → дорога без графа
FALLBACK_KMH = 30.0         # средняя скорость без графа (как в навигации)
MEASURE_MIN_VISITS = 5      # меньше подтверждённых выездов — берём норму офиса
CACHE_KEY = "workload_cache"


def _setting_int(key: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(str(db.get_setting(key) or "").strip() or 0)
    except (TypeError, ValueError):
        return default
    return value if lo <= value <= hi else default


def work_minutes() -> int:
    return _setting_int("work_minutes", WORK_MIN_DEFAULT, 120, 900)


def visit_minutes_default() -> int:
    return _setting_int("visit_minutes", VISIT_MIN_DEFAULT, 3, 120)


def office_point(employee: str) -> tuple[float, float] | None:
    """Откуда выездник стартует: центр города офиса (employee_city)."""
    city = (db.employee_cities().get(employee) or "").strip()
    if not city:
        return None
    hit = gazetteer.lookup(city, "")
    return (hit[0], hit[1]) if hit else None


def portfolio_points(employee: str) -> list[dict]:
    """Адреса портфеля на карте: по одному на человека, с признаком «годен
    для расчёта» (точность адреса позволяет судить о выезде)."""
    out = []
    for p in db.persons_for_employee(employee):
        pt = None
        for c in p["contracts"]:
            if c["geo_lat"] is not None and c["geo_lon"] is not None:
                tol = db.geo_tolerance_m(
                    c["geo_precision"] if "geo_precision" in c.keys() else "",
                    c["geo_src"] if "geo_src" in c.keys() else "")
                pt = {"rep": p["rep"], "lat": float(c["geo_lat"]), "lon": float(c["geo_lon"]),
                      "usable": tol is not None,
                      "visited": any(db.bot_visit_snapshot(x) is not None
                                     for x in p["contracts"])}
                break
        out.append(pt or {"rep": p["rep"], "lat": None, "lon": None,
                          "usable": False, "visited": False})
    return out


def geometry(points: list[dict], office: tuple[float, float] | None) -> dict:
    """Насколько портфель разбросан: радиус (медиана расстояния от офиса) и
    доля дальних адресов. Без офиса — от центра самих точек."""
    geo = [p for p in points if p["lat"] is not None]
    usable = [p for p in geo if p["usable"]]
    base = usable or geo
    if not base:
        return {"total": len(points), "on_map": len(geo), "usable": len(usable),
                "radius_km": None, "far_share": None, "center": office}
    if office is None:
        office = (statistics.median(p["lat"] for p in base),
                  statistics.median(p["lon"] for p in base))
    dists = sorted(evidence.haversine_m(p["lat"], p["lon"], office[0], office[1]) / 1000.0
                   for p in base)
    return {"total": len(points), "on_map": len(geo), "usable": len(usable),
            "radius_km": round(statistics.median(dists), 1),
            "p75_km": round(dists[int(len(dists) * 0.75)], 1),
            "far_share": round(sum(1 for d in dists if d > FAR_KM) / len(dists), 2),
            "center": office}


def _matrices(coords: list[tuple[float, float]], backends) -> tuple[list, list, str]:
    """Матрицы времени (с) и расстояния (м): дорожный граф или прямая."""
    table = routing_client.best_table(coords, backends=backends) if backends else None
    if table:
        return table["durations"], table["distances"], f"osrm_{table['name']}"
    dist = [[evidence.haversine_m(a[0], a[1], b[0], b[1]) * ROAD_FACTOR for b in coords]
            for a in coords]
    dur = [[d / 1000.0 / FALLBACK_KMH * 3600.0 for d in row] for row in dist]
    return dur, dist, "straight"


def travel_estimate(points: list[dict], start: tuple[float, float] | None,
                    backends=None, vroom_url: str | None = None) -> dict:
    """Дорога за расчётный день: SAMPLE_N ещё не отработанных адресов, ближайших
    к старту, в порядке объезда. Возвращает минуты на выезд и километры."""
    cand = [p for p in points if p["lat"] is not None and p["usable"]]
    if not cand:
        cand = [p for p in points if p["lat"] is not None]
    if not cand:
        return {"per_visit_min": None, "total_min": None, "km": None, "n": 0,
                "src": "none"}
    fresh = [p for p in cand if not p["visited"]] or cand
    center = start or (statistics.median(p["lat"] for p in fresh),
                       statistics.median(p["lon"] for p in fresh))
    fresh.sort(key=lambda p: evidence.haversine_m(p["lat"], p["lon"], center[0], center[1]))
    sample = fresh[:SAMPLE_N]
    coords = [(p["lat"], p["lon"]) for p in sample]
    if start is not None:
        coords = [start, *coords]
    durations, distances, src = _matrices(coords, backends)
    jobs = list(range(1, len(coords)))
    order = routing_client.vroom_order(durations, distances, 0, jobs, vroom_url=vroom_url) \
        if jobs else []
    if order is None or not order:
        order = routing_client._matrix_order(durations, 0, jobs) if jobs else []
    path = [0, *order]
    total_s = sum(durations[a][b] for a, b in zip(path, path[1:]))
    total_m = sum(distances[a][b] for a, b in zip(path, path[1:]))
    n = len(sample)
    return {"per_visit_min": round(total_s / 60.0 / n, 1), "total_min": round(total_s / 60.0),
            "km": round(total_m / 1000.0, 1), "n": n, "src": src}


def measured(employee: str, d_from: str, d_to: str) -> dict:
    """Фактический темп по журналу выездов за окно."""
    rows = db.visits_for_employee(employee, d_from, d_to)
    near, fill, days = [], [], {}
    for v in rows:
        days[v["created_at"]] = days.get(v["created_at"], 0) + 1
        ev = evidence.parse(v["evidence"])
        if ev and ev.get("level") == "confirmed" and isinstance(ev.get("near_min"), int):
            near.append(ev["near_min"])
        if v["fill_seconds"] is not None:
            fill.append(v["fill_seconds"] / 60.0)
    return {
        "visits": len(rows), "field_days": len(days),
        "visits_per_day": round(len(rows) / len(days), 1) if days else None,
        "near_min": round(statistics.median(near), 1) if len(near) >= MEASURE_MIN_VISITS else None,
        "near_n": len(near),
        "fill_min": round(statistics.median(fill), 1) if fill else None,
    }


def suggested_norm(travel_min, visit_min, work_min: int | None = None) -> int | None:
    if travel_min is None or visit_min is None:
        return None
    work = work_min or work_minutes()
    per_visit = float(travel_min) + float(visit_min)
    if per_visit <= 0:
        return None
    return int(max(NORM_MIN, min(NORM_MAX, work * BUFFER // per_visit)))


def _cache() -> dict:
    try:
        data = json.loads(db.get_setting(CACHE_KEY) or "{}")
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def _cache_put(employee: str, travel: dict) -> None:
    data = _cache()
    data[employee] = {"day": date.today().isoformat(), **travel}
    db.set_setting(CACHE_KEY, json.dumps(data, ensure_ascii=False))


def travel_for(employee: str, points: list[dict], office, *, backends=None,
               vroom_url=None, refresh: bool = False) -> dict:
    """Дорога на выезд — с дневным кэшем: матрица на 35 человек по каждому
    открытию экрана норм ни к чему, портфель за день не меняется."""
    cached = _cache().get(employee)
    if cached and not refresh and cached.get("day") == date.today().isoformat():
        return cached
    travel = travel_estimate(points, office, backends=backends, vroom_url=vroom_url)
    try:
        _cache_put(employee, travel)
    except Exception as exc:  # noqa: BLE001 — кэш не должен ломать расчёт
        logging.warning("workload cache: %s", exc)
    return travel


def employee_row(employee: str, d_from: str, d_to: str, *, backends=None,
                 vroom_url=None, refresh: bool = False) -> dict:
    office = office_point(employee)
    points = portfolio_points(employee)
    geo = geometry(points, office)
    travel = travel_for(employee, points, office, backends=backends,
                        vroom_url=vroom_url, refresh=refresh)
    fact = measured(employee, d_from, d_to)
    visit_min = fact["near_min"] if fact["near_min"] is not None else visit_minutes_default()
    norm = suggested_norm(travel.get("per_visit_min"), visit_min)
    now = db.visit_plan_for(employee)
    return {
        "employee": employee,
        "office": db.employee_cities().get(employee, ""),
        "portfolio_total": geo["total"], "portfolio_points": geo["on_map"],
        "portfolio_usable": geo["usable"],
        "radius_km": geo["radius_km"], "far_share": geo["far_share"],
        "travel_min": travel.get("per_visit_min"), "travel_src": travel.get("src"),
        "day_km": travel.get("km"),
        "visit_min": visit_min, "visit_min_measured": fact["near_min"] is not None,
        "norm_suggested": norm, "norm_now": now,
        "gap": (norm - now) if norm is not None else None,
        "visits_per_day": fact["visits_per_day"], "field_days": fact["field_days"],
        "near_avg_min": fact["near_min"], "fill_min": fact["fill_min"],
    }


def workload_data(mgr: dict, rng: dict, employees: list[str], *, backends=None,
                  vroom_url=None, refresh: bool = False) -> dict:
    rows = [employee_row(e, rng["from"], rng["to"], backends=backends,
                         vroom_url=vroom_url, refresh=refresh) for e in employees]
    rows.sort(key=lambda r: ((r["gap"] if r["gap"] is not None else 0), r["employee"].lower()))
    srcs = {r["travel_src"] for r in rows if r.get("travel_src")}
    return {
        "screen": "workload", "range": rng,
        "rows": rows,
        "defaults": {"work_min": work_minutes(), "visit_min": visit_minutes_default(),
                     "travel_src": ("дорогам (OSRM)" if any(s.startswith("osrm") for s in srcs)
                                    else "прямой с поправкой на дороги")},
        "totals": {
            "employees": len(rows),
            "norm_suggested": sum(r["norm_suggested"] or 0 for r in rows),
            "norm_now": sum(r["norm_now"] or 0 for r in rows),
            "under": sum(1 for r in rows if r["gap"] is not None and r["gap"] < 0),
            "over": sum(1 for r in rows if r["gap"] is not None and r["gap"] > 0),
        },
    }


def suggested_map(employees: list[str], d_from: str, d_to: str, *, backends=None,
                  vroom_url=None) -> dict[str, dict]:
    """{ФИО: {norm, why}} — для экрана норм рядом с ручной нормой."""
    out = {}
    for e in employees:
        try:
            r = employee_row(e, d_from, d_to, backends=backends, vroom_url=vroom_url)
        except Exception as exc:  # noqa: BLE001 — подсказка не должна ломать экран норм
            logging.warning("workload suggest %s: %s", e, exc)
            continue
        if r["norm_suggested"] is None:
            continue
        why = (f"дорога {r['travel_min']} мин + у адреса {r['visit_min']} мин"
               + (" (измерено)" if r["visit_min_measured"] else ""))
        if r["radius_km"] is not None:
            why += f", радиус {r['radius_km']} км"
        out[e] = {"norm": r["norm_suggested"], "why": why}
    return out


def _ms(value) -> int | None:
    return evidence.to_ms(value)


def day_rows(employee: str, d_from: str, d_to: str, *, backends=None) -> list[dict]:
    """Разбор дней: протяжённость, у адресов, дорога, простой, км."""
    rows = db.visits_for_employee(employee, d_from, d_to)
    by_day: dict[str, list] = {}
    for v in rows:
        by_day.setdefault(v["created_at"], []).append(v)
    out = []
    for day in sorted(by_day):
        items = []
        for v in by_day[day]:
            ev = evidence.parse(v["evidence"]) or {}
            ms = _ms(v["saved_at"]) or _ms(v["received_at"])
            items.append({"ms": ms, "lat": v["visit_lat"], "lon": v["visit_lon"],
                          "near_min": ev.get("near_min") if ev.get("level") == "confirmed" else None,
                          "fill_min": (v["fill_seconds"] or 0) / 60.0 if v["fill_seconds"] else None,
                          "opened_ms": (ms - (v["fill_seconds"] or 0) * 1000) if ms else None})
        timed = sorted((i for i in items if i["ms"]), key=lambda i: i["ms"])
        first = min((i["opened_ms"] or i["ms"] for i in timed), default=None)
        last = max((i["ms"] for i in timed), default=None)
        span = round((last - first) / 60000) if first and last else None
        address = 0.0
        for i in items:
            if i["near_min"] is not None:
                address += i["near_min"]
            elif i["fill_min"] is not None:
                address += i["fill_min"]
        pts = [(i["lat"], i["lon"]) for i in timed if i["lat"] is not None]
        travel_min = km = None
        if len(pts) >= 2:
            try:
                dur, dist, _src = _matrices(pts, backends)
                travel_min = round(sum(dur[k][k + 1] for k in range(len(pts) - 1)) / 60.0)
                km = round(sum(dist[k][k + 1] for k in range(len(pts) - 1)) / 1000.0, 1)
            except Exception as exc:  # noqa: BLE001
                logging.warning("day travel %s %s: %s", employee, day, exc)
        idle = None
        if span is not None:
            idle = max(0, span - round(address) - (travel_min or 0))
        d = db._as_date(day)
        out.append({"date": day, "label": f"{d.day:02d}.{d.month:02d}" if d else day,
                    "visits": len(items),
                    "first": evidence.local_hhmm(first), "last": evidence.local_hhmm(last),
                    "span_min": span, "address_min": round(address) if items else None,
                    "travel_min": travel_min, "idle_min": idle, "km": km})
    return out


def employee_data(employee: str, rng: dict, *, backends=None, vroom_url=None) -> dict:
    row = employee_row(employee, rng["from"], rng["to"], backends=backends,
                       vroom_url=vroom_url)
    return {"screen": "workload", "range": rng, "employee": employee,
            "summary": row,
            "days": day_rows(employee, rng["from"], rng["to"], backends=backends)}
