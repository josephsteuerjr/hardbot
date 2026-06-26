"""Тесты оптимизатора объезда: 2-opt не ухудшает, дельта == полному пересчёту, фолбэк."""
import random

import routing


def _old_two_opt(order, mat, rounds=60):
    """Эталон: 2-opt со старым ПОЛНЫМ пересчётом длины (до O2-оптимизации)."""
    def length(o):
        return sum(mat[o[i]][o[i + 1]] for i in range(len(o) - 1))
    best = order[:]; improved = True; it = 0
    while improved and it < rounds:
        improved = False; it += 1
        for i in range(1, len(best) - 1):
            for k in range(i + 1, len(best)):
                cand = best[:i] + best[i:k + 1][::-1] + best[k + 1:]
                if length(cand) < length(best) - 1e-9:
                    best = cand; improved = True
    return best


def _length(order, mat):
    return sum(mat[order[i]][order[i + 1]] for i in range(len(order) - 1))


def test_two_opt_not_worse_than_nn():
    rnd = random.Random(1)
    for _ in range(30):
        n = rnd.randint(4, 14)
        pts = [(rnd.uniform(40, 41), rnd.uniform(69, 70)) for _ in range(n)]
        mat = routing._matrix(pts)
        nn = routing._nearest_neighbour(mat, 0)
        opt = routing._two_opt(nn[:], mat)
        assert _length(opt, mat) <= _length(nn, mat) + 1e-9


def test_two_opt_matches_full_recompute_symmetric_and_asym():
    """Инкрементальная дельта даёт ТОТ ЖЕ порядок, что старый полный пересчёт."""
    rnd = random.Random(2)
    for trial in range(120):
        n = rnd.randint(4, 12)
        sym = trial % 2 == 0
        mat = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                if i == j:
                    mat[i][j] = 0.0
                elif sym and j < i:
                    mat[i][j] = mat[j][i]
                else:
                    mat[i][j] = round(rnd.uniform(0.5, 100), 4)
        start = routing._nearest_neighbour(mat, 0)
        assert routing._two_opt(start[:], mat) == _old_two_opt(start[:], mat)


def test_haversine_symmetric():
    a, b = (40.28, 69.62), (39.91, 69.01)
    assert routing.haversine(a, b) == routing.haversine(b, a)


def test_finalize_km_equals_manual_sum():
    pts = [{"lat": 40.0 + i * 0.1, "lon": 69.0 + i * 0.05, "debt": 1000 * i, "rep": str(i)}
           for i in range(6)]
    start = (40.0, 69.0)
    r = routing.optimize(pts, start=start)   # без OSRM → гаверсинус; depot = start
    # порядок посещения узлов: depot, затем точки по r["order"] (0-based индексы в pts)
    seq = [start] + [(pts[i]["lat"], pts[i]["lon"]) for i in r["order"]]
    manual = sum(routing.haversine(seq[i], seq[i + 1]) for i in range(len(seq) - 1))
    assert abs(r["km"] - round(manual, 1)) < 0.05


def test_fallback_is_haversine_without_osrm():
    pts = [{"lat": 40.2 + i * 0.01, "lon": 70.4 + i * 0.01, "debt": 5000, "rep": str(i)}
           for i in range(5)]
    assert routing.optimize(pts)["matrix_src"] == "haversine"


def test_empty_route():
    r = routing.optimize([])
    assert r["order"] == [] and r["km"] == 0.0 and r["matrix_src"] == "none"


def test_sane_rejects_degenerate_osrm():
    assert routing._sane([[0, 0], [0, 0]]) is None        # все недиаг ~0 → не граф
    assert routing._sane([[0, None], [5, 0]]) is None     # дырка → не доверяем
    assert routing._sane([[0, 5], [7, 0]]) is not None    # нормальная матрица
