"""Сегодняшний RTH-день против вчерашнего и против карты зон.

Признаки известны к моменту решения (open / после A / после IB), исходы —
по итогу дня. Зоны карты (все — по RTH-профилям прошлых дней):
  * old_balance — VA композитов баланса [VAL, VAH], доступны со дня после конца;
  * hvn_S / hvn_R / hvn_range / hvn_new — первородные HVN-зоны с классом на дату
    (по отторжениям на 30m-барах строго до этого дня; «new» — ещё не было отторжений);
  * grey — незаполненные single prints прошлых дней (≥ grey_min_ticks); зона
    живёт, пока диапазон какого-нибудь дня не пройдёт её целиком.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

LETTERS = "ABCDEFGHIJKLM"


def spike(g: pd.DataFrame, min_move: float) -> tuple[str, float, float]:
    """Спайк в последнем периоде дня: ('up'|'down'|'none', lo, hi зоны спайка)."""
    g = g.sort_values("period")
    last, prev = g.iloc[-1], g.iloc[:-1]
    up = last.high - prev.high.max()
    dn = prev.low.min() - last.low
    if max(up, dn) < min_move:
        return "none", np.nan, np.nan
    if up >= dn:
        return "up", prev.high.max(), last.high
    return "down", last.low, prev.low.min()


def single_runs(counts: np.ndarray, low: int, row: int, min_rows: int) -> list[tuple[int, int]]:
    """Участки подряд идущих строк с TPO = 1 длиной ≥ min_rows: [(lo, hi)] в тиках."""
    out, i, n = [], 0, len(counts)
    while i < n:
        if counts[i] == 1:
            j = i
            while j + 1 < n and counts[j + 1] == 1:
                j += 1
            if j - i + 1 >= min_rows:
                out.append((low + i * row, low + j * row))
            i = j + 1
        else:
            i += 1
    return out


def grey_zones(daily: pd.DataFrame, profiles: dict, tick: float, min_ticks: int) -> pd.DataFrame:
    """Single prints прошлых дней и дата заполнения (первый день, чей диапазон
    покрыл участок целиком)."""
    d = daily[~daily.short_day].sort_values("date").reset_index(drop=True)
    dates, H, L = d.date.to_numpy(), d.high.to_numpy(), d.low.to_numpy()
    rows = []
    for i, r in d.iterrows():
        p = profiles[r.date]
        for lo_t, hi_t in single_runs(p.counts, p.low, p.row, max(1, min_ticks // p.row)):
            lo, hi = lo_t * tick, hi_t * tick
            later = np.flatnonzero((L[i + 1:] <= lo) & (H[i + 1:] >= hi))
            filled = dates[i + 1 + later[0]] if len(later) else np.datetime64("NaT")
            avail = dates[i + 1] if i + 1 < len(d) else np.datetime64("NaT")
            rows.append({"lo": lo, "hi": hi, "formed": r.date, "available_from": avail, "filled": filled})
    return pd.DataFrame(rows)


def open_location(o: float, p) -> str:
    if o > p.high:
        return "выше High"
    if o > p.vah:
        return "VAH–High"
    if o >= p.val:
        return "внутри VA"
    if o >= p.low:
        return "Low–VAL"
    return "ниже Low"


def loc_vs_va(x_lo: float, x_hi: float, p) -> str:
    """Где отрезок [x_lo, x_hi] относительно VA прошлого дня."""
    if x_lo > p.vah:
        return "выше VA"
    if x_hi < p.val:
        return "ниже VA"
    if x_lo >= p.val and x_hi <= p.vah:
        return "внутри VA"
    return "перекрывает край VA"


def bucket_rel(x: float, lo: float, hi: float) -> str:
    if np.isnan(x):
        return "?"
    return "узкий" if x < lo else ("широкий" if x > hi else "обычный")


def tail_bucket(ticks: float, long_ticks: int) -> str:
    return "нет" if ticks <= 0 else ("длинный" if ticks >= long_ticks else "короткий")
