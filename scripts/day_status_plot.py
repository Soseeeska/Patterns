"""Контекст дня на графике: свечи вчера и сегодня, уровни профиля, голые POC, зоны 1.8 и лента статуса по периодам.

Сверху — RTH 30m вчера и сегодня (цены графика), вчерашние VAH / VAL (пунктир), POC (оранжевый), High / Low (серые),
голые POC (фиолетовый пунктир), зоны 1.8 на сегодня (синие ярко, красные / зелёные бледно, серые не рисуются),
развивающийся POC дня (оранжевые точки), TPO-профили обоих дней справа (столбики по 1 п.).
Снизу — статус после каждого периода: вероятность цели до конца RTH (модель — scripts.day_status, учится на днях до
2025). Под свечой периода — его столбец. Зелёная рамка — цель потом взята, · — уже взята к этому моменту, пусто —
уровня нет; у целей, которые меняются по ходу дня (синие, nPOC), в ячейке — цена цели.

Запуск:  python -m scripts.day_status_plot 2026-11-05 [ещё даты ...]     Результат: output/z18/img/status_<дата>.png
"""
from __future__ import annotations

import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.patches import Rectangle

from scripts.day_status import TARGETS, status_probs
from scripts.zones18_mp import OUT
from tpo.data import load_bars, load_config
from tpo.day_status import LETTERS, naked_pocs
from tpo.profile import build_profile, poc_index
from tpo.zones18 import day_table

ZC = {"CORE": ("#00158B", 0.32), "HVN_R": ("#8B0000", 0.13), "HVN_S": ("#006400", 0.13)}
CMAP = LinearSegmentedColormap.from_list("st", ["#f7f7f7", "#c6dbef", "#4292c6", "#08306b"])
ROW_PX = {"y_poc": "y_poc", "y_vah": "y_vah", "y_val": "y_val", "y_high": "y_high", "y_low": "y_low"}


def candles(ax, g, x0):
    for i, r in enumerate(g.itertuples()):
        col = "#1a7f37" if r.close >= r.open else "#c62828"
        ax.vlines(x0 + i, r.low, r.high, color=col, lw=1)
        ax.add_patch(Rectangle((x0 + i - 0.33, min(r.open, r.close)), 0.66, max(abs(r.close - r.open), 0.25), color=col, lw=0))


def tpo_side(ax, g, x0, width, tick, color):
    lo_t = np.rint(g.low.to_numpy() / tick).astype(int)
    hi_t = np.rint(g.high.to_numpy() / tick).astype(int)
    pf = build_profile(lo_t, hi_t, 4)                     # строки по 1 п.
    mx = pf.counts.max()
    for i, c in enumerate(pf.counts):
        y = pf.price_of(i) * tick
        ax.add_patch(Rectangle((x0, y), width * c / mx, 1.0, color=color, alpha=0.35, lw=0))


def plot_day(D, real, split, bars, days, snaps, npocs, tick):
    D = pd.Timestamp(D)
    st = status_probs(real, D, split)
    if st.empty:
        print("нет дня", D)
        return None
    i = days.index[days.date == D][0]
    day, yday = days.loc[i], days.loc[i - 1]
    g = bars[bars.date == D].sort_values("period").reset_index(drop=True)
    gy = bars[bars.date == yday.date].sort_values("period").reset_index(drop=True)
    X0 = len(gy) + 1
    fig = plt.figure(figsize=(15, 10.5))
    gs = fig.add_gridspec(2, 1, height_ratios=[3.1, 2.0], hspace=0.06)
    ax = fig.add_subplot(gs[0])
    hx = fig.add_subplot(gs[1], sharex=ax)
    lo = min(g.low.min(), gy.low.min())
    hi = max(g.high.max(), gy.high.max())
    pad = (hi - lo) * 0.06
    ylo, yhi = lo - pad, hi + pad
    xr = X0 + len(g) + 7
    # зоны 1.8
    z = snaps[(snaps.day == D) & (snaps.kind != "GREY") & (snaps.hi >= ylo) & (snaps.lo <= yhi)]
    for r in z.itertuples():
        c, a = ZC[r.kind]
        ax.add_patch(Rectangle((-0.5, r.lo), xr + 0.5, r.hi - r.lo, color=c, alpha=a, lw=0, zorder=0))
    # уровни вчера и голые POC
    lv = [(day.y_vah, "VAH вчера", "#333333", "--"), (day.y_val, "VAL вчера", "#333333", "--"),
          (day.y_poc, "POC вчера", "#ef6c00", "-"), (day.y_high, "High вчера", "#888888", ":"),
          (day.y_low, "Low вчера", "#888888", ":")]
    lv += [(p, "nPOC", "#8e24aa", "-.") for p in npocs.get(D, []) if ylo <= p <= yhi]
    lv = sorted([v for v in lv if ylo <= v[0] <= yhi], key=lambda v: v[0])
    gap = (yhi - ylo) * 0.028                              # подписи не налезают: раздвигаются вверх
    ly = -1e9
    for y, name, col, ls in lv:
        ax.hlines(y, -0.5, xr, color=col, ls=ls, lw=1.3 if "POC" in name else 1.0, zorder=2)
        ly = max(y, ly + gap)
        ax.text(xr - 0.2, ly, f"{name} {y:.2f}", fontsize=7.5, color=col, va="center", ha="right", zorder=3,
                bbox=dict(fc="white", ec="none", alpha=0.75, pad=0.6))
    candles(ax, gy, 0)
    candles(ax, g, X0)
    # развивающийся POC дня
    lo_t = np.rint(g.low.to_numpy() / tick).astype(int)
    hi_t = np.rint(g.high.to_numpy() / tick).astype(int)
    for k in range(len(g)):
        pf = build_profile(lo_t[:k + 1], hi_t[:k + 1], 1)
        ax.plot(X0 + k, (pf.low + poc_index(pf)) * tick, "o", ms=3.2, color="#ef6c00", zorder=4)
    tpo_side(ax, gy, X0 + len(g) + 0.3, 2.8, tick, "#777777")
    tpo_side(ax, g, X0 + len(g) + 3.4, 2.8, tick, "#1f5fbf")
    ax.axvspan(X0 - 0.5, X0 + len(g) - 0.5, color="#fff59d", alpha=0.12, lw=0, zorder=0)
    ax.set_ylim(ylo, yhi)
    ax.set_xlim(-0.8, xr)
    ax.grid(axis="y", lw=0.3, alpha=0.5)
    ax.tick_params(labelbottom=False, labelsize=8)
    ax.set_title(f"{D:%d.%m.%Y}  ·  вчера {yday.date:%d.%m}  ·  open {day.open:.2f} → close {day.close:.2f}  ·  "
                 f"открытие: {st.open_loc.iloc[0]}, A {st.a_accept.iloc[0]}", fontsize=10.5)
    ax.text(X0 + len(g) + 0.3, yhi, "TPO вчера", fontsize=7, va="top", color="#555")
    ax.text(X0 + len(g) + 3.4, yhi, "сегодня", fontsize=7, va="top", color="#1f5fbf")
    # лента статуса
    rows = list(TARGETS.items())
    for j, (tg, (name, dist, pxc)) in enumerate(rows):
        yrow = len(rows) - 1 - j
        lvl = ""
        if pxc:
            px = st[pxc].dropna()
            lvl = f" {px.iloc[-1]:.2f}" if len(px) and px.nunique() == 1 else ""
        hx.text(X0 - 0.7, yrow + 0.5, name + lvl, ha="right", va="center", fontsize=8)
        for _, r in st.iterrows():
            x = X0 + r.k
            v = r[tg]
            if pd.isna(v):
                taken = (tg in ROW_PX) or tg.startswith("ext")
                hx.add_patch(Rectangle((x - 0.47, yrow + 0.04), 0.94, 0.92, color="#eeeeee", lw=0))
                if taken:
                    hx.text(x, yrow + 0.5, "·", ha="center", va="center", fontsize=10, color="#999")
                continue
            p = r["p_" + tg]
            fc = "white" if p > 0.55 else "#222"
            hx.add_patch(Rectangle((x - 0.47, yrow + 0.04), 0.94, 0.92, color=CMAP(p), lw=0))
            moving = pxc and not lvl and not pd.isna(r[pxc])
            hx.text(x, yrow + (0.62 if moving else 0.5), f"{p:.0%}", ha="center", va="center", fontsize=7.6,
                    color=fc, fontweight="bold")
            if moving:                                     # цель меняется по ходу дня — её цена в ячейке
                hx.text(x, yrow + 0.25, f"{r[pxc]:.0f}", ha="center", va="center", fontsize=6, color=fc)
            if v == 1:                                     # цель потом взята — зелёная рамка
                hx.add_patch(Rectangle((x - 0.44, yrow + 0.07), 0.88, 0.86, fill=False, ec="#2e9d3a", lw=2.2))
    hx.set_ylim(0, len(rows))
    hx.set_yticks([])
    hx.set_xticks([X0 + k for k in range(len(g))], [f"{LETTERS[k]}" for k in range(len(g))], fontsize=8.5)
    for s in hx.spines.values():
        s.set_visible(False)
    hx.text(X0 + len(g) - 0.4, -0.75, "статус после периода: вероятность взять до конца RTH · зелёная рамка — потом взяли · «·» — уже взята",
            fontsize=7.5, ha="right", color="#555")
    ax.set_xticks([])
    for k in range(len(gy)):
        ax.text(k, ylo, LETTERS[k], fontsize=6.5, ha="center", va="bottom", color="#999")
    out = OUT / "img" / f"status_{D:%Y-%m-%d}.png"
    out.parent.mkdir(exist_ok=True)
    fig.savefig(out, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return out


def main(*dates):
    cfg = load_config(instrument="ES")
    tick = cfg["data"]["instruments"]["ES"]["tick_size"]
    split = pd.Timestamp(cfg["day_context"]["split_date"])
    bars = load_bars("ES", cfg)
    days = day_table(bars, cfg, tick)
    real = pd.read_csv(OUT / "day_status.csv", parse_dates=["date"])
    snaps = pd.concat([pd.read_csv(f, parse_dates=["day"]) for f in sorted(OUT.glob("snap_*.csv"))])
    npocs = naked_pocs(days)
    for D in dates or ("2026-11-05",):
        print(plot_day(D, real, split, bars, days, snaps, npocs, tick))


if __name__ == "__main__":
    main(*sys.argv[1:])
