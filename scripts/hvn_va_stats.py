"""HVN внутри VA каждого дня: сколько TPO-принтов наслоено друг на друга.

Для каждого полного дня (13 периодов):
  hvn_tpo      — максимум TPO в строке внутри VA (самая «толстая» строка = POC);
  va_mean_tpo  — средний TPO-счёт строк VA (строка = 1 тик);
  band_rows / band_mean_tpo — HVN-полоса вокруг POC (строки ≥ intraday.hvn_share × max,
                 непрерывно от POC), её ширина в тиках и средний TPO в ней.
По всем дням: среднее, σ и коридор среднее ± 1σ.

Запуск:  python -m scripts.hvn_va_stats [ES]
Результат: output/<INSTR>_hvn_va_daily.csv, output/<INSTR>_hvn_va_summary.csv
"""
from __future__ import annotations

import sys

import pandas as pd

from scripts.stage1_profiles import load_adjusted
from tpo.data import ROOT, load_config
from tpo.intraday import hvn_band

METRICS = {
    "hvn_tpo": "HVN в VA, TPO в строке (максимум)",
    "va_mean_tpo": "Средний TPO строки внутри VA",
    "band_mean_tpo": "Средний TPO в HVN-полосе",
    "band_rows": "Ширина HVN-полосы, тиков",
}


def per_day(daily: pd.DataFrame, profiles: dict, tick: float, share: float) -> pd.DataFrame:
    rows = []
    for d in daily[~daily.short_day].itertuples():
        p = profiles[d.date]
        poc, val, vah = (int(round(x / tick)) for x in (d.poc, d.val, d.vah))
        va = p.counts[(val - p.low) // p.row:(vah - p.low) // p.row + 1]
        lo, hi = hvn_band(p, poc, share)
        band = p.counts[(lo - p.low) // p.row:(hi - p.low) // p.row + 1]
        rows.append({
            "date": d.date, "poc": d.poc, "val": d.val, "vah": d.vah,
            "hvn_tpo": int(va.max()),
            "va_mean_tpo": va.mean(),
            "band_lo": lo * tick, "band_hi": hi * tick,
            "band_rows": len(band),
            "band_mean_tpo": band.mean(),
        })
    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    out = []
    for col, name in METRICS.items():
        s = df[col]
        m, sd = s.mean(), s.std()
        out.append({
            "metric": col, "description": name, "days": len(s),
            "mean": m, "std": sd, "minus_1sd": m - sd, "plus_1sd": m + sd,
            "median": s.median(), "share_within_1sd": s.between(m - sd, m + sd).mean(),
        })
    return pd.DataFrame(out)


def main(instrument: str = "ES") -> None:
    cfg = load_config()
    out = ROOT / "output"
    out.mkdir(exist_ok=True)
    _, daily, profiles, _, tick = load_adjusted(instrument, cfg)
    df = per_day(daily, profiles, tick, cfg["intraday"]["hvn_share"])
    summ = summarize(df)
    df.to_csv(out / f"{instrument}_hvn_va_daily.csv", index=False, float_format="%.2f")
    summ.to_csv(out / f"{instrument}_hvn_va_summary.csv", index=False, float_format="%.3f")

    print(f"{instrument}: {len(df)} полных дней, строка = {cfg['tpo']['price_row_ticks']} тик\n")
    for r in summ.itertuples():
        print(f"{r.description:38s} среднее {r.mean:5.2f}  σ {r.std:4.2f}  "
              f"−1σ {r.minus_1sd:5.2f}  +1σ {r.plus_1sd:5.2f}  "
              f"(медиана {r.median:.1f}, в ±1σ {r.share_within_1sd:.0%} дней)")
    print("\nHVN в VA (TPO) по годам:")
    g = df.groupby(df.date.dt.year)["hvn_tpo"].agg(["count", "mean", "std"])
    g["-1σ"], g["+1σ"] = g["mean"] - g["std"], g["mean"] + g["std"]
    print(g.round(2).to_string())
    print("\nРаспределение HVN в VA (TPO -> дней):")
    print(df.hvn_tpo.value_counts().sort_index().to_string())


if __name__ == "__main__":
    main(*sys.argv[1:])
