"""
Оптимизация объезда (VRP-лайт): «в каком порядке объезжать точки за день».

Лёгкое ядро без зависимостей: матрица (гаверсинус ИЛИ реальные времена OSRM) →
nearest-neighbour → 2-opt. Геометрия маршрута — по дорогам (OSRM /route) или прямыми.
Время в пути даётся с лёгким запасом (×1.15) — это ЧИСТАЯ езда, без времени на адресах.

`optimize` сам ищет порядок (NN+2-opt). `summarize` считает те же метрики для УЖЕ
заданного порядка — это нужно, когда порядок назначил внешний решатель (VROOM).
"""
from __future__ import annotations
import math
import os
import json
import urllib.request

R_KM = 6371.0
BUFFER = 1.15   # лёгкий запас на манёвры/парковку/поиск/пробки


def haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = a; lat2, lon2 = b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1); dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R_KM * math.asin(math.sqrt(h))


def _matrix(pts: list[tuple[float, float]]) -> list[list[float]]:
    n = len(pts)
    return [[haversine(pts[i], pts[j]) for j in range(n)] for i in range(n)]


def osrm_table(pts: list[tuple[float, float]], base: str) -> list[list[float]] | None:
    """Боевая матрица времён (сек) из OSRM /table. pts=[(lat,lon)]. None при ошибке."""
    try:
        coords = ";".join(f"{lon},{lat}" for lat, lon in pts)
        url = f"{base.rstrip('/')}/table/v1/driving/{coords}?annotations=duration"
        with urllib.request.urlopen(url, timeout=20) as r:
            return json.loads(r.read()).get("durations")
    except Exception:
        return None


def osrm_route_geometry(nodes: list[tuple[float, float]], base: str) -> list[list[float]] | None:
    """Геометрия по дорогам из OSRM /route. nodes=[(lat,lon)] по порядку → [[lon,lat],…]."""
    try:
        coords = ";".join(f"{lon},{lat}" for lat, lon in nodes)
        url = f"{base.rstrip('/')}/route/v1/driving/{coords}?overview=full&geometries=geojson"
        with urllib.request.urlopen(url, timeout=20) as r:
            return json.loads(r.read())["routes"][0]["geometry"]["coordinates"]
    except Exception:
        return None


def _nearest_neighbour(mat: list[list[float]], start: int = 0) -> list[int]:
    n = len(mat); unvisited = set(range(n)); order = [start]; unvisited.discard(start)
    cur = start
    while unvisited:
        nxt = min(unvisited, key=lambda j: mat[cur][j])
        order.append(nxt); unvisited.discard(nxt); cur = nxt
    return order


def _two_opt(order: list[int], mat: list[list[float]], rounds: int = 60) -> list[int]:
    """2-opt для ОТКРЫТОГО пути. Оценка свопа — инкрементальной ДЕЛЬТОЙ по изменённому
    участку (а не пересчётом всей длины на каждый кандидат — было ~O(n) лишних операций
    в двойном цикле, затык на реальном городе в сотни точек).

    Δ = (новые рёбра границы + развёрнутый внутренний сегмент) − (старые те же).
    Прочая часть пути не меняется и в Δ не входит. Для симметричной матрицы (гаверсинус)
    внутренний сегмент сокращается; для АСИММЕТРИЧНОЙ (времена OSRM, рёбра направленные)
    учитываем разворот направления внутри сегмента — поэтому результат бит-в-бит совпадает
    со старым «полным пересчётом» при том же пороге 1e-9 (см. tests/test_routing.py)."""
    n = len(order)
    best = order[:]
    improved = True
    it = 0
    while improved and it < rounds:
        improved = False
        it += 1
        for i in range(1, n - 1):
            a = best[i - 1]
            for k in range(i + 1, n):
                b, c = best[i], best[k]
                old = mat[a][b]            # ребро перед сегментом
                new = mat[a][c]
                if k + 1 < n:
                    d = best[k + 1]
                    old += mat[c][d]       # ребро после сегмента (открытый путь — может отсутствовать)
                    new += mat[b][d]
                # внутренние рёбра сегмента: при развороте меняют направление
                for j in range(i, k):
                    old += mat[best[j]][best[j + 1]]
                    new += mat[best[j + 1]][best[j]]
                if new < old - 1e-9:
                    best[i:k + 1] = best[i:k + 1][::-1]
                    improved = True
    return best


def _sane(dur) -> list[list[float]] | None:
    """Годен ли OSRM-матрица времён. Граф у нас — только Таджикистан: точки UZ/RU
    «прилипают» к одному пограничному узлу → времена вырождаются (None/нули).
    В таком случае не доверяем OSRM и считаем по гаверсинусу (честная оценка)."""
    if not dur:
        return None
    flat = [v for row in dur for v in row]
    if any(v is None for v in flat):
        return None
    off = [dur[i][j] for i in range(len(dur)) for j in range(len(dur)) if i != j]
    if off and max(off) <= 0:        # все недиагональные ~0 → точки вне графа
        return None
    return dur


def _empty() -> dict:
    return {"order": [], "stops_xy": [], "line": [], "km": 0.0,
            "minutes": 0, "drive_min": 0, "debt_sum": 0,
            "profit_per_min": 0, "matrix_src": "none"}


def _finalize(points: list[dict], nodes: list[tuple[float, float]], order: list[int],
              dur: list[list[float]] | None, speed_kmh: float,
              osrm_url: str | None, start) -> dict:
    """Общий хвост: по готовому порядку узлов считает км/время/геометрию/долг.
    Км — сумма гаверсинуса ВДОЛЬ порядка (O(n)), а не через полную матрицу O(n²):
    нужны лишь рёбра маршрута, а не все пары."""
    km = sum(haversine(nodes[order[i]], nodes[order[i + 1]]) for i in range(len(order) - 1))
    drive_min = (sum(dur[order[i]][order[i + 1]] for i in range(len(order) - 1)) / 60.0
                 if dur else km / max(speed_kmh, 1) * 60.0)
    minutes = drive_min * BUFFER
    ordered_nodes = [nodes[i] for i in order]
    real_order = [i - 1 for i in order if not (start and i == 0)]
    stops_xy = [[points[i]["lon"], points[i]["lat"]] for i in real_order]
    line = osrm_route_geometry(ordered_nodes, osrm_url) if (osrm_url and dur) else None
    if not line:
        line = [[lon, lat] for (lat, lon) in ordered_nodes]
    debt_sum = sum(int(points[i].get("debt", 0)) for i in real_order)
    return {"order": real_order, "stops_xy": stops_xy, "line": line, "km": round(km, 1),
            "minutes": round(minutes), "drive_min": round(drive_min), "debt_sum": debt_sum,
            "profit_per_min": round(debt_sum / minutes) if minutes else 0,
            "matrix_src": "osrm" if dur else "haversine"}


def optimize(points: list[dict], start: tuple[float, float] | None = None,
             speed_kmh: float = 30.0, osrm_url: str | None = None) -> dict:
    """points: [{lat,lon,debt,...}]. Возвращает порядок объезда, точки-остановки,
    полилинию по дорогам, км, время (с запасом), сумму долга. Порядок ищем сами (2-opt)."""
    if not points:
        return _empty()
    pts = [(p["lat"], p["lon"]) for p in points]
    nodes = ([start] + pts) if start else pts
    osrm_url = osrm_url or os.getenv("OSRM_URL")
    dur = _sane(osrm_table(nodes, osrm_url)) if osrm_url else None
    mat = dur if dur else _matrix(nodes)
    order = _two_opt(_nearest_neighbour(mat, 0), mat)
    return _finalize(points, nodes, order, dur, speed_kmh, osrm_url, start)


def summarize(points: list[dict], start: tuple[float, float] | None = None,
              speed_kmh: float = 30.0, osrm_url: str | None = None) -> dict:
    """Те же метрики, что у optimize, но для УЖЕ заданного порядка точек (без
    переоптимизации) — когда порядок назначил внешний решатель (VROOM)."""
    if not points:
        return _empty()
    pts = [(p["lat"], p["lon"]) for p in points]
    nodes = ([start] + pts) if start else pts
    osrm_url = osrm_url or os.getenv("OSRM_URL")
    dur = _sane(osrm_table(nodes, osrm_url)) if osrm_url else None
    order = list(range(len(nodes)))   # identity — уважаем порядок решателя
    return _finalize(points, nodes, order, dur, speed_kmh, osrm_url, start)
