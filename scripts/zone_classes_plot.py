"""Зоны возле текущей цены, раскрашенные по классу (правило «по отторжениям»).

Слева — дневные свечи за последние N дней и зоны в ± R п. от последнего
close (полоса с дня доступности зоны). Справа — из чего сложился класс:
доли лонг-отторжений, шорт-отторжений и проторговки по каждой зоне.

Запуск:  python -m scripts.zone_classes_plot [ES] [days] [range_points]
         (сначала python -m scripts.zone_classes)
Результат: output/<INSTR>_zone_classes.png
"""
from __future__ import annotations

import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Patch

from scripts.stage1_profiles import load_adjusted
from scripts.top_zones_plot import INK, MUTED, SURFACE, _clean, candles
from tpo.data import ROOT, load_config
from tpo.zone_classes import RANGE, RESISTANCE, SUPPORT

COLOR = {SUPPORT: "#008300", RESISTANCE: "#e34948", RANGE: "#8a8984"}


def main(instrument: str = "ES", days: str = "200", rng_pts: str = "150") -> None:
    cfg = load_config()
    out = ROOT / "output"
    n_days, R = int(days), float(rng_pts)
    _, daily, _, _, _ = load_adjusted(instrument, cfg)
    daily = daily.sort_values("date").reset_index(drop=True)
    z = pd.read_csv(out / f"{instrument}_zone_classes_by_rejections.csv", parse_dates=["available_from"])
    last = daily.close.iloc[-1]
    z = z[(z.hi >= last - R) & (z.lo <= last + R)].sort_values("lo").reset_index(drop=True)
    win = daily.iloc[-n_days:].reset_index(drop=True)
    pos = {d: i for i, d in enumerate(win.date)}
    share = cfg["wicks"]["class_rejection_share"]

    fig, (ax, bx) = plt.subplots(1, 2, figsize=(16, 9), gridspec_kw={"width_ratios": [3, 1.25], "wspace": 0.28})
    fig.patch.set_facecolor(SURFACE)
    n = len(win)
    for _, r in z.iterrows():
        col = COLOR[r["class"]]
        x0 = pos[r.available_from] - 0.5 if r.available_from in pos else -0.5
        ax.add_patch(plt.Rectangle((x0, r.lo), n - 0.5 - x0, r.hi - r.lo, color=col,
                                   alpha=0.30 if r["class"] != RANGE else 0.18, lw=0, zorder=1))
        ax.text(n + 0.5, (r.lo + r.hi) / 2, f"#{r.zone_id}", fontsize=6.5, color=col, va="center")
    candles(ax, np.arange(n), win.open.to_numpy(), win.high.to_numpy(), win.low.to_numpy(),
            win.close.to_numpy(), w=0.7)
    ax.axhline(last, color=INK, lw=0.7, ls="--", zorder=4)
    ax.text(-0.5, last, f" close {last:.2f}", fontsize=7, color=INK, va="bottom")
    ax.set_ylim(last - R - 25, last + R + 25)
    ax.set_xlim(-0.5, n + 4)
    t = list(range(0, n, max(n // 10, 1)))
    ax.set_xticks(t, [f"{win.date[i]:%d.%m.%y}" for i in t])
    _clean(ax)
    ax.set_title(f"Зоны в ±{R:g} п. от close, последние {n_days} дней", fontsize=10, loc="left", color=INK)

    # справа: состав взаимодействий
    y = np.arange(len(z))
    tot = (z.n_long + z.n_short + z.n_range).to_numpy()
    parts = [("лонг-отторжение", z.n_long / tot, COLOR[SUPPORT]),
             ("шорт-отторжение", z.n_short / tot, COLOR[RESISTANCE]),
             ("проторговка (close внутри / насквозь)", z.n_range / tot, "#c9c8c2")]
    left = np.zeros(len(z))
    for name, v, col in parts:
        bx.barh(y, v, left=left, color=col, height=0.72, edgecolor=SURFACE, lw=1, label=name)
        left += v.to_numpy()
    for i, r in z.iterrows():
        rej = r.n_long + r.n_short
        sh = f"{max(r.n_long, r.n_short) / rej:.0%}" if rej else "—"
        bx.text(1.02, i, f"{r['class']}  ({sh}, n={int(tot[i])})", va="center", fontsize=7,
                color=COLOR[r["class"]] if r["class"] != RANGE else MUTED, transform=bx.get_yaxis_transform())
    bx.set_yticks(y, [f"#{r.zone_id}  {r.lo:.2f}–{r.hi:.2f}" for r in z.itertuples()], fontsize=7)
    bx.set_xlim(0, 1)
    bx.set_xticks([0, 0.5, 1], ["0", "50%", "100%"])
    _clean(bx)
    bx.grid(False)
    bx.set_title("из чего сложился класс", fontsize=9, loc="left", color=MUTED)
    bx.legend(loc="upper center", bbox_to_anchor=(0.5, -0.04), fontsize=7, frameon=False, ncol=1)

    handles = [Patch(color=COLOR[c], alpha=0.5, label=c) for c in (SUPPORT, RESISTANCE, RANGE)]
    fig.legend(handles=handles, loc="upper right", ncol=3, fontsize=8, frameon=False)
    fig.suptitle(f"{instrument}: классы первородных зон по 30m-взаимодействиям "
                 f"(≥ {share:.0%} отторжений в одну сторону — поддержка / сопротивление, иначе проторговка)",
                 x=0.01, ha="left", fontsize=11, color=INK)
    fig.text(0.01, 0.01, "Класс посчитан по всей жизни зоны, включая последние дни. Цены back-adjusted (ESZ6).",
             fontsize=7, color=MUTED)
    fig.subplots_adjust(left=0.05, right=0.9, top=0.9, bottom=0.12)
    fig.savefig(out / f"{instrument}_zone_classes.png", dpi=140, facecolor=SURFACE)
    plt.close(fig)
    print(out / f"{instrument}_zone_classes.png")


if __name__ == "__main__":
    main(*sys.argv[1:])
