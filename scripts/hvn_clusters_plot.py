"""Картинки кластеров HVN-зон: несколько «нормальных» и несколько «спорных».

Кластер — группа зон, связанных несклеенными парами (пересечение ≥ 50%,
но объединение шире 10 п.).
  нормальный: 2–3 зоны, нет зон с двойной ролью, все дни в пределах 60 дней;
  спорный:    есть зоны «поддержка+сопротивление»; берутся самые свежие
              кластеры размером 5–6, 8–13 и ≥ 20 зон.

Слева — 30m-бары (back-adjusted) и зоны: полоса начинается в день, из
которого зона построена, и тянется вправо; точки — дни, вошедшие в зону.
Справа — «лестница»: зоны кластера по цене, чтобы видеть перекрытия.

Запуск:  python -m scripts.hvn_clusters_plot [ES]   (сначала scripts.hvn_zones)
Результат: output/<INSTR>_hvn_clusters_normal.png, output/<INSTR>_hvn_clusters_disputed.png
"""
from __future__ import annotations

import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Patch, Rectangle

from scripts.stage1_profiles import load_adjusted
from tpo.data import ROOT, load_config
from tpo.hvn_zones import BOTH, RANGE, RESISTANCE, SUPPORT
from tpo.plot import bars_on_days

STYLE = {  # цвет, штриховка, короткая подпись
    SUPPORT: ("#008300", None, "П"),
    RESISTANCE: ("#e34948", None, "С"),
    BOTH: ("#6250d6", "///", "П+С"),
    RANGE: ("#8a8984", None, "пр."),
}
INK, MUTED, SURFACE = "#0b0b0b", "#52514e", "#fcfcfb"


def cluster_table(z: pd.DataFrame) -> pd.DataFrame:
    g = z.groupby("cluster_id").agg(
        n=("zone_id", "size"), both=("role", lambda s: (s == BOTH).sum()),
        d0=("first_day", "min"), d1=("last_day", "max"), lo=("lo", "min"), hi=("hi", "max"))
    g["span"] = (g.d1 - g.d0).dt.days
    return g


def pick(g: pd.DataFrame) -> tuple[list[int], list[int]]:
    normal = g[g.n.between(2, 3) & (g.both == 0) & (g.span <= 60)].sort_values("d1").index[-3:]
    disputed = []
    for lo, hi in [(5, 6), (8, 13), (20, 10_000)]:
        c = g[(g.both > 0) & g.n.between(lo, hi)].sort_values("d1")
        if len(c):
            disputed.append(c.index[-1])
    return list(normal), disputed


def draw_cluster(ax, lad, zc: pd.DataFrame, daily: pd.DataFrame, bars: pd.DataFrame, periods: int,
                 title: str) -> None:
    dates = list(daily.date)
    pos = {d: i for i, d in enumerate(dates)}
    i0 = max(pos[zc.first_day.min()] - 10, 0)
    i1 = min(pos[zc.last_day.max()] + 15, len(dates) - 1)
    win = daily.iloc[i0:i1 + 1]
    pad = max(8.0, 0.25 * (zc.hi.max() - zc.lo.min()))
    y0, y1 = zc.lo.min() - pad, zc.hi.max() + pad

    bars_on_days(ax, bars, list(win.date), periods, color=MUTED, lw=0.5, alpha=0.7)
    right = len(win) - 0.5
    for r in zc.sort_values("first_day").itertuples():
        color, hatch, short = STYLE[r.role]
        x = pos[r.first_day] - i0
        ax.add_patch(Rectangle((x, r.lo), right - x, r.hi - r.lo, facecolor=color, alpha=0.13,
                               edgecolor="none", zorder=2))
        ax.add_patch(Rectangle((x, r.lo), right - x, r.hi - r.lo, facecolor="none", edgecolor=color,
                               lw=1.0, hatch=hatch, alpha=0.8, zorder=3))
        for d in r.days.split():
            ax.plot(pos[pd.Timestamp(d)] - i0, (r.lo + r.hi) / 2, "o", ms=4, color=color,
                    mec=SURFACE, mew=1, zorder=4)
    ax.set_ylim(y0, y1)
    ax.set_xlim(-0.5, right)
    step = max(len(win) // 6, 1)
    ticks = list(range(0, len(win), step))
    ax.set_xticks(ticks, [f"{win.date.iloc[t]:%d.%m.%y}" for t in ticks], fontsize=7, color=MUTED)
    ax.tick_params(axis="y", labelsize=7, colors=MUTED)
    ax.set_title(title, fontsize=9, loc="left", color=INK)
    _clean(ax)

    zs = zc.sort_values(["lo", "hi"]).reset_index(drop=True)
    for k, r in zs.iterrows():
        color, hatch, short = STYLE[r.role]
        lad.add_patch(Rectangle((k - 0.32, r.lo), 0.64, r.hi - r.lo, facecolor=color, alpha=0.85,
                                edgecolor=SURFACE, lw=1, hatch=hatch))
        if len(zs) <= 12:
            lad.text(k, r.hi + pad * 0.08, f"#{r.zone_id}\n{short}", ha="center", va="bottom",
                     fontsize=6, color=INK)
    lad.set_xlim(-0.8, len(zs) - 0.2)
    lad.set_ylim(y0, y1)
    lad.set_xticks([])
    lad.tick_params(axis="y", labelsize=7, colors=MUTED)
    lad.set_title("зоны по цене", fontsize=8, loc="left", color=MUTED)
    _clean(lad)


def _zones_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "зона"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "зоны"
    return "зон"


def _clean(ax) -> None:
    ax.set_facecolor(SURFACE)
    ax.grid(axis="y", color="#e4e3df", lw=0.6)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#c9c8c2")


def figure(clusters, z, g, daily, bars, periods, title, path) -> None:
    fig, axes = plt.subplots(len(clusters), 2, figsize=(13, 3.6 * len(clusters)),
                             gridspec_kw={"width_ratios": [3.2, 1]}, squeeze=False)
    fig.patch.set_facecolor(SURFACE)
    for (ax, lad), cid in zip(axes, clusters):
        c = g.loc[cid]
        zc = z[z.cluster_id == cid]
        roles = ", ".join(f"{STYLE[k][2]} {v}" for k, v in zc.role.value_counts().items())
        draw_cluster(ax, lad, zc, daily, bars, periods,
                     f"Кластер {cid}: {c.n} {_zones_word(c.n)}, {c.lo:.2f}–{c.hi:.2f} ({c.hi - c.lo:.1f} п.), "
                     f"{c.d0:%d.%m.%Y}–{c.d1:%d.%m.%Y}  ·  {roles}")
    handles = [Patch(facecolor=STYLE[r][0], hatch=STYLE[r][1], edgecolor=SURFACE, label=r)
               for r in (SUPPORT, RESISTANCE, BOTH)]
    fig.legend(handles=handles, loc="upper right", ncol=3, fontsize=8, frameon=False)
    fig.suptitle(title, x=0.01, ha="left", fontsize=11, color=INK)
    fig.text(0.01, 0.005, "Полоса зоны начинается в день, из которого она построена; точки — дни зоны. "
             "Серые штрихи — 30m-бары (диапазон high–low). Цены back-adjusted (ESZ6).",
             fontsize=7, color=MUTED)
    fig.tight_layout(rect=(0, 0.015, 1, 0.97))
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def main(instrument: str = "ES") -> None:
    cfg = load_config(instrument=instrument)
    out = ROOT / "output"
    z = pd.read_csv(out / f"{instrument}_hvn_zones.csv", parse_dates=["first_day", "last_day"])
    bars, daily, _, _, _ = load_adjusted(instrument, cfg)
    periods = cfg["data"]["expected_periods_per_day"]
    g = cluster_table(z)
    normal, disputed = pick(g)
    figure(normal, z, g, daily, bars, periods, "Нормальные кластеры: пара зон, одна над другой", out / f"{instrument}_hvn_clusters_normal.png")
    figure(disputed, z, g, daily, bars, periods, "Спорные кластеры: цепочки зон, у которых роль двойная",
           out / f"{instrument}_hvn_clusters_disputed.png")
    print("нормальные:", normal, "спорные:", disputed)
    print(g.loc[normal + disputed].to_string())


if __name__ == "__main__":
    main(*sys.argv[1:])
