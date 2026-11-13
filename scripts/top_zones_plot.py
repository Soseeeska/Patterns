"""Самые тестируемые первородные зоны за последние N дней.

Берёт тесты из output/<INSTR>_zone_wicks_tests.csv (сначала scripts.zone_wicks),
считает тесты за последние N торговых дней и рисует top-K зон:
  1) обзор — 30m-бары (штрихи high–low) и зоны;
  2) по каждой зоне — 30m-свечи только тех дней, когда зона тестировалась
     (дни склеены подряд, между днями — тонкий разделитель); отмечены начала
     тестов: удержание / пробой, и хвосты через границу зоны.

Запуск:  python -m scripts.top_zones_plot [ES] [days] [top] [бары: ES_ETH]
Результат: output/<INSTR>_top_zones_overview.png, output/<INSTR>_top_zones_30m.png
"""
from __future__ import annotations

import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D

from scripts.stage1_profiles import load_adjusted, load_test_bars, tag
from tpo.data import ROOT, load_config
from tpo.plot import bars_on_days
from tpo.wicks import BOUNDARY, wick_positions

INK, MUTED, SURFACE, GRID = "#0b0b0b", "#52514e", "#fcfcfb", "#e4e3df"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#6250d6", "#008300", "#e34948"]
HOLD, BREAK = "#008300", "#e34948"


def _clean(ax):
    ax.set_facecolor(SURFACE)
    ax.grid(axis="y", color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#c9c8c2")
    ax.tick_params(labelsize=7, colors=MUTED)


def candles(ax, x, o, h, l, c, w=0.6):
    up = c >= o
    ax.vlines(x, l, h, color=MUTED, lw=0.6, zorder=2)
    ax.bar(x[up], (c - o)[up], bottom=o[up], width=w, color=SURFACE, edgecolor=MUTED, lw=0.6, zorder=3)
    ax.bar(x[~up], (o - c)[~up], bottom=c[~up], width=w, color="#3d3c39", edgecolor="#3d3c39", lw=0.6, zorder=3)


def main(instrument: str = "ES", days: str = "200", top: str = "6", bars_instrument: str | None = None) -> None:
    cfg = load_config(instrument=instrument)
    out = ROOT / "output"
    n_days, k = int(days), int(top)
    bars, _, _, _, tick = load_adjusted(instrument, cfg)
    periods = cfg["data"]["expected_periods_per_day"]
    if bars_instrument and bars_instrument != instrument:
        bars, periods = load_test_bars(bars_instrument)
    name = tag(instrument, bars_instrument, None)
    # «дни» — сессии тех баров, на которых шли тесты
    daily = (bars.groupby("date").agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                                      close=("close", "last")).reset_index())
    start = daily.date.iloc[-n_days]
    tests = pd.read_csv(out / f"{name}_zone_wicks_tests.csv", parse_dates=["date"])
    zones = pd.read_csv(out / f"{name}_zone_wicks_zones.csv", parse_dates=["available_from"]).set_index("zone_id")
    r = tests[tests.date >= start]
    g = r.groupby("zone_id").agg(n=("outcome", "size"), hold=("outcome", lambda s: (s == "hold").mean()),
                                 test_days=("date", "nunique"))
    sel = g.sort_values("n", ascending=False).head(k).join(zones[["lo", "hi", "available_from"]])
    sel = sel.sort_values("lo", ascending=False)
    mw = cfg["wicks"]["min_wick_ticks"] * tick

    # 1) обзор
    win = daily[daily.date >= start].reset_index(drop=True)
    pos = {d: i for i, d in enumerate(win.date)}
    fig, ax = plt.subplots(figsize=(14, 7.5))
    fig.patch.set_facecolor(SURFACE)
    bars_on_days(ax, bars, list(win.date), periods, color=MUTED, lw=0.35,
                 alpha=0.8)
    lo = min(win.low.min(), sel.lo.min()) - 20
    hi = max(win.high.max(), sel.hi.max()) + 20
    gap = (hi - lo) * 0.035
    ys, prev = [], None                       # подписи сверху вниз без наложений
    for _, z in sel.iterrows():
        y = (z.lo + z.hi) / 2
        y = y if prev is None else min(y, prev - gap)
        ys.append(y)
        prev = y
    for j, (zid, z) in enumerate(sel.iterrows()):
        col = SERIES[j]
        x0 = pos.get(z.available_from, 0) - 0.5 if z.available_from >= start else -0.5
        ax.add_patch(plt.Rectangle((x0, z.lo), len(win) - 0.5 - x0, z.hi - z.lo, color=col, alpha=0.25,
                                   lw=0, zorder=1))
        ax.hlines([z.lo, z.hi], x0, len(win) - 0.5, color=col, lw=0.8, zorder=1)
        ax.annotate(f"#{zid}  {z.lo:.2f}–{z.hi:.2f}  ·  {int(z.n)} тестов, удерж. {z.hold:.0%}",
                    (len(win) - 0.5, (z.lo + z.hi) / 2), xytext=(len(win) + 3, ys[j]), textcoords="data",
                    va="center", fontsize=7.5, color=INK, annotation_clip=False,
                    arrowprops=dict(arrowstyle="-", color=col, lw=0.8),
                    bbox=dict(boxstyle="square,pad=0.15", fc=SURFACE, ec=col, lw=1))
    ax.set_ylim(lo, hi)
    ax.set_xlim(-0.5, len(win) - 0.5)
    t = list(range(0, len(win), max(len(win) // 10, 1)))
    ax.set_xticks(t, [f"{win.date[i]:%d.%m.%y}" for i in t])
    _clean(ax)
    ax.set_title(f"{name}: {k} самых тестируемых первородных зон за последние {n_days} дней "
                 f"(тесты 30m, правило этапа 6)", fontsize=10, loc="left", color=INK)
    fig.text(0.01, 0.01, "Серые штрихи — 30m-бары (high–low). Зона закрашена с дня, когда стала доступна. "
             "Цены back-adjusted (ESZ6).",
             fontsize=7, color=MUTED)
    fig.tight_layout(rect=(0, 0.02, 0.82, 1))
    fig.savefig(out / f"{name}_top_zones_overview.png", dpi=150, facecolor=SURFACE)
    plt.close(fig)

    # 2) 30m по каждой зоне
    b = bars.sort_values("ts").reset_index(drop=True)
    ncols = 2
    nrows = int(np.ceil(len(sel) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(16, 4.3 * nrows), squeeze=False)
    fig.patch.set_facecolor(SURFACE)
    for ax, (j, (zid, z)) in zip(axes.flat, enumerate(sel.iterrows())):
        col = SERIES[j]
        zt = r[r.zone_id == zid]
        tdays = sorted(zt.date.unique())
        sub = b[b.date.isin(tdays)].reset_index(drop=True)
        x = np.arange(len(sub))
        o, h, l, c = (sub[q].to_numpy() for q in ("open", "high", "low", "close"))
        ax.axhspan(z.lo, z.hi, color=col, alpha=0.22, lw=0, zorder=0)
        candles(ax, x, o, h, l, c)
        lw_, uw_ = wick_positions(o, h, l, c, z.lo, z.hi, mw)
        mk = lw_ == BOUNDARY
        ax.scatter(x[mk], l[mk], marker="^", s=10, color=col, zorder=4, linewidths=0)
        mk = uw_ == BOUNDARY
        ax.scatter(x[mk], h[mk], marker="v", s=10, color=col, zorder=4, linewidths=0)
        idx = {(d, p): i for i, (d, p) in enumerate(zip(sub.date, sub.period))}
        for tt in zt.itertuples():
            i = idx[(tt.date, tt.period)]
            y = z.hi if tt.role == "support" else z.lo
            ax.plot(i, y, "o" if tt.outcome == "hold" else "X", ms=6,
                    color=HOLD if tt.outcome == "hold" else BREAK, mec=SURFACE, mew=0.8, zorder=5)
        day_start = np.flatnonzero(np.r_[True, sub.date.to_numpy()[1:] != sub.date.to_numpy()[:-1]])
        for s0 in day_start[1:]:
            ax.axvline(s0 - 0.5, color=GRID, lw=0.6, zorder=0)
        step = max(len(day_start) // 6, 1)
        ax.set_xticks(day_start[::step], [f"{sub.date[i]:%d.%m}" for i in day_start[::step]])
        pad = max(15, (z.hi - z.lo) * 2)
        ax.set_ylim(max(l.min(), z.lo - 4 * pad), min(h.max(), z.hi + 4 * pad))
        ax.set_xlim(-1, len(sub))
        _clean(ax)
        ax.set_title(f"#{zid}  {z.lo:.2f}–{z.hi:.2f} ({z.hi - z.lo:.2f} п.)  ·  {int(z.n)} тестов за "
                     f"{len(tdays)} дн., удерж. {z.hold:.0%}", fontsize=9, loc="left", color=INK)
    for ax in axes.flat[len(sel):]:
        ax.axis("off")
    handles = [Line2D([], [], marker="o", ls="", color=HOLD, label="тест: удержание"),
               Line2D([], [], marker="X", ls="", color=BREAK, label="тест: пробой (2 close за зоной)"),
               Line2D([], [], marker="^", ls="", color=MUTED, label="хвост через границу зоны")]
    fig.legend(handles=handles, loc="upper right", ncol=3, fontsize=8, frameon=False)
    fig.suptitle(f"30m-свечи только в дни тестов (дни склеены подряд), последние {n_days} дней",
                 x=0.01, ha="left", fontsize=11, color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.965))
    fig.savefig(out / f"{name}_top_zones_30m.png", dpi=140, facecolor=SURFACE)
    plt.close(fig)
    print(sel.to_string())


if __name__ == "__main__":
    main(*sys.argv[1:])
