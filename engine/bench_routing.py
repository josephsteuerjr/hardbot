"""
Бенч O2: новый routing._two_opt (инкрементальная дельта; для симметричной матрицы — O(1)
на своп) против СТАРОГО полного пересчёта длины. Цель — показать ускорение на N≈300 при
неухудшении маршрута.

  python engine/bench_routing.py [N] [rounds]
"""
from __future__ import annotations
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import routing  # noqa: E402


def old_two_opt(order, mat, rounds):
    """Старая реализация: length() пересчитывает ВСЮ длину на каждый кандидат."""
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


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    random.seed(123)
    pts = [(random.uniform(40, 41), random.uniform(69, 71)) for _ in range(n)]
    mat = routing._matrix(pts)               # симметричная (гаверсинус) — реальный случай
    start = routing._nearest_neighbour(mat, 0)

    t0 = time.perf_counter()
    new = routing._two_opt(start[:], mat, rounds=rounds)
    t_new = time.perf_counter() - t0

    t0 = time.perf_counter()
    old = old_two_opt(start[:], mat, rounds=rounds)
    t_old = time.perf_counter() - t0

    ln_nn, ln_new, ln_old = _length(start, mat), _length(new, mat), _length(old, mat)
    speedup = t_old / t_new if t_new else float("inf")

    sys.stdout.reconfigure(encoding="utf-8")
    print(f"N={n}  rounds={rounds}  (симметричная матрица, гаверсинус)")
    print(f"  NN (старт):     длина {ln_nn:8.1f} км")
    print(f"  СТАРЫЙ 2-opt:   длина {ln_old:8.1f} км   время {t_old*1000:8.1f} мс")
    print(f"  НОВЫЙ  2-opt:   длина {ln_new:8.1f} км   время {t_new*1000:8.1f} мс")
    print(f"  → ускорение ×{speedup:.0f};  маршрут не хуже: {ln_new <= ln_old + 1e-6} "
          f"(идентичен: {new == old})")


if __name__ == "__main__":
    main()
