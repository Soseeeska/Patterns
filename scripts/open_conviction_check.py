"""Потенциал дня по месту открытия (Dalton): односторонний ход от open, двусторонность,
тип открытия по периоду A.

Тип открытия (по периоду A, RTH 30m):
  Open Drive  — open у края A (в пределах 10% диапазона A) и цена после A не возвращалась к open;
  Open Test Drive / Rejection-Reverse / Auction — остальное, грубо:
     ORR — после A цена ушла за противоположный от open край A и день закрылся там;
     Auction — всё остальное.
Запуск:  python -m scripts.open_conviction_check
"""
import numpy as np
import pandas as pd

from scripts.stage1_profiles import load_adjusted
from tpo.data import load_config
from tpo.day_type import classify_days

cfg = load_config()
bars, daily, prof, _, tick = load_adjusted("ES", cfg)
d = classify_days(daily, prof, cfg, tick)
d = d[d.day_type != "Short"].sort_values("date").reset_index(drop=True)
d["avg"] = (d.high - d.low).rolling(20, min_periods=10).mean().shift(1)
per = {k: g.sort_values("period").reset_index(drop=True) for k, g in bars.groupby("date")}
rows = []
for i in range(1, len(d)):
    t, p = d.iloc[i], d.iloc[i - 1]
    if np.isnan(t.avg):
        continue
    o = t.open
    loc = ("1 внутри VA" if p.val <= o <= p.vah else
           "2 вне VA, внутри диапазона" if p.low <= o <= p.high else "3 вне диапазона")
    g = per[t.date]
    up_x, dn_x = (t.high - o) / t.avg, (o - t.low) / t.avg
    a = g.iloc[0]
    rest = g.iloc[1:]
    a_rng = max(a.high - a.low, tick)
    at_low = (o - a.low) <= 0.1 * a_rng
    at_high = (a.high - o) <= 0.1 * a_rng
    drive = (at_low and (rest.low > o).all()) or (at_high and (rest.high < o).all())
    rows.append(dict(loc=loc, one_side=max(up_x, dn_x), two_side=min(up_x, dn_x),
                     big_one=max(up_x, dn_x) >= 1.0, both_half=min(up_x, dn_x) >= 0.5, drive=drive,
                     trend=t.day_type == "Trend"))
f = pd.DataFrame(rows)
g = f.groupby("loc")
out = pd.DataFrame({
    "дней": g.size(),
    "макс. ход от open в одну сторону, мед. (в ср. range)": g.one_side.median().round(2),
    "ход ≥1 среднего range в одну сторону %": (100 * g.big_one.mean()).round(1),
    "ход ≥0.5 range в ОБЕ стороны %": (100 * g.both_half.mean()).round(1),
    "Open Drive %": (100 * g.drive.mean()).round(1),
    "Trend-день %": (100 * g.trend.mean()).round(1),
})
base = pd.DataFrame({
    "дней": [len(f)], "макс. ход от open в одну сторону, мед. (в ср. range)": [round(f.one_side.median(), 2)],
    "ход ≥1 среднего range в одну сторону %": [round(100 * f.big_one.mean(), 1)],
    "ход ≥0.5 range в ОБЕ стороны %": [round(100 * f.both_half.mean(), 1)],
    "Open Drive %": [round(100 * f.drive.mean(), 1)], "Trend-день %": [round(100 * f.trend.mean(), 1)],
}, index=["ВСЕ ДНИ"])
pd.set_option("display.width", 250)
print(pd.concat([out, base]).to_string())
