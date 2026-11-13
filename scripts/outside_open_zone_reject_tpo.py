"""Примеры сценария «open вне диапазона → удар о зону проторговки → цели прошлого дня»
RTH TPO-принтами (A–M). Слева прошлый день, справа день открытия; синяя полоса —
зона композита, буквы периода удара — красные.

Запуск:  python -m scripts.outside_open_zone_reject_tpo [ES] [YYYY-MM-DD ...]
         (сначала python -m scripts.outside_open_zone_reject ES)
Результат: output/<INSTR>_outside_open_zone_tpo_<N>.png
"""
from __future__ import annotations

import math
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from scripts.prev_day_rejections_plot import LV, ZONE
from scripts.prev_day_rejections_tpo import LETTERS, draw_prints
from scripts.stage1_profiles import load_adjusted
from scripts.top_zones_plot import INK, MUTED, SURFACE
from tpo.balance import find_balances
from tpo.data import ROOT, load_config
from tpo.intraday import build_bands

DEFAULT = ["2025-08-07", "2026-04-02", "2026-09-21", "2026-10-30"]
SRC = "композиты баланса"


def main(instrument: str = "ES", *dates: str) -> None:
    dates = list(dates) or DEFAULT
    cfg = load_config(instrument=instrument)
    out = ROOT / "output"
    bars, daily, profiles, _, tick = load_adjusted(instrument, cfg)
    comps, comp_prof = find_balances(bars, daily, profiles, cfg, tick)
    comps = comps[comps.n_days >= cfg["zones"].get("min_composite_days", 0)]
    bands = build_bands(comps, comp_prof, cfg, tick)
    tol = cfg["interaction"]["touch_tolerance_ticks"] * tick
    a = pd.read_csv(out / f"{instrument}_outside_open_zone_reject.csv", parse_dates=["date"])
    a = a[a.source == SRC]
    d = daily[~daily.short_day].sort_values("date").reset_index(drop=True)
    for n, ds in enumerate(dates, 1):
        r = a[a.date == pd.Timestamp(ds)].iloc[0]
        z = bands.iloc[int(r.zone_idx)]
        k = int(d.index[d.date == r.date][0])
        p, cur = d.iloc[k - 1], d.iloc[k]
        gp, gc = bars[bars.date == p.date], bars[bars.date == cur.date]
        lo = min(p.low, cur.low, z.lo) - 3
        hi = max(p.high, cur.high, z.hi) + 3
        R = max(1.0, math.ceil((hi - lo) / 60 / 0.5) * 0.5)
        fig, ax = plt.subplots(figsize=(13, max(7.0, (hi - lo) / R * 0.19)))
        fig.patch.set_facecolor(SURFACE)
        w1 = draw_prints(ax, gp, 0, R)
        x2 = w1 + 4
        w2 = draw_prints(ax, gc, x2, R, int(r.hit_period))
        right = x2 + w2 + 1
        ax.axhspan(z.lo - tol, z.hi + tol, color=ZONE, alpha=0.22, lw=0, zorder=1)
        ax.hlines([z.lo, z.hi], -1, right, color=ZONE, lw=0.8, zorder=1)
        ax.hlines(cur.open, x2 - 1, right, color=INK, lw=0.8, linestyles=":", zorder=2)
        ax.text(x2 - 1.2, cur.open, f"open {cur.open:.2f}", ha="right", va="center", fontsize=8.5, color=INK)
        levels = (("High", p.high), ("VAH", p.vah), ("POC", p.poc), ("VAL", p.val), ("Low", p.low))
        labels = {}
        for name, val in levels:
            ax.hlines(val, -1, right, color=LV[name], lw=0.9, linestyles="--", zorder=2)
            labels.setdefault(round(val, 2), []).append(name)
        prev = None
        for val in sorted(labels, reverse=True):
            y = val if prev is None else min(val, prev - 1.6 * R)
            prev = y
            ax.annotate(f"{' / '.join(labels[val])} {val:.2f}", (right, val), xytext=(right + 0.6, y),
                        fontsize=9, color=LV[labels[val][0]], va="center", annotation_clip=False,
                        arrowprops=dict(arrowstyle="-", color=LV[labels[val][0]], lw=0.6))
        ax.text(w1 / 2, hi + R * 0.3, f"{p.date:%d.%m} — прошлый день", ha="center", fontsize=10, color=MUTED)
        ax.text(x2 + w2 / 2, hi + R * 0.3, f"{cur.date:%d.%m} — открытие вне диапазона", ha="center",
                fontsize=10, color=INK)
        ax.set_xlim(-1.5, right + 10)
        ax.set_ylim(lo, hi + R * 2)
        ax.set_xticks([])
        ax.set_facecolor(SURFACE)
        ax.grid(axis="y", color="#ecebe7", lw=0.5)
        ax.tick_params(axis="y", labelsize=8.5, colors=MUTED)
        for s in ("top", "right", "bottom"):
            ax.spines[s].set_visible(False)
        ax.spines["left"].set_color("#c9c8c2")
        where = "выше High" if r.side == "шорт" else "ниже Low"
        got = [nm for col, nm in (("range_hit", "граница диапазона"), ("va_near_hit", "VAH" if r.side == "шорт" else "VAL"),
                                  ("poc_hit", "POC"), ("va_far_hit", "VAL" if r.side == "шорт" else "VAH")) if r[col]]
        res = ("дошла: " + ", ".join(got)) if got else "до диапазона прошлого дня не вернулась"
        ztype = {"HVN": "HVN-полоса", "POC": "POC", "VAH": "VAH", "VAL": "VAL"}[z.level_type]
        ax.set_title(f"{cur.date:%d.%m.%Y} ({r.side}): open {where} прошлого дня, удар о зону в периоде "
                     f"{LETTERS[int(r.hit_period)]}{' — зона пробита (2 close за ней)' if r.broke else ''}\n"
                     f"Зона композита #{z.composite_id}: {ztype} {z.lo:.2f}–{z.hi:.2f} — синяя полоса  ·  {res}  ·  "
                     f"строка {R:g} п.", fontsize=10.5, loc="left", color=INK)
        fig.text(0.01, 0.005, "RTH TPO: A = 08:30 … M = 14:30 CT. Красные буквы — период удара о зону. "
                 "Пунктир — open дня. Цены back-adjusted (ESZ6).", fontsize=8, color=MUTED)
        fig.tight_layout(rect=(0, 0.015, 1, 1))
        path = out / f"{instrument}_outside_open_zone_tpo_{n}.png"
        fig.savefig(path, dpi=130, facecolor=SURFACE)
        plt.close(fig)
        print(path)


if __name__ == "__main__":
    main(*sys.argv[1:])
