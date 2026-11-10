"""Этап 4: таблица касаний зон прошлых композитов.

Запуск:  python -m scripts.stage4_events [ES]
Результат: output/<INSTR>_events.csv
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from scripts.stage1_profiles import load_adjusted
from tpo.balance import find_balances
from tpo.data import ROOT, load_config
from tpo.day_type import classify_days
from tpo.events import build_events


def touch_bucket(n: int) -> str:
    return "1 (virgin)" if n == 1 else ("2-3" if n <= 3 else "4+")


def build_all(instrument: str, cfg: dict):
    bars, daily, profiles, rolls, tick = load_adjusted(instrument, cfg)
    days = classify_days(daily, profiles, cfg, tick)
    comps, _ = find_balances(bars, daily, profiles, cfg, tick)
    ev = build_events(bars, days, comps, rolls, cfg, tick)
    ev["touch_bucket"] = ev["touch_number"].map(touch_bucket)
    return bars, days, comps, ev, tick


def main(instrument: str = "ES") -> None:
    cfg = load_config()
    out = ROOT / "output"
    out.mkdir(exist_ok=True)
    _, days, comps, ev, _ = build_all(instrument, cfg)
    ev.to_csv(out / f"{instrument}_events.csv", index=False, float_format="%.3f")

    full = days[days.day_type != "Short"]
    print(f"{instrument}: событий {len(ev)}, зон {ev.zone_id.nunique()} из "
          f"{3 * ((comps.end_reason != 'open') & (comps.n_days >= cfg['zones'].get('min_composite_days', 0))).sum()}, дней с касанием {ev.date.nunique()} "
          f"из {len(full)} ({100 * ev.date.nunique() / len(full):.0f}%)")
    print(f"Проверка look-ahead: касаний раньше available_from — "
          f"{int((ev.date < ev.merge(comps, on='composite_id').available_from).sum())}")
    print(f"Касаний в день пробоя (breakout_day, исключаются из статистики): "
          f"{int(ev.breakout_day.sum())}")
    if cfg["interaction"]["exclude_breakout_day"]:
        ev = ev[~ev.breakout_day]
    print("\nСобытий по номеру касания:")
    print(ev.touch_bucket.value_counts().sort_index().to_string())
    print(f"\nrolls_crossed = 0: {(ev.rolls_crossed == 0).sum()}, > 0: {(ev.rolls_crossed > 0).sum()}")

    def summary(g):
        return pd.Series({
            "n": len(g),
            "принятие %": 100 * (g.outcome == "acceptance").mean(),
            "до противоп. d0 %": 100 * g.reached_opposite_d0.mean(),
            "d3 %": 100 * g.reached_opposite_d3.mean(),
            "MFE мед., тик": g.mfe_ticks.median(),
            "MAE мед., тик": g.mae_ticks.median(),
        })

    pd.set_option("display.width", 200)
    print("\nПредварительно (без базовой линии, выводы — на этапе 5):")
    print(ev.groupby(["level_type", "touch_bucket"]).apply(summary, include_groups=False)
          .round(1).to_string())


if __name__ == "__main__":
    main(*sys.argv[1:])
