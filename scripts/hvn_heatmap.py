"""Тепловая карта HVN-пиков 8–10 принтов, продлённых вправо до конца данных.

Для каждого полного дня, где максимум TPO внутри VA = 8–10, берутся строки
внутри VA с TPO ≥ 8 (сам пик). Со следующего дня каждая такая строка
«горит» бесконечно вправо. Плотность в день t на цене p — сколько пиков,
появившихся до t, покрывают корзину p (корзина = bin_points пунктов).

Запуск:  python -m scripts.hvn_heatmap [ES] [bin_points] [zoom_days]
Результат: output/<INSTR>_hvn_heatmap.png, output/<INSTR>_hvn_density.csv
           (плотность по ценам на последний день)
"""
from __future__ import annotations

import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap

from scripts.stage1_profiles import load_adjusted
from tpo.data import ROOT, load_config

INK, MUTED, SURFACE = "#0b0b0b", "#52514e", "#fcfcfb"
CMAP = LinearSegmentedColormap.from_list("heat", ["#fde9d3", "#f6a35c", "#d95926", "#8c2a0c", "#3d1003"])


def peak_rows(daily, profiles, tick, lo_tpo, hi_tpo):
    """[(date, массив цен строк пика в пунктах)] для зачётных дней."""
    out = []
    for d in daily[~daily.short_day].itertuples():
        p = profiles[d.date]
        a = (int(round(d.val / tick)) - p.low) // p.row
        b = (int(round(d.vah / tick)) - p.low) // p.row
        va = p.counts[a:b + 1]
        if lo_tpo <= va.max() <= hi_tpo:
            idx = np.flatnonzero(va >= lo_tpo) + a
            out.append((d.date, (p.low + idx * p.row) * tick))
    return out


def density(daily, peaks, bin_pts):
    dates = list(daily.date)
    pos = {d: i for i, d in enumerate(dates)}
    pmin = daily.low.min() - 5
    pmax = daily.high.max() + 5
    edges = np.arange(np.floor(pmin), np.ceil(pmax) + bin_pts, bin_pts)
    inc = np.zeros((len(dates), len(edges) - 1), dtype=np.int32)
    for d, prices in peaks:
        i = pos[d] + 1                       # доступен со следующего дня
        if i >= len(dates):
            continue
        bins = np.unique(np.clip(np.searchsorted(edges, prices, side="right") - 1, 0, len(edges) - 2))
        inc[i, bins] += 1
    return edges, np.cumsum(inc, axis=0)


def draw(ax, prof_ax, daily, edges, dens, i0, title):
    win = daily.iloc[i0:]
    D = dens[i0:].T.astype(float)
    lo, hi = win.low.min() - 20, win.high.max() + 20
    rows = (edges[:-1] >= lo) & (edges[1:] <= hi)
    D = D[rows]
    e = edges[np.r_[rows, False] | np.r_[False, rows]]
    D[D == 0] = np.nan
    vmax = np.nanpercentile(D, 99.5) if np.isfinite(D).any() else 1
    x = np.arange(len(win) + 1) - 0.5
    m = ax.pcolormesh(x, e, D, cmap=CMAP, vmin=1, vmax=vmax, shading="flat", rasterized=True)
    ax.plot(np.arange(len(win)), win.close, color=INK, lw=0.7)
    ax.vlines(np.arange(len(win)), win.low, win.high, color=INK, lw=0.4, alpha=0.35)
    ax.set_ylim(lo, hi)
    ax.set_xlim(-0.5, len(win) - 0.5)
    step = max(len(win) // 8, 1)
    t = list(range(0, len(win), step))
    ax.set_xticks(t, [f"{win.date.iloc[k]:%d.%m.%y}" for k in t], fontsize=7, color=MUTED)
    ax.tick_params(axis="y", labelsize=7, colors=MUTED)
    ax.set_title(title, fontsize=9, loc="left", color=INK)
    last = dens[-1][rows]
    centers = (e[:-1] + e[1:]) / 2
    prof_ax.barh(centers, last, height=np.diff(e), color="#d95926", alpha=0.85)
    prof_ax.axhline(daily.close.iloc[-1], color=INK, lw=0.8, ls="--")
    prof_ax.set_ylim(lo, hi)
    prof_ax.set_yticks([])
    prof_ax.tick_params(axis="x", labelsize=7, colors=MUTED)
    prof_ax.set_title("плотность сейчас", fontsize=8, loc="left", color=MUTED)
    for a in (ax, prof_ax):
        a.set_facecolor(SURFACE)
        for s in ("top", "right"):
            a.spines[s].set_visible(False)
        for s in ("left", "bottom"):
            a.spines[s].set_color("#c9c8c2")
    return m


def main(instrument: str = "ES", bin_points: str = "1", zoom_days: str = "250") -> None:
    cfg = load_config()
    zc = cfg["hvn_zones"]
    bin_pts, zoom = float(bin_points), int(zoom_days)
    _, daily, profiles, _, tick = load_adjusted(instrument, cfg)
    daily = daily.sort_values("date").reset_index(drop=True)
    peaks = peak_rows(daily, profiles, tick, zc["min_tpo"], zc["max_tpo"])
    edges, dens = density(daily, peaks, bin_pts)

    out = ROOT / "output"
    centers = (edges[:-1] + edges[1:]) / 2
    pd.DataFrame({"price": centers, "density": dens[-1]}).query("density > 0").to_csv(
        out / f"{instrument}_hvn_density.csv", index=False, float_format="%.2f")

    fig, axes = plt.subplots(2, 2, figsize=(15, 13), gridspec_kw={"width_ratios": [5, 1], "hspace": 0.15,
                                                                   "wspace": 0.03})
    fig.subplots_adjust(top=0.9, bottom=0.08, left=0.05, right=0.98)
    fig.patch.set_facecolor(SURFACE)
    m = draw(axes[0, 0], axes[0, 1], daily, edges, dens, 0,
             f"Вся история: {len(peaks)} пиков 8–10 TPO, продлены вправо")
    draw(axes[1, 0], axes[1, 1], daily, edges, dens, max(len(daily) - zoom, 0),
         f"Последние {zoom} дней")
    cb = fig.colorbar(m, cax=fig.add_axes([0.62, 0.935, 0.25, 0.01]), orientation="horizontal")
    cb.set_label("сколько пиков на цене", fontsize=8, color=MUTED)
    cb.ax.tick_params(labelsize=7, colors=MUTED)
    fig.suptitle(f"{instrument}: плотность HVN-пиков 8–10 принтов (строки внутри VA с TPO ≥ {zc['min_tpo']}), "
                 f"корзина {bin_pts:g} п.", x=0.01, ha="left", y=0.955, fontsize=11, color=INK)
    fig.text(0.01, 0.035, "Каждый пик начинает гореть со следующего дня и не гаснет. Чёрная линия — close, "
             "штрихи — дневной диапазон. Цены back-adjusted (ESZ6). Справа — плотность на последний день; "
             "пунктир — последний close.", fontsize=7, color=MUTED)
    fig.savefig(out / f"{instrument}_hvn_heatmap.png", dpi=140, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)

    last = daily.close.iloc[-1]
    near = pd.DataFrame({"price": centers, "density": dens[-1]})
    near = near[(near.price - last).abs() <= 150]
    print(f"Пиков: {len(peaks)}, строк в пиках: медиана {np.median([len(p) for _, p in peaks]):.0f}")
    print(f"Плотность сейчас: максимум {dens[-1].max()} на {centers[dens[-1].argmax()]:.1f}")
    print(f"Топ-10 цен в ±150 п. от {last:.2f}:")
    print(near.sort_values("density", ascending=False).head(10).to_string(index=False))


if __name__ == "__main__":
    main(*sys.argv[1:])
