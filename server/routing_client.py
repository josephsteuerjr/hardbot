"""Country-safe OSRM and VROOM client for hardbot route endpoints.

Multiple neighbouring OSRM graphs may return ``Ok`` by snapping an out-of-country
point to a border road.  We therefore try every configured graph and accept only
the one whose OSRM waypoints are close to *all* requested coordinates.
"""

from __future__ import annotations

import json
import math
import os
import urllib.request


USER_AGENT = "hardbot-miniapp/2.0"
DEFAULT_TIMEOUT_S = 6.0
DEFAULT_MAX_SNAP_M = 5000.0


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    radius = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(h))


def configured_backends() -> list[tuple[str, str]]:
    """Return de-duplicated OSRM backends in a stable preference order."""
    raw = (
        ("uz", os.getenv("OSRM_URL_UZ", "")),
        ("tj", os.getenv("OSRM_URL_TJ", "")),
        # РФ — отдельный граф (Самарская область). Порядок здесь не про приоритет:
        # ниже всё равно выбирается тот граф, где ВСЕ точки притянулись близко,
        # поэтому лишний backend не портит маршрут, а только расширяет покрытие.
        ("ru", os.getenv("OSRM_URL_RU", "")),
        ("default", os.getenv("OSRM_URL", "")),
    )
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name, value in raw:
        url = (value or "").strip().rstrip("/")
        if url and url not in seen:
            result.append((name, url))
            seen.add(url)
    return result


def _request_json(url: str, payload: dict | None = None, timeout: float | None = None) -> dict | None:
    try:
        body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        headers = {"User-Agent": USER_AGENT}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout or DEFAULT_TIMEOUT_S) as response:
            value = json.loads(response.read())
        return value if isinstance(value, dict) else None
    except Exception:  # noqa: BLE001 - routing must fall back, never break field work
        return None


def _snap_limit(value: float | None = None) -> float:
    if value is not None:
        return float(value)
    try:
        return max(0.0, float(os.getenv("OSRM_MAX_SNAP_M", DEFAULT_MAX_SNAP_M)))
    except ValueError:
        return DEFAULT_MAX_SNAP_M


def _coordinate_path(points: list[tuple[float, float]]) -> str:
    return ";".join(f"{lon:.7f},{lat:.7f}" for lat, lon in points)


def _finite_matrix(value: object, size: int) -> list[list[float]] | None:
    if not isinstance(value, list) or len(value) != size:
        return None
    output: list[list[float]] = []
    for row in value:
        if not isinstance(row, list) or len(row) != size:
            return None
        converted: list[float] = []
        for item in row:
            if item is None:
                return None
            try:
                number = float(item)
            except (TypeError, ValueError):
                return None
            if not math.isfinite(number) or number < 0:
                return None
            converted.append(number)
        output.append(converted)
    return output


def _snaps(data: dict, size: int) -> list[float] | None:
    sources = data.get("sources")
    if not isinstance(sources, list) or len(sources) != size:
        return None
    result: list[float] = []
    for source in sources:
        try:
            distance = float(source["distance"])
        except (KeyError, TypeError, ValueError):
            return None
        if not math.isfinite(distance) or distance < 0:
            return None
        result.append(distance)
    return result


def best_table(
    points: list[tuple[float, float]],
    backends: list[tuple[str, str]] | None = None,
    max_snap_m: float | None = None,
) -> dict | None:
    """Choose the road graph that really covers every point and return its matrices."""
    if not points:
        return None
    choices = configured_backends() if backends is None else backends
    limit = _snap_limit(max_snap_m)
    candidates: list[tuple[tuple[float, float, str], dict]] = []
    coords = _coordinate_path(points)
    for name, base in choices:
        url = (f"{base.rstrip('/')}/table/v1/driving/{coords}"
               "?annotations=duration,distance")
        data = _request_json(url)
        if not data or data.get("code") != "Ok":
            continue
        durations = _finite_matrix(data.get("durations"), len(points))
        distances = _finite_matrix(data.get("distances"), len(points))
        snaps = _snaps(data, len(points))
        if durations is None or distances is None or snaps is None or max(snaps) > limit:
            continue
        # Catch a graph that reports a degenerate near-zero matrix for separated points.
        spread_km = max(
            (haversine_km(a, b) for i, a in enumerate(points) for b in points[i + 1:]),
            default=0.0,
        )
        offdiag = [durations[i][j] for i in range(len(points)) for j in range(len(points)) if i != j]
        if spread_km > 1.0 and offdiag and max(offdiag) <= 1.0:
            continue
        result = {
            "name": name,
            "url": base,
            "durations": durations,
            "distances": distances,
            "snaps_m": snaps,
            "max_snap_m": max(snaps),
        }
        candidates.append(((max(snaps), sum(snaps), name), result))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def best_route(
    start: tuple[float, float],
    finish: tuple[float, float],
    backends: list[tuple[str, str]] | None = None,
    max_snap_m: float | None = None,
) -> dict | None:
    """Return road geometry from the graph with the smallest valid waypoint snaps."""
    choices = configured_backends() if backends is None else backends
    limit = _snap_limit(max_snap_m)
    coords = _coordinate_path([start, finish])
    candidates: list[tuple[tuple[float, float, str], dict]] = []
    for name, base in choices:
        url = (f"{base.rstrip('/')}/route/v1/driving/{coords}"
               "?overview=full&geometries=geojson")
        data = _request_json(url)
        if not data or data.get("code") != "Ok" or not data.get("routes"):
            continue
        waypoints = data.get("waypoints")
        if not isinstance(waypoints, list) or len(waypoints) != 2:
            continue
        try:
            snaps = [float(item["distance"]) for item in waypoints]
            route = data["routes"][0]
            distance_m = float(route["distance"])
            duration_s = float(route["duration"])
            line = route["geometry"]["coordinates"]
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        if any(not math.isfinite(item) or item < 0 for item in snaps) or max(snaps) > limit:
            continue
        if distance_m < 0 or duration_s < 0 or not isinstance(line, list) or len(line) < 2:
            continue
        straight_m = haversine_km(start, finish) * 1000.0
        if straight_m > 1000 and distance_m < straight_m * 0.65:
            continue
        result = {
            "name": name,
            "url": base,
            "line": line,
            "km": distance_m / 1000.0,
            "minutes": duration_s / 60.0,
            "snaps_m": snaps,
            "max_snap_m": max(snaps),
        }
        candidates.append(((max(snaps), sum(snaps), name), result))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def _path_cost(path: list[int], matrix: list[list[float]]) -> float:
    return sum(matrix[path[i]][path[i + 1]] for i in range(len(path) - 1))


def _matrix_order(matrix: list[list[float]], start_index: int, jobs: list[int]) -> list[int]:
    remaining = list(jobs)
    order: list[int] = []
    current = start_index
    while remaining:
        nxt = min(remaining, key=lambda item: (matrix[current][item], item))
        remaining.remove(nxt)
        order.append(nxt)
        current = nxt
    path = [start_index, *order]
    improved = True
    rounds = 0
    while improved and rounds < 20:
        improved = False
        rounds += 1
        baseline = _path_cost(path, matrix)
        for left in range(1, len(path) - 1):
            for right in range(left + 1, len(path)):
                candidate = path[:left] + list(reversed(path[left:right + 1])) + path[right + 1:]
                cost = _path_cost(candidate, matrix)
                if cost < baseline - 1e-6:
                    path = candidate
                    baseline = cost
                    improved = True
    return path[1:]


def vroom_order(
    durations: list[list[float]],
    distances: list[list[float]],
    start_index: int,
    jobs: list[int],
    vroom_url: str | None = None,
) -> list[int] | None:
    """Ask VROOM to order one open route using our already country-safe matrices."""
    base = (vroom_url if vroom_url is not None else os.getenv("VROOM_URL", "")).strip()
    if not base or not jobs:
        return None
    payload = {
        "jobs": [{"id": index, "location_index": index} for index in jobs],
        "vehicles": [{"id": 1, "profile": "car", "start_index": start_index}],
        "matrices": {
            "car": {
                "durations": [[max(0, round(value)) for value in row] for row in durations],
                "distances": [[max(0, round(value)) for value in row] for row in distances],
            }
        },
    }
    data = _request_json(base.rstrip("/") + "/", payload=payload)
    if not data or data.get("code") != 0 or data.get("unassigned") or not data.get("routes"):
        return None
    try:
        order = [int(step["id"]) for step in data["routes"][0]["steps"] if step.get("type") == "job"]
    except (KeyError, TypeError, ValueError):
        return None
    return order if len(order) == len(jobs) and set(order) == set(jobs) else None


def _haversine_matrices(points: list[tuple[float, float]]) -> tuple[list[list[float]], list[list[float]]]:
    distances = [[haversine_km(a, b) * 1000.0 for b in points] for a in points]
    durations = [[distance / 30_000.0 * 3600.0 for distance in row] for row in distances]
    return durations, distances


def plan_day(
    points: list[dict],
    start: tuple[float, float] | None = None,
    backends: list[tuple[str, str]] | None = None,
    vroom_url: str | None = None,
) -> dict:
    """Order up to a field-day worth of points, preserving all existing fallbacks."""
    if not points:
        return {"order": [], "legs_km": [], "km": 0.0, "matrix_src": "none", "solver": "none"}
    point_coords = [(float(item["lat"]), float(item["lon"])) for item in points]
    if start is None:
        coordinates = point_coords
        start_index = 0
        jobs = list(range(1, len(coordinates)))
        fixed_prefix = [0]
    else:
        coordinates = [(float(start[0]), float(start[1])), *point_coords]
        start_index = 0
        jobs = list(range(1, len(coordinates)))
        fixed_prefix = []

    table = best_table(coordinates, backends=backends)
    if table:
        durations = table["durations"]
        distances = table["distances"]
        matrix_src = f"osrm_{table['name']}"
        max_snap_m = table["max_snap_m"]
    else:
        durations, distances = _haversine_matrices(coordinates)
        matrix_src = "haversine"
        max_snap_m = None

    ordered_jobs = vroom_order(durations, distances, start_index, jobs, vroom_url=vroom_url)
    if ordered_jobs is None:
        ordered_jobs = _matrix_order(durations, start_index, jobs)
        solver = "2opt"
    else:
        solver = "vroom"
    coordinate_order = fixed_prefix + ordered_jobs
    point_order = coordinate_order if start is None else [index - 1 for index in coordinate_order]

    legs_km: list[float | None] = []
    total_m = 0.0
    previous = start_index
    for position, coordinate_index in enumerate(coordinate_order):
        if start is None and position == 0:
            legs_km.append(None)
        else:
            leg_m = distances[previous][coordinate_index]
            total_m += leg_m
            legs_km.append(round(leg_m / 1000.0, 1))
        previous = coordinate_index
    return {
        "order": point_order,
        "legs_km": legs_km,
        "km": round(total_m / 1000.0, 1),
        "matrix_src": matrix_src,
        "solver": solver,
        "max_snap_m": round(max_snap_m) if max_snap_m is not None else None,
    }
