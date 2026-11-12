"""Подгонка границ зоны по краям тел 30m-свечей.

Для каждой границы (верх и низ) перебираются цены p на сетке тиков в
окне ± search вокруг исходной границы. Очки цены p:
  * 1    — бар, у которого край тела (open или close) в p ± 1 тик и от этого
           края дальше идёт хвост ≥ min_wick через p (тело закончилось на p,
           дальше только хвост);
  * w_plain — край тела в p ± 1 тик без такого хвоста.
Граница переносится на цену с наибольшими очками (при равенстве — где больше
точных совпадений, затем ближайшую к исходной), если очков ≥ min_score; иначе остаётся. Ширина зоны после
подгонки — от min_width до max_width, иначе зона не меняется.
"""
from __future__ import annotations

import numpy as np


def edge_scores(o, h, l, c, grid: np.ndarray, tick: float, min_wick: float, w_plain: float,
                tol_ticks: int = 1) -> np.ndarray:
    top, bot = np.maximum(o, c), np.minimum(o, c)
    up_wick = (h - top) >= min_wick
    dn_wick = (bot - l) >= min_wick
    near_top = np.abs(top[None, :] - grid[:, None]) <= tol_ticks * tick + 1e-9
    near_bot = np.abs(bot[None, :] - grid[:, None]) <= tol_ticks * tick + 1e-9
    anchor = (near_top & up_wick[None, :]) | (near_bot & dn_wick[None, :])
    plain = (near_top | near_bot) & ~anchor
    return anchor.sum(axis=1) + w_plain * plain.sum(axis=1)


def align_zone(o, h, l, c, lo: float, hi: float, tick: float, search: float, min_wick: float,
               w_plain: float, min_score: float, min_width: float, max_width: float):
    """Возвращает (new_lo, new_hi, score_lo, score_hi)."""
    def best(edge):
        grid = np.round(np.arange(edge - search, edge + search + tick / 2, tick) / tick) * tick
        sc = edge_scores(o, h, l, c, grid, tick, min_wick, w_plain)
        exact = edge_scores(o, h, l, c, grid, tick, min_wick, w_plain, tol_ticks=0)
        top = sc.max() if len(sc) else 0
        if top < min_score:
            return edge, float(top)
        key = sc + 1e-3 * exact                      # при равенстве — где точных совпадений больше
        cand = grid[key == key.max()]
        return float(cand[np.argmin(np.abs(cand - edge))]), float(top)

    new_hi, s_hi = best(hi)
    new_lo, s_lo = best(lo)
    if not (min_width <= new_hi - new_lo <= max_width):
        return lo, hi, s_lo, s_hi
    return new_lo, new_hi, s_lo, s_hi
