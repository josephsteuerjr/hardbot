"""
Раздача дня на БРИГАДУ: N выездников.

Два решателя, один и тот же формат ответа:
  • VROOM (настоящий VRP) — если поднят и отвечает по $VROOM_URL: окна рабочего дня,
    service-time на адрес, приоритет = скор, вместимость (балансирует нагрузку на всех N).
  • Фолбэк (всегда работает): гео-балансировка (мини k-means) + 2-opt на каждого по OSRM.

Геометрию/км/минуты в обоих случаях добираем через `routing` (реальные дороги OSRM),
поэтому ответы VROOM и фолбэка взаимозаменяемы для дашборда/миниаппа.
"""
from __future__ import annotations
import datetime as _dt
import json
import math
import os
import random
import urllib.request

import routing

PALETTE = ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2"]

# 230-ФЗ: ЖЁСТКОЕ окно часов контакта — будни 08:00–22:00, выходные 09:00–20:00.
# Это ограничение оптимизатора (time_window машины в VROOM), а не пожелание.
LAW_WEEKDAY = (8 * 3600, 22 * 3600)
LAW_WEEKEND = (9 * 3600, 20 * 3600)
SERVICE = int(os.getenv("VROOM_SERVICE_SEC", "1200"))           # 20 мин на адрес


def _day_window(when: _dt.date | None = None) -> tuple[int, int]:
    """Окно рабочего дня (сек от полуночи) по 230-ФЗ для нужной даты. Оператор может
    лишь СУЗИТЬ окно через env (VROOM_DAY_START/END) — расширить за закон нельзя."""
    when = when or _dt.date.today()
    lo, hi = LAW_WEEKEND if when.weekday() >= 5 else LAW_WEEKDAY
    env_lo = os.getenv("VROOM_DAY_START")
    env_hi = os.getenv("VROOM_DAY_END")
    if env_lo:
        lo = max(lo, int(env_lo))     # только сужение (не раньше закона)
    if env_hi:
        hi = min(hi, int(env_hi))     # только сужение (не позже закона)
    return lo, hi


def _kmeans(coords, k, iters=30):
    if k <= 1 or len(coords) <= k:
        return list(range(len(coords))) if k > 1 else [0] * len(coords)
    random.seed(7)
    cents = [coords[i] for i in random.sample(range(len(coords)), k)]
    labels = [0] * len(coords)
    for _ in range(iters):
        for i, c in enumerate(coords):
            labels[i] = min(range(k), key=lambda j: (c[0]-cents[j][0])**2 + (c[1]-cents[j][1])**2)
        new = []
        for j in range(k):
            grp = [coords[i] for i in range(len(coords)) if labels[i] == j]
            new.append((sum(p[0] for p in grp)/len(grp), sum(p[1] for p in grp)/len(grp)) if grp else cents[j])
        if new == cents:
            break
        cents = new
    return labels


def _agent(seq, start, osrm_url):
    """Один выездник: метрики по уже заданному порядку точек (routing.summarize)."""
    r = routing.summarize(seq, start=start, osrm_url=osrm_url)
    return {"color": None, "count": len(seq), "stops_xy": r["stops_xy"], "line": r["line"],
            "km": r["km"], "minutes": r["minutes"], "drive_min": r.get("drive_min"),
            "debt_sum": r["debt_sum"], "matrix_src": r["matrix_src"]}


def _wrap(agents: list[dict], solver: str, unassigned: int = 0) -> dict:
    for i, a in enumerate(agents):
        a["id"] = i + 1
        a["color"] = PALETTE[i % len(PALETTE)]
    return {"agents": agents, "n": len(agents),
            "km": round(sum(a["km"] for a in agents), 1),
            "minutes": max((a["minutes"] for a in agents), default=0),   # параллельно → max
            "debt_sum": sum(a["debt_sum"] for a in agents),
            "count": sum(a["count"] for a in agents),
            "unassigned": unassigned, "solver": solver}


def _vroom_plan(pts, n, start, vroom_url, osrm_url):
    """Настоящий VROOM. None при любой ошибке → caller делает фолбэк на OSRM-сплит."""
    depot = [start[1], start[0]] if start else None     # (lat,lon) → [lon,lat]
    cap = max(1, math.ceil(len(pts) / n))                # вместимость → задействуем всех N и балансируем
    day_start, day_end = _day_window()                   # 230-ФЗ окно часов (будни/выходные)
    vehicles = []
    for i in range(n):
        v = {"id": i + 1, "capacity": [cap], "time_window": [day_start, day_end]}
        if depot:
            v["start"] = depot; v["end"] = depot
        vehicles.append(v)
    id2pt, jobs = {}, []
    for idx, p in enumerate(pts):
        jid = idx + 1
        id2pt[jid] = p
        jobs.append({"id": jid, "location": [p["lon"], p["lat"]],
                     "service": SERVICE, "delivery": [1],
                     "priority": max(0, min(100, int(round(p.get("score", 0) or 0))))})
    req = {"vehicles": vehicles, "jobs": jobs, "options": {"g": False}}
    try:
        rq = urllib.request.Request(vroom_url.rstrip("/"), data=json.dumps(req).encode(),
                                    headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(rq, timeout=20) as r:
            sol = json.loads(r.read())
        if sol.get("code") != 0:
            return None
    except Exception:
        return None

    routes = []
    for rt in sol.get("routes", []):
        seq = [id2pt[s["id"]] for s in rt.get("steps", []) if s.get("type") == "job"]
        if seq:
            routes.append(seq)
    if not routes:
        return None

    # неназначенные (тугие окна/вместимость) — подвесить к ближайшей бригаде, чтобы карта была полной.
    # Ключ — стабильный contract_id (rep), а не id() объекта в памяти (было хрупко: ломалось бы
    # при копировании точек; rep уникален по реестру). Фолбэк на id() — только если rep пуст.
    def _key(p):
        k = p.get("rep")
        return k if k not in (None, "") else id(p)
    seen = {_key(p) for seq in routes for p in seq}
    leftover = [p for p in pts if _key(p) not in seen]
    for p in sorted(leftover, key=lambda x: x.get("score", 0) or 0, reverse=True):
        j = min(range(len(routes)), key=lambda k: min(
            routing.haversine((p["lat"], p["lon"]), (q["lat"], q["lon"])) for q in routes[k]))
        routes[j].append(p)

    agents = [_agent(seq, start, osrm_url) for seq in routes]
    return _wrap(agents, "vroom", unassigned=len(leftover))


def _split_plan(pts, n, start, osrm_url):
    """Фолбэк: гео-балансировка (k-means) + 2-opt на каждого (реальные дороги OSRM)."""
    coords = [(p["lat"], p["lon"]) for p in pts]
    labels = _kmeans(coords, n) if n > 1 else [0] * len(pts)
    agents = []
    for j in range(n):
        grp = [pts[i] for i in range(len(pts)) if labels[i] == j]
        if not grp:
            continue
        r = routing.optimize(grp, start=start, osrm_url=osrm_url)
        agents.append({"color": None, "count": len(grp), "stops_xy": r["stops_xy"],
                       "line": r["line"], "km": r["km"], "minutes": r["minutes"],
                       "drive_min": r.get("drive_min"), "debt_sum": r["debt_sum"],
                       "matrix_src": r["matrix_src"]})
    return _wrap(agents, "split")


def plan(points: list[dict], n_agents: int, start=None, osrm_url=None, vroom_url=None) -> dict:
    pts = [p for p in points if p.get("lat") is not None]
    n = max(1, min(int(n_agents), 6))
    if not pts:
        return {"agents": [], "n": 0, "km": 0, "minutes": 0, "debt_sum": 0,
                "count": 0, "unassigned": 0, "solver": "none"}
    vroom_url = vroom_url or os.getenv("VROOM_URL")
    if vroom_url:
        res = _vroom_plan(pts, n, start, vroom_url, osrm_url)
        if res:
            return res     # VROOM ответил — используем его
    return _split_plan(pts, n, start, osrm_url)   # фолбэк
