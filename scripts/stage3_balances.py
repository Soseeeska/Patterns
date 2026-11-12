"""Этап 3: балансы и композиты.

Запуск:  python -m scripts.stage3_balances [ES]
Результат: output/<INSTR>_composites.csv, output/<INSTR>_balances_overview.png,
           output/<INSTR>_composites_sample.png
"""
from __future__ import annotations

import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Rectangle

from scripts.stage1_profiles import load_adjusted
from tpo.balance import find_balances
from tpo.data import ROOT, load_config


def draw_period(ax, daily, comps, start, end, label_ids=False):
    d = daily[(daily.date >= start) & (daily.date <= end) & ~daily.short_day].reset_index(drop=True)
    x = {dt: i for i, dt in enumerate(d.date)}
    ax.vlines(range(len(d)), d.low, d.high, color="0.55", lw=0.8)
    ax.vlines(range(len(d)), d.val, d.vah, color="0.15", lw=2.2)
    for c in comps[(comps.end >= start) & (comps.start <= end)].itertuples():
        x0 = x.get(c.start, 0) - 0.5
        x1 = x.get(c.end, len(d) - 1) + 0.5
        color = "tab:orange" if c.end_reason == "open" else "tab:blue"
        ax.add_patch(Rectangle((x0, c.val), x1 - x0, c.vah - c.val, color=color, alpha=0.25, lw=0))
        ax.add_patch(Rectangle((x0, c.low), x1 - x0, c.high - c.low, fill=False,
                               ec=color, lw=0.6, alpha=0.7))
        ax.hlines(c.poc, x0, x1, color="tab:red", lw=0.8)
        if label_ids:
            ax.text(x0, c.high, f"#{c.composite_id}", fontsize=5, va="bottom")
    ticks = list(range(0, len(d), max(len(d) // 12, 1)))
    ax.set_xticks(ticks)
    ax.set_xticklabels([d.date[i].strftime("%Y-%m-%d") for i in ticks], fontsize=6, rotation=30)
    ax.tick_params(axis="y", labelsize=6)
    ax.set_xlim(-1, len(d))


def main(instrument: str = "ES") -> None:
    cfg = load_config(instrument=instrument)
    out = ROOT / "output"
    out.mkdir(exist_ok=True)
    bars, daily, profiles, _, tick = load_adjusted(instrument, cfg)
    comps, _ = find_balances(bars, daily, profiles, cfg, tick)
    comps.to_csv(out / f"{instrument}_composites.csv", index=False, float_format="%.2f")

    full = daily[~daily.short_day]
    closed = comps[comps.end_reason != "open"]
    in_bal = closed.n_days.sum()
    print(f"{instrument}: балансов {len(closed)} (+{len(comps) - len(closed)} формируется), "
          f"дней внутри балансов {in_bal} из {len(full)} ({100 * in_bal / len(full):.1f}%)")
    print("\nДлительность (дней):")
    print(closed.n_days.describe().round(1).to_string())
    print("\nПричина закрытия:")
    print(closed.end_reason.value_counts().to_string())
    w = (closed.vah - closed.val)
    print(f"\nШирина VA композита: медиана {w.median():.2f} п., "
          f"High-Low: медиана {(closed.high - closed.low).median():.2f} п.")
    print("\nБалансов по годам:")
    print(closed.groupby(closed.start.dt.year).size().to_string())

    # Обзор по полугодиям
    halves = pd.date_range(full.date.min().to_period("Q").start_time, full.date.max(), freq="6MS")
    fig, axes = plt.subplots(len(halves), 1, figsize=(18, 3.2 * len(halves)))
    for ax, h in zip(axes, halves):
        draw_period(ax, daily, comps, h, h + pd.DateOffset(months=6) - pd.Timedelta(days=1))
    fig.suptitle(f"{instrument}: балансы. Серая линия — range дня, чёрная — VA дня; синий "
                 "прямоугольник — VA композита, контур — High/Low, красная — POC; "
                 "оранжевый — формируется", fontsize=10)
    fig.tight_layout()
    fig.savefig(out / f"{instrument}_balances_overview.png", dpi=110)
    plt.close(fig)

    # 10 случайных композитов крупно
    sample = closed.sample(min(10, len(closed)), random_state=11).sort_values("start")
    fig, axes = plt.subplots(2, 5, figsize=(20, 9))
    idx = full.date.reset_index(drop=True)
    for ax, c in zip(axes.flat, sample.itertuples()):
        i0 = max(idx[idx == c.start].index[0] - 4, 0)
        i1 = min(idx[idx == c.end].index[0] + 6, len(idx) - 1)
        draw_period(ax, daily, comps, idx[i0], idx[i1], label_ids=True)
        ax.set_title(f"#{c.composite_id}: {c.start:%Y-%m-%d} – {c.end:%Y-%m-%d}, {c.n_days} дн.\n"
                     f"VA {c.val:.2f}–{c.vah:.2f}, POC {c.poc:.2f}, конец: {c.end_reason}",
                     fontsize=8)
    fig.suptitle(f"{instrument}: 10 случайных композитов (4 дня до и 6 после)", fontsize=10)
    fig.tight_layout()
    fig.savefig(out / f"{instrument}_composites_sample.png", dpi=120)
    plt.close(fig)


if __name__ == "__main__":
    main(*sys.argv[1:])
