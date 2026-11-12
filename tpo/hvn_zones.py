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


RANGE, RESISTANCE, SUPPORT, BOTH = "проторговка", "сопротивление", "поддержка", "поддержка+сопротивление"


def blocked_pairs(zones: list[Zone], overlap_min: float) -> list[tuple[int, int]]:
    """Пары итоговых зон, которые пересекаются ≥ overlap_min, но не склеены.
    После merge_zones такое возможно только из-за лимита ширины."""
    out = []
    for i in range(len(zones)):
        for j in range(i + 1, len(zones)):
            if overlap_share(zones[i], zones[j]) >= overlap_min:
                out.append((i, j))
    return out


def zone_roles(zones: list[Zone], overlap_min: float) -> tuple[list[str], list[list[int]], list[list[int]]]:
    """Роль каждой зоны. В каждой несклеенной паре верхняя (по середине,
    при равенстве — по верхнему краю) — сопротивление, нижняя — поддержка.
    Зона без таких пар — проторговка; зона, бывшая верхней в одной паре и
    нижней в другой, — поддержка+сопротивление.
    Возвращает роли и для каждой зоны индексы соседей снизу и сверху."""
    below = [[] for _ in zones]   # зоны, над которыми эта стоит (она для них сопротивление)
    above = [[] for _ in zones]
    for i, j in blocked_pairs(zones, overlap_min):
        a, b = zones[i], zones[j]
        if (a.lo + a.hi, a.hi) > (b.lo + b.hi, b.hi):
            upper, lower = i, j
        else:
            upper, lower = j, i
        below[upper].append(lower)
        above[lower].append(upper)
    roles = []
    for k in range(len(zones)):
        if below[k] and above[k]:
            roles.append(BOTH)
        elif below[k]:
            roles.append(RESISTANCE)
        elif above[k]:
            roles.append(SUPPORT)
        else:
            roles.append(RANGE)
    return roles, below, above


def zone_clusters(zones: list[Zone], overlap_min: float) -> list[int]:
    """Кластер = связная группа зон по несклеенным парам (пересечение ≥
    overlap_min). Зона проторговки — кластер из одной зоны. Номер кластера —
    наименьший индекс зоны в нём."""
    parent = list(range(len(zones)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j in blocked_pairs(zones, overlap_min):
        a, b = find(i), find(j)
        parent[max(a, b)] = min(a, b)
    return [find(i) for i in range(len(zones))]
