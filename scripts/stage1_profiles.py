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
    cfg = load_config()
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
