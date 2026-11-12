"""TPO-профили дней с HVN 8–10 принтов в строке внутри VA.

По 4 случайных дня (seed фиксирован) на каждое значение HVN: 8, 9, 10.
Зелёная полоса — HVN-зона дня (строки вокруг POC с TPO ≥ 70% максимума).

Запуск:  python -m scripts.hvn_profiles_plot [ES] [seed]
Результат: output/<INSTR>_hvn_profiles.png
"""
from __future__ import annotations

import sys

import matplotlib.pyplot as plt

from scripts.hvn_va_stats import per_day
from scripts.stage1_profiles import load_adjusted
from tpo.data import ROOT, load_config
from tpo.plot import draw_day

PER_VALUE = 4


def main(instrument: str = "ES", seed: str = "7") -> None:
    cfg = load_config()
    zc = cfg["hvn_zones"]
    bars, daily, profiles, _, tick = load_adjusted(instrument, cfg)
    days = per_day(daily, profiles, tick, cfg["intraday"]["hvn_share"])
    values = range(zc["min_tpo"], zc["max_tpo"] + 1)
    fig, axes = plt.subplots(len(values), PER_VALUE, figsize=(3.4 * PER_VALUE, 6.2 * len(values)),
                             squeeze=False)
    by_date = daily.set_index("date")
    for row, v in zip(axes, values):
        sample = days[days.hvn_tpo == v].sample(PER_VALUE, random_state=int(seed)).sort_values("date")
        for ax, r in zip(row, sample.itertuples()):
            d = by_date.loc[r.date]
            ax.axhspan(r.band_lo - tick / 2, r.band_hi + tick / 2, color="#008300", alpha=0.15, zorder=0)
            ax.axvline(v, color="#008300", lw=0.8, ls="--", alpha=0.7)
            draw_day(ax, bars[bars.date == r.date], d, tick,
                     f"{r.date:%Y-%m-%d}  HVN {v} TPO\n"
                     f"зона {r.band_lo:.2f}–{r.band_hi:.2f} ({r.band_hi - r.band_lo:.2f} п.)\n"
                     f"VA {d.vah - d.val:.2f} п., диапазон {d.high - d.low:.2f} п.")
    fig.suptitle("Профили с HVN 8–10 принтов внутри VA (по строкам: 8, 9, 10)\n"
                 "TPO по периодам: A (тёмно-фиолетовый) → M (жёлтый); зелёная полоса — HVN-зона "
                 "(строки ≥ 70% максимума), пунктир — уровень HVN; чёрная черта — IB, "
                 "красный треугольник — close. Цены back-adjusted.", fontsize=9)
    fig.tight_layout(rect=(0, 0, 1, 0.975))
    path = ROOT / "output" / f"{instrument}_hvn_profiles.png"
    fig.savefig(path, dpi=110)
    plt.close(fig)
    print(path)


if __name__ == "__main__":
    main(*sys.argv[1:])
