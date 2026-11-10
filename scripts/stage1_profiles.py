"""Этап 1: дневные TPO-профили + картинки для визуальной сверки.

Запуск:  python -m scripts.stage1_profiles [ES]
Результат: output/<INSTR>_daily_profiles.csv, output/<INSTR>_rolls.csv,
           output/<INSTR>_tpo_sample.png
"""
from __future__ import annotations

import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from tpo.data import ROOT, load_bars, load_config
from tpo.profile import daily_profiles, detect_rolls


def plot_tpo_days(bars, daily, dates, tick, path):
    fig, axes = plt.subplots(1, len(dates), figsize=(3.2 * len(dates), 9), squeeze=False)
    for ax, date in zip(axes[0], dates):
        g = bars[bars["date"] == date].sort_values("period")
        d = daily[daily["date"] == date].iloc[0]
        filled: dict[int, int] = {}
        xs, ys, cs = [], [], []
        for _, r in g.iterrows():
            for t in range(int(round(r.low / tick)), int(round(r.high / tick)) + 1):
                x = filled.get(t, 0)
                filled[t] = x + 1
                xs.append(x + 0.5)
                ys.append(t * tick)
                cs.append(int(r.period))
        cmap = plt.get_cmap("viridis", 13)
        ax.barh(ys, 1, left=np.array(xs) - 0.5, height=tick, color=cmap(cs),
                edgecolor="none")
        for lvl, color, name in [(d.vah, "tab:blue", "VAH"), (d.val, "tab:blue", "VAL"),
                                 (d.poc, "tab:red", "POC")]:
            ax.axhline(lvl, color=color, lw=0.8, alpha=0.7)
            ax.text(1, lvl, f" {name} {lvl:.2f}", color=color, fontsize=6,
                    va="bottom", ha="right", transform=ax.get_yaxis_transform())
        ax.plot([-0.15, -0.15], [d.ib_low, d.ib_high], color="k", lw=2)
        ax.set_ylim(d.low - 2 * tick, d.high + 2 * tick)
        ax.set_xlim(-0.2, 13)
        ax.set_xticks([])
        ax.set_title(pd.Timestamp(date).strftime("%Y-%m-%d"), fontsize=8)
        ax.tick_params(labelsize=6)
    fig.suptitle("TPO по периодам: A (тёмно-фиолетовый) → M (жёлтый); "
                 "чёрная черта слева — IB", fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main(instrument: str = "ES") -> None:
    cfg = load_config()
    tick = cfg["data"]["instruments"][instrument]["tick_size"]
    out = ROOT / "output"
    out.mkdir(exist_ok=True)

    bars = load_bars(instrument, cfg)
    daily, _ = daily_profiles(bars, cfg, tick)
    daily.to_csv(out / f"{instrument}_daily_profiles.csv", index=False, float_format="%.2f")

    rolls = detect_rolls(daily)
    rolls.to_csv(out / f"{instrument}_rolls.csv", float_format="%.2f")

    full = daily[~daily["short_day"]]
    print(f"{instrument}: {len(daily)} дней ({daily.date.min():%Y-%m-%d} – "
          f"{daily.date.max():%Y-%m-%d}), полных {len(full)}, коротких {daily.short_day.sum()}")
    print(f"Средняя ширина VA: {(full.vah - full.val).mean():.2f} п., "
          f"средний диапазон: {(full.high - full.low).mean():.2f} п.")
    print(f"Найдено ролловеров: {len(rolls)}, медианный гэп после: "
          f"{rolls.gap_to_next.median():.2f} п.")

    sample = full.sort_values("date").tail(8)["date"].tolist()
    plot_tpo_days(bars, daily, sample, tick, out / f"{instrument}_tpo_sample.png")
    print("\nПоследние 20 полных дней (для сверки с платформой):")
    cols = ["date", "ib_high", "ib_low", "poc", "vah", "val", "high", "low"]
    print(full[cols].tail(20).to_string(index=False,
          formatters={"date": lambda x: f"{x:%Y-%m-%d}"}))


if __name__ == "__main__":
    main(*sys.argv[1:])
