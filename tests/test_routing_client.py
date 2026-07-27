import routing_client


def _table(snaps, durations=None, distances=None):
    durations = durations or [[0, 60], [60, 0]]
    distances = distances or [[0, 1000], [1000, 0]]
    return {
        "code": "Ok",
        "durations": durations,
        "distances": distances,
        "sources": [{"distance": value} for value in snaps],
    }


def test_best_table_rejects_wrong_country_snap(monkeypatch):
    def fake(url, payload=None, timeout=None):
        assert payload is None
        return _table([80, 120]) if "osrm-uz" in url else _table([38_000, 41_000])

    monkeypatch.setattr(routing_client, "_request_json", fake)
    result = routing_client.best_table(
        [(39.65, 66.96), (39.77, 64.42)],
        backends=[("tj", "http://osrm-tj"), ("uz", "http://osrm-uz")],
    )
    assert result["name"] == "uz"
    assert result["max_snap_m"] == 120


def test_best_route_chooses_smallest_valid_snap(monkeypatch):
    def fake(url, payload=None, timeout=None):
        snap = 35 if "osrm-uz" in url else 700
        return {
            "code": "Ok",
            "waypoints": [{"distance": snap}, {"distance": snap + 10}],
            "routes": [{
                "distance": 12_000,
                "duration": 1_200,
                "geometry": {"coordinates": [[66.96, 39.65], [67.05, 39.70]]},
            }],
        }

    monkeypatch.setattr(routing_client, "_request_json", fake)
    result = routing_client.best_route(
        (39.65, 66.96), (39.70, 67.05),
        backends=[("tj", "http://osrm-tj"), ("uz", "http://osrm-uz")],
    )
    assert result["name"] == "uz"
    assert result["km"] == 12


def test_vroom_uses_custom_country_safe_matrix(monkeypatch):
    captured = {}

    def fake(url, payload=None, timeout=None):
        captured["url"] = url
        captured["payload"] = payload
        return {
            "code": 0,
            "unassigned": [],
            "routes": [{"steps": [
                {"type": "start", "location_index": 0},
                {"type": "job", "id": 2},
                {"type": "job", "id": 1},
            ]}],
        }

    monkeypatch.setattr(routing_client, "_request_json", fake)
    matrix = [[0, 10, 20], [10, 0, 8], [20, 8, 0]]
    assert routing_client.vroom_order(matrix, matrix, 0, [1, 2], "http://vroom:3000") == [2, 1]
    assert captured["url"] == "http://vroom:3000/"
    assert captured["payload"]["matrices"]["car"]["durations"] == matrix
    assert all("location" not in job for job in captured["payload"]["jobs"])


def test_plan_day_uses_vroom_order_and_road_legs(monkeypatch):
    monkeypatch.setattr(routing_client, "best_table", lambda points, backends=None: {
        "name": "uz",
        "durations": [[0, 10, 30], [10, 0, 8], [30, 8, 0]],
        "distances": [[0, 1000, 3000], [1000, 0, 800], [3000, 800, 0]],
        "max_snap_m": 72.4,
    })
    monkeypatch.setattr(
        routing_client, "vroom_order",
        lambda durations, distances, start_index, jobs, vroom_url=None: [2, 1],
    )
    points = [{"lat": 40.1, "lon": 69.1}, {"lat": 40.2, "lon": 69.2}]
    plan = routing_client.plan_day(points, start=(40.0, 69.0), vroom_url="http://vroom")
    assert plan == {
        "order": [1, 0],
        "legs_km": [3.0, 0.8],
        "km": 3.8,
        "matrix_src": "osrm_uz",
        "solver": "vroom",
        "max_snap_m": 72,
    }


def test_plan_day_without_live_start_keeps_highest_priority_first(monkeypatch):
    monkeypatch.setattr(routing_client, "best_table", lambda points, backends=None: None)
    monkeypatch.setattr(routing_client, "vroom_order", lambda *args, **kwargs: None)
    points = [
        {"lat": 40.0, "lon": 69.0},
        {"lat": 40.1, "lon": 69.1},
        {"lat": 41.0, "lon": 70.0},
    ]
    plan = routing_client.plan_day(points)
    assert plan["order"][0] == 0
    assert plan["legs_km"][0] is None
    assert plan["matrix_src"] == "haversine"
    assert plan["solver"] == "2opt"
