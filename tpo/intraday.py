"""Внутридневная реакция на зоны композитов по 30-минутным барам, с флипом.

Зона — полоса [lo, hi]: для VAH/VAL/POC это точка (lo = hi), для HVN — полоса
вокруг POC композита, где TPO-счёт строк ≥ hvn_share × максимум (непрерывно от POC).

Правило (config.intraday):
  * сторона цены — по close предыдущего бара: выше hi → зона поддержка,
    ниже lo → сопротивление; close внутри полосы — тест не начинается;
  * тест начинается с бара, который дотянулся до зоны (± touch_tolerance_ticks);
  * пробой (флип) — бар закрылся за дальним краем зоны И следующий бар тоже
    закрылся за ним. После пробоя зона меняет роль (поддержка ↔ сопротивление);
  * удержание — раньше пробоя появился бар целиком на исходной стороне
    (не дотягивается до зоны). Одиночное закрытие за зоной с возвратом —
    ложный пробой, тест продолжается;
  * reaction_ticks — максимум отхода от зоны в исходную сторону за бары после
    бара касания до конца теста + 2 бара (бар касания не берётся: внутри бара
    порядок high/low неизвестен); adverse_ticks — максимум прокола за зону
    во время теста.
Зона доступна с первого бара дня available_from композита.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def hvn_band(profile, poc_ticks: int, share: float) -> tuple[int, int]:
    c = profile.counts
    i = (poc_ticks - profile.low) // profile.row
    thr = share * c.max()
    lo = hi = i
    while lo > 0 and c[lo - 1] >= thr:
        lo -= 1
    while hi < len(c) - 1 and c[hi + 1] >= thr:
        hi += 1
    return profile.low + lo * profile.row, profile.low + hi * profile.row


def build_bands(comps: pd.DataFrame, comp_profiles: dict, cfg: dict, tick: float) -> pd.DataFrame:
    icfg = cfg["intraday"]
    rows = []
    closed = comps[comps.end_reason != "open"]
    for c in closed.itertuples():
        for lv in icfg["levels"]:
            if lv == "HVN":
                lo_t, hi_t = hvn_band(comp_profiles[c.composite_id], int(round(c.poc / tick)), icfg["hvn_share"])
                lo, hi = lo_t * tick, hi_t * tick
            else:
                lo = hi = getattr(c, lv.lower())
            rows.append({"zone_id": f"{c.composite_id}_{lv}", "composite_id": c.composite_id,
                         "level_type": lv, "lo": lo, "hi": hi, "composite_days": c.n_days,
                         "available_from": c.available_from, "composite_end": c.end})
    return pd.DataFrame(rows)


def run_tests(bars: pd.DataFrame, bands: pd.DataFrame, cfg: dict, tick: float) -> pd.DataFrame:
    icfg = cfg["intraday"]
    tol = cfg["interaction"]["touch_tolerance_ticks"] * tick
    after = icfg["reaction_bars_after"]
    b = bars.sort_values("ts").reset_index(drop=True)
    L, H, C = b["low"].to_numpy(), b["high"].to_numpy(), b["close"].to_numpy()
    dates = b["date"].to_numpy()
    periods = b["period"].to_numpy()
    n = len(b)
    first_bar = pd.Series(np.arange(n), index=b["date"]).groupby(level=0).min()

    out = []
    for z in bands.itertuples():
        if z.available_from not in first_bar.index:
            continue
        s0 = int(first_bar[z.available_from])
        lo, hi = z.lo, z.hi
        # бары, дотянувшиеся до зоны
        touch = (L[s0:] <= hi + tol) & (H[s0:] >= lo - tol)
        idx = np.flatnonzero(touch) + s0
        role_flips = 0
        tests_since_flip = 0
        k = 0
        last_end = s0 - 1
        while k < len(idx):
            t = idx[k]
            if t <= last_end or t == 0:
                k += 1
                continue
            pc = C[t - 1]
            if pc > hi:
                above = True
            elif pc < lo:
                above = False
            else:
                k += 1
                continue
            # разворачиваем тест
            j = t
            outcome = None
            false_break = False
            while j < n:
                beyond_j = C[j] < lo if above else C[j] > hi
                if beyond_j:
                    if j + 1 < n and (C[j + 1] < lo if above else C[j + 1] > hi):
                        outcome = "break"
                        end = j + 1
                        break
                    false_break = True
                away = L[j] > hi + tol if above else H[j] < lo - tol
                if away and j > t:
                    outcome = "hold"
                    end = j
                    break
                j += 1
            if outcome is None:
                break
            seg = slice(t + 1, min(end + after, n - 1) + 1)
            if above:
                react = (H[seg].max() - hi) if seg.start < seg.stop else 0.0
                adverse = max(0.0, lo - L[t:end + 1].min())
            else:
                react = (lo - L[seg].min()) if seg.start < seg.stop else 0.0
                adverse = max(0.0, H[t:end + 1].max() - hi)
            if outcome == "break":
                # для пробоя реакция в исходную сторону до пробоя
                seg2 = slice(t + 1, end + 1)
                react = ((H[seg2].max() - hi) if above else (lo - L[seg2].min())) if seg2.start < seg2.stop else 0.0
            tests_since_flip += 1
            out.append({
                "zone_id": z.zone_id, "composite_id": z.composite_id, "level_type": z.level_type,
                "composite_days": z.composite_days, "date": pd.Timestamp(dates[t]), "period": int(periods[t]),
                "role": "support" if above else "resistance",
                "zone_state": "original" if role_flips == 0 else "flipped",
                "flips_before": role_flips, "test_no": tests_since_flip,
                "outcome": outcome, "false_break": false_break, "bars": int(end - t + 1),
                "reaction_ticks": round(max(react, 0.0) / tick), "adverse_ticks": round(adverse / tick),
                "breakout_day": pd.Timestamp(dates[t]) == z.available_from,
            })
            if outcome == "break":
                role_flips += 1
                tests_since_flip = 0
            last_end = end
            k += 1
    return pd.DataFrame(out)


def placebo_bands(bands: pd.DataFrame, comps: pd.DataFrame, real_levels: np.ndarray,
                  rng: np.random.Generator, cfg: dict, tick: float) -> pd.DataFrame:
    """Сдвигает все зоны композита вместе на ±U(lo, hi) ширин VA; сдвиг принимается,
    только если ни один сдвинутый край не ближе min_dist тиков к реальному уровню
    (VAH/VAL/POC любого композита). Иначе до 200 попыток, затем композит пропускается."""
    pc = cfg["stats"]["placebo"]
    a, b_ = pc["shift_va_widths"]
    min_d = cfg["intraday"]["placebo_min_dist_ticks"] * tick
    width = (comps.set_index("composite_id")["vah"] - comps.set_index("composite_id")["val"]).clip(
        lower=pc["min_va_width_ticks"] * tick)
    out = []
    skipped = 0
    for cid, g in bands.groupby("composite_id"):
        edges = np.concatenate([g.lo.to_numpy(), g.hi.to_numpy()])
        ok = False
        for _ in range(200):
            sh = rng.uniform(a, b_) * width[cid] * rng.choice([-1, 1])
            sh = np.round(sh / tick) * tick
            if np.min(np.abs(real_levels[None, :] - (edges + sh)[:, None])) >= min_d:
                ok = True
                break
        if not ok:
            skipped += 1
            continue
        g2 = g.copy()
        g2["lo"] += sh
        g2["hi"] += sh
        out.append(g2)
    res = pd.concat(out) if out else bands.iloc[0:0]
    res.attrs["skipped"] = skipped
    return res
