"""Принятие при входе в композит за 1 и 2 дня, реальные против фальш-композитов.

Принятие (close): close дня в POC композита (± допуск) или дальше POC от стороны входа.
  d0    — по close дня входа;
  d0–d1 — по close дня входа ИЛИ следующего дня.
Дотянулась до POC (касание): цена хотя бы дошла до POC (± допуск)
  d0    — с периода входа до конца дня;
  d0–d1 — то же или в любой момент следующего дня.
Отказ = 100% − принятие.

Запуск:  python -m scripts.acceptance_horizon [ES]   (нужны output/ES_events.csv и
         output/ES_placebo_events.csv от этапов 4–5)
Результат: output/<INSTR>_acceptance_horizon.csv
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from scripts.stage1_profiles import load_adjusted
from tpo.data import ROOT, load_config


def add_horizons(ev: pd.DataFrame, bars: pd.DataFrame, daily: pd.DataFrame, tol: float) -> pd.DataFrame:
    full = daily[~daily.short_day].sort_values("date").reset_index(drop=True)
    nxt = full.set_index("date")[["close", "high", "low"]].shift(-1)
    nxt.columns = ["close_d1", "high_d1", "low_d1"]
    day = full.set_index("date")[["close"]]
    per = {d: g.sort_values("period") for d, g in bars.groupby("date")}
    ev = ev.join(day, on="date").join(nxt, on="date")
    above = ev["approach"] == "above"
    poc = ev["comp_poc"]

    def beyond(price):
        return np.where(above, price <= poc + tol, price >= poc - tol)

    ev["acc_d0"] = beyond(ev["close"])
    ev["acc_d01"] = ev["acc_d0"] | (beyond(ev["close_d1"]) & ev["close_d1"].notna())
    lo_after, hi_after = [], []
    for r in ev.itertuples():
        g = per[r.date]
        g = g[g.period >= r.first_touch_period]
        lo_after.append(g.low.min())
        hi_after.append(g.high.max())
    ev["lo_after"], ev["hi_after"] = lo_after, hi_after
    ev["touch_poc_d0"] = np.where(above, ev["lo_after"] <= poc + tol, ev["hi_after"] >= poc - tol)
    ev["touch_poc_d01"] = ev["touch_poc_d0"] | np.where(above, ev["low_d1"] <= poc + tol,
                                                        ev["high_d1"] >= poc - tol)
    return ev[ev["close_d1"].notna()]   # последний день данных без следующего — выкидываем


METRICS = {"acc_d0": "Принятие по close, день входа",
           "acc_d01": "Принятие по close, день входа или следующий",
           "touch_poc_d0": "Дотянулась до POC, день входа",
           "touch_poc_d01": "Дотянулась до POC, день входа или следующий"}


def main(instrument: str = "ES") -> None:
    cfg = load_config()
    tick = cfg["data"]["instruments"][instrument]["tick_size"]
    tol = cfg["interaction"]["acceptance"].get("poc_tolerance_ticks", 2) * tick
    bars, daily, _, _, _ = load_adjusted(instrument, cfg)
    out = ROOT / "output"
    real = pd.read_csv(out / f"{instrument}_events.csv", parse_dates=["date"])
    real = real[~real.breakout_day]
    plac = pd.read_csv(out / f"{instrument}_placebo_events.csv", parse_dates=["date"])
    real = add_horizons(real, bars, daily, tol)
    plac = add_horizons(plac, bars, daily, tol)

    rows = []
    for grp, rsel, psel in [("все входы", real, plac),
                            ("через VAH сверху", real[real.level_type == "VAH"], plac[plac.level_type == "VAH"]),
                            ("через VAL снизу", real[real.level_type == "VAL"], plac[plac.level_type == "VAL"])]:
        for m, label in METRICS.items():
            v = 100 * rsel[m].mean()
            runs = 100 * psel.groupby("run")[m].mean()
            lo, hi = np.percentile(runs, [2.5, 97.5])
            rows.append({"группа": grp, "показатель": label, "n": len(rsel),
                         "реальные %": round(v, 1), "отказ %": round(100 - v, 1),
                         "фальш %": round(runs.mean(), 1), "фальш 95%": f"{lo:.1f}–{hi:.1f}",
                         "итог": "выше фона" if v > hi else "ниже фона" if v < lo else "как фон"})
    t = pd.DataFrame(rows)
    t.to_csv(out / f"{instrument}_acceptance_horizon.csv", index=False)
    pd.set_option("display.width", 200)
    print(t.to_string(index=False))
    # из принявших за 2 дня — сколько приняли уже в день входа
    a = real[real.acc_d01]
    print(f"\nИз принятий за 2 дня в сам день входа: {100 * a.acc_d0.mean():.1f}% "
          f"({int(a.acc_d0.sum())} из {len(a)})")
    print(f"Дотянулась до POC в день входа, но close не принят: "
          f"{100 * (real.touch_poc_d0 & ~real.acc_d0).mean():.1f}% входов")


if __name__ == "__main__":
    main(*sys.argv[1:])
