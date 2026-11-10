"""Тип дня по Далтону, форма профиля, миграция value.

Правила проверяются по порядку (первое сработавшее):
  1. Neutral Center / Extreme — значимое RE в обе стороны
  2. Double Distribution      — RE в одну сторону + внутренний разрыв из single prints
  3. Trend                    — RE в одну сторону, range > 2×IB, close у края по направлению RE,
                                профиль вытянутый (VA ≥ 45% range, max TPO в строке ≤ 9)
  4. Normal Variation         — RE в одну сторону (остальное)
  5. Non-trend                — без RE, range < 0.6 × средний range
  6. Normal                   — без RE (остальное)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .profile import Profile


def internal_single_gap(counts: np.ndarray, max_tpo: int = 1) -> int:
    """Длина (в строках) самого длинного «тонкого» участка (TPO ≤ max_tpo),
    у которого и выше, и ниже есть строки с TPO > max_tpo (т.е. не хвост)."""
    multi = np.flatnonzero(counts > max_tpo)
    if len(multi) < 2:
        return 0
    inner = counts[multi[0]:multi[-1] + 1]
    best = run = 0
    for v in inner:
        run = run + 1 if v <= max_tpo else 0
        best = max(best, run)
    return best


def profile_shape(p: Profile, poc: int, dd: bool, cfg: dict) -> tuple[str, float, float]:
    """Возвращает (форма, доля TPO выше POC, положение POC в диапазоне 0..1)."""
    scfg = cfg["profile_shape"]
    c = p.counts
    i_poc = (poc - p.low) // p.row
    above = c[i_poc + 1:].sum()
    below = c[:i_poc].sum()
    share_above = above / max(above + below, 1)
    poc_pos = i_poc / max(len(c) - 1, 1)
    # центр масс TPO в диапазоне 0..1: у P-профиля «толстая» часть сверху
    mass_pos = float((np.arange(len(c)) * c).sum() / c.sum() / max(len(c) - 1, 1))
    m = scfg["p_b_mass_center_min"]
    if dd:
        shape = "B"
    elif c.max() <= scfg["thin_max_tpo_per_row"]:
        shape = "thin"
    elif poc_pos >= 2 / 3 and mass_pos >= m:
        shape = "P"
    elif poc_pos <= 1 / 3 and mass_pos <= 1 - m:
        shape = "b"
    else:
        shape = "D"
    return shape, float(share_above), float(poc_pos)


def value_migration(val, vah, pval, pvah) -> str:
    if np.isnan(pval):
        return ""
    if val > pvah:
        return "higher"
    if vah < pval:
        return "lower"
    if val >= pval and vah <= pvah:
        return "inside"
    if val < pval and vah > pvah:
        return "outside"
    return "overlap_higher" if vah > pvah else "overlap_lower"


def classify_days(daily: pd.DataFrame, profiles: dict, cfg: dict, tick: float) -> pd.DataFrame:
    dcfg = cfg["day_type"]
    d = daily.sort_values("date").reset_index(drop=True).copy()
    full = ~d["short_day"]

    rng = d["high"] - d["low"]
    ib = d["ib_high"] - d["ib_low"]
    ext_up = (d["high"] - d["ib_high"]).clip(lower=0)
    ext_dn = (d["ib_low"] - d["low"]).clip(lower=0)
    min_ext = dcfg["min_extension_share_of_ib"] * ib
    re_up = ext_up > min_ext
    re_dn = ext_dn > min_ext
    avg_rng = rng.where(full).rolling(dcfg["avg_range_lookback_days"], min_periods=10).mean().shift(1)
    close_pos = (d["close"] - d["low"]) / rng.replace(0, np.nan)

    ddcfg = dcfg["double_distribution"]
    gaps, shapes, imb, pocpos = [], [], [], []
    for _, r in d.iterrows():
        p = profiles[r.date]
        g = internal_single_gap(p.counts, ddcfg["gap_max_tpo_per_row"]) * p.row
        n_rows = len(p.counts) * p.row
        is_dd_gap = g >= ddcfg["min_single_print_gap_ticks"] and g >= ddcfg["min_gap_share_of_range"] * n_rows
        gaps.append(g if is_dd_gap else 0)
        s, a, pp = profile_shape(p, int(round(r.poc / tick)), False, cfg)
        shapes.append(s)
        imb.append(a)
        pocpos.append(pp)
    d["dd_gap_ticks"] = gaps
    d["va_share_of_range"] = (d["vah"] - d["val"]) / rng.replace(0, np.nan)
    d["max_tpo_row"] = [int(profiles[x].counts.max()) for x in d["date"]]
    elongated = ((d["va_share_of_range"] >= dcfg["trend_min_va_share_of_range"])
                 & (d["max_tpo_row"] <= dcfg["trend_max_tpo_per_row"]))
    d["tpo_share_above_poc"] = imb
    d["poc_position"] = pocpos

    one_side = re_up ^ re_dn
    trend_close = np.where(re_up, close_pos >= 1 - dcfg["trend_close_extreme_share"],
                           close_pos <= dcfg["trend_close_extreme_share"])
    lo_c, hi_c = dcfg["neutral"]["center_close_band"]

    types = []
    for i in range(len(d)):
        if not full[i]:
            types.append("Short")
        elif re_up[i] and re_dn[i]:
            types.append("Neutral Center" if lo_c <= close_pos[i] <= hi_c else "Neutral Extreme")
        elif one_side[i] and d.at[i, "dd_gap_ticks"] > 0:
            types.append("Double Distribution")
        elif (one_side[i] and rng[i] > dcfg["trend_range_vs_ib_min"] * ib[i]
              and trend_close[i] and elongated[i]):
            types.append("Trend")
        elif one_side[i]:
            types.append("Normal Variation")
        elif not np.isnan(avg_rng[i]) and rng[i] < dcfg["non_trend_range_vs_avg_max"] * avg_rng[i]:
            types.append("Non-trend")
        else:
            types.append("Normal")
    d["day_type"] = types
    d["re_direction"] = np.select([re_up & re_dn, re_up, re_dn], ["both", "up", "down"], "none")
    d["range_vs_ib"] = rng / ib.replace(0, np.nan)
    d["range_vs_avg"] = rng / avg_rng
    d["close_position"] = close_pos
    d["profile_shape"] = np.where(d["day_type"] == "Double Distribution", "B", shapes)
    d["va_width_rel"] = (d["vah"] - d["val"]) / (d["vah"] - d["val"]).where(full).rolling(
        dcfg["avg_range_lookback_days"], min_periods=10).mean().shift(1)
    d["value_migration"] = [
        value_migration(a, b, c, e)
        for a, b, c, e in zip(d["val"], d["vah"], d["val"].shift(), d["vah"].shift())
    ]
    return d
