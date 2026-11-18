"""Контекст дня на графике: свечи вчера и сегодня, уровни профиля, голые POC, зоны 1.8 и лента статуса по периодам.

Сверху — RTH 30m вчера и сегодня (цены графика), вчерашние VAH / VAL (пунктир), POC (оранжевый), High / Low (серые),
голые POC (фиолетовый пунктир), зоны 1.8 на сегодня (синие ярко, красные / зелёные бледно, серые не рисуются),
развивающийся POC дня (оранжевые точки), TPO-профили обоих дней справа (столбики по 1 п.).
Снизу — статус после каждого периода: вероятность цели до конца RTH (модель — scripts.day_status, учится на днях до
2025). Под свечой периода — его столбец. Зелёная рамка — цель потом взята, · — уже взята к этому моменту, пусто —
уровня нет; у целей, которые меняются по ходу дня (синие, nPOC), в ячейке — цена цели.

Запуск:  python -m scripts.day_status_plot 2026-11-05 [ещё даты ...]     Результат: output/z18/img/status_<дата>.png
         python -m scripts.day_status_plot 2026-11-06@C [...]   — вид «сейчас»: график до конца периода, у уровней —
                                                                  вероятности; output/z18/img/now_<дата>_<период>.png
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




def plot_moment(D, letter, real, split, bars, days, snaps, npocs, tick):
    """Вид «сейчас»: график до конца периода letter, у каждого уровня — вероятность дойти до него до конца дня."""
    D = pd.Timestamp(D)
    k = LETTERS.index(letter)
    st = status_probs(real, D, split)
    r = st[st.k == k].iloc[0]
    i = days.index[days.date == D][0]
    day, yday = days.loc[i], days.loc[i - 1]
    g = bars[bars.date == D].sort_values("period").reset_index(drop=True)
    gy = bars[bars.date == yday.date].sort_values("period").reset_index(drop=True)
    X0 = len(gy) + 1
    gk = g.iloc[:k + 1]
    fig, ax = plt.subplots(figsize=(13, 7.5))
    lo = min(g.low.min(), gy.low.min())
    hi = max(g.high.max(), gy.high.max())
    pad = (hi - lo) * 0.08
    ylo, yhi = lo - pad, hi + pad
    xr = X0 + 13 + 9
    z = snaps[(snaps.day == D) & (snaps.hi >= ylo) & (snaps.lo <= yhi)]
    for zz in z.itertuples():                              # все зоны 1.8 на сегодня, цвета индикатора, серые бледно
        c, a = ZC.get(zz.kind, ("#808080", 0.10))
        ax.add_patch(Rectangle((-0.5, zz.lo), xr + 0.5, zz.hi - zz.lo, color=c, alpha=min(a + 0.05, 0.3), lw=0, zorder=0))
    candles(ax, gy, 0)
    candles(ax, gk, X0)
    ax.axvline(X0 + k + 0.5, color="#999", lw=1, ls=":")
    ax.text(X0 + k + 0.7, ylo, f"сейчас: после {letter}", fontsize=9, va="bottom", color="#555")
    # уровни с вероятностями
    items = [("POC вчера", day.y_poc, "y_poc", "#ef6c00", "-"), ("VAH вчера", day.y_vah, "y_vah", "#333", "--"),
             ("VAL вчера", day.y_val, "y_val", "#333", "--"), ("High вчера", day.y_high, "y_high", "#777", ":"),
             ("Low вчера", day.y_low, "y_low", "#777", ":"),
             ("nPOC", r.npoc_up_px, "npoc_up", "#8e24aa", "-."), ("nPOC", r.npoc_dn_px, "npoc_dn", "#8e24aa", "-."),
             ("синяя зона", r.blue_up_px, "blue_up", "#00158B", "-"), ("синяя зона", r.blue_dn_px, "blue_dn", "#00158B", "-")]
    hk, lk = gk.high.max(), gk.low.min()
    items += [("новый High дня (выше)", hk, "ext_up", "#1a7f37", "-"), ("новый Low дня (ниже)", lk, "ext_dn", "#c62828", "-")]
    lab = []
    for name, y, tg, col, ls in items:
        if pd.isna(y) or not (ylo <= y <= yhi):
            continue
        v = r[tg]
        if pd.isna(v):
            txt = f"{name} {y:.2f} — уже взят"
            fc, tc = "#eeeeee", "#888"
        else:
            p = r["p_" + tg]
            txt = f"{name} {y:.2f} — {p:.0%}"
            fc, tc = CMAP(p), ("white" if p > 0.55 else "#111")
        ax.hlines(y, -0.5, X0 + 13, color=col, ls=ls, lw=1.4 if tg.startswith(("y_poc", "ext")) else 1.0, zorder=2)
        lab.append((y, txt, fc, tc))
    lab.sort()
    gap = (yhi - ylo) * 0.045
    ly = -1e9
    for y, txt, fc, tc in lab:
        ly = max(y, ly + gap)
        ax.annotate(txt, (X0 + 13, y), (X0 + 13.6, ly), fontsize=9.5, va="center", color=tc,
                    bbox=dict(fc=fc, ec="#bbb", boxstyle="round,pad=0.3"),
                    arrowprops=dict(arrowstyle="-", color="#bbb", lw=0.8))
    ctrl = {3: "покупатель (+3)", 2: "покупатель (+2)", 1: "скорее покупатель", 0: "поровну",
            -1: "скорее продавец", -2: "продавец (−2)", -3: "продавец (−3)"}[int(r.control)]
    info = (f"Открылись: {r.open_loc} вчерашнего профиля · A {r.a_accept}\n"
            f"Сейчас цена {r.px_vs_yva} вчера, {'выше' if r.px_vs_dpoc == 'выше' else 'ниже'} POC дня · "
            f"кто обновляет принты: {ctrl}\n"
            f"В рамке — вероятность, что цена дойдёт до уровня до конца RTH (по похожим дням до 2025)")
    ax.text(0, yhi, info, fontsize=9.5, va="top", bbox=dict(fc="white", ec="#ccc", alpha=0.92))
    ax.set_ylim(ylo, yhi)
    ax.set_xlim(-0.8, xr)
    ax.set_xticks([x for x in range(13)] + [X0 + j for j in range(13)],
                  list(LETTERS) + list(LETTERS), fontsize=7.5)
    ax.tick_params(axis="y", labelsize=8)
    ax.grid(axis="y", lw=0.3, alpha=0.4)
    ax.set_title(f"{D:%d.%m.%Y} после {letter}  (слева — вчера {yday.date:%d.%m}, справа — сегодня)", fontsize=11)
    out = OUT / "img" / f"now_{D:%Y-%m-%d}_{letter}.png"
    fig.savefig(out, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return out


def main_moment(*specs):
    cfg = load_config(instrument="ES")
    tick = cfg["data"]["instruments"]["ES"]["tick_size"]
    split = pd.Timestamp(cfg["day_context"]["split_date"])
    bars = load_bars("ES", cfg)
    days = day_table(bars, cfg, tick)
    real = pd.read_csv(OUT / "day_status.csv", parse_dates=["date"])
    snaps = pd.concat([pd.read_csv(f, parse_dates=["day"]) for f in sorted(OUT.glob("snap_*.csv"))])
    for sp in specs:
        d, letter = sp.split("@")
        print(plot_moment(d, letter, real, split, bars, days, snaps, None, tick))


if __name__ == "__main__":
    a = sys.argv[1:]
    main_moment(*a) if a and "@" in a[0] else main(*a)
