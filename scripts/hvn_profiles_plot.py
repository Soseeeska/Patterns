"""TPO-профили дней с HVN [min_tpo, max_tpo] принтов в строке внутри VA.

По 4 случайных дня (seed фиксирован) на значение HVN: все значения, если их
не больше трёх (RTH: 8, 9, 10), иначе нижнее, среднее и верхнее.
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
    cfg = load_config(instrument=instrument)
    zc = cfg["hvn_zones"]
    bars, daily, profiles, _, tick = load_adjusted(instrument, cfg)
    days = per_day(daily, profiles, tick, cfg["intraday"]["hvn_share"])
    lo_v, hi_v = zc["min_tpo"], zc["max_tpo"]
    values = list(range(lo_v, hi_v + 1)) if hi_v - lo_v <= 2 else [lo_v, (lo_v + hi_v) // 2, hi_v]
    periods = cfg["data"]["expected_periods_per_day"]
    wcol = 3.4 if periods <= 13 else 4.6
    fig, axes = plt.subplots(len(values), PER_VALUE, figsize=(wcol * PER_VALUE, 6.2 * len(values)),
                             squeeze=False)
    by_date = daily.set_index("date")
    for row, v in zip(axes, values):
        sample = days[days.hvn_tpo == v].sample(PER_VALUE, random_state=int(seed)).sort_values("date")
        for ax, r in zip(row, sample.itertuples()):
            d = by_date.loc[r.date]
            ax.axhspan(r.band_lo - tick / 2, r.band_hi + tick / 2, color="#008300", alpha=0.15, zorder=0)
            ax.axvline(v, color="#008300", lw=0.8, ls="--", alpha=0.7)
            draw_day(ax, bars[bars.date == r.date], d, tick, periods=cfg["data"]["expected_periods_per_day"], title=
                     f"{r.date:%Y-%m-%d}  HVN {v} TPO\n"
                     f"зона {r.band_lo:.2f}–{r.band_hi:.2f} ({r.band_hi - r.band_lo:.2f} п.)\n"
                     f"VA {d.vah - d.val:.2f} п., диапазон {d.high - d.low:.2f} п.")
    fig.suptitle(f"{instrument}: профили с HVN {lo_v}–{hi_v} принтов внутри VA (по строкам: "
                 f"{', '.join(map(str, values))}), {periods} периодов в сессии\n"
                 "TPO по периодам: первый (тёмно-фиолетовый) → последний (жёлтый); зелёная полоса — HVN-зона "
                 "(строки ≥ 70% максимума), пунктир — уровень HVN; чёрная черта — IB, "
                 "красный треугольник — close. Цены back-adjusted.", fontsize=9)
    fig.tight_layout(rect=(0, 0, 1, 0.975))
    path = ROOT / "output" / f"{instrument}_hvn_profiles.png"
    fig.savefig(path, dpi=110)
    plt.close(fig)
    print(path)


if __name__ == "__main__":
    main(*sys.argv[1:])
