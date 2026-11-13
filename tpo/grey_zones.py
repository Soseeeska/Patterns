"""Серые зоны: выглядят как зоны, но истории мало или они слишком узкие.

Источники (пороги — config.grey_zones), всё по RTH-профилям, без look-ahead:
  * comp  — короткие композиты баланса (меньше zones.min_composite_days дней);
            зона = HVN-полоса вокруг POC композита (VA двухдневного композита
            в медиане ~30 п. — это уже не «мелкая» зона), доступна со дня после конца;
  * hvn   — первородные зоны из дней со слабым HVN (TPO в VA ниже hvn_zones.min_tpo);
            тот же алгоритм формирования / фиксации, что и у нормальных;
  * young — нормальная первородная зона, пока у неё мало истории:
            дней формирования + подтверждений строго до даты < min_history_days.
            Статус зависит от даты: после подтверждения зона становится нормальной.
Ширина серой зоны — от min_width_points (узкие зоны тоже берутся) до max_width_points.

Положение серой зоны относительно нормальных на дату (classify_vs_normal):
  внутри     — целиком покрыта нормальными зонами;
  частично   — пересекается с нормальными, но не покрыта целиком;
  в пустоте  — не пересекается, нормальные зоны есть и выше, и ниже;
  с краю     — не пересекается, нормальные только с одной стороны (или их нет).
"""
from __future__ import annotations

import numpy as np

INSIDE, PARTIAL, GAP, EDGE = "внутри", "частично", "в пустоте", "с краю"
POSITIONS = [GAP, EDGE, PARTIAL, INSIDE]


def covered_share(lo: float, hi: float, n_lo: np.ndarray, n_hi: np.ndarray, tick: float) -> float:
    """Доля цен зоны [lo, hi] (по тикам, края включительно), покрытая объединением нормальных зон."""
    grid = np.arange(round(lo / tick), round(hi / tick) + 1) * tick
    m = np.zeros(len(grid), bool)
    for a, b in zip(n_lo, n_hi):
        if a <= hi and b >= lo:
            m |= (grid >= a - 1e-9) & (grid <= b + 1e-9)
    return float(m.mean())


def classify_vs_normal(lo: float, hi: float, n_lo: np.ndarray, n_hi: np.ndarray,
                       tick: float) -> tuple[str, float]:
    """(положение, доля покрытия) серой зоны относительно нормальных зон."""
    n_lo, n_hi = np.asarray(n_lo, float), np.asarray(n_hi, float)
    cov = covered_share(lo, hi, n_lo, n_hi, tick)
    if cov >= 1.0:
        return INSIDE, cov
    if cov > 0:
        return PARTIAL, cov
    above, below = (n_lo > hi).any(), (n_hi < lo).any()
    return (GAP if above and below else EDGE), cov


def history_on(formation_days: int, conf_dates, date) -> int:
    """Дней истории у первородной зоны на дату: формирование + подтверждения строго раньше даты."""
    c = np.sort(np.asarray(conf_dates or [], dtype="datetime64[ns]"))
    return int(formation_days) + int(np.searchsorted(c, np.datetime64(date)))


def grey_until(formation_days: int, conf_dates, min_history: int):
    """Первая дата, с которой первородная зона уже не серая (день после нужного
    подтверждения). None — серой не бывает; NaT — серая всё время данных."""
    need = min_history - int(formation_days)
    if need <= 0:
        return None
    c = sorted(conf_dates or [])
    if len(c) < need:
        return np.datetime64("NaT", "ns")
    return np.datetime64(c[need - 1], "ns") + np.timedelta64(1, "D")
