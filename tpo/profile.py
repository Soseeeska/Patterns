"""Дневные TPO-профили: POC, Value Area (Dalton), IB, single prints, хвосты.

Цены внутри профиля хранятся в целых тиках (price / tick_size), чтобы
избежать ошибок округления float.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class Profile:
    low: int                 # нижняя строка (в тиках)
    counts: np.ndarray       # TPO-счёт по строкам, counts[i] -> строка low + i*row
    row: int                 # шаг строки в тиках

    @property
    def high(self) -> int:
        return self.low + (len(self.counts) - 1) * self.row

    def price_of(self, i: int) -> int:
        return self.low + i * self.row


def build_profile(lows: np.ndarray, highs: np.ndarray, row: int = 1) -> Profile:
    """lows/highs — экстремумы периодов в тиках. Каждый период ставит по
    одному TPO в каждую строку от своего low до high включительно."""
    lo = int(np.floor(lows.min() / row) * row)
    hi = int(np.floor(highs.max() / row) * row)
    n = (hi - lo) // row + 1
    diff = np.zeros(n + 1, dtype=np.int64)
    for l, h in zip(lows, highs):
        a = (int(np.floor(l / row) * row) - lo) // row
        b = (int(np.floor(h / row) * row) - lo) // row
        diff[a] += 1
        diff[b + 1] -= 1
    return Profile(lo, np.cumsum(diff)[:-1], row)


def merge_profiles(profiles: list[Profile]) -> Profile:
    """Композит: поточечная сумма TPO нескольких профилей."""
    row = profiles[0].row
    lo = min(p.low for p in profiles)
    hi = max(p.high for p in profiles)
    counts = np.zeros((hi - lo) // row + 1, dtype=np.int64)
    for p in profiles:
        a = (p.low - lo) // row
        counts[a:a + len(p.counts)] += p.counts
    return Profile(lo, counts, row)


def poc_index(p: Profile) -> int:
    """Строка с максимумом TPO; при равенстве — ближайшая к центру диапазона,
    при равном расстоянии — нижняя."""
    c = p.counts
    cand = np.flatnonzero(c == c.max())
    center = (len(c) - 1) / 2
    return int(cand[np.argmin(np.abs(cand - center))])


def value_area(p: Profile, pct: float = 0.70) -> tuple[int, int, int]:
    """Value Area по Далтону: от POC сравниваются суммы двух следующих
    строк сверху и снизу, добавляется большая пара. При равенстве
    добавляются обе. Возвращает (poc, val, vah) в тиках."""
    c = p.counts
    n = len(c)
    poc = poc_index(p)
    target = pct * c.sum()
    lo = hi = poc
    acc = c[poc]
    while acc < target and (lo > 0 or hi < n - 1):
        up = c[hi + 1:min(hi + 3, n)].sum() if hi < n - 1 else -1
        dn = c[max(lo - 2, 0):lo].sum() if lo > 0 else -1
        if up >= dn and up >= 0:
            new_hi = min(hi + 2, n - 1)
            acc += c[hi + 1:new_hi + 1].sum()
            hi = new_hi
            if up == dn and lo > 0:
                new_lo = max(lo - 2, 0)
                acc += c[new_lo:lo].sum()
                lo = new_lo
        else:
            new_lo = max(lo - 2, 0)
            acc += c[new_lo:lo].sum()
            lo = new_lo
    return p.price_of(poc), p.price_of(lo), p.price_of(hi)


def tail_rows(counts: np.ndarray, from_top: bool) -> int:
    """Число подряд идущих строк с TPO=1 от края профиля."""
    seq = counts[::-1] if from_top else counts
    k = 0
    for v in seq:
        if v != 1:
            break
        k += 1
    return k


def daily_profiles(bars: pd.DataFrame, cfg: dict, tick: float) -> tuple[pd.DataFrame, dict]:
    """Таблица дневных уровней (цены в пунктах) и словарь date -> Profile."""
    tcfg = cfg["tpo"]
    row = tcfg["price_row_ticks"]
    ib_n = tcfg["ib_periods"]
    full = cfg["data"]["min_periods_full_day"]
    min_tail = cfg["tpo"]["tail_min_rows"]

    rows, profiles = [], {}
    for date, g in bars.groupby("date", sort=True):
        g = g.sort_values("period")
        lows = np.rint(g["low"].to_numpy() / tick).astype(np.int64)
        highs = np.rint(g["high"].to_numpy() / tick).astype(np.int64)
        p = build_profile(lows, highs, row)
        poc, val, vah = value_area(p, tcfg["value_area_pct"])
        ib = g[g["period"] < ib_n]
        t_up = tail_rows(p.counts, True)
        t_dn = tail_rows(p.counts, False)
        rows.append({
            "date": date,
            "n_periods": len(g),
            "short_day": len(g) < full,
            "open": g["open"].iloc[0],
            "high": g["high"].max(),
            "low": g["low"].min(),
            "close": g["close"].iloc[-1],
            "volume": g["volume"].sum(),
            "ib_high": ib["high"].max(),
            "ib_low": ib["low"].min(),
            "poc": poc * tick,
            "vah": vah * tick,
            "val": val * tick,
            "total_tpo": int(p.counts.sum()),
            "single_prints": int((p.counts == 1).sum()),
            "tail_up_ticks": t_up * row if t_up >= min_tail else 0,
            "tail_down_ticks": t_dn * row if t_dn >= min_tail else 0,
            "poor_high": bool(p.counts[-1] >= 2),
            "poor_low": bool(p.counts[0] >= 2),
        })
        profiles[date] = p
    return pd.DataFrame(rows), profiles


def detect_rolls(daily: pd.DataFrame, rel_threshold: float = 0.55) -> pd.DataFrame:
    """Эвристика для непрерывного ряда без склейки: перед сменой контракта
    объём в истекающем контракте падает. Возвращает последний
    «низкообъёмный» день перед каждым ролловом и гэп к следующему дню."""
    d = daily[~daily["short_day"]].set_index("date")
    rel = d["volume"] / d["volume"].rolling(11, center=True, min_periods=5).median()
    low = rel < rel_threshold
    gap = d["open"].shift(-1) - d["close"]
    out = pd.DataFrame({"rel_volume": rel, "gap_to_next": gap})[low]
    # Оставляем последний день в каждом квартальном кластере.
    keep = out.index.to_series().diff(-1).abs() > pd.Timedelta(days=10)
    keep.iloc[-1] = True
    return out[keep.values]
