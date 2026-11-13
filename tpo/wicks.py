"""Хвосты 30m-свечей относительно зоны [lo, hi].

У свечи два хвоста: нижний [low, min(open, close)] и верхний
[max(open, close), high]. «Основной» хвост — тот, что за close:
у медвежьей свечи (close < open) нижний, у бычьей (close > open) верхний.
Второй — «противоположный» (за open). Доджи (close == open) — оба хвоста
считаются противоположными.

Положение хвоста относительно зоны (хвост короче min_wick не считается):
  boundary — хвост пересекает границу зоны: тело по одну сторону lo или hi,
             кончик хвоста по другую (или на самой границе);
  inside   — хвост целиком внутри [lo, hi];
  outside  — иначе (зону не задевает).
"""
from __future__ import annotations

import numpy as np

NONE, INSIDE, BOUNDARY, OUTSIDE = 0, 1, 2, 3


def wick_positions(o, h, l, c, lo: float, hi: float, min_wick: float):
    """Возвращает (pos_lower, pos_upper) — коды положения нижнего и верхнего хвоста."""
    bot, top = np.minimum(o, c), np.maximum(o, c)
    lower = np.full(len(o), OUTSIDE)
    upper = np.full(len(o), OUTSIDE)
    # нижний хвост [l, bot]
    lb = ((l <= hi) & (bot > hi)) | ((l <= lo) & (bot > lo))
    li = (l >= lo) & (bot <= hi)
    lower[li] = INSIDE
    lower[lb] = BOUNDARY
    lower[(bot - l) < min_wick] = NONE
    # верхний хвост [top, h]
    ub = ((top < lo) & (h >= lo)) | ((top < hi) & (h >= hi))
    ui = (top >= lo) & (h <= hi)
    upper[ui] = INSIDE
    upper[ub] = BOUNDARY
    upper[(h - top) < min_wick] = NONE
    return lower, upper


def main_opposite(o, c, lower, upper):
    """Перекладывает (нижний, верхний) в (основной, противоположный)."""
    bear, bull = c < o, c > o
    main = np.where(bear, lower, np.where(bull, upper, NONE))
    opp = np.where(bear, upper, np.where(bull, lower, NONE))
    doji = ~(bear | bull)
    return main, opp, doji
