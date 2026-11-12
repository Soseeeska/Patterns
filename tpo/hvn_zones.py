"""Зоны из дневных HVN и их склейка.

Зона дня — HVN-полоса вокруг POC (строки с TPO ≥ hvn_share × максимум,
непрерывно от POC); берутся только дни, где максимум TPO внутри VA в
диапазоне [min_tpo, max_tpo]. Цены — в тиках.

Склейка идёт по времени: новая зона сливается с уже существующей, если
пересечение ≥ merge_overlap_min от ширины более узкой из двух (ширина в
строках, края включительно) и объединение не шире max_width_ticks.
Из подходящих выбирается зона с наибольшим пересечением; после слияния
поиск повторяется, поэтому выросшая зона может поглотить и соседнюю.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Zone:
    lo: int
    hi: int
    dates: list = field(default_factory=list)

    @property
    def rows(self) -> int:
        return self.hi - self.lo + 1


def overlap_share(a: Zone, b: Zone) -> float:
    inter = min(a.hi, b.hi) - max(a.lo, b.lo) + 1
    return max(inter, 0) / min(a.rows, b.rows)


def merge_zones(day_zones: list[Zone], overlap_min: float, max_width_ticks: int) -> list[Zone]:
    """day_zones — в хронологическом порядке, по одной на день."""
    zones: list[Zone] = []
    for z in day_zones:
        z = Zone(z.lo, z.hi, list(z.dates))
        while True:
            best, best_ov = None, -1.0
            for i, e in enumerate(zones):
                ov = overlap_share(z, e)
                if ov >= overlap_min and max(z.hi, e.hi) - min(z.lo, e.lo) <= max_width_ticks and ov > best_ov:
                    best, best_ov = i, ov
            if best is None:
                break
            e = zones.pop(best)
            z = Zone(min(z.lo, e.lo), max(z.hi, e.hi), sorted(e.dates + z.dates))
        zones.append(z)
    return zones
