"""Первородные HVN-зоны: формирование, фиксация, подтверждения, тесты.

Дневная HVN-полоса строится для каждого полного дня (строки вокруг POC с
TPO ≥ hvn_share × максимум). «Зачётный» день — максимум TPO внутри VA в
[min_tpo, max_tpo]. Пересечение — доля от более узкой полосы (как в hvn_zones).

Дни идут по порядку. Одновременно формируется не больше одной зоны.
  * Нет формирующейся зоны, день зачётный:
      - полоса пересекается ≥ overlap_min с зафиксированной зоной → +1
        подтверждение той, с которой пересечение больше; новой зоны нет;
      - иначе с этого дня начинается новая формирующаяся зона.
  * Есть формирующаяся зона F и цена ещё в зоне: leave_by="hvn" — полоса
      дня (любого) пересекается с F ≥ overlap_min; leave_by="va" — VA дня
      пересекается с F хотя бы одной строкой. Если день зачётный и объединение ≤ max_width — F
      расширяется до объединения и день добавляется в её дни; иначе F не
      меняется.
  * Иначе цена ушла: F
    фиксируется (fixed_on = этот день, доступна со следующего), а день
    разбирается заново как день без формирующейся зоны.
Зафиксированная зона больше не меняется, только копит подтверждения.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class PrimaryZone:
    lo: int
    hi: int
    days: list = field(default_factory=list)          # дни формирования (зачётные)
    fixed_on: object = None                            # день, когда цена ушла
    confirmations: list = field(default_factory=list)  # зачётные дни после фиксации


def _overlap(lo1: int, hi1: int, lo2: int, hi2: int) -> float:
    inter = min(hi1, hi2) - max(lo1, lo2) + 1
    return max(inter, 0) / min(hi1 - lo1 + 1, hi2 - lo2 + 1)


def build_primary_zones(days: pd.DataFrame, overlap_min: float, max_width_ticks: int,
                        leave_by: str = "hvn") -> list[PrimaryZone]:
    """days: date, lo, hi (полоса в тиках), ok (зачётный), по возрастанию даты;
    для leave_by="va" ещё va_lo, va_hi (тики)."""
    fixed: list[PrimaryZone] = []
    cur: PrimaryZone | None = None
    for r in days.itertuples():
        if cur is not None:
            if leave_by == "va":
                stay = r.va_lo <= cur.hi and r.va_hi >= cur.lo
            else:
                stay = _overlap(cur.lo, cur.hi, r.lo, r.hi) >= overlap_min
            if stay:
                if r.ok and _overlap(cur.lo, cur.hi, r.lo, r.hi) >= overlap_min and max(cur.hi, r.hi) - min(cur.lo, r.lo) <= max_width_ticks:
                    cur.lo, cur.hi = min(cur.lo, r.lo), max(cur.hi, r.hi)
                    cur.days.append(r.date)
                continue
            cur.fixed_on = r.date
            fixed.append(cur)
            cur = None
        if not r.ok:
            continue
        best, best_ov = None, -1.0
        for z in fixed:
            ov = _overlap(z.lo, z.hi, r.lo, r.hi)
            if ov >= overlap_min and ov > best_ov:
                best, best_ov = z, ov
        if best is not None:
            best.confirmations.append(r.date)
        else:
            cur = PrimaryZone(r.lo, r.hi, [r.date])
    if cur is not None:
        fixed.append(cur)          # не зафиксирована до конца данных
    return fixed


def zone_tests(daily: pd.DataFrame, zones: pd.DataFrame, tol: float) -> pd.DataFrame:
    """Дневные тесты зон после фиксации.

    zones: zone_id, lo, hi (пункты), available_from, conf_dates (list).
    Подход — по open дня: выше зоны → тест поддержки, ниже → сопротивления,
    open внутри зоны — день пропускается. Касание: low ≤ hi + tol (сверху)
    или high ≥ lo − tol (снизу). Исход по close: на стороне подхода —
    удержание, за зоной — пробой, внутри — принятие в зоне.
    Для каждого теста: номер теста, был ли раньше пробой (флип роли),
    сколько подтверждений было к этому дню.
    """
    d = daily.sort_values("date")
    dates = d.date.to_numpy()
    o, h, l, c = (d[k].to_numpy() for k in ("open", "high", "low", "close"))
    out = []
    for z in zones.itertuples():
        start = np.searchsorted(dates, np.datetime64(z.available_from))
        above, below = o[start:] > z.hi, o[start:] < z.lo
        touch = (above & (l[start:] <= z.hi + tol)) | (below & (h[start:] >= z.lo - tol))
        conf = np.array(sorted(getattr(z, "conf_dates", None) or []), dtype="datetime64[ns]")
        n, broken = 0, False
        for k in np.flatnonzero(touch):
            i = start + k
            sup = bool(above[k])
            if sup:
                res = "hold" if c[i] > z.hi else ("break" if c[i] < z.lo else "inside")
            else:
                res = "hold" if c[i] < z.lo else ("break" if c[i] > z.hi else "inside")
            n += 1
            out.append({
                "zone_id": z.zone_id, "date": dates[i], "role": "support" if sup else "resistance",
                "result": res, "test_n": n, "after_break": broken,
                "confirmations_before": int(np.searchsorted(conf, dates[i])),
            })
            broken = broken or res == "break"
    return pd.DataFrame(out)
