"""Карта зон на 30m-графике «как в жизни»: последние N сессий.

Свечи — набор баров (по умолчанию ES_ETH, ETH+RTH), RTH-часы подсвечены.
Зоны (по RTH-профилям, scripts/day_context.zone_map) рисуются по сессиям —
только те, что были известны в эту сессию; HVN-зоны окрашены по классу на
этот день (поддержка / сопротивление / проторговка / без истории), серые
(незаполненные single prints) исчезают после проторговки. Для каждой
сессии — VAH / VAL / POC предыдущего RTH-дня.

Запуск:  python -m scripts.zone_map_plot [ES] [ES_ETH] [sessions] [full|clean]
Результат: output/<TAG>_zone_map.png, output/<TAG>_zone_map_clean.png
"""
from __future__ import annotations

import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from scripts.day_context import active, hvn_class_table, zone_map
from scripts.hvn_va_stats import per_day
from scripts.primary_zones import zone_frame
from scripts.stage1_profiles import load_adjusted, load_test_bars, tag
from scripts.top_zones_plot import INK, MUTED, SURFACE, _clean, candles
from tpo.balance import find_balances
from tpo.data import ROOT, load_config
from tpo.day_context import grey_zones

STYLE = {  # цвет, прозрачность, штриховка, подпись
    "old_balance": ("#2a78d6", 0.10, None, "старый баланс (VA композита)"),
    "hvn_S": ("#008300", 0.30, None, "HVN · поддержка"),
    "hvn_R": ("#e34948", 0.30, None, "HVN · сопротивление"),
    "hvn_range": ("#6250d6", 0.25, None, "HVN · проторговка"),
    "hvn_new": ("#8a8984", 0.25, None, "HVN · без истории"),
    "grey": ("#8a8984", 0.18, "////", "серая (незаполненные single prints)"),
}
ORDER = ["old_balance", "grey", "hvn_new", "hvn_range", "hvn_S", "hvn_R"]


def main(instrument: str = "ES", bars_instrument: str = "ES_ETH", sessions: str = "10", view: str = "full") -> None:
    """view: full — все зоны; clean — HVN-зоны с классами, старые балансы контуром, без серых."""
    cfg = load_config(instrument=instrument)
    cc, zc = cfg["day_context"], cfg["hvn_zones"]
    out = ROOT / "output"
    n_s = int(sessions)
    rbars, daily, profiles, _, tick = load_adjusted(instrument, cfg)
    comps, _ = find_balances(rbars, daily, profiles, cfg, tick)
    comps = comps[(comps.end_reason != "open") & (comps.n_days >= cfg["zones"].get("min_composite_days", 0))]
    dd = per_day(daily, profiles, tick, cfg["intraday"]["hvn_share"]).sort_values("date")
    dd["ok"] = dd.hvn_tpo.between(zc["min_tpo"], zc["max_tpo"])
    prim = zone_frame(dd, daily, cfg, tick)
    prim = prim[prim.available_from.notna()].reset_index(drop=True)
    grey = grey_zones(daily, profiles, tick, cc["grey_min_ticks"])
    share = cfg["wicks"]["class_rejection_share"]
    cls_tab = hvn_class_table(rbars, prim, cfg["wicks"]["min_wick_ticks"] * tick, share)
    zmap = zone_map(comps, prim, grey)

    b = (load_test_bars(bars_instrument)[0] if bars_instrument != instrument else rbars)
    b = b.sort_values("ts").reset_index(drop=True)
    sess = np.sort(b.date.unique())[-n_s:]
    w = b[b.date.isin(sess)].reset_index(drop=True)
    x = np.arange(len(w))
    lo_p, hi_p = w.low.min() - 15, w.high.max() + 15
    rth_days = daily[~daily.short_day].sort_values("date").reset_index(drop=True)
    last_rth = rth_days.date.max()

    fig, ax = plt.subplots(figsize=(18, 10))
    fig.patch.set_facecolor(SURFACE)
    starts = np.flatnonzero(np.r_[True, w.date.to_numpy()[1:] != w.date.to_numpy()[:-1]])
    ends = np.r_[starts[1:], len(w)]
    tod = (w.ts.dt.hour * 60 + w.ts.dt.minute).to_numpy()
    rth = (tod >= 8 * 60 + 30) & (tod < 15 * 60)
    for s0, s1 in zip(starts, ends):
        d = w.date[s0]
        x0, x1 = s0 - 0.5, s1 - 0.5
        # RTH-часы сессии
        idx = np.flatnonzero(rth[s0:s1]) + s0
        if len(idx):
            ax.axvspan(idx.min() - 0.5, idx.max() + 0.5, color="#f1f0ec", zorder=0, lw=0)
        ax.axvline(x0, color="#d8d7d2", lw=0.6, zorder=0)
        # зоны, известные в эту сессию (по RTH-профилям до неё)
        a = active(zmap, d, cls_tab, share)
        a = a[(a.hi >= lo_p) & (a.lo <= hi_p)]
        for t in ORDER:
            col, alpha, hatch, _ = STYLE[t]
            if view == "clean" and t == "grey":
                continue
            if view == "clean" and t == "old_balance":
                for z in a[a.type == t].itertuples():
                    ax.hlines([z.lo, z.hi], x0, x1, color=col, lw=1.0, linestyles=(0, (6, 3)), zorder=2)
                continue
            for z in a[a.type == t].itertuples():
                ax.add_patch(plt.Rectangle((x0, z.lo), x1 - x0, max(z.hi - z.lo, tick), facecolor=col,
                                           alpha=alpha, edgecolor=col if hatch else "none", hatch=hatch,
                                           lw=0, zorder=1))
                if t != "old_balance":
                    ax.hlines([z.lo, z.hi], x0, x1, color=col, lw=0.5, alpha=0.8, zorder=1)
        # вчерашний RTH-день
        k = np.searchsorted(rth_days.date.to_numpy(), np.datetime64(d)) - 1
        if k >= 0:
            p = rth_days.iloc[k]
            ax.hlines([p.vah, p.val], x0, x1, color=INK, lw=0.9, linestyles="--", zorder=3)
            ax.hlines(p.poc, x0, x1, color="#eb6834", lw=1.4, zorder=3)
        ax.text((x0 + x1) / 2, hi_p, f"{pd.Timestamp(d):%a %d.%m}", ha="center", va="top", fontsize=8,
                color=MUTED)
    candles(ax, x, w.open.to_numpy(), w.high.to_numpy(), w.low.to_numpy(), w.close.to_numpy(), w=0.6)
    ax.set_ylim(lo_p, hi_p)
    ax.set_xlim(-1, len(w) + 1)
    st = np.flatnonzero((w.ts.dt.hour.to_numpy() == 8) & (w.ts.dt.minute.to_numpy() == 30))
    ax.set_xticks(st, ["08:30" for _ in st], fontsize=7)
    _clean(ax)
    ax.yaxis.tick_right()
    shown = [t for t in reversed(ORDER) if not (view == "clean" and t in ("grey", "old_balance"))]
    handles = [Patch(facecolor=STYLE[t][0], alpha=min(1, STYLE[t][1] * 2.2), hatch=STYLE[t][2],
                     edgecolor=STYLE[t][0] if STYLE[t][2] else "none", label=STYLE[t][3]) for t in shown]
    if view == "clean":
        handles.append(Line2D([], [], color=STYLE["old_balance"][0], lw=1.0, ls=(0, (6, 3)),
                              label="старый баланс (VA композита), границы"))
    handles += [Line2D([], [], color="#eb6834", lw=1.4, label="POC вчерашнего RTH"),
                Line2D([], [], color=INK, lw=0.9, ls="--", label="VAH / VAL вчерашнего RTH")]
    ax.legend(handles=handles, loc="upper left", fontsize=8, frameon=True, facecolor=SURFACE, edgecolor="#d8d7d2",
              ncol=2)
    name = tag(instrument, bars_instrument, None)
    # какая доля цен в окне графика покрыта зонами на последнюю сессию
    a_last = active(zmap, sess[-1], cls_tab, share)
    grid = np.arange(lo_p, hi_p, tick)
    def cover(types):
        z = a_last[a_last.type.isin(types)]
        m = np.zeros(len(grid), bool)
        for r in z.itertuples():
            m |= (grid >= r.lo) & (grid <= r.hi)
        return m.mean()
    hv = ["hvn_S", "hvn_R", "hvn_range", "hvn_new"]
    cov = (f"Покрытие цен окна зонами на {pd.Timestamp(sess[-1]):%d.%m}: все {cover(ORDER):.0%}, "
           f"HVN {cover(hv):.0%}, старые балансы {cover(['old_balance']):.0%}, серые {cover(['grey']):.0%}")
    print(cov)
    ax.set_title(f"{name}: карта зон на 30m, последние {n_s} сессий (время CT; светлый фон — RTH 08:30–15:00)",
                 fontsize=11, loc="left", color=INK)
    note = (cov + ".  " + f"Зоны и уровни — по RTH-профилям до каждой сессии; RTH-данные по {last_rth:%d.%m.%Y}, "
            f"после — зоны на эту дату. Цены back-adjusted (ESZ6).")
    fig.text(0.01, 0.01, note, fontsize=7.5, color=MUTED)
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    path = out / f"{name}_zone_map{'_clean' if view == 'clean' else ''}.png"
    fig.savefig(path, dpi=140, facecolor=SURFACE)
    plt.close(fig)
    print(path)


if __name__ == "__main__":
    main(*sys.argv[1:])
