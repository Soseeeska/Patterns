"""Этап 1: дневные TPO-профили (с back-adjust роллов) + картинка для сверки.

Запуск:  python -m scripts.stage1_profiles [ES]
Результат: output/<INSTR>_daily_profiles.csv, output/<INSTR>_rolls.csv,
           output/<INSTR>_tpo_sample.png
"""
from __future__ import annotations

import sys

from tpo.data import ROOT, load_bars, load_config
from tpo.plot import draw_days
from tpo.profile import daily_profiles
from tpo.rolls import back_adjust, roll_table


def load_adjusted(instrument: str, cfg: dict):
    tick = cfg["data"]["instruments"][instrument]["tick_size"]
    raw = load_bars(instrument, cfg)
    rolls = roll_table(raw, cfg, tick)
    bars = back_adjust(raw, rolls)
    daily, profiles = daily_profiles(bars, cfg, tick)
    return bars, daily, profiles, rolls, tick


def main(instrument: str = "ES") -> None:
    cfg = load_config(instrument=instrument)
    out = ROOT / "output"
    out.mkdir(exist_ok=True)
    bars, daily, _, rolls, tick = load_adjusted(instrument, cfg)
    daily.to_csv(out / f"{instrument}_daily_profiles.csv", index=False, float_format="%.2f")
    rolls.to_csv(out / f"{instrument}_rolls.csv", index=False, float_format="%.2f")

    full = daily[~daily["short_day"]]
    print(f"{instrument}: {len(daily)} дней ({daily.date.min():%Y-%m-%d} – "
          f"{daily.date.max():%Y-%m-%d}), полных {len(full)}, коротких {daily.short_day.sum()}")
    print(f"Средняя ширина VA: {(full.vah - full.val).mean():.2f} п., "
          f"средний диапазон: {(full.high - full.low).mean():.2f} п.")
    print(f"Ролловеров: {len(rolls)}, суммарный сдвиг самой старой истории: "
          f"{rolls.spread.sum():+.2f} п.")
    print(rolls.to_string(index=False, float_format=lambda x: f"{x:.2f}",
                          formatters={"roll_date": lambda x: f"{x:%Y-%m-%d}"}))

    sample = full.sort_values("date").tail(8)["date"].tolist()
    draw_days(bars, daily, sample, tick, out / f"{instrument}_tpo_sample.png", ncols=8)
    print("\nПоследние 20 полных дней (для сверки с платформой):")
    cols = ["date", "ib_high", "ib_low", "poc", "vah", "val", "high", "low"]
    print(full[cols].tail(20).to_string(index=False,
          formatters={"date": lambda x: f"{x:%Y-%m-%d}"}))


if __name__ == "__main__":
    main(*sys.argv[1:])


def load_test_bars(bars_instrument: str, since: str | None = None):
    """Back-adjusted 30m-бары другого набора (например, ES_ETH) — только для
    тестов зон; профили и зоны при этом строятся по основному инструменту.
    since — оставить бары с этой даты сессии (YYYY-MM-DD)."""
    import pandas as pd
    from tpo.data import load_config as _lc
    cfg = _lc(instrument=bars_instrument)
    tick = cfg["data"]["instruments"][bars_instrument]["tick_size"]
    raw = load_bars(bars_instrument, cfg)
    bars = back_adjust(raw, roll_table(raw, cfg, tick))
    if since:
        bars = bars[bars["date"] >= pd.Timestamp(since)].reset_index(drop=True)
    return bars, cfg["data"]["expected_periods_per_day"]


def align_zones(zones, bars):
    """Сдвигает available_from на первую сессию в bars не раньше него (зоны,
    ставшие доступны до начала bars, тестируются с начала bars); зоны,
    доступные после конца bars, выбрасываются."""
    import numpy as np
    days = np.sort(bars["date"].unique())
    z = zones.copy()
    idx = np.searchsorted(days, z["available_from"].to_numpy(dtype="datetime64[ns]"))
    keep = idx < len(days)
    z = z[keep].copy()
    z["available_from"] = days[idx[keep]]
    return z


def tag(instrument: str, bars_instrument: str | None, since: str | None) -> str:
    """Имя для файлов результатов: ES, ES_x_ES_ETH, ES_since2023-07-13 ..."""
    t = instrument
    if bars_instrument and bars_instrument != instrument:
        t += f"_x_{bars_instrument}"
    if since:
        t += f"_since{since}"
    return t
