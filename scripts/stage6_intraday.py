"""Этап 6: внутридневная реакция на зоны композитов (30m), с флипом, против фальш-зон.

Запуск:  python -m scripts.stage6_intraday [ES]
Результат: output/<INSTR>_intraday_tests.csv, output/<INSTR>_intraday_summary.csv
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from scripts.stage1_profiles import load_adjusted
from tpo.balance import find_balances
from tpo.data import ROOT, load_config
from tpo.intraday import build_bands, placebo_bands, run_tests


def bucket(n):
    return "1" if n == 1 else ("2-3" if n <= 3 else "4+")


def summarize(t: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    g = t.groupby(by, observed=True)
    return pd.DataFrame({
        "n": g.size(),
        "hold": 100 * g["outcome"].apply(lambda s: (s == "hold").mean()),
        "react_med": g["reaction_ticks"].median(),
        "react_ge8": 100 * g["reaction_ticks"].apply(lambda s: (s >= 8).mean()),
        "react_ge16": 100 * g["reaction_ticks"].apply(lambda s: (s >= 16).mean()),
        "adverse_med": g["adverse_ticks"].median(),
        "false_break": 100 * g["false_break"].mean(),
    })


def prep(t: pd.DataFrame, cfg) -> pd.DataFrame:
    if cfg["interaction"]["exclude_breakout_day"]:
        t = t[~t.breakout_day]
    t = t.copy()
    t["test_bucket"] = t["test_no"].map(bucket)
    t["all"] = "все"
    return t


def main(instrument: str = "ES") -> None:
    cfg = load_config()
    out = ROOT / "output"
    bars, daily, profiles, _, tick = load_adjusted(instrument, cfg)
    comps, comp_prof = find_balances(bars, daily, profiles, cfg, tick)
    comps = comps[comps.n_days >= cfg["zones"].get("min_composite_days", 0)]
    bands = build_bands(comps, comp_prof, cfg, tick)
    real = prep(run_tests(bars, bands, cfg, tick), cfg)
    real.to_csv(out / f"{instrument}_intraday_tests.csv", index=False)
    print(f"Зон: {len(bands)}, тестов: {len(real)}; медианная ширина HVN: "
          f"{((bands[bands.level_type == 'HVN'].hi - bands[bands.level_type == 'HVN'].lo) / tick).median():.0f} тиков")

    real_levels = np.concatenate([comps.vah.to_numpy(), comps.val.to_numpy(), comps.poc.to_numpy()])
    rng = np.random.default_rng(cfg["stats"]["placebo"]["seed"])
    plac = []
    for r in range(cfg["stats"]["placebo"]["runs"]):
        pb = placebo_bands(bands, comps, real_levels, rng, cfg, tick)
        t = prep(run_tests(bars, pb, cfg, tick), cfg)
        t["run"] = r
        plac.append(t)
        print(f"  фальш-прогон {r}: композитов пропущено {pb.attrs['skipped']}, тестов {len(t)}")

    slices = [["all"], ["level_type"], ["level_type", "zone_state"], ["level_type", "role"],
              ["level_type", "test_bucket"]]
    rows = []
    for by in slices:
        rs = summarize(real, by)
        ps = [summarize(p, by) for p in plac]
        for key in rs.index:
            key_t = key if isinstance(key, tuple) else (key,)
            row = {"slice": "+".join(by), "group": " · ".join(map(str, key_t)), "n": int(rs.at[key, "n"])}
            for m in ["hold", "react_med", "react_ge8", "react_ge16", "adverse_med", "false_break"]:
                vals = np.array([p.at[key, m] for p in ps if key in p.index], float)
                v = float(rs.at[key, m])
                lo, hi = np.percentile(vals, [2.5, 97.5]) if len(vals) else (np.nan, np.nan)
                row[m] = round(v, 1)
                row[m + "_pl"] = round(float(np.mean(vals)), 1) if len(vals) else np.nan
                row[m + "_pl95"] = f"{lo:.1f}–{hi:.1f}"
                row[m + "_v"] = "выше" if v > hi else "ниже" if v < lo else "фон"
            rows.append(row)
    s = pd.DataFrame(rows)
    s.to_csv(out / f"{instrument}_intraday_summary.csv", index=False)
    pd.set_option("display.width", 250)
    cols = ["slice", "group", "n", "hold", "hold_pl", "hold_pl95", "hold_v",
            "react_ge16", "react_ge16_pl", "react_ge16_v", "react_med", "react_med_pl"]
    print(s[cols].to_string(index=False))


if __name__ == "__main__":
    main(*sys.argv[1:])
