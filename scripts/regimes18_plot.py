"""Картинки режимов: по два последних дня каждого режима (`python -m scripts.regimes18 chart` → output/z18/regimes_chart.csv).

На картинке — RTH 30m-бары 20 сессий до дня и сам день (выделен), в ценах графика (ESZ6 без склейки):
балансы, известные к открытию дня (рамка — диапазон композита, заливка — ядро; синяя рамка — формирующийся,
оранжевая толстая — «свой» баланс режима, фиолетовая — цель по направлению),
зоны 1.8 на этот день (цвет индикатора, серые бледнее), пунктир — VAH / VAL вчера, оранжевый — POC вчера.

Запуск:  python -m scripts.regimes18_plot [дней_на_режим=2]     Результат: output/z18/img/regime_*.png
"""
from __future__ import annotations

import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Rectangle

from scripts.zones18_mp import OUT
from tpo.data import load_bars, load_config
from tpo.regimes import REGIMES
from tpo.zones18 import day_table

COLORS = {"CORE": "#00158B", "HVN_R": "#8B0000", "HVN_S": "#006400", "GREY": "#808080"}


def panel(ax, D, reg, bars, days, snaps, comps, n_back=20):
    dl = list(days.date[(days.date <= D)].tail(n_back + 1))
    b = bars[bars.date.isin(dl)].sort_values("ts").reset_index(drop=True)
    x = range(len(b))
    first = {d: i for i, d in reversed(list(enumerate(b.date)))}
    lo, hi = b.low.min(), b.high.max()
    pad = (hi - lo) * 0.08
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_xlim(-1, len(b) + 1)
    # зоны дня
    z = snaps[(snaps.day == D) & (snaps.hi >= lo - pad) & (snaps.lo <= hi + pad)]
    for r in z.itertuples():
        ax.axhspan(r.lo, r.hi, color=COLORS[r.kind], alpha=0.12 if r.kind == "GREY" else 0.3, lw=0)
    # балансы дня (цены графика: без shift)
    c = comps[comps.day == D]
    for r in c.itertuples():
        if r.hi < lo - pad or r.lo > hi + pad:
            continue
        home = abs(r.lo - reg.home_lo) < 1e-6 and abs(r.hi - reg.home_hi) < 1e-6
        tgt = abs(r.lo - reg.tgt_lo) < 1e-6 and abs(r.hi - reg.tgt_hi) < 1e-6
        x0 = first.get(r.start, -1) if r.start >= dl[0] else -1
        e = [i for i, d in enumerate(b.date) if d == r.end]
        x1 = e[-1] + 1 if e else len(b)
        col = "#ef6c00" if home else "#8e24aa" if tgt else "#1f5fbf" if r.type == "live" else "#666666"
        ax.add_patch(Rectangle((x0, r.lo), x1 - x0, r.hi - r.lo, fill=False, ec=col, lw=2.2 if home or tgt else 0.9,
                               ls="-" if r.type == "live" or home or tgt else "--"))
        ax.add_patch(Rectangle((x0, r.core_lo), x1 - x0, max(r.core_hi - r.core_lo, 0.5), color="#5a7bd8", alpha=0.35, lw=0))
        ax.text(x0 + 0.5, r.hi, f"{r.start:%d.%m}–{r.end:%d.%m}{' форм.' if r.type == 'live' else ''}"
                + (" · свой баланс" if home else " · цель" if tgt else ""), fontsize=7 if home or tgt else 6.5,
                va="bottom", color=col, fontweight="bold" if home or tgt else "normal")
    # бары
    for i, r in b.iterrows():
        col = "#1a7f37" if r.close >= r.open else "#c62828"
        ax.vlines(i, r.low, r.high, color=col, lw=0.8)
        ax.add_patch(Rectangle((i - 0.35, min(r.open, r.close)), 0.7, max(abs(r.close - r.open), 0.25), color=col, lw=0))
    d0 = first[D]
    ax.axvspan(d0 - 0.5, len(b) - 0.5, color="#ffd54f", alpha=0.15, lw=0)
    y = days[days.date == D].iloc[0]
    for v, ls, col in ((y.y_vah, "--", "#333"), (y.y_val, "--", "#333"), (y.y_poc, "-", "#ef6c00")):
        ax.hlines(v, d0 - 0.5, len(b) - 0.5, color=col, ls=ls, lw=1)
    ticks = [first[d] for d in dl[::4]] + [d0]
    ax.set_xticks(ticks, [f"{d:%d.%m}" for d in dl[::4]] + [f"{D:%d.%m}"], fontsize=7)
    ax.tick_params(axis="y", labelsize=7)
    ax.grid(axis="y", lw=0.3, alpha=0.5)
    direction = "" if reg.dir == "—" else f" ({reg.dir})"
    ax.set_title(f"{D:%d.%m.%Y}: {reg.regime}{direction}\n"
                 f"open {y.open:.2f} → close {y.close:.2f}; POC вчера {'взят' if reg.y_poc else 'не взят'}"
                 + ("" if pd.isna(reg.home_core) else f", ядро своего баланса {'взято' if reg.home_core else 'не взято'}"),
                 fontsize=8.5)


def main(per=2):
    cfg = load_config(instrument="ES")
    tick = cfg["data"]["instruments"]["ES"]["tick_size"]
    bars = load_bars("ES", cfg)
    days = day_table(bars, cfg, tick)
    reg = pd.read_csv(OUT / "regimes_chart.csv", parse_dates=["date"])
    snaps = pd.concat([pd.read_csv(f, parse_dates=["day"]) for f in sorted(OUT.glob("snap_*.csv"))])
    comps = pd.concat([pd.read_csv(f, parse_dates=["day", "start", "end", "av"]) for f in sorted(OUT.glob("comps_*.csv"))])
    img = OUT / "img"
    img.mkdir(exist_ok=True)
    for k, name in enumerate(REGIMES):
        r = reg[reg.regime == name].sort_values("date")
        picks, last = [], None
        for row in r[::-1].itertuples():                # последние дни режима, не подряд
            if last is None or (last - row.date).days > 7:
                picks.append(row)
                last = row.date
            if len(picks) == per:
                break
        if not picks:
            continue
        fig, axs = plt.subplots(1, len(picks), figsize=(8 * len(picks), 5.2), squeeze=False)
        for ax, p in zip(axs[0], picks):
            panel(ax, p.date, p, bars, days, snaps, comps)
        fig.tight_layout()
        out = img / f"regime_{k + 1}.png"
        fig.savefig(out, dpi=130)
        plt.close(fig)
        print(out, name, [f"{p.date:%Y-%m-%d}" for p in picks])


if __name__ == "__main__":
    main(*map(int, sys.argv[1:]))
