"""Этап 7: открытие RTH вне диапазона прошлого дня × зоны прошлых композитов.

Вопрос: меняется ли вероятность вернуться в диапазон / VA / к POC прошлого дня,
если на пути назад (между open и границей прошлого дня) стоит уровень старого
композита, если граница прошлого дня совпадает с уровнем композита, или если
день открылся внутри VA старого композита. Каждое отличие сверяется с теми же
расчётами на фальш-композитах (stats.placebo), чтобы отделить эффект уровня
от простой геометрии (больший гэп чаще содержит любой уровень).

Запуск:  python -m scripts.stage7_outside_open [ES]
Результат: output/<INSTR>_outside_open.csv, output/<INSTR>_outside_open_combo.csv
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from scripts.hvn_va_stats import per_day
from scripts.primary_zones import placebo as primary_placebo
from scripts.primary_zones import zone_frame
from scripts.stage1_profiles import load_adjusted
from tpo.balance import find_balances
from tpo.data import ROOT, load_config
from tpo.stats import placebo_comps, wilson

TARGETS = ["range", "va", "poc"]
WINDOWS = {"A": 0, "1h": 1, "day": 99}
CONFLUENCE_TICKS = 8          # граница прошлого дня ≈ уровень композита (± 2 п.)


def base_table(bars: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """Дни с открытием вне High–Low прошлого дня и периоды первого касания уровней."""
    d = daily.sort_values("date").reset_index(drop=True)
    by_day = {k: g.sort_values("period") for k, g in bars.groupby("date")}
    rows = []
    for i in range(1, len(d)):
        p, t = d.iloc[i - 1], d.iloc[i]
        if t.open > p.high:
            side, lv = "above", {"range": p.high, "va": p.vah, "poc": p.poc}
        elif t.open < p.low:
            side, lv = "below", {"range": p.low, "va": p.val, "poc": p.poc}
        else:
            continue
        g = by_day[t.date]
        r = {"date": t.date, "side": side, "open": t.open, "short_day": bool(t.short_day),
             "gap": abs(t.open - lv["range"]), "prev_range": p.high - p.low}
        for k, x in lv.items():
            hit = (g["low"] <= x) if side == "above" else (g["high"] >= x)
            r[f"{k}_level"] = x
            r[f"{k}_period"] = int(g["period"][hit].iloc[0]) if hit.any() else np.nan
        rows.append(r)
    return pd.DataFrame(rows)


def zone_features(base: pd.DataFrame, comps: pd.DataFrame, min_days: int, tick: float) -> pd.DataFrame:
    """Признаки по зонам композитов, доступных в день открытия (без look-ahead)."""
    c = comps[(comps.end_reason != "open") & (comps.n_days >= min_days)]
    av = c.available_from.to_numpy()
    lv = c[["vah", "val", "poc"]].to_numpy()
    vah, val = c.vah.to_numpy(), c.val.to_numpy()
    tol = CONFLUENCE_TICKS * tick
    out = []
    for r in base.itertuples():
        m = av <= np.datetime64(r.date)
        levels = lv[m].ravel()
        lo, hi = sorted((r.open, r.range_level))
        # уровень строго между open и границей прошлого дня = препятствие на пути назад
        obstacle = int(((levels > lo + tick) & (levels < hi - tick)).sum())
        confl = bool((np.abs(levels - r.range_level) <= tol).any())
        in_va = bool(((val[m] <= r.open) & (r.open <= vah[m])).any())
        out.append({"obstacle": obstacle > 0, "n_obstacles": obstacle,
                    "confluence": confl, "open_inside": in_va})
    return pd.DataFrame(out, index=base.index)


def band_features(base: pd.DataFrame, zones: pd.DataFrame, tick: float) -> pd.DataFrame:
    """То же для первичных HVN-зон (полосы lo–hi), доступных в день открытия."""
    av = zones.available_from.to_numpy(dtype="datetime64[ns]")
    zlo, zhi = zones.lo.to_numpy(), zones.hi.to_numpy()
    tol = CONFLUENCE_TICKS * tick
    out = []
    for r in base.itertuples():
        m = av <= np.datetime64(r.date)
        lo_, hi_ = zlo[m], zhi[m]
        lo, hi = sorted((r.open, r.range_level))
        # полоса целиком строго между open и границей прошлого дня
        obstacle = int(((lo_ > lo + tick) & (hi_ < hi - tick)).sum())
        confl = bool(((lo_ - tol <= r.range_level) & (r.range_level <= hi_ + tol)).any())
        inside = bool(((lo_ <= r.open) & (r.open <= hi_)).any())
        out.append({"obstacle": obstacle > 0, "n_obstacles": obstacle,
                    "confluence": confl, "open_inside": inside})
    return pd.DataFrame(out, index=base.index)


def rates(df: pd.DataFrame) -> dict:
    res = {"n": len(df)}
    for t in TARGETS:
        p = df[f"{t}_period"]
        for w, k in WINDOWS.items():
            res[f"{t}_{w}"] = float((p <= k).mean()) if len(df) else np.nan
    return res


FEATURES = ["obstacle", "confluence", "open_inside"]


def split_rates(df: pd.DataFrame, feat: str) -> dict:
    return {flag: rates(df[df[feat] == flag]) for flag in (True, False)}


def combo(real: pd.DataFrame, placebo: list[pd.DataFrame]) -> pd.DataFrame:
    """Доли возврата с признаком и без, разница против разброса фальш-зон."""
    rows = []
    gap_q = pd.qcut(real.gap, 3, labels=["малый", "средний", "большой"])
    subsets = {"все": slice(None), "выше": real.side == "above", "ниже": real.side == "below"}
    for g in gap_q.cat.categories:
        subsets[f"гэп {g}"] = gap_q == g
    for sname, mask in subsets.items():
        for feat in FEATURES:
            rr = split_rates(real[mask], feat)
            pr = [split_rates(p[mask], feat) for p in placebo]
            for flag in (True, False):
                n = rr[flag]["n"]
                for t in TARGETS:
                    for w in WINDOWS:
                        k = f"{t}_{w}"
                        v = rr[flag][k]
                        pv = np.array([x[flag][k] for x in pr], dtype=float)
                        ci = wilson(v, n) if n else (np.nan, np.nan)
                        # разница «с признаком − без» у реальных и у фальш-зон
                        dv = rr[True][k] - rr[False][k]
                        dp = np.array([x[True][k] - x[False][k] for x in pr], dtype=float)
                        rows.append({
                            "subset": sname, "feature": feat, "flag": flag, "target": t, "window": w,
                            "n": n, "real": v, "ci_lo": ci[0], "ci_hi": ci[1],
                            "placebo": np.nanmean(pv), "placebo_lo": np.nanpercentile(pv, 2.5),
                            "placebo_hi": np.nanpercentile(pv, 97.5),
                            "diff_real": dv, "diff_placebo": np.nanmean(dp),
                            "diff_placebo_lo": np.nanpercentile(dp, 2.5),
                            "diff_placebo_hi": np.nanpercentile(dp, 97.5),
                            "n_placebo_true": float(np.mean([x[True]["n"] for x in pr])),
                        })
    res = pd.DataFrame(rows)
    res["edge"] = np.where(res.diff_real > res.diff_placebo_hi, "выше фона",
                           np.where(res.diff_real < res.diff_placebo_lo, "ниже фона", "как фон"))
    return res


def main(instrument: str = "ES") -> None:
    cfg = load_config()
    out = ROOT / "output"
    out.mkdir(exist_ok=True)
    bars, daily, profiles, _, tick = load_adjusted(instrument, cfg)
    comps, _ = find_balances(bars, daily, profiles, cfg, tick)
    min_days = cfg["zones"].get("min_composite_days", 0)

    base = base_table(bars, daily)
    pc = cfg["stats"]["placebo"]
    rng = np.random.default_rng(pc["seed"])
    real_c = pd.concat([base, zone_features(base, comps, min_days, tick)], axis=1)
    plac_c = [pd.concat([base, zone_features(base, placebo_comps(comps, rng, cfg, tick), min_days, tick)],
                        axis=1) for _ in range(pc["runs"])]

    zc, pz = cfg["hvn_zones"], cfg["primary_zones"]
    hd = per_day(daily, profiles, tick, cfg["intraday"]["hvn_share"]).sort_values("date")
    hd["ok"] = hd.hvn_tpo.between(zc["min_tpo"], zc["max_tpo"])
    zones = zone_frame(hd, daily, cfg, tick)
    zones = zones[zones.available_from.notna()]
    real_p = pd.concat([base, band_features(base, zones, tick)], axis=1)
    rng2 = np.random.default_rng(42)
    plac_p = [pd.concat([base, band_features(base, primary_placebo(zones, rng2, pz, tick, False), tick)],
                        axis=1) for _ in range(pz["placebo_runs"])]

    keep = ["obstacle", "n_obstacles", "confluence", "open_inside"]
    real_c.join(real_p[keep].add_prefix("hvn_")).to_csv(
        out / f"{instrument}_outside_open.csv", index=False, float_format="%.2f")
    frames = []
    for src, real, placebo in [("композиты", real_c, plac_c), ("первичные HVN", real_p, plac_p)]:
        frames.append(combo(real, placebo).assign(source=src))
    res = pd.concat(frames)
    res.to_csv(out / f"{instrument}_outside_open_combo.csv", index=False, float_format="%.4f")
    real = real_c

    print(f"{instrument}: открытий вне диапазона {len(real)} "
          f"(выше {int((real.side == 'above').sum())}, ниже {int((real.side == 'below').sum())})")
    b = rates(real)
    print("База: " + "  ".join(f"{t}: A {b[t+'_A']:.1%} / 1ч {b[t+'_1h']:.1%} / день {b[t+'_day']:.1%}"
                               for t in TARGETS))


    pd.set_option("display.width", 250)
    show = res[(res.flag) & (res.window == "day")]
    print(show[["source", "subset", "feature", "target", "n", "real", "diff_real", "diff_placebo",
                "diff_placebo_lo", "diff_placebo_hi", "edge"]].round(3).to_string(index=False))


if __name__ == "__main__":
    main(*sys.argv[1:])
